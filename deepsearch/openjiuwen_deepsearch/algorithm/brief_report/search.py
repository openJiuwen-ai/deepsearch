"""Brief 报告级搜索结果标准化与确定性候选筛选。"""

from collections import OrderedDict
from difflib import SequenceMatcher
from typing import Any

from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefQuery,
    BriefSearchResult,
)
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    canonicalize_url,
    extract_source,
    generate_doc_id,
    generate_source_id,
)
from openjiuwen_deepsearch.algorithm.research_collector.collector_function import (
    filter_search_results_by_exclude_domains,
    filter_search_results_by_exclude_urls,
    filter_web_records_by_temporal_scope,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    _resolve_source_date_scope,
)


def _raw_text(item: dict[str, Any]) -> str:
    """提取搜索接口已经返回的摘要或片段文本。

    Args:
        item: 搜索供应商返回的单条记录。

    Returns:
        可供写作模型消费的摘要或片段；不读取结果 URL。
    """
    return str(item.get("content") or item.get("snippet") or item.get("summary") or "").strip()


def _is_explicit_low_quality_page(item: dict[str, Any]) -> bool:
    """判断供应商是否明确标注了低质量或聚合页。

    Args:
        item: 搜索供应商返回的单条记录。

    Returns:
        供应商显式标注为低质量或聚合页面时返回 True。
    """
    quality = str(item.get("quality") or item.get("quality_level") or "").casefold()
    page_type = str(item.get("page_type") or item.get("result_type") or "").casefold()
    return quality in {"low", "blocked", "spam"} or page_type in {
        "aggregator",
        "search_results",
        "tag_index",
    }


def _near_duplicate(left: BriefSearchResult, right: BriefSearchResult) -> bool:
    """以标题和摘要同时高度相似判定镜像重复。

    Args:
        left: 已保留的搜索结果。
        right: 待比较的搜索结果。

    Returns:
        标题和摘要都达到镜像重复阈值时返回 True。
    """
    title_ratio = SequenceMatcher(None, left.title.casefold(), right.title.casefold()).ratio()
    snippet_ratio = SequenceMatcher(None, left.snippet.casefold(), right.snippet.casefold()).ratio()
    return title_ratio >= 0.97 and snippet_ratio >= 0.97


def _normalize_item(item: dict[str, Any], query: BriefQuery, rank: int) -> BriefSearchResult | None:
    """将单条搜索结果规整为 Brief 数据契约。

    Args:
        item: 搜索供应商返回的单条记录。
        query: 产生此结果的 Brief 查询。
        rank: 该查询内的搜索排名。

    Returns:
        规范化结果；缺少 URL、标题或摘要时返回 None。
    """
    url = canonicalize_url(str(item.get("url") or item.get("link") or item.get("source_url") or ""))
    title = str(item.get("title") or item.get("name") or "").strip()
    snippet = _raw_text(item)
    if not url.startswith(("http://", "https://")) or not title or not snippet:
        return None
    doc_id = generate_doc_id(url=url, title=title, source_type="web")
    source_id = generate_source_id(doc_id, content=snippet)
    date_metadata = item.get("date_metadata") or {}
    if not isinstance(date_metadata, dict):
        date_metadata = {}
    publish_time = str(
        item.get("publish_time")
        or item.get("date")
        or date_metadata.get("parsed_date")
        or ""
    )
    return BriefSearchResult(
        source_id=source_id,
        title=title,
        url=url,
        source=str(item.get("source") or extract_source(url)),
        publish_time=publish_time,
        snippet=snippet,
        search_rank=rank,
        section_ids=list(query.section_ids),
        step_ids=list(query.step_ids),
    )


def _merge_result(existing: BriefSearchResult, incoming: BriefSearchResult) -> None:
    """合并同一或镜像 URL 的摘要及其 Query 路由信息。

    Args:
        existing: 已保留的结果，会被原地更新。
        incoming: 新发现的等价结果。
    """
    existing.section_ids = list(dict.fromkeys([*existing.section_ids, *incoming.section_ids]))
    existing.step_ids = list(dict.fromkeys([*existing.step_ids, *incoming.step_ids]))
    snippets = list(dict.fromkeys([existing.snippet, incoming.snippet]))
    existing.snippet = "\n".join(snippets)
    existing.search_rank = min(existing.search_rank, incoming.search_rank)


def normalize_brief_search_results(
    query_batches: list[tuple[BriefQuery, list[dict[str, Any]]]],
    intent: ResearchIntent,
) -> list[BriefSearchResult]:
    """标准化既有搜索工具结果，并按 canonical URL 合并摘要和路由关系。

    Args:
        query_batches: 已由框架搜索工具执行的 Query 与对应原始结果列表。
        intent: 用户的显式排除和时间约束。

    Returns:
        过滤、去重并保留章节路由关系后的搜索结果。
    """
    merged: OrderedDict[str, BriefSearchResult] = OrderedDict()
    for query, raw_batch in query_batches:
        if not isinstance(raw_batch, list):
            continue
        filtered = filter_search_results_by_exclude_domains(raw_batch, intent.exclude_domains)
        filtered = filter_search_results_by_exclude_urls(filtered, intent.exclude_url, intent.exclude_titles)
        filtered = filter_web_records_by_temporal_scope(filtered, _resolve_source_date_scope(intent))
        for rank, raw in enumerate(filtered, start=1):
            if not isinstance(raw, dict) or _is_explicit_low_quality_page(raw):
                continue
            normalized = _normalize_item(raw, query, rank)
            if normalized is None:
                continue
            existing = merged.get(normalized.url)
            if existing is None:
                existing = next(
                    (item for item in merged.values() if _near_duplicate(item, normalized)),
                    None,
                )
                if existing is None:
                    merged[normalized.url] = normalized
                    continue
            _merge_result(existing, normalized)
    return list(merged.values())


def build_section_candidates(
    results: list[BriefSearchResult | dict[str, Any]],
    section_ids: list[str],
) -> dict[str, list[BriefSearchResult]]:
    """按 Query 声明的 section_id 构建章节候选包。

    Args:
        results: 已标准化的搜索结果或等价字典。
        section_ids: 当前 Brief 大纲的合法章节 ID。

    Returns:
        每个合法章节对应的候选搜索结果，未关联章节保持空列表。
    """
    packages = {section_id: [] for section_id in section_ids}
    for item in results:
        result = item if isinstance(item, BriefSearchResult) else BriefSearchResult.model_validate(item)
        for section_id in result.section_ids:
            if section_id in packages:
                packages[section_id].append(result)
    return packages
