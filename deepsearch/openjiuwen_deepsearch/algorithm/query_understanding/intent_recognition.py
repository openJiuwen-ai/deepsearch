# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import re
from typing import Dict, List

from pydantic import BaseModel, Field, ValidationError
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.research_collector.target_paper import (
    normalize_arxiv_id,
    normalize_doi,
    normalize_pmid,
    normalize_title,
)
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import canonicalize_url
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ReportTypePolicy,
    ResearchIntent,
    TargetPaper,
    TemporalScope,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import run_web_search
from openjiuwen_deepsearch.utils.common_utils import llm_utils
from openjiuwen_deepsearch.utils.common_utils.url_utils import extract_domain_from_url
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

EMIT_INTENT_TOOL = "emit_report_intent"

_DEFAULT_WEB_SEARCH_ENGINE = "petal"
_VALID_REPORT_TYPES = frozenset({"professional", "brief"})
MAX_RESEARCH_QUERY_LENGTH = 390
_PMID_IN_QUERY_RE = re.compile(r"(?i)pmid\s*[:#]?\s*(\d+)")
_DOI_IN_QUERY_RE = re.compile(r"(?i)(?:doi\s*[:#]?\s*)?(10\.\d{4,9}/[-._;()/:a-z0-9]+)")
_ARXIV_IN_QUERY_RE = re.compile(
    r"(?i)(?:arxiv\s*[:#]?\s*)?((?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v\d+)?)"
)
_HTTP_URL_IN_QUERY_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def normalize_research_query(raw: str | None) -> str:
    """归一化 research_query：去首尾空白并限制最大长度。"""
    return (raw or "").strip()[:MAX_RESEARCH_QUERY_LENGTH]


def normalize_report_type(raw: str | None) -> str | None:
    """归一化报告类型字段。

    Only explicit enum values are accepted:
    - "professional" / "brief" -> normalized value
    - empty/unknown/alias -> None (means "not explicitly specified")

    NOTE:
    Keeping None is intentional. Downstream `generate_questions` uses this signal
    to force a clarification question asking user to choose professional vs brief,
    while policy resolution still defaults to professional when needed.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in _VALID_REPORT_TYPES:
        return s
    return None


def resolve_report_type_policy(
        normalized_report_type: str | None,
) -> ReportTypePolicy:
    """按报告类型解析策略。"""
    if normalized_report_type == "brief":
        return ReportTypePolicy(
            report_type="brief",
            paragraph_style="concise",
            require_summary_first=True,
            require_methodology_and_risk=True,
        )
    return ReportTypePolicy(
        report_type="professional",
        paragraph_style="detailed",
        require_summary_first=False,
        require_methodology_and_risk=False,
    )


class IntentRecognitionResult(BaseModel):
    """
    意图识别完整结果：保留原始输入、入口预搜索查询与报告约束。
    """
    original_query: str = Field(default="", description="用户原始输入，完整保留")
    research_query: str = Field(default="", description="清洗后的研究主题，仅用于入口预搜索，不写入 SearchContext，最长400字符")
    research_intent: ResearchIntent = Field(default_factory=ResearchIntent, description="结构化报告约束")
    lang: str = Field(default="zh-CN", description="检测到的用户语言（归一化前的原始值）")
    entry_search_results: List[Dict] = Field(default_factory=list, description="初始网络搜索结果")


def _default_fallback(original_query: str | None) -> IntentRecognitionResult:
    text = original_query if original_query is not None else ""
    stripped = (text or "").strip()
    target_papers = _extract_explicit_target_papers(stripped)
    return IntentRecognitionResult(
        original_query=text,
        research_query=normalize_research_query(stripped),
        research_intent=ResearchIntent(
            include_url=[paper.url for paper in target_papers if paper.url],
            target_papers=target_papers,
        ),
    )


def _extract_explicit_target_papers(query: str) -> list[TargetPaper]:
    """Recover deterministic academic identifiers and paper URLs when intent LLM is unavailable."""
    papers: list[TargetPaper] = []
    seen: set[tuple[str, str]] = set()
    url_spans: list[tuple[int, int]] = []
    for match in _HTTP_URL_IN_QUERY_RE.finditer(query or ""):
        url_spans.append(match.span())
        url = match.group(0).rstrip(".,;:)")
        pmid = normalize_pmid(url)
        arxiv_id = normalize_arxiv_id(url)
        if not (pmid or arxiv_id):
            continue
        key = ("url", url.casefold())
        if key in seen:
            continue
        seen.add(key)
        payload = {"url": url}
        if pmid:
            payload["pmid"] = pmid
        if arxiv_id:
            payload["arxiv_id"] = arxiv_id
        papers.append(TargetPaper(**payload))
    for field_name, pattern in (
        ("pmid", _PMID_IN_QUERY_RE),
        ("doi", _DOI_IN_QUERY_RE),
        ("arxiv_id", _ARXIV_IN_QUERY_RE),
    ):
        for match in pattern.finditer(query or ""):
            if any(start <= match.start() and match.end() <= end for start, end in url_spans):
                continue
            value = match.group(1).rstrip(".,;:)")
            key = (field_name, value.casefold())
            if not value or key in seen:
                continue
            seen.add(key)
            papers.append(TargetPaper(**{field_name: value}))
    return papers


def _to_str_list(raw) -> list[str]:
    """Safely convert a value to a list of strings.

    Handles the case where LLM returns a comma-separated string instead of a list.
    Supports Chinese punctuation: ，、；and English separators: , ; \n
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        return [item.strip() for item in re.split(r'[,，、;；\n]', raw) if item.strip()]
    return []


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = (item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _normalize_task_type(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if not value:
        return None
    aliases = {
        "compare": "comparison",
        "comparison_analysis": "comparison",
        "classify": "classification",
        "categorization": "classification",
        "trend_judgment": "trend_judgement",
        "trend_judgement": "trend_judgement",
        "trend_judgement_analysis": "trend_judgement",
        "feasibility_judgement": "trend_judgement",
        "feasibility_assessment": "trend_judgement",
    }
    return aliases.get(value, value)


def _normalize_temporal_scope(raw: object) -> TemporalScope | None:
    """校验 LLM 输出的时间范围，非法时仅关闭时间约束。

    Args:
        raw: 意图识别工具返回的 temporal_scope 原始值。

    Returns:
        合法的时间范围；缺失或非法时返回 None。
    """
    if not isinstance(raw, dict):
        return None
    try:
        return TemporalScope.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        return None


def _normalize_target_papers(raw: object) -> list[TargetPaper]:
    """Normalize valid target-paper items and discard malformed LLM output."""
    if not isinstance(raw, list):
        return []
    field_names = ("title", "pmid", "doi", "arxiv_id", "url", "dataset", "data_year", "topic")
    papers: list[TargetPaper] = []
    identity_to_index: dict[tuple[str, str], int] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        payload = {name: str(item.get(name) or "").strip() for name in field_names}
        try:
            paper = TargetPaper.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            continue
        paper.pmid = normalize_pmid(paper.pmid)
        paper.doi = normalize_doi(paper.doi)
        paper.arxiv_id = normalize_arxiv_id(paper.arxiv_id)
        if paper.url:
            pmid = normalize_pmid(paper.url)
            arxiv_id = normalize_arxiv_id(paper.url)
            if pmid and not paper.pmid:
                paper.pmid = pmid
            if arxiv_id and not paper.arxiv_id:
                paper.arxiv_id = arxiv_id

        identity_values = (
            ("pmid", paper.pmid),
            ("doi", paper.doi),
            ("arxiv_id", paper.arxiv_id),
            ("url", canonicalize_url(paper.url) if paper.url else ""),
            ("title", normalize_title(paper.title)),
        )
        identities = []
        for kind, value in identity_values:
            if value:
                identities.append((kind, value))
        if not identities:
            fallback = tuple(getattr(paper, name).casefold() for name in field_names)
            identities.append(("fallback", "\0".join(fallback)))
        duplicate_index = next(
            (identity_to_index[identity] for identity in identities if identity in identity_to_index),
            None,
        )
        if duplicate_index is not None:
            existing = papers[duplicate_index]
            for name in field_names:
                if not getattr(existing, name) and getattr(paper, name):
                    setattr(existing, name, getattr(paper, name))
            for identity in identities:
                identity_to_index[identity] = duplicate_index
            continue

        duplicate_index = len(papers)
        papers.append(paper)
        for identity in identities:
            identity_to_index[identity] = duplicate_index
    return papers


def _normalize_research_intent(data: dict) -> ResearchIntent:
    """归一化意图识别工具参数。

    Args:
        data: LLM tool call 返回的原始字段。

    Returns:
        可供工作流消费的结构化研究意图。
    """
    raw_section = data.get("section_count")
    section_count = None
    if raw_section is not None:
        try:
            n = int(raw_section)
            if n > 0:
                section_count = n
        except (TypeError, ValueError):
            pass

    tone_raw = data.get("tone")
    tone = str(tone_raw).strip().lower() if tone_raw is not None and str(tone_raw).strip() else None

    rt_raw = data.get("report_type")
    report_type = normalize_report_type(str(rt_raw).strip() if rt_raw is not None else None)

    ar_raw = data.get("audience_role")
    audience_role = str(ar_raw).strip() if ar_raw is not None and str(ar_raw).strip() else None

    include_url = _dedupe_preserve_order(_to_str_list(data.get("include_url")))
    exclude_url = _dedupe_preserve_order(_to_str_list(data.get("exclude_url")))
    exclude_titles = _dedupe_preserve_order(_to_str_list(data.get("exclude_titles")))
    include_domains = _dedupe_preserve_order(
        [str(d).strip() for d in _to_str_list(data.get("include_domains")) if str(d).strip()]
    )
    exclude_domains = _dedupe_preserve_order(
        [str(d).strip() for d in _to_str_list(data.get("exclude_domains")) if str(d).strip()]
    )

    for url in include_url:
        domain = extract_domain_from_url(url)
        if domain and domain not in include_domains:
            include_domains.append(domain)
    include_domains = _dedupe_preserve_order(include_domains)

    target_papers = _normalize_target_papers(data.get("target_papers"))
    for paper in target_papers:
        if paper.url and paper.url not in include_url:
            include_url.append(paper.url)

    return ResearchIntent(
        task_type=_normalize_task_type(data.get("task_type")),
        required_dimensions=_dedupe_preserve_order(_to_str_list(data.get("required_dimensions"))),
        comparison_targets=_dedupe_preserve_order(_to_str_list(data.get("comparison_targets"))),
        section_count=section_count,
        audience_role=audience_role,
        tone=tone,
        report_type=report_type,
        include_url=include_url,
        exclude_url=exclude_url,
        exclude_titles=exclude_titles,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        temporal_scope=_normalize_temporal_scope(data.get("temporal_scope")),
        target_papers=target_papers,
    )


async def _emit_report_intent(**kwargs) -> IntentRecognitionResult:
    """将 LLM tool_call args 转换为意图识别结果。"""
    research_query = normalize_research_query(kwargs.get("research_query"))
    language = kwargs.get("language") or "zh-CN"

    return IntentRecognitionResult(
        research_query=research_query,
        research_intent=_normalize_research_intent(kwargs),
        lang=language,
    )


def _merge_explicit_target_papers(intent: ResearchIntent, original_query: str) -> ResearchIntent:
    """Supplement a successful model intent with identifiers stated verbatim by the user."""
    explicit_papers = _extract_explicit_target_papers(original_query)
    if not explicit_papers:
        return intent

    target_papers = _normalize_target_papers([
        *(paper.model_dump() for paper in intent.target_papers),
        *(paper.model_dump() for paper in explicit_papers),
    ])
    include_url = list(intent.include_url)
    for paper in target_papers:
        if paper.url and paper.url not in include_url:
            include_url.append(paper.url)

    return intent.model_copy(update={
        "target_papers": target_papers,
        "include_url": include_url,
    })


def _log_exclude_intent(result: IntentRecognitionResult, original_query: str) -> None:
    """exclude 字段非空时输出 [EXCLUDE_INTENT] 观测日志.

    遵循 ``LogManager.is_sensitive()`` 脱敏约定：敏感模式下只记录字段计数，
    不输出 URL、标题、域名或 query 内容；session_id 由日志上下文自动注入。
    """
    intent = result.research_intent
    if not (intent.exclude_url or intent.exclude_domains or intent.exclude_titles):
        return
    if LogManager.is_sensitive():
        logger.info(
            "[EXCLUDE_INTENT] (redacted) exclude_url=%d exclude_domains=%d exclude_titles=%d",
            len(intent.exclude_url),
            len(intent.exclude_domains),
            len(intent.exclude_titles),
        )
        return
    logger.info(
        "[EXCLUDE_INTENT] exclude_url=%s exclude_domains=%s exclude_titles=%s query=%s",
        intent.exclude_url,
        intent.exclude_domains,
        intent.exclude_titles,
        (original_query or "")[:120],
    )


def _create_emit_intent_tool() -> LocalFunction:
    card = ToolCard(
        id=EMIT_INTENT_TOOL,
        name=EMIT_INTENT_TOOL,
        description=(
            "Emit structured report constraints and the cleaned research_query. "
            "You MUST call this tool exactly once for research requests."
        ),
        input_params={
            "type": "object",
            "properties": {
                "research_query": {
                    "type": "string",
                    "description": (
                        "The core research topic only (what to investigate). "
                        "Exclude meta instructions about chapters, audience, tone, or URLs. "
                        "Keep the same language as the user's original query — do NOT translate, "
                        "rewrite into English keywords, or internationalize wording. "
                        "Mixed-language entities (e.g., Jensen Huang, Blackwell/Rubin) stay as-is. "
                        "Keep it concise; MUST NOT exceed 400 characters."
                    ),
                },
                "language": {
                    "type": "string",
                    "description": "The user's detected language locale (e.g., zh-CN, en-US, ja-JP).",
                },
                "task_type": {
                    "type": "string",
                    "description": (
                        "Primary task type as a concise English enum-like label, such as "
                        "comparison, classification, trend_judgement, recommendation, evaluation."
                    ),
                },
                "required_dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Named comparison or analysis dimensions that must be covered.",
                },
                "comparison_targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Named entities, options, or categories that should be compared explicitly.",
                },
                "section_count": {
                    "type": "integer",
                    "description": "Max or desired number of sections/chapters if user specified; else omit.",
                },
                "audience_role": {
                    "type": "string",
                    "description": "Target reader role as a short phrase (e.g., CTO, investor). Omit if not stated.",
                },
                "tone": {
                    "type": "string",
                    "description": (
                        "Writing tone as ONE English enum value: objective, formal, analytical, informative, "
                        "explanatory, persuasive, descriptive, critical, comparative, simple, casual. "
                        "Omit if unclear."
                    ),
                },
                "report_type": {
                    "type": "string",
                    "enum": ["professional", "brief"],
                    "description": (
                        "Report type. MUST be exactly 'professional' or 'brief'. "
                        "Use 'professional' for full deep-research reports (专业版, 深度研究); "
                        "use 'brief' for concise reports (精简版, 简报, 概述). "
                        "Omit if unclear after reading context."
                    ),
                },
                "include_url": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Full HTTP(S) URLs the user explicitly lists or asks to use / focus on. "
                        "Do NOT invent URLs; extract exactly as given in the text."
                    ),
                },
                "exclude_url": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Full HTTP(S) URLs the user explicitly asks to avoid. Article-level exclusion "
                        "ALWAYS goes here, even when multiple forbidden URLs share one domain. "
                        "Do NOT invent URLs."
                    ),
                },
                "exclude_titles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Verbatim titles of articles/papers the user explicitly asks to avoid, ignore, "
                        "or not quote. Extract alongside exclude_url whenever the rule names an article; "
                        "the same article may appear on mirror sites under different URLs."
                    ),
                },
                "include_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Domains to prefer or restrict to (hostname only, lowercase, no scheme). "
                        "ONLY when the user explicitly expresses a site-level preference "
                        "(e.g. 只用维基百科 → wikipedia.org)."
                    ),
                },
                "exclude_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Domains to exclude (hostname only, lowercase, no scheme). ONLY when the user "
                        "explicitly expresses site-level exclusion (e.g. 不要用CSDN的文章). "
                        "NEVER derive domains from exclude_url; banning N articles on the same domain "
                        "is NOT site-level exclusion."
                    ),
                },
                "target_papers": {
                    "type": "array",
                    "description": (
                        "Papers explicitly identified by title, URL, PMID, DOI, or arXiv ID, or implicitly "
                        "described by dataset, data year, and discriminative topic clues. Do not invent values."
                    ),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "title": {"type": "string", "description": "User-supplied full paper title."},
                            "pmid": {"type": "string", "description": "User-supplied PubMed PMID."},
                            "doi": {"type": "string", "description": "User-supplied DOI."},
                            "arxiv_id": {"type": "string", "description": "User-supplied arXiv ID."},
                            "url": {"type": "string", "description": "User-supplied academic paper URL."},
                            "dataset": {"type": "string", "description": "Named dataset clue."},
                            "data_year": {"type": "string", "description": "Dataset observation year clue."},
                            "topic": {"type": "string", "description": "Discriminative study-topic clue."},
                        },
                    },
                },
                "temporal_scope": {
                    "type": "object",
                    "description": (
                        "Explicit research time constraint. Omit when the user does not specify a time boundary."
                    ),
                    "properties": {
                        "constraint_type": {
                            "type": "string",
                            "enum": ["source_date", "content_date"],
                            "description": (
                                "Use source_date when source publication/availability is bounded; "
                                "use content_date when only facts or data are bounded."
                            ),
                        },
                        "start_date": {
                            "type": "string",
                            "format": "date",
                            "description": "Inclusive lower boundary in YYYY-MM-DD format; omit when absent.",
                        },
                        "end_date": {
                            "type": "string",
                            "format": "date",
                            "description": "Inclusive upper boundary in YYYY-MM-DD format; omit when absent.",
                        },
                    },
                    "required": ["constraint_type"],
                },
            },
            "required": ["research_query", "language"],
        },
    )

    return LocalFunction(card=card, func=_emit_report_intent)


async def _invoke_llm_for_intent(
    prompt_name: str,
    original_query: str,
    messages: list,
    llm_model_name: str,
) -> tuple[IntentRecognitionResult | None, dict]:
    """公共 LLM 调用逻辑：构建提示词、调用 LLM、解析 tool_call 结果
    """
    prompt_ctx = {
        "original_query": original_query,
        "messages": messages,
    }
    prompts = apply_system_prompt(prompt_name, prompt_ctx)

    tool = _create_emit_intent_tool()
    llm = llm_context.get().get(llm_model_name)
    response = await llm_utils.ainvoke_llm_with_stats(
        llm,
        prompts,
        llm_type="basic",
        agent_name=AgentLlmName.INTENT_RECOGNITION.value,
        tools=[tool.card.tool_info()],
        need_stream_out=False,
    )
    tool_calls = response.get("tool_calls") or []

    if not tool_calls:
        return (None, response)

    tool_call = tool_calls[0]
    if tool_call.get("name") and tool_call.get("name") != tool.card.name:
        logger.warning(
            "[_invoke_llm_for_intent] Tool name mismatch(%s): %s",
            tool.card.name,
            "**" if LogManager.is_sensitive() else tool_call.get("name"),
        )

    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        return (None, response)

    tool_result = await tool.invoke(args)
    research_query = normalize_research_query(tool_result.research_query) or normalize_research_query(
        original_query
    )
    merged_intent = _merge_explicit_target_papers(tool_result.research_intent, original_query)
    result = tool_result.model_copy(
        update={
            "original_query": original_query,
            "research_query": research_query,
            "research_intent": merged_intent,
        }
    )
    return (result, response)


async def recognize_report_intent(current_inputs: dict) -> IntentRecognitionResult:
    """
    使用 LLM + 单次 tool call 解析报告意图与入口预搜索查询。

    Args:
        current_inputs: 需包含 ``original_query``；可选 ``messages``、``llm_model_name``。

    Returns:
        IntentRecognitionResult: LLM 失败或无 tool call 时回退为 research_query=original_query、空 intent。
    """
    original_query = current_inputs.get("original_query").strip()
    if not original_query:
        return _default_fallback(original_query)

    try:
        result, response = await _invoke_llm_for_intent(
            "intent_recognition", original_query,
            current_inputs.get("messages") or [],
            current_inputs.get("llm_model_name"),
        )
        if result is None:
            logger.warning("[recognize_report_intent] No tool_calls in LLM response, using fallback.")
            return _default_fallback(original_query)

        _log_exclude_intent(result, original_query)

        if LogManager.is_sensitive():
            logger.info("[recognize_report_intent] parsed successfully (redacted).")
        else:
            logger.info(
                f"[recognize_report_intent] original_query={original_query}\n"
                f"research_query={result.research_query}\n"
                f"intent={result.research_intent.model_dump()}"
            )
        return result

    except Exception as exc:
        if LogManager.is_sensitive():
            logger.warning("[recognize_report_intent] Exception, using fallback.")
        else:
            logger.warning("[recognize_report_intent] Exception, using fallback: %s", exc)
        return _default_fallback(original_query)


async def classify_and_recognize_intent(current_inputs: dict) -> IntentRecognitionResult:
    """
    意图识别（入口函数）：解析报告意图与入口预搜索查询；research_query 仅用于入口 run_web_search。

    所有查询均进入研究报告生成流程，LLM 无 tool_calls 时回退为 research_query=original_query、空 intent。

    Args:
        current_inputs: 需包含 ``original_query``；可选 ``messages``、``llm_model_name``。

    Returns:
        IntentRecognitionResult: 包含研究约束的完整意图对象。
    """
    original_query = (current_inputs.get("original_query") or "").strip()
    if not original_query:
        return _default_fallback(original_query)

    try:
        result, response = await _invoke_llm_for_intent(
            "intent_recognition_entry", original_query,
            current_inputs.get("messages") or [],
            current_inputs.get("llm_model_name"),
        )
        if result is None:
            logger.warning("[classify_and_recognize_intent] No tool_calls in LLM response, using fallback.")
            return _default_fallback(original_query)

        _log_exclude_intent(result, original_query)

        if LogManager.is_sensitive():
            logger.info("[classify_and_recognize_intent] parsed successfully (redacted).")
        else:
            logger.info(
                f"[classify_and_recognize_intent] original_query={original_query}\n"
                f"research_query={result.research_query}\n"
                f"lang={result.lang}\n"
                f"intent={result.research_intent.model_dump()}"
            )
        return result

    except Exception as exc:
        if LogManager.is_sensitive():
            logger.warning("[classify_and_recognize_intent] Exception, using fallback.")
        else:
            logger.warning("[classify_and_recognize_intent] Exception, using fallback: %s", exc)
        return _default_fallback(original_query)


async def web_search_for_query(inputs: dict) -> dict:
    """
    根据用户查询执行网络搜索并返回结果。

    Args:
        inputs: dict 包含：
            - query: str - 用户搜索查询
            - web_search_engine_name: str - 搜索引擎名称（默认 "petal"）

    Returns:
        dict 包含：
            - search_results: list - 网络搜索结果
            - error_msg: str - 错误信息（失败时）
    """
    logger.info("[web_search_for_query] Begin web search for query.")
    query = inputs.get("query", "")
    search_engine_name = inputs.get("web_search_engine_name") or _DEFAULT_WEB_SEARCH_ENGINE

    error_msg = ""
    search_results = []
    try:
        result = await run_web_search(query, search_engine_name)
        search_results = result.get("search_results", [])
        if "Error when run web search" in search_results:
            search_results = []
    except Exception as e:
        error_msg = f"Web search failed: {e}"
        logger.error(error_msg)

    logger.info("[web_search_for_query] End web search, got %d results.", len(search_results))
    return {
        "search_results": search_results,
        "error_msg": error_msg
    }
