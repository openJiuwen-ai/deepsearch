# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import re
from html import unescape
from typing import Any

from openjiuwen_deepsearch.common.common_constants import MAX_URL_LENGTH, MAX_SEARCH_CONTENT_LENGTH
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


# 镜像/聚合站点为页面标题追加的站点标记词（如 "原标题 | MDPI"、"原标题 - ProQuest"），
# 归一化后位于标题尾部时允许剥离后再做精确匹配。只收明确的站点名，避免误剥正文词汇。
_AGGREGATOR_SUFFIX_TOKENS = {
    "proquest", "mdpi", "researchgate", "sciencedirect", "springer", "springerlink",
    "ieee", "xplore", "nature", "wiley", "semanticscholar", "jstor",
    "acm", "oup", "sage", "tandfonline", "ebsco", "scopus", "bohrium", "aminer", "dblp",
}


def _strip_aggregator_suffix(normalized_title: str) -> str:
    """去掉归一化标题尾部的聚合/出版站点标记词，返回剩余部分（无标记时原样返回）."""
    words = normalized_title.split()
    while words and words[-1] in _AGGREGATOR_SUFFIX_TOKENS:
        words.pop()
    return " ".join(words)


def is_title_blocked(title: Any, blocked_titles: list[str]) -> bool:
    """判断标题是否命中用户要求排除的文章标题.

    匹配规则（任一命中即视为 blocked）：归一化后完全相同；或剥离明确的聚合站后缀
    （``_AGGREGATOR_SUFFIX_TOKENS``，如 ``| MDPI``、`` - ProQuest``）后完全相同。
    不做子串/包含匹配——被禁标题可能只是另一篇论文标题的前缀，子串规则会误伤不同文献。
    """
    if not blocked_titles:
        return False

    target = _normalize_title_for_match(title)
    if not target:
        return False
    target_stripped = _strip_aggregator_suffix(target)
    for blocked_title in blocked_titles:
        blocked = _normalize_title_for_match(blocked_title)
        if not blocked:
            continue
        if target == blocked or target_stripped == _strip_aggregator_suffix(blocked):
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

    filtered_items = []
    removed_count = 0
    for item in items:
        if not isinstance(item, dict):
            filtered_items.append(item)
            continue
        item_url = item.get("url") or item.get("link") or item.get("source_url") or ""
        item_title = item.get("title") or item.get("name") or ""
        url_hit = item_url and is_url_blocked(item_url, exclude_urls)
        title_hit = item_title and is_title_blocked(item_title, exclude_titles)
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


def _normalize_web_search_item(item: Any) -> dict | None:
    """Normalize common web search result field aliases."""
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
    return {
        "type": "page",
        "title": title[:MAX_SEARCH_CONTENT_LENGTH],
        "url": url[:MAX_URL_LENGTH],
        "content": content[:MAX_SEARCH_CONTENT_LENGTH],
    }


def process_tavily_search_result(agent_input: dict, tool_content: Any) -> (list, dict):
    """Tavily搜索工具结果处理方法"""
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
            new_item = _normalize_web_search_item(item)
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
            new_item = _normalize_web_search_item(item)
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
            new_item = _normalize_web_search_item(item)
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
