"""Brief 报告的一轮采集与有界阻断缺口补搜编排。"""

import json
import logging

from openjiuwen_deepsearch.algorithm.brief_report.evaluation import evaluate_brief_sections
from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefCitationRecord,
    BriefCollectionContext,
    BriefCollectionResult,
    BriefCollectorRequest,
    BriefQuery,
    BriefQueryRequest,
    BriefSearchResult,
    BriefSectionEvidence,
    BriefStepCoverage,
)
from openjiuwen_deepsearch.algorithm.brief_report.search import build_section_candidates
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    build_research_intent_prompt_context,
    build_temporal_scope_prompt_context,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


logger = logging.getLogger(__name__)


async def generate_brief_queries(llm: object, request: BriefQueryRequest) -> list[BriefQuery]:
    """一次生成报告级正式 Query 或全部阻断缺口的补充 Query。

    Args:
        llm: `info_collecting` 槽位的运行时 LLM。
        request: 大纲、已执行 Query 和可选阻断缺口。

    Returns:
        已按合法章节/步骤及去重规则清洗的 Query 列表。
    """
    prompt_context = request.model_dump(exclude={"research_intent"})
    prompt_context.update(build_research_intent_prompt_context(request.research_intent))
    prompt_context.update(build_temporal_scope_prompt_context(request.research_intent))
    messages = apply_system_prompt("brief_collector_query_generation", prompt_context)
    last_error: Exception | None = None
    attempts = max(1, Config().service_config.info_collector_max_retry_num)
    for attempt_num in range(1, attempts + 1):
        try:
            response = await ainvoke_llm_with_stats(
                llm,
                messages,
                agent_name=AgentLlmName.BRIEF_COLLECTOR_QUERY_GENERATION.value,
            )
            if not isinstance(response, dict):
                raise ValueError("brief query response must be an object")
            payload = json.loads(normalize_json_output(str(response.get("content", ""))))
            raw_queries = payload.get("queries", []) if isinstance(payload, dict) else payload
            if not isinstance(raw_queries, list):
                raise ValueError("brief queries payload must contain a list")
            queries = _clean_brief_queries(raw_queries, request)
            if not queries:
                raise ValueError("brief query payload contains no valid query after cleaning")
            return queries
        except Exception as exc:
            last_error = exc
            logger.warning(
                "[BriefCollector] Query generation attempt failed; attempt=%d/%d error=%s. %s",
                attempt_num,
                attempts,
                "<detail masked>" if LogManager.is_sensitive() else exc,
                "Retry." if attempt_num < attempts else "No retries remain.",
                exc_info=not LogManager.is_sensitive(),
            )
    else:
        raise ValueError(f"brief query generation failed: {last_error}") from last_error

    raise AssertionError("unreachable")


def _clean_brief_queries(raw_queries: list[object], request: BriefQueryRequest) -> list[BriefQuery]:
    """验证并清洗模型返回的 Query，保留合法且未重复的记录。"""
    seen = {" ".join(item.casefold().split()) for item in request.executed_queries}
    result: list[BriefQuery] = []
    valid_sections = {section.id for section in request.outline.sections}
    valid_steps = {step.id for section in request.outline.sections for step in section.research_steps}
    for raw in raw_queries:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("query") or "").strip()
        key = " ".join(text.casefold().split())
        raw_section_ids = raw.get("section_ids", [])
        raw_step_ids = raw.get("step_ids", [])
        section_ids = (
            [item for item in raw_section_ids if item in valid_sections]
            if isinstance(raw_section_ids, list)
            else []
        )
        step_ids = [item for item in raw_step_ids if item in valid_steps] if isinstance(raw_step_ids, list) else []
        if not text:
            continue
        if key in seen:
            continue
        if not section_ids:
            continue
        if not step_ids:
            continue
        seen.add(key)
        result.append(
            BriefQuery(
                query=text,
                section_ids=section_ids,
                step_ids=step_ids,
            )
        )
    return result


def _blocking_gaps(section_evidence: dict[str, BriefSectionEvidence]) -> list[BriefStepCoverage]:
    """提取允许启动一次补搜的 weak/missing 阻断缺口。

    Args:
        section_evidence: 当前首轮章节评估结果。

    Returns:
        仅含阻断的 weak 或 missing 覆盖记录。
    """
    blocking_gaps = []
    for evidence in section_evidence.values():
        for item in evidence.coverage:
            if item.status.value in {"weak", "missing"} and item.blocking_gap:
                blocking_gaps.append(item)
    return blocking_gaps


def merge_search_results(
    first: list[BriefSearchResult],
    second: list[BriefSearchResult],
) -> list[BriefSearchResult]:
    """按 URL 合并两轮结果，并保留两轮实际可见片段。

    Args:
        first: 首轮搜索结果。
        second: 补搜结果。

    Returns:
        URL 唯一且路由、摘要已合并的结果列表。
    """
    merged = {item.url: item.model_copy(deep=True) for item in first}
    for item in second:
        existing = merged.get(item.url)
        if existing is None:
            merged[item.url] = item.model_copy(deep=True)
            continue
        existing.section_ids = list(dict.fromkeys([*existing.section_ids, *item.section_ids]))
        existing.step_ids = list(dict.fromkeys([*existing.step_ids, *item.step_ids]))
        existing.snippet = "\n".join(dict.fromkeys([existing.snippet, item.snippet]))
        existing.search_rank = min(existing.search_rank, item.search_rank)
    return list(merged.values())


def _selected_results_for_sections(
    results: list[BriefSearchResult],
    evidence: dict[str, BriefSectionEvidence],
    section_ids: list[str],
) -> list[BriefSearchResult]:
    """仅提取受影响章节首轮已入选资料，不重新输入淘汰候选。

    Args:
        results: 首轮搜索结果。
        evidence: 首轮章节评估。
        section_ids: 将要重评的章节 ID。

    Returns:
        只包含首轮 selected_docs 的搜索结果副本。
    """
    selected_ids = {
        item.source_id
        for section_id in section_ids
        for item in evidence.get(section_id, BriefSectionEvidence()).selected_docs
    }
    return [item.model_copy(deep=True) for item in results if item.source_id in selected_ids]


def merge_collection_rounds(
    first: dict[str, BriefSectionEvidence],
    second: dict[str, BriefSectionEvidence],
) -> dict[str, BriefSectionEvidence]:
    """确定性合并受影响章节的选文和覆盖状态。

    Args:
        first: 首轮章节评估结果。
        second: 补搜后的受影响章节重评结果。

    Returns:
        合并后的全部章节证据。
    """
    merged = {key: value.model_copy(deep=True) for key, value in first.items()}
    status_order = {"covered": 3, "weak": 2, "missing": 1, "unknown": 0}
    for section_id, later in second.items():
        earlier = merged.get(section_id, BriefSectionEvidence())
        selected = {item.source_id: item for item in earlier.selected_docs}
        for item in later.selected_docs:
            existing = selected.get(item.source_id)
            if existing is None or item.evaluation_rank < existing.evaluation_rank:
                selected[item.source_id] = item
            else:
                existing.step_ids = list(dict.fromkeys([*existing.step_ids, *item.step_ids]))
        coverage_by_step = {item.step_id: item for item in earlier.coverage}
        for item in later.coverage:
            existing = coverage_by_step.get(item.step_id)
            if existing is None or status_order.get(item.status.value, 0) > status_order.get(existing.status.value, 0):
                coverage_by_step[item.step_id] = item
        merged[section_id] = BriefSectionEvidence(
            selected_docs=sorted(selected.values(), key=lambda item: (item.evaluation_rank, item.source_id)),
            coverage=list(coverage_by_step.values()),
        )
    return merged


def build_citation_registry(
    results: list[BriefSearchResult],
    evidence: dict[str, BriefSectionEvidence],
) -> list[BriefCitationRecord]:
    """按最终入选 URL 分配报告级稳定引用编号。

    Args:
        results: 所有轮次合并后的搜索结果。
        evidence: 最终章节评估结果。

    Returns:
        URL 唯一的报告级引用注册表。
    """
    result_by_source = {item.source_id: item for item in results}
    record_by_url: dict[str, BriefCitationRecord] = {}
    for section_evidence in evidence.values():
        for selected in sorted(section_evidence.selected_docs, key=lambda item: (item.evaluation_rank, item.source_id)):
            result = result_by_source.get(selected.source_id)
            if result is None:
                continue
            record = record_by_url.get(result.url)
            if record is None:
                index = len(record_by_url) + 1
                record = BriefCitationRecord(
                    source_id=result.source_id,
                    index=index,
                    title=result.title,
                    url=result.url,
                    original_content=result.snippet,
                )
                record_by_url[result.url] = record
            elif result.snippet not in record.original_content:
                record.original_content = f"{record.original_content}\n{result.snippet}"
    return list(record_by_url.values())


async def collect_initial_brief_evidence(
    request: BriefCollectorRequest,
    formal_queries: list[BriefQuery],
    first_results: list[BriefSearchResult],
) -> tuple[BriefCollectionResult, BriefCollectionContext]:
    """基于节点已经执行的首轮搜索，评估并登记 Brief 证据。

    Args:
        request: Brief 大纲和评估 LLM。
        formal_queries: 已生成并由框架搜索工具执行的首轮 Query。
        first_results: 已由框架标准化的首轮搜索结果。

    Returns:
        首轮证据结果及供审阅、补搜消费的搜索上下文。
    """
    section_ids = [section.id for section in request.outline.sections]
    section_evidence = await evaluate_brief_sections(
        request.llm,
        request.outline,
        build_section_candidates(first_results, section_ids),
    )
    registry = build_citation_registry(first_results, section_evidence)
    return BriefCollectionResult(
        section_evidence=section_evidence,
        citation_registry=registry,
    ), BriefCollectionContext(
        executed_queries=[item.query for item in formal_queries],
        search_results=first_results,
    )


async def supplement_brief_evidence(
    request: BriefCollectorRequest,
    collection: BriefCollectionResult,
    context: BriefCollectionContext,
    supplementary: list[BriefQuery],
    new_results: list[BriefSearchResult],
) -> tuple[BriefCollectionResult, BriefCollectionContext]:
    """合并节点已经执行的唯一一次补搜，并重评受影响章节。"""
    if not supplementary:
        return collection, context

    affected_ids = list(
        dict.fromkeys(section_id for query in supplementary for section_id in query.section_ids)
    )
    affected_outline = request.outline.model_copy(
        update={
            "sections": [
                section for section in request.outline.sections if section.id in affected_ids
            ]
        }
    )
    first_selected = _selected_results_for_sections(
        context.search_results, collection.section_evidence, affected_ids
    )
    reevaluation_results = merge_search_results(first_selected, new_results)
    reevaluated = await evaluate_brief_sections(
        request.llm,
        affected_outline,
        build_section_candidates(reevaluation_results, affected_ids),
    )
    section_evidence = merge_collection_rounds(collection.section_evidence, reevaluated)
    all_results = merge_search_results(context.search_results, new_results)
    return BriefCollectionResult(
        section_evidence=section_evidence,
        citation_registry=build_citation_registry(all_results, section_evidence),
    ), BriefCollectionContext(
        executed_queries=[*context.executed_queries, *(item.query for item in supplementary)],
        search_results=all_results,
    )
