"""Brief 章节的一次调用并行写作与证据预算装配。"""

import asyncio
import logging
import re

from openjiuwen_deepsearch.algorithm.brief_report.markdown import sanitize_brief_chapter
from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefAssemblyRequest,
    BriefChapter,
    BriefReportAssembly,
    BriefSection,
    BriefSummaryRequest,
    BriefWritingEvidence,
    BriefWritingRequest,
)
from openjiuwen_deepsearch.algorithm.source_trace.source_tracer import SourceTracer
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.search_nodes.llm_utils import _is_context_limit_error as _matches_context_limit
from openjiuwen_deepsearch.common.common_constants import ENGLISH
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


logger = logging.getLogger(__name__)
_RESPONSE_PREVIEW_LIMIT = 500
_MERMAID_FENCE_PATTERN = re.compile(r"(?im)^\s*```\s*mermaid\b")


def _response_preview(content: str) -> str:
    """记录模型输出的有限摘要；敏感模式下只暴露长度。"""
    if LogManager.is_sensitive():
        return f"<{len(content)} chars>"
    preview = content.replace("\n", "\\n")
    return preview[:_RESPONSE_PREVIEW_LIMIT] + ("..." if len(preview) > _RESPONSE_PREVIEW_LIMIT else "")


def _chapter_validation_error(content: str) -> ValueError | None:
    """校验章节正文，返回需要重试的校验错误。

    拒绝正文代理越权生成图表（Mermaid），并区分空响应与未闭合代码围栏。
    章节长度仅由 prompt 的目标长度软约束引导，不做代码级上限校验。

    Args:
        content: 模型输出的章节 Markdown 正文。

    Returns:
        校验失败时返回携带失败原因的 ValueError；通过时返回 None。
    """
    if not content:
        return ValueError("brief chapter validation failed: empty_content")
    if _MERMAID_FENCE_PATTERN.search(content):
        return ValueError("brief chapter validation failed: mermaid_output_forbidden")
    fence_count = content.count("```")
    if fence_count % 2:
        return ValueError(
            "brief chapter validation failed: "
            f"unclosed_code_fence (code_fence_count={fence_count})"
        )
    return None


def _numbered_chapter_outline(section: BriefSection) -> str:
    """仅提供固定一级标题，避免将内部研究步骤暴露为读者可见标题。"""
    return f"{section.id} {section.title}"


def _writing_prompt_input(
    request: BriefWritingRequest,
    section: BriefSection,
    documents: list[dict],
) -> dict:
    """按原 Brief 子报告契约构造单章 Prompt 与引用包围的资料消息。"""
    format_requirements = [
        f"输出形式：{', '.join(item.value for item in section.output_formats)}。",
        *([section.format_note] if section.format_note else []),
        *[f"研究要求：{step.requirement}" for step in section.research_steps],
        *([f"报告模板约束：{request.user_format}"] if request.user_format else []),
    ]
    collected_information = "\n\n".join(
        "\n".join(
            [
                f"[citation:{item['index']} begin]",
                f"Title: {item['title']}",
                f"URL: {item['url']}",
                str(item["snippet"]),
                f"[citation:{item['index']} end]",
            ]
        )
        for item in documents
    )
    messages = [
        {
            "role": "user",
            "content": f"Collected Information:\n{collected_information or 'No collected information is available.'}",
        }
    ]
    guidance = request.writing_guidance
    section_guidance = next(
        (item.guidance for item in guidance.section_guidance if item.section_id == section.id),
        "",
    ) if guidance else ""
    if guidance and (guidance.report_strategy or section_guidance):
        guidance_lines = ["Internal Writing Guidance (editorial only; not evidence):"]
        if guidance.report_strategy:
            guidance_lines.append(f"报告主线：{guidance.report_strategy}")
        if section_guidance:
            guidance_lines.append(f"本章指引：{section_guidance}")
        messages.append({"role": "user", "content": "\n".join(guidance_lines)})
    return {
        "language": request.language,
        "audience_role": request.audience_role,
        "tone": request.tone,
        "outline": request.outline.model_dump(),
        "current_section": section.title,
        "current_section_description": section.goal,
        "current_section_format_requirements": "\n".join(format_requirements),
        "current_chapter_outline": _numbered_chapter_outline(section),
        "messages": messages,
    }


def build_writing_evidence(request: BriefWritingRequest, section: BriefSection) -> BriefWritingEvidence:
    """按阻断步骤、最小覆盖和评估顺序装配可进入上下文的证据。"""
    evidence = request.collection.section_evidence[section.id]
    registry_by_source = {item.source_id: item for item in request.collection.citation_registry}
    coverage_by_step = {item.step_id: item for item in evidence.coverage}
    ranked = sorted(
        evidence.selected_docs,
        key=lambda item: (
            0
            if any(
                coverage_by_step.get(step_id) and coverage_by_step[step_id].blocking_gap
                for step_id in item.step_ids
            )
            else 1,
            item.evaluation_rank,
            item.source_id,
        ),
    )
    required_steps = [
        step.id
        for step in section.research_steps
        if coverage_by_step.get(step.id) and coverage_by_step[step.id].blocking_gap
    ]
    covered_steps: list[str] = []
    for step in section.research_steps:
        coverage = coverage_by_step.get(step.id)
        if coverage is None or coverage.status.value != "covered":
            continue
        if step.id not in required_steps:
            covered_steps.append(step.id)
    required_steps.extend(covered_steps)
    ordered, used = [], set()
    for step_id in required_steps:
        best = next((item for item in ranked if step_id in item.step_ids), None)
        if best is not None and best.source_id not in used:
            ordered.append(best)
            used.add(best.source_id)
    ordered.extend(item for item in ranked if item.source_id not in used)
    rows: list[dict] = []
    for item in ordered:
        record = registry_by_source.get(item.source_id)
        if record is None:
            continue
        row = {
            "index": record.index,
            "title": record.title,
            "url": record.url,
            "snippet": record.original_content,
            "step_ids": item.step_ids,
        }
        rows.append(row)
    return BriefWritingEvidence(documents=rows, coverage=evidence.coverage)


def _shrink_writing_documents(documents: list[dict]) -> list[dict] | None:
    """按证据优先级递进缩减章节写作上下文。

    多条证据时移除末尾的低优先级条目；只剩一条时将其摘要缩短一半。

    Args:
        documents: 当前章节按优先级排序的写作证据。

    Returns:
        可用于下一轮重试的较小证据列表；无法再缩减时返回 None。
    """
    if len(documents) > 1:
        return documents[:-1]
    if not documents:
        return None
    snippet = str(documents[0].get("snippet") or "")
    shortened = snippet[: len(snippet) // 2].rstrip()
    if not shortened or shortened == snippet:
        return None
    return [{**documents[0], "snippet": shortened}]


async def _write_one(
    request: BriefWritingRequest,
    section: BriefSection,
    section_idx: int,
) -> BriefChapter:
    """对一个章节执行一次正常写作调用及既有重试。"""
    evidence = build_writing_evidence(request, section)
    documents = evidence.documents
    last_error: Exception | None = None
    max_attempts = max(1, Config().service_config.report_max_generate_retry_num)
    logger.info(
        "[BriefWriter] Start chapter section_id=%s documents=%d coverage=%d max_attempts=%d.",
        section.id, len(documents), len(evidence.coverage), max_attempts,
    )
    for attempt_num in range(max_attempts):
        prompt_input = _writing_prompt_input(request, section, documents)
        raw = ""
        try:
            logger.info(
                "[BriefWriter] Generate chapter section_id=%s attempt=%d/%d documents=%d.",
                section.id, attempt_num + 1, max_attempts, len(documents),
            )
            response = await ainvoke_llm_with_stats(
                request.llm,
                apply_system_prompt("brief_sub_reporter", prompt_input),
                agent_name=AgentLlmName.BRIEF_SUB_REPORTER.value,
                need_stream_out=True,
                stream_meta={"section_id": section.id, "section_idx": str(section_idx)},
            )
            raw = str(response.get("content") or "").strip()
            validation_error = _chapter_validation_error(raw)
            if validation_error is not None:
                raise validation_error
            allowed_citation_ids = {row["index"] for row in documents}
            clean = sanitize_brief_chapter(
                raw,
                section,
                allowed_citation_ids,
            )
            logger.info(
                "[BriefWriter] Generated chapter section_id=%s attempt=%d/%d content_chars=%d cleaned_chars=%d.",
                section.id, attempt_num + 1, max_attempts, len(raw), len(clean),
            )
            logger.info(
                "[BriefWriter] Generated chapter output section_id=%s raw_markdown=%s",
                section.id, _response_preview(clean),
            )
            return BriefChapter(section_id=section.id, raw_markdown=clean)
        except Exception as exc:
            last_error = exc
            validation = ""
            if "empty_content" in str(exc):
                validation = "empty_content"
            elif "mermaid_output_forbidden" in str(exc):
                validation = "mermaid_output_forbidden"
            elif "unclosed_code_fence" in str(exc):
                validation = "unclosed_code_fence"
            detail = "<detail masked>" if LogManager.is_sensitive() else str(exc)
            logger.warning(
                "[BriefWriter] Chapter generation failed; section_id=%s attempt=%d/%d "
                "documents=%d validation=%s content_chars=%d code_fence_count=%d "
                "response_preview=%s error=%s. Retry.",
                section.id, attempt_num + 1, max_attempts, len(documents), validation or "none",
                len(raw), raw.count("```"), _response_preview(raw), detail,
            )
            if _matches_context_limit(str(exc), exc):
                reduced = _shrink_writing_documents(documents)
                if reduced is None:
                    logger.warning(
                        "[BriefWriter] Context limit cannot be reduced further; "
                        "section_id=%s attempt=%d/%d. Mark chapter failed.",
                        section.id, attempt_num + 1, max_attempts,
                    )
                    break
                logger.warning(
                    "[BriefWriter] Context limit returned by model; section_id=%s "
                    "attempt=%d/%d documents=%d -> %d.",
                    section.id, attempt_num + 1, max_attempts, len(documents), len(reduced),
                )
                documents = reduced
    logger.error(
        "[BriefWriter] Chapter generation exhausted retries; section_id=%s max_attempts=%d error=%s.",
        section.id, max_attempts, "<detail masked>" if LogManager.is_sensitive() else last_error,
        exc_info=(
            None
            if LogManager.is_sensitive()
            else (type(last_error), last_error, last_error.__traceback__)
        ),
    )
    raise ValueError(f"brief chapter generation failed for {section.id}: {last_error}") from last_error


async def write_brief_chapters(request: BriefWritingRequest) -> list[BriefChapter]:
    """并行生成章节；单章失败不阻断同批其他章节。"""
    logger.info("[BriefWriter] Start chapter batch sections=%d.", len(request.outline.sections))
    results = await asyncio.gather(
        *(
            _write_one(request, section, section_idx)
            for section_idx, section in enumerate(request.outline.sections, start=1)
        ),
        return_exceptions=True,
    )
    chapters: list[BriefChapter] = []
    for section, result in zip(request.outline.sections, results, strict=True):
        if isinstance(result, BriefChapter):
            chapters.append(result)
            continue
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            logger.error(
                "[BriefWriter] Chapter failed after retries; section_id=%s error=%s.",
                section.id,
                "<detail masked>" if LogManager.is_sensitive() else result,
                exc_info=(
                    None
                    if LogManager.is_sensitive()
                    else (type(result), result, result.__traceback__)
                ),
            )
            continue
        if isinstance(result, BaseException):
            raise result
        raise TypeError(f"unexpected brief chapter result for {section.id}: {result!r}")
    order = {section.id: index for index, section in enumerate(request.outline.sections)}
    ordered = sorted(chapters, key=lambda chapter: order[chapter.section_id])
    logger.info("[BriefWriter] End chapter batch chapters=%d.", len(ordered))
    return ordered


def _first_body_paragraph(markdown: str) -> str:
    """提取首个非标题、非代码块正文段落作为摘要降级素材。"""
    return next(
        (
            p.strip()
            for p in re.split(r"\n\s*\n", markdown)
            if p.strip() and not p.strip().startswith(("#", "```"))
        ),
        "",
    )


def _summary_excerpt_parts(markdown: str) -> list[str]:
    """提取章节标题、首段及带引用事实句，供预算内渐进式压缩。"""
    heading = next((line.strip() for line in markdown.splitlines() if line.strip().startswith("#")), "")
    first_paragraph = _first_body_paragraph(markdown)
    cited_facts = [
        sentence.strip()
        for sentence in re.split(r"(?<=[。！？.!?])\s+|\n", markdown)
        if "[citation:" in sentence
    ]
    parts: list[str] = []
    for part in [heading, first_paragraph, *cited_facts]:
        if part and part not in parts:
            parts.append(part)
    return parts


def _summary_prompt_input(
    request: BriefSummaryRequest,
    chapters: list[dict[str, str]],
    gaps: list[dict[str, str]],
) -> dict:
    """按专业版摘要的 Main Content 契约传递实际保留章节文本。"""
    gap_text = "\n".join(
        f"- {item['reason']}" for item in gaps if item.get("reason")
    )
    chapter_text = "\n\n".join(item["markdown"] for item in chapters)
    content_parts = [f"Report Title: {request.title}"]
    if request.writing_guidance and request.writing_guidance.report_strategy:
        content_parts.append(
            f"报告主线：{request.writing_guidance.report_strategy}\n"
            "（仅内部编辑指引，不是事实或引用来源。）"
        )
    if gap_text:
        content_parts.append(f"Known evidence limitations:\n{gap_text}")
    content_parts.append(chapter_text)
    main_content = "\n\n".join(part for part in content_parts if part)
    return {
        "language": request.language,
        "audience_role": request.audience_role,
        "tone": request.tone,
        "user_format": request.user_format,
        "messages": [{"role": "user", "content": f"Main Content:\n{main_content}"}],
    }


def _summary_allowed_citation_ids(
    request: BriefSummaryRequest,
    chapters: list[dict[str, str]],
) -> set[int]:
    """从实际保留章节文本推导摘要输出可保留的引用编号。"""
    registered = {item.index for item in request.citation_registry}
    used = {
        int(citation_id)
        for chapter in chapters
        for citation_id in re.findall(r"\[citation:(\d+)\]", chapter["markdown"])
    }
    return registered & used


def _reduce_summary_chapters(chapters: list[dict[str, str]]) -> list[dict[str, str]] | None:
    """按完整章节、引用事实句、章节数量的顺序缩减摘要上下文。"""
    compacted = [
        {**chapter, "markdown": "\n\n".join(_summary_excerpt_parts(chapter["markdown"]))}
        for chapter in chapters
    ]
    if compacted != chapters:
        return compacted
    for index in range(len(chapters) - 1, -1, -1):
        parts = _summary_excerpt_parts(chapters[index]["markdown"])
        cited_indexes = [position for position, part in enumerate(parts) if "[citation:" in part]
        if cited_indexes:
            updated = [*chapters]
            kept = [part for position, part in enumerate(parts) if position != cited_indexes[-1]]
            updated[index] = {**chapters[index], "markdown": "\n\n".join(kept)}
            return updated
    return chapters[:-1] if len(chapters) > 1 else None


def _summary_fallback(request: BriefSummaryRequest) -> str:
    """无法再缩减摘要上下文时，不再发起重复的模型请求。"""
    return "\n".join(
        f"- {_first_body_paragraph(chapter.raw_markdown)}"
        for chapter in request.chapters
        if _first_body_paragraph(chapter.raw_markdown)
    )


async def generate_brief_summary(request: BriefSummaryRequest) -> str:
    """生成一次顶部核心摘要，失败时用章节首段确定性降级。"""
    gaps = []
    for section_id, evidence in request.section_evidence.items():
        for item in evidence.coverage:
            if item.status.value in {"weak", "missing", "unknown"}:
                gaps.append(
                    {
                        "section_id": section_id,
                        "step_id": item.step_id,
                        "status": item.status.value,
                        "reason": item.reason,
                    }
                )
    logger.info(
        "[BriefWriter] Start executive summary source_chapters=%d gaps=%d.",
        len(request.chapters), len(gaps),
    )
    chapters = [
        {"section_id": chapter.section_id, "markdown": chapter.raw_markdown}
        for chapter in request.chapters
    ]
    if not chapters:
        logger.warning(
            "[BriefWriter] Summary input cannot fit before call; retained_chapters=%d. Use fallback.",
            len(chapters),
        )
        return _summary_fallback(request)
    max_attempts = max(1, request.max_retries or Config().service_config.report_max_generate_retry_num)
    for attempt_num in range(max_attempts):
        prompt = _summary_prompt_input(request, chapters, gaps)
        raw = ""
        try:
            allowed_citation_ids = _summary_allowed_citation_ids(request, chapters)
            logger.info(
                "[BriefWriter] Generate executive summary attempt=%d/%d chapters=%d allowed_citations=%d.",
                attempt_num + 1, max_attempts, len(chapters), len(allowed_citation_ids),
            )
            response = await ainvoke_llm_with_stats(
                request.llm,
                apply_system_prompt("brief_reporter", prompt),
                agent_name=AgentLlmName.BRIEF_REPORTER.value,
            )
            raw = str(response.get("content") or "")
            matched = re.search(r"(?s)<executive_summary>\s*(.*?)\s*</executive_summary>", raw)
            if not matched:
                raise ValueError("missing executive_summary tag")
            summary = re.sub(
                r"\[citation:(\d+)\]",
                lambda m: m.group(0) if int(m.group(1)) in allowed_citation_ids else "",
                matched.group(1).strip(),
            )
            if summary:
                logger.info(
                    "[BriefWriter] Generated executive summary attempt=%d/%d content_chars=%d summary_chars=%d.",
                    attempt_num + 1, max_attempts, len(raw), len(summary),
                )
                logger.info(
                    "[BriefWriter] Generated executive summary output: %s", _response_preview(summary),
                )
                return summary
            raise ValueError("empty executive_summary")
        except Exception as exc:
            detail = "<detail masked>" if LogManager.is_sensitive() else str(exc)
            logger.warning(
                "[BriefWriter] Executive summary generation failed; attempt=%d/%d chapters=%d "
                "content_chars=%d response_preview=%s error=%s. Retry.",
                attempt_num + 1, max_attempts, len(chapters), len(raw), _response_preview(raw), detail,
            )
            if _matches_context_limit(str(exc), exc):
                reduced = _reduce_summary_chapters(chapters)
                if reduced is None:
                    logger.warning(
                        "[BriefWriter] Summary context limit cannot be reduced further; "
                        "attempt=%d/%d. Use fallback.", attempt_num + 1, max_attempts,
                    )
                    return _summary_fallback(request)
                logger.warning(
                    "[BriefWriter] Summary context limit returned by model; "
                    "attempt=%d/%d chapters=%d -> %d.",
                    attempt_num + 1, max_attempts, len(chapters), len(reduced),
                )
                chapters = reduced
            continue
    logger.warning(
        "[BriefWriter] Executive summary exhausted retries; max_attempts=%d. Use fallback.", max_attempts,
    )
    return _summary_fallback(request)


def assemble_brief_report(request: BriefAssemblyRequest) -> BriefReportAssembly:
    """拼装完整报告后一次性整理已有引用。"""
    chapters = sorted(request.chapters, key=lambda item: request.section_order[item.section_id])
    heading = "## Executive Summary" if request.language == ENGLISH else "## 核心摘要"
    refs = "## References" if request.language == ENGLISH else "## 参考文章"
    report = "\n\n".join(
        [
            f"# {request.title}",
            heading,
            request.executive_summary.strip(),
            *(c.raw_markdown.strip() for c in chapters),
            refs,
        ]
    ).strip()
    classified = [item.model_dump() for item in request.citation_registry]
    allowed = {int(item["index"]) for item in classified if int(item.get("index", 0)) > 0}
    cleaned = re.sub(
        r"\[\s*citation:\s*(\d+)\s*\]",
        lambda match: match.group(0) if int(match.group(1)) in allowed else "",
        report,
    )
    traced = SourceTracer({"report": cleaned, "classified_content": classified}).add_source_to_report()
    return BriefReportAssembly(
        report_content=traced.get("modified_report", ""),
        merged_trace_source_datas=traced.get("datas", []),
    )
