"""Brief 章节候选的批量评估、分片与确定性降级。"""

import asyncio
import json
import logging

from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefOutline,
    BriefSearchResult,
    BriefSection,
    BriefSectionEvidence,
    BriefSelectedDoc,
    BriefStepCoverage,
    CoverageStatus,
)
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


logger = logging.getLogger(__name__)


def _is_context_limit_error(error: BaseException) -> bool:
    """识别供应商常见上下文超限错误，仅用于分片重试。

    Args:
        error: 当前调用及其异常链中的错误。

    Returns:
        错误文本表明 Prompt 超限时返回 True。
    """
    phrases = (
        "context_length_exceeded",
        "context length",
        "context window",
        "maximum context",
        "too many tokens",
        "input is too long",
        "prompt is too long",
        "token limit",
    )
    current: BaseException | None = error
    parts: list[str] = []
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(str(current).casefold())
        current = current.__cause__ or current.__context__
    return any(phrase in " ".join(parts) for phrase in phrases)


def _prompt_char_count(messages: object) -> int:
    """记录渲染 Prompt 的大小，不向日志写入任何证据正文。"""
    if isinstance(messages, list):
        return sum(
            len(str(message.get("content", "")))
            for message in messages
            if isinstance(message, dict)
        )
    return len(json.dumps(messages, ensure_ascii=False, default=str))


def _partition_by_step(
    section: BriefSection,
    candidates: list[BriefSearchResult],
) -> list[list[BriefSearchResult]]:
    """将候选作为一个分片；真实超限后再递归拆分。

    Args:
        section: 当前待评估章节。
        candidates: 已路由到该章节的候选。
    Returns:
        保持输入次序的候选分片列表。
    """
    return [candidates]


def _validate_evaluation_output(
    section: BriefSection,
    candidates: list[BriefSearchResult],
    parsed: dict,
) -> BriefSectionEvidence:
    """删除模型虚构 ID，并补齐每个研究步骤的覆盖记录。

    Args:
        section: 当前章节。
        candidates: 当前调用中允许选择的候选。
        parsed: LLM 返回的已解析 JSON。

    Returns:
        仅含合法 source_id 和 step_id 的章节证据。
    """
    if not isinstance(parsed, dict):
        raise ValueError("brief evaluation payload must be an object")
    allowed_sources = {item.source_id for item in candidates}
    allowed_steps = {step.id for step in section.research_steps}
    result = BriefSectionEvidence(**parsed)
    selected: list[BriefSelectedDoc] = []
    for item in result.selected_docs:
        if item.source_id not in allowed_sources:
            continue
        item.step_ids = [step_id for step_id in item.step_ids if step_id in allowed_steps]
        if item.step_ids:
            selected.append(item)
    coverage_by_step = {item.step_id: item for item in result.coverage if item.step_id in allowed_steps}
    coverage = [
        coverage_by_step.get(step.id)
        or BriefStepCoverage(step_id=step.id, status="missing", reason="评估未返回该步骤。")
        for step in section.research_steps
    ]
    return BriefSectionEvidence(selected_docs=selected, coverage=coverage)


async def _evaluate_shard(
    llm: object,
    section: BriefSection,
    candidates: list[BriefSearchResult],
) -> BriefSectionEvidence:
    """调用 LLM 评估当前章节的一份候选分片。

    Args:
        llm: `info_collecting` 槽位的运行时 LLM。
        section: 当前章节。
        candidates: 当前 Prompt 中的候选分片。

    Returns:
        经输入边界校验后的章节证据。

    Raises:
        ValueError: 既有重试预算耗尽且无法解析有效评估结果时抛出。
    """
    messages = apply_system_prompt(
        "brief_doc_evaluator",
        {"section": section.model_dump(), "candidates": [item.model_dump() for item in candidates]},
    )
    attempts = max(1, Config().service_config.info_collector_max_retry_num)
    last_error: Exception | None = None
    prompt_chars = _prompt_char_count(messages)
    logger.info(
        "[BriefEvaluator] Start section evaluation section_id=%s candidates=%d "
        "prompt_chars=%d max_attempts=%d.",
        section.id,
        len(candidates),
        prompt_chars,
        attempts,
    )
    for attempt_num in range(1, attempts + 1):
        stage = "llm_invoke"
        try:
            logger.info(
                "[BriefEvaluator] Start evaluation attempt section_id=%s attempt=%d/%d "
                "candidates=%d prompt_chars=%d.",
                section.id,
                attempt_num,
                attempts,
                len(candidates),
                prompt_chars,
            )
            response = await ainvoke_llm_with_stats(
                llm,
                messages,
                agent_name=AgentLlmName.BRIEF_DOC_EVALUATOR.value,
            )
            stage = "response_shape"
            if not isinstance(response, dict):
                raise ValueError("brief evaluation response must be an object")
            stage = "json_decode"
            parsed = json.loads(normalize_json_output(str(response.get("content", ""))))
            stage = "contract_validation"
            evidence = _validate_evaluation_output(section, candidates, parsed)
            logger.info(
                "[BriefEvaluator] Evaluation succeeded section_id=%s attempt=%d/%d "
                "selected_docs=%d coverage=%d.",
                section.id,
                attempt_num,
                attempts,
                len(evidence.selected_docs),
                len(evidence.coverage),
            )
            return evidence
        except Exception as exc:  # 由调用层决定继续拆分还是规则降级。
            if _is_context_limit_error(exc):
                logger.warning(
                    "[BriefEvaluator] Evaluation context limit section_id=%s attempt=%d/%d "
                    "stage=%s candidates=%d prompt_chars=%d error_type=%s.",
                    section.id,
                    attempt_num,
                    attempts,
                    stage,
                    len(candidates),
                    prompt_chars,
                    type(exc).__name__,
                )
                # 同一候选集合不可能在下一次调用自动变短，交由外层递归拆分。
                raise
            last_error = exc
            logger.warning(
                "[BriefEvaluator] Evaluation attempt failed section_id=%s attempt=%d/%d "
                "stage=%s candidates=%d prompt_chars=%d error_type=%s error=%s.",
                section.id,
                attempt_num,
                attempts,
                stage,
                len(candidates),
                prompt_chars,
                type(exc).__name__,
                "<detail masked>" if LogManager.is_sensitive() else exc,
            )
    raise ValueError(f"brief section evaluation failed: {last_error}") from last_error


def _fallback(section: BriefSection, candidates: list[BriefSearchResult]) -> BriefSectionEvidence:
    """在评估失败时按搜索排名和来源稳定顺序保留候选。

    Args:
        section: 发生降级的章节。
        candidates: 已路由到该章节的候选。

    Returns:
        覆盖状态为 unknown 的确定性章节证据。
    """
    ranked = sorted(candidates, key=lambda item: (item.search_rank, item.source, item.url))
    selected = [
        BriefSelectedDoc(
            source_id=item.source_id,
            step_ids=[step_id for step_id in item.step_ids if step_id.startswith(f"{section.id}-")],
            evaluation_rank=index,
        )
        for index, item in enumerate(ranked, start=1)
    ]
    coverage = [
        BriefStepCoverage(
            step_id=step.id,
            status=CoverageStatus.UNKNOWN,
            reason="批量评估重试耗尽，覆盖状态未知。",
        )
        for step in section.research_steps
    ]
    return BriefSectionEvidence(
        selected_docs=selected,
        coverage=coverage,
    )


def _merge_shards(section: BriefSection, shards: list[BriefSectionEvidence]) -> BriefSectionEvidence:
    """使用代码合并多个评估分片，不再发起额外 LLM 合并调用。

    Args:
        section: 当前章节。
        shards: 每个候选分片的评估结果。

    Returns:
        合并后的章节证据。
    """
    selected_by_source: dict[str, BriefSelectedDoc] = {}
    for shard in shards:
        for selected in shard.selected_docs:
            current = selected_by_source.get(selected.source_id)
            if current is None or selected.evaluation_rank < current.evaluation_rank:
                selected_by_source[selected.source_id] = selected
            else:
                current.step_ids = list(dict.fromkeys([*current.step_ids, *selected.step_ids]))
    status_order = {
        CoverageStatus.COVERED: 3,
        CoverageStatus.WEAK: 2,
        CoverageStatus.MISSING: 1,
        CoverageStatus.UNKNOWN: 0,
    }
    merged_coverage: list[BriefStepCoverage] = []
    for step in section.research_steps:
        matches = []
        for shard in shards:
            for item in shard.coverage:
                if item.step_id == step.id:
                    matches.append(item)
        merged_coverage.append(
            max(matches, key=lambda item: status_order.get(item.status, 0))
            if matches
            else BriefStepCoverage(step_id=step.id, status="missing", reason="评估未返回该步骤。")
        )
    return BriefSectionEvidence(
        selected_docs=sorted(selected_by_source.values(), key=lambda item: (item.evaluation_rank, item.source_id)),
        coverage=merged_coverage,
    )


async def evaluate_brief_sections(
    llm: object,
    outline: BriefOutline,
    candidates: dict[str, list[BriefSearchResult]],
) -> dict[str, BriefSectionEvidence]:
    """按章节并行评估候选，并在单章失败时独立降级。

    Args:
        llm: `info_collecting` 槽位的运行时 LLM。
        outline: 已清洗的 Brief 大纲。
        candidates: 每章的候选搜索结果。
    Returns:
        章节 ID 到独立评估证据的映射。
    """
    async def evaluate_one(section: BriefSection) -> tuple[str, BriefSectionEvidence]:
        section_candidates = [
            item if isinstance(item, BriefSearchResult) else BriefSearchResult.model_validate(item)
            for item in candidates.get(section.id, [])
        ]
        try:
            results: list[BriefSectionEvidence] = []
            pending = _partition_by_step(section, section_candidates)
            while pending:
                shard = pending.pop(0)
                try:
                    results.append(await _evaluate_shard(llm, section, shard))
                except Exception as exc:
                    if _is_context_limit_error(exc) and len(shard) > 1:
                        midpoint = len(shard) // 2
                        logger.info(
                            "[BriefEvaluator] Split context-limited shard section_id=%s "
                            "candidates=%d left=%d right=%d.",
                            section.id,
                            len(shard),
                            len(shard[:midpoint]),
                            len(shard[midpoint:]),
                        )
                        pending[0:0] = [shard[:midpoint], shard[midpoint:]]
                        continue
                    raise
            return section.id, _merge_shards(section, results)
        except Exception as exc:
            logger.warning(
                "[BriefEvaluator] Evaluation fallback section_id=%s candidates=%d error_type=%s error=%s.",
                section.id,
                len(section_candidates),
                type(exc).__name__,
                "<detail masked>" if LogManager.is_sensitive() else exc,
            )
            return section.id, _fallback(section, section_candidates)

    return dict(await asyncio.gather(*(evaluate_one(section) for section in outline.sections)))
