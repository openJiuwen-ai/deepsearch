# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import re
from collections import namedtuple
from datetime import date
from html import unescape
from typing import Any

import threading

import jieba

# Initialize jieba's dictionary in a background thread to avoid blocking
# the asyncio event loop on first call. jieba.cut itself is fast for short
# title strings (typically < 50 chars).
_jieba_ready = threading.Event()


def _init_jieba():
    try:
        jieba.initialize()
    except Exception as e:
        logger.warning("jieba initialize failed: %s", e)
    finally:
        _jieba_ready.set()


threading.Thread(target=_init_jieba, daemon=True).start()

from openjiuwen_deepsearch.common.common_constants import (
    MAX_COLLECTOR_DOC_CONTENT_LENGTH,
    MAX_URL_LENGTH,
    MAX_SEARCH_CONTENT_LENGTH,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import TemporalScope
from openjiuwen_deepsearch.utils.common_utils.date_utils import parse_published_date
from openjiuwen_deepsearch.framework.openjiuwen.tools import build_runtime_api_search_payload 
from openjiuwen_deepsearch.utils.common_utils.url_utils import extract_domain_from_url, is_url_blocked, \
    normalize_domains
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def _get_exclude_domains(agent_input: dict) -> list[str]:
    """从 agent_input 的 research_intent 中获取需要排除的域名."""
    research_intent = agent_input.get("research_intent") or {}
    if isinstance(research_intent, dict):
        return normalize_domains(research_intent.get("exclude_domains"))
    return normalize_domains(getattr(research_intent, "exclude_domains", []))


def _get_exclude_urls(agent_input: dict) -> list[str]:
    """从 agent_input 的 research_intent 中获取需要排除的链接."""
    research_intent = agent_input.get("research_intent") or {}
    if isinstance(research_intent, dict):
        value = research_intent.get("exclude_url")
    else:
        value = getattr(research_intent, "exclude_url", [])
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _get_exclude_titles(agent_input: dict) -> list[str]:
    """从 agent_input 的 research_intent 中获取需要排除的文章标题."""
    research_intent = agent_input.get("research_intent") or {}
    if isinstance(research_intent, dict):
        value = research_intent.get("exclude_titles")
    else:
        value = getattr(research_intent, "exclude_titles", [])
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_title_for_match(title: Any) -> str:
    """归一化标题用于等价匹配：反转义 HTML 实体、小写、去标点、合并空白（保留 CJK 字符）."""
    text = unescape(str(title or "")).strip().lower()
    text = re.sub(r"[^\w一-鿿]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# CJK 字符范围（用于判断是否需要 jieba 分词）
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _tokenize_title(normalized_title: str) -> frozenset[str]:
    """将归一化后的标题拆分为词集合（用于词重叠率计算）.

    英文按空格分词；包含 CJK 字符时使用 jieba 分词后合并。
    """
    tokens = set()
    # 按空格拆分，对每个片段判断是否含 CJK
    for segment in normalized_title.split():
        if _CJK_PATTERN.search(segment):
            _jieba_ready.wait(timeout=2.0)
            tokens.update(jieba.cut(segment))
        else:
            tokens.add(segment)
    # 过滤中英文常见虚词，避免短标题 Jaccard 误判
    _stopwords = frozenset({
        "的", "了", "是", "在", "和", "与", "及", "等", "之", "其",
        "a", "an", "the", "of", "and", "in", "on", "for", "to",
    })
    tokens -= _stopwords
    return frozenset(tokens)


# 预处理的被禁标题结构
_PreprocessedBlockedTitle = namedtuple("_PreprocessedBlockedTitle", [
    "raw",            # 原始被禁标题
    "normalized",     # 归一化后的完整字符串
    "stripped",       # 剥离聚合站后缀后的字符串
    "tokens",         # 归一化后的词集合
    "stripped_tokens",  # 剥离后缀后的词集合
])


def preprocess_blocked_titles(titles: list[str]) -> list[_PreprocessedBlockedTitle]:
    """预处理被禁标题列表，避免每次匹配重复归一化.

    Returns:
        预处理后的被禁标题列表，每个元素含归一化字符串、剥离后缀字符串和词集合。
    """
    result = []
    for t in titles:
        norm = _normalize_title_for_match(t)
        if not norm:
            continue
        stripped = _strip_aggregator_suffix(norm)
        result.append(_PreprocessedBlockedTitle(
            raw=t,
            normalized=norm,
            stripped=stripped,
            tokens=_tokenize_title(norm),
            stripped_tokens=_tokenize_title(stripped),
        ))
    return result


# 镜像/聚合站点为页面标题追加的站点标记词（如 "原标题 | MDPI"、"原标题 - ProQuest"），
# 归一化后位于标题尾部时允许剥离后再做精确匹配。只收明确的站点名，避免误剥正文词汇。
_AGGREGATOR_SUFFIX_TOKENS = {
    "proquest", "mdpi", "researchgate", "sciencedirect", "springer", "springerlink",
    "ieee", "xplore", "nature", "wiley", "semanticscholar", "jstor",
    "acm", "oup", "sage", "tandfonline", "ebsco", "scopus", "bohrium", "aminer", "dblp",
    "ideals",
}


def _strip_aggregator_suffix(normalized_title: str) -> str:
    """去掉归一化标题尾部的聚合/出版站点标记词，返回剩余部分（无标记时原样返回）."""
    words = normalized_title.split()
    while words and words[-1] in _AGGREGATOR_SUFFIX_TOKENS:
        words.pop()
    return " ".join(words)


def is_title_blocked(title: Any, blocked_titles: list[str]) -> bool:
    """判断标题是否命中用户要求排除的文章标题.

    匹配规则（任一命中即视为 blocked）：
    1. 归一化后完全相同；
    2. 剥离聚合站后缀后完全相同；
    3. 当被禁标题足够长（≥30 归一化字符）时，做包含匹配；
    4. 词重叠率 ≥ 70% 时视为同一文献（捕获镜像站标题变体）。
    """
    if not blocked_titles:
        return False
    preprocessed = preprocess_blocked_titles(blocked_titles)
    return _is_title_blocked_preprocessed(title, preprocessed)


def _is_title_blocked_preprocessed(
    title: Any,
    blocked_preprocessed: list[_PreprocessedBlockedTitle],
) -> bool:
    """使用预处理后的被禁标题进行匹配（内部函数）."""
    if not blocked_preprocessed:
        return False

    target = _normalize_title_for_match(title)
    if not target:
        return False
    target_stripped = _strip_aggregator_suffix(target)
    target_tokens = _tokenize_title(target)
    target_stripped_tokens = _tokenize_title(target_stripped)

    for bp in blocked_preprocessed:
        # Rule 1 & 2: exact match (raw and stripped)
        if target == bp.normalized or target_stripped == bp.stripped:
            return True
        # Rule 3: containment match for long titles
        # Skip when blocked title is a strict prefix of target at the START
        # (e.g. "topic" vs "topic: A Survey" → likely different paper).
        # But DO block when blocked is in the MIDDLE/END (same paper with
        # metadata wrapping, e.g. "[PDF] topic Author Name").
        if len(bp.normalized) >= 30:
            if bp.tokens and target_tokens and bp.tokens < target_tokens:
                # Skip only when blocked is a strict prefix AND the extra part
                # is NOT just aggregator suffix tags (e.g. "topic | IDEALS"
                # should still be blocked, "topic: A Survey" should not).
                if target.startswith(bp.normalized) and target_stripped != bp.stripped:
                    continue
            if bp.normalized in target:
                return True
            if bp.stripped in target_stripped:
                return True
        # Rule 4: word overlap (Jaccard similarity) >= 70%
        if target_tokens and bp.tokens:
            overlap = len(target_tokens & bp.tokens)
            union = len(target_tokens | bp.tokens)
            if union > 0 and overlap / union >= 0.70:
                # Keep existing prefix/subset skip logic
                if not (bp.tokens < target_tokens
                        and target.startswith(bp.normalized)
                        and target_stripped != bp.stripped):
                    return True
        if target_stripped_tokens and bp.stripped_tokens:
            overlap = len(target_stripped_tokens & bp.stripped_tokens)
            union = len(target_stripped_tokens | bp.stripped_tokens)
            if union > 0 and overlap / union >= 0.70:
                if not (bp.stripped_tokens < target_stripped_tokens and target_stripped.startswith(bp.stripped)):
                    return True
    return False


def _is_domain_match(domain: str, target_domain: str) -> bool:
    """判断 domain 是否命中指定域名或其子域名."""
    if not domain or not target_domain:
        return False
    return domain == target_domain or domain.endswith(f".{target_domain}")


def filter_search_results_by_exclude_domains(items: list, exclude_domains: list[str]) -> list:
    """按 exclude_domains 过滤搜索结果."""
    normalized_exclude_domains = normalize_domains(exclude_domains)
    if not normalized_exclude_domains:
        return items

    filtered_items = []
    removed_count = 0
    for item in items:
        if not isinstance(item, dict):
            filtered_items.append(item)
            continue
        item_url = item.get("url") or item.get("link") or ""
        item_domain = extract_domain_from_url(item_url)
        if item_domain and any(_is_domain_match(item_domain, domain) for domain in normalized_exclude_domains):
            removed_count += 1
            continue
        filtered_items.append(item)
    logger.info(
        "[COLLECTOR FUNCTION] exclude_domains filter applied. before=%s after=%s removed=%s",
        len(items),
        len(filtered_items),
        removed_count,
    )
    return filtered_items


def filter_search_results_by_exclude_urls(
        items: list,
        exclude_urls: list[str],
        exclude_titles: list[str] | None = None,
) -> list:
    """按 exclude_url / exclude_titles 过滤搜索结果.

    URL 命中禁引列表（归一化 host+path 精确匹配）或标题命中禁引文章标题的条目会被剔除，
    防止用户明确要求避开的页面/文献（含同文献的镜像变体）进入收集、抓取与引用环节。
    """
    if not exclude_urls and not exclude_titles:
        return items

    # 预处理被禁标题，避免循环内重复归一化
    preprocessed_titles = preprocess_blocked_titles(exclude_titles) if exclude_titles else []

    filtered_items = []
    removed_count = 0
    for item in items:
        if not isinstance(item, dict):
            filtered_items.append(item)
            continue
        item_url = item.get("url") or item.get("link") or item.get("source_url") or ""
        item_title = item.get("title") or item.get("name") or ""
        url_hit = item_url and is_url_blocked(item_url, exclude_urls)
        title_hit = bool(item_title and _is_title_blocked_preprocessed(item_title, preprocessed_titles))
        if url_hit or title_hit:
            removed_count += 1
            if LogManager.is_sensitive():
                logger.info(
                    "[COLLECTOR FUNCTION] blocked item excluded (redacted, url_hit=%s, title_hit=%s)",
                    bool(url_hit), bool(title_hit),
                )
            else:
                logger.info(
                    "[COLLECTOR FUNCTION] blocked item excluded (url_hit=%s, title_hit=%s). url=%s title=%s",
                    bool(url_hit), bool(title_hit), str(item_url)[:120], str(item_title)[:100],
                )
            continue
        filtered_items.append(item)
    logger.info(
        "[COLLECTOR FUNCTION] exclude_url/title filter applied. before=%s after=%s removed=%s",
        len(items),
        len(filtered_items),
        removed_count,
    )
    return filtered_items


async def process_tool_call(response, agent_input: dict, tool_dict: dict, step_info: dict) -> dict:
    """处理工具调用"""
    agent_input = check_agent_input(agent_input)
    # Research 只保留第一个工具调用
    tool_call = response.get("tool_calls", [])[-1]
    call_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [tool_call]
    }
    agent_input["messages"].append(call_message)

    agent_input = await handle_single_tool_call(tool_call, agent_input, tool_dict, step_info)

    return agent_input


def check_agent_input(agent_input: dict, section_idx: int = 0) -> dict:
    """检查agent_input是否包含必要的key"""
    necessary_keys = ["messages", "web_page_search_record", "local_text_search_record", "other_tool_record"]
    for key in necessary_keys:
        if key not in agent_input:
            agent_input[key] = []
            logger.info(f"section_idx: {section_idx} | "
                        f"[COLLECTOR FUNCTION] agent_input missing key: {key}, has been added.")
    return agent_input


async def handle_single_tool_call(tool_call: dict, agent_input: dict, tool_dict: dict, step_info: dict) -> dict:
    """处理单个工具调用"""

    tool_results = await execute_tool(tool_call, agent_input, tool_dict, step_info)
    agent_input = create_tool_message(tool_results, tool_call, agent_input)
    return agent_input


async def execute_tool(tool_call: dict, agent_input: dict, tool_dict: dict, step_info: dict) -> list:
    """执行工具调用"""
    section_idx = step_info.get("section_idx", 0)
    step_title = step_info.get("step_title", "")
    query = step_info.get("search_query", step_title)
    web_search_engine_name = step_info.get("web_search_engine_name") or ""
    local_search_engine_name = step_info.get("local_search_engine_name") or ""

    processed_results = []
    if not LogManager.is_sensitive():
        logger.debug("section_idx: %s | step title %s | Collecting info for query: %s | "
                     "[COLLECTOR FUNCTION] Tool call: %s", section_idx, step_title, query, tool_call)
    tool_name = tool_call.get("name", "")
    if tool_name not in tool_dict:
        if LogManager.is_sensitive():
            logger.error(f"section_idx: {section_idx} | "
                         f"[COLLECTOR FUNCTION] tool name '{tool_name}' not found, skipping")
        else:
            logger.error(f"section_idx: {section_idx} | step title {step_title} | Collecting info for query: {query} |"
                         f"[COLLECTOR FUNCTION] tool name '{tool_name}' not found, skipping")
        return processed_results

    try:
        args = tool_call.get("args", {})
        if isinstance(args, str):
            args = json.loads(args)
        if tool_name == "local_search_tool":
            args["search_engine_name"] = local_search_engine_name
        elif tool_name == "web_search_tool":
            args["search_engine_name"] = web_search_engine_name
        result = await tool_dict[tool_name].invoke(args)
        tool_result = json.dumps(result, ensure_ascii=False, indent=4)
        processed_results = process_tool_result(tool_name, tool_result, agent_input)
    except Exception as e:
        if LogManager.is_sensitive():
            logger.error(f"section_idx: {section_idx} | "
                         f"[COLLECTOR FUNCTION] ReAct Tool '{tool_name}' execute error")
        else:
            logger.exception(f"section_idx: {section_idx} | step title {step_title} | "
                             f"Collecting info for query: {query} | "
                             f"[COLLECTOR FUNCTION] ReAct Tool '{tool_name}' execute error: {e}")
        return processed_results

    if tool_name not in ("web_search_tool", "local_search_tool"):
        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | "
                        f"[COLLECTOR FUNCTION] Custom tool '{tool_name}' call finished. "
                        f"result_count={len(processed_results)}")
        else:
            logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                        f"Collecting info for query: {query} | "
                        f"[COLLECTOR FUNCTION] Custom tool '{tool_name}' call finished. "
                        f"result_count={len(processed_results)}")

    if LogManager.is_sensitive():
        logger.info(f"section_idx: {section_idx} | "
                    f"[COLLECTOR FUNCTION] Finish ReAct Tool call.")
    else:
        logger.info(f"section_idx: {section_idx} | step title {step_title} | Collecting info for query: {query} | "
                    f"[COLLECTOR FUNCTION] Finish ReAct Tool call.")

    return processed_results


def process_tool_result(tool_name: str, tool_content: Any, agent_input: dict) -> list:
    """处理工具返回结果"""

    if tool_name == "web_search_tool":
        tool_result, agent_input = web_search_jiuwen(agent_input, tool_content)
    elif tool_name == "local_search_tool":
        tool_result, agent_input = process_local_search_result(agent_input, tool_content)
    else:
        tool_result = json.loads(tool_content)
        runtime_api_search_payload = build_runtime_api_search_payload(tool_result)
        if runtime_api_search_payload is not None:
            tool_result, agent_input = web_search_jiuwen(
                agent_input,
                json.dumps(runtime_api_search_payload, ensure_ascii=False),
            )
        else:
            result_dict = {
                "tool_name": tool_name,
                "content": tool_content,
            }
            agent_input["other_tool_record"].append(result_dict)

    return tool_result


def web_search_jiuwen(agent_input: dict, tool_content: Any) -> (list, dict):
    """处理jiuwen搜索工具结果"""
    tool_content = json.loads(tool_content)
    engine = tool_content.get("search_engine", "")
    results = tool_content.get("search_results", "")

    if tool_content.get("error") or (isinstance(results, list) and any(isinstance(item, str) for item in results)):
        error_msg = tool_content.get("error") or (results[0] if isinstance(results, list) and
                                                                results else "unknown error")
        if LogManager.is_sensitive():
            logger.error(f"[COLLECTOR FUNCTION] Search engine '{engine}' returned error")
        else:
            logger.error(f"[COLLECTOR FUNCTION] Search engine '{engine}' returned error: {error_msg}")
        return [], agent_input

    if engine == "google":
        tool_result, agent_input = process_google_search_result(agent_input, results)
    elif engine == "tavily":
        tool_result, agent_input = process_tavily_search_result(agent_input, results)
    else:
        tool_result, agent_input = process_common_search_result(agent_input, results)

    return tool_result, agent_input


def _first_non_empty(item: dict, keys: tuple[str, ...]) -> str:
    """Return the first non-empty string value from a search result row."""
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        value = str(value).strip()
        if value:
            return value
    return ""


def _parse_absolute_date(value: Any) -> date | None:
    """仅解析 Tavily 已归一化的 ISO 日期。

    Args:
        value: Tavily 结果中的统一发表日期。

    Returns:
        可确认的日期；非 ISO 日期返回 None。
    """
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _normalize_web_search_item(
        item: Any,
        include_date_metadata: bool = False,
) -> dict | None:
    """归一化 web 结果，并按需附加发表日期。

    Args:
        item: 搜索引擎返回的单条结果。
        include_date_metadata: 是否附加发表日期。Tavily 走已归一化的
            ``source_date``+``source_date_type`` 契约（严格 ISO）；无则按序
            取原生 ``published``/``published_at``/``published_date``（容错
            解析，覆盖 arxiv ISO 8601 与 PubMed ``YYYY Mon DD``）；解析不出
            不附加。

    Returns:
        归一化文档；缺少 URL 或输入非法时返回 None。
    """
    if not isinstance(item, dict):
        return None

    url = _first_non_empty(item, ("url", "link", "source_url"))
    if not url:
        return None

    title = _first_non_empty(item, ("title", "name")) or url
    content = _first_non_empty(
        item,
        ("content", "raw_content", "snippet", "summary", "answer"),
    )
    normalized = {
        "type": "page",
        "title": title[:MAX_SEARCH_CONTENT_LENGTH],
        "url": url[:MAX_URL_LENGTH],
        "content": content[:MAX_SEARCH_CONTENT_LENGTH],
    }
    full_text_status = str(item.get("full_text_status") or "").strip().casefold()
    if full_text_status in {"available", "unavailable", "failed"}:
        full_text = str(item.get("full_text") or "")[:MAX_COLLECTOR_DOC_CONTENT_LENGTH]
        if full_text_status == "available" and not full_text.strip():
            full_text_status = "unavailable"
        normalized["full_text"] = full_text
        normalized["content_type"] = str(item.get("content_type") or "abstract")
        normalized["full_text_url"] = str(item.get("full_text_url") or "")[:MAX_URL_LENGTH]
        normalized["full_text_format"] = str(item.get("full_text_format") or "")
        normalized["full_text_status"] = full_text_status
        normalized["full_text_truncated"] = item.get("full_text_truncated") is True
    if item.get("skip_webpage_enrichment") is True:
        normalized["skip_webpage_enrichment"] = True
    academic_source = str(item.get("source") or "").strip().lower()
    if academic_source in {"pubmed", "arxiv"}:
        normalized["academic_source"] = academic_source
        academic_source_id = str(item.get("source_id") or "").strip()
        if academic_source_id:
            normalized["academic_source_id"] = academic_source_id
    doi = str(item.get("doi") or "").strip()
    if doi:
        normalized["doi"] = doi
    if include_date_metadata:
        source_date_type = str(item.get("source_date_type") or "").strip()
        raw_date = item.get("source_date")
        field_name = "source_date"
        if not (raw_date is not None and str(raw_date).strip()
                and source_date_type == "published"):
            # 退到原生 published* 字段（pubmed/arxiv 等）；字段名本身声明发表语义
            raw_date = None
            for cand in ("published", "published_at", "published_date"):
                v = item.get(cand)
                if v is not None and str(v).strip():
                    raw_date = v
                    field_name = cand
                    break
        if raw_date is not None and str(raw_date).strip():
            parsed_date = (
                _parse_absolute_date(raw_date)
                if field_name == "source_date"
                else parse_published_date(raw_date)
            )
            if parsed_date:
                normalized["date_metadata"] = {
                    "field": field_name,
                    "type": "published",
                    "value": str(raw_date)[:MAX_SEARCH_CONTENT_LENGTH],
                    "parsed_date": parsed_date.isoformat(),
                }
    # Preserve relevance score from search APIs (Tavily, etc.)
    raw_score = item.get("score")
    if isinstance(raw_score, (int, float)):
        normalized["score"] = float(raw_score)
    return normalized


def filter_web_records_by_temporal_scope(
        records: list[dict],
        temporal_scope: TemporalScope | dict | None,
) -> list[dict]:
    """按来源发表时间过滤归一化 web 文档，日期未知时保留召回。

    Args:
        records: 已归一化的 web 文档列表。
        temporal_scope: 结构化时间范围。

    Returns:
        保留的文档列表。
    """
    scope = None
    if temporal_scope is not None:
        try:
            scope = temporal_scope if isinstance(temporal_scope, TemporalScope) else TemporalScope.model_validate(
                temporal_scope
            )
        except (TypeError, ValueError):
            scope = None

    if scope is None or scope.constraint_type != "source_date":
        return records

    kept = []
    date_unknown = 0
    filtered_out = 0
    for record in records:
        metadata = record.get("date_metadata") or {}
        parsed_text = metadata.get("parsed_date") or ""
        parsed_date = _parse_absolute_date(parsed_text)
        if parsed_date is None:
            date_unknown += 1
            kept.append(record)
            continue

        reason = ""
        if scope.start_date and parsed_date < scope.start_date:
            reason = "before_start_date"
        elif scope.end_date and parsed_date > scope.end_date:
            reason = "after_end_date"
        if not reason:
            kept.append(record)
            continue

        filtered_out += 1

    logger.info(
        "[COLLECTOR FUNCTION] source_date filter applied. raw=%s kept=%s filtered_out=%s date_unknown=%s",
        len(records),
        len(kept),
        filtered_out,
        date_unknown,
    )
    return kept


def _apply_temporal_filter(agent_input: dict, records: list[dict]) -> list[dict]:
    """按当前研究意图过滤单批 web 记录。

    Args:
        agent_input: 当前 query 的 collector 内部状态。
        records: 已归一化的 web 记录。

    Returns:
        通过时间过滤的正式 web 记录。
    """
    research_intent = agent_input.get("research_intent") or {}
    temporal_scope = (
        research_intent.get("temporal_scope")
        if isinstance(research_intent, dict)
        else getattr(research_intent, "temporal_scope", None)
    )
    return filter_web_records_by_temporal_scope(
        records,
        temporal_scope,
    )


def process_tavily_search_result(agent_input: dict, tool_content: Any) -> (list, dict):
    """归一化、过滤并保存 Tavily 搜索结果。

    Args:
        agent_input: 当前 query 的 collector 内部状态。
        tool_content: Tavily 搜索结果列表。

    Returns:
        时间过滤后的紧凑工具视图和更新后的 collector 内部状态。
    """
    original_records = agent_input.get("web_page_search_record", [])
    if not isinstance(original_records, list):
        original_records = []
    tool_result = []
    try:
        raw_results = tool_content if isinstance(tool_content, list) else []
        raw_results = filter_search_results_by_exclude_domains(raw_results, _get_exclude_domains(agent_input))
        raw_results = filter_search_results_by_exclude_urls(
            raw_results, _get_exclude_urls(agent_input), _get_exclude_titles(agent_input))
        added_records = []
        for item in raw_results:
            new_item = _normalize_web_search_item(item, include_date_metadata=True)
            if new_item is not None:
                added_records.append(new_item)
        added_records = _apply_temporal_filter(agent_input, added_records)
        tool_result = added_records
        combined_records = original_records + added_records
        agent_input["web_page_search_record"] = remove_duplicate_items(combined_records)
    except Exception as e:
        agent_input["web_page_search_record"] = original_records
        if LogManager.is_sensitive():
            logger.error(f"[COLLECTOR FUNCTION] Error when get web search records")
        else:
            logger.error(f"[COLLECTOR FUNCTION] Error when get web search records '{e}': {tool_content}")

    return tool_result, agent_input


def process_google_search_result(agent_input: dict, tool_content: Any) -> (list, dict):
    """Google Serper搜索工具结果处理方法"""
    original_records = agent_input.get("web_page_search_record", [])
    if not isinstance(original_records, list):
        original_records = []
    tool_result = []
    try:
        tool_result = tool_content if isinstance(tool_content, list) else []
        tool_result = filter_search_results_by_exclude_domains(tool_result, _get_exclude_domains(agent_input))
        tool_result = filter_search_results_by_exclude_urls(
            tool_result, _get_exclude_urls(agent_input), _get_exclude_titles(agent_input))
        added_records = []
        for item in tool_result:
            new_item = _normalize_web_search_item(item, include_date_metadata=True)
            if new_item is None:
                continue
            added_records.append(new_item)
        combined_records = original_records + added_records
        agent_input["web_page_search_record"] = remove_duplicate_items(combined_records)
    except Exception as e:
        agent_input["web_page_search_record"] = original_records
        if LogManager.is_sensitive():
            logger.error(f"[COLLECTOR FUNCTION] Error when get web search records")
        else:
            logger.error(f"[COLLECTOR FUNCTION] Error when get web search records '{e}': {tool_content}")

    return tool_result, agent_input


def process_common_search_result(agent_input: dict, tool_content: Any) -> (list, dict):
    """标准搜索工具结果处理方法"""
    original_records = agent_input.get("web_page_search_record", [])
    if not isinstance(original_records, list):
        original_records = []
    tool_result = []
    try:
        tool_result = tool_content if isinstance(tool_content, list) else []
        tool_result = filter_search_results_by_exclude_domains(tool_result, _get_exclude_domains(agent_input))
        tool_result = filter_search_results_by_exclude_urls(
            tool_result, _get_exclude_urls(agent_input), _get_exclude_titles(agent_input))
        added_records = []
        for item in tool_result:
            new_item = _normalize_web_search_item(item, include_date_metadata=True)
            if new_item is not None:
                added_records.append(new_item)
        combined_records = original_records + added_records
        agent_input["web_page_search_record"] = remove_duplicate_items(combined_records)
    except Exception as e:
        agent_input["web_page_search_record"] = original_records
        if LogManager.is_sensitive():
            logger.error(f"[COLLECTOR FUNCTION] Error when get web search records")
        else:
            logger.error(f"[COLLECTOR FUNCTION] Error when get web search records '{e}': {tool_content}")

    return tool_result, agent_input


def process_local_search_result(agent_input: dict, tool_content: Any) -> (list, dict):
    """本地搜索工具结果处理方法"""

    tool_content = json.loads(tool_content)

    results = tool_content.get("search_results", "")
    if tool_content.get("error") or (isinstance(results, list) and any(isinstance(item, str) for item in results)):
        error_msg = tool_content.get("error") or (results[0] if isinstance(results, list) and
                                                                results else "unknown error")
        if LogManager.is_sensitive():
            logger.error(f"[COLLECTOR FUNCTION] Local search engine returned error")
        else:
            logger.error(f"[COLLECTOR FUNCTION] Local search engine returned error: {error_msg}")
        return [], agent_input

    tool_result, agent_input = process_local_search_common(agent_input, results)
    agent_input["local_text_search_record"] = remove_duplicate_items(agent_input["local_text_search_record"])

    return tool_result, agent_input


def process_local_search_common(agent_input: dict, tool_content: Any) -> (list, dict):
    """标准搜索工具结果处理方法"""
    original_records = agent_input.get("local_text_search_record", [])
    if not isinstance(original_records, list):
        original_records = []
    tool_result = []
    try:
        tool_result = tool_content if isinstance(tool_content, list) else []
        added_records = []
        for item in tool_result:
            if not isinstance(item, dict):
                continue
            knowledge_base_id = item.get("knowledge_base_id", "")
            file_id = item.get("file_id", "")
            source_title = (
                item.get("title")
                or item.get("document_name")
                or file_id
            )
            result = {
                "type": "text",
                "url": f"localdataset://result//{knowledge_base_id}//{file_id}",
                "title": str(source_title)[:MAX_SEARCH_CONTENT_LENGTH],
                "content": item.get("content", "")[:MAX_SEARCH_CONTENT_LENGTH],
                "score": item.get("score", 0.0)
            }
            added_records.append(result)
        combined_records = original_records + added_records
        agent_input["local_text_search_record"] = remove_duplicate_items(combined_records)
    except Exception as e:
        agent_input["local_text_search_record"] = original_records
        if LogManager.is_sensitive():
            logger.error(f"[COLLECTOR FUNCTION] Error when get local search records")
        else:
            logger.error(f"[COLLECTOR FUNCTION] Error when get local search records '{e}': {tool_content}")

    return tool_result, agent_input


def remove_duplicate_items(items: list[dict]) -> list[dict]:
    """去除重复的搜索结果或 evidence 项。

    Args:
        items: 搜索结果或已结构化 evidence 列表。

    Returns:
        去重后的列表；带 source_id 的 evidence 优先按 source_id 去重，原始搜索结果按
        title/url/content 去重，无 content 时退回 title/url。
    """
    seen = set()
    unique_items = []

    for item in items:
        if isinstance(item, dict) and ('title' in item and 'url' in item):
            source_id = item.get("source_id")
            if source_id:
                key = ("source_id", source_id)
            elif "content" in item:
                # 搜索工具可能对同一 URL/title 返回不同 query-specific snippet，需保留不同证据片段。
                key = ("title_url_content", item['title'], item['url'], item.get("content") or "")
            else:
                key = ("title_url", item['title'], item['url'])
            if key not in seen:
                seen.add(key)
                unique_items.append(item)

    logger.info(f"Remove duplicate items, original {len(items)} items, left {len(unique_items)} items.")

    return unique_items


def create_tool_message(results: list, tool_call: dict, agent_input: dict) -> dict:
    """创建工具消息"""

    tool_name = tool_call.get("name", "")
    tool_message = {
        "role": "tool",
        "content": json.dumps(results, ensure_ascii=False),
        "name": tool_name,
        "tool_call_id": tool_call["id"]
    }

    agent_input["messages"].append(tool_message)

    return agent_input
