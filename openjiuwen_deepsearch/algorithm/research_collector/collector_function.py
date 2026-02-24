# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging

from openjiuwen_deepsearch.common.common_constants import MAX_URL_LENGTH, MAX_SEARCH_CONTENT_LENGTH
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


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
        else:
            args["search_engine_name"] = ""
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

    if LogManager.is_sensitive():
        logger.info(f"section_idx: {section_idx} | "
                    f"[COLLECTOR FUNCTION] Finish ReAct Tool call.")
    else:
        logger.info(f"section_idx: {section_idx} | step title {step_title} | Collecting info for query: {query} | "
                    f"[COLLECTOR FUNCTION] Finish ReAct Tool call.")

    return processed_results


def process_tool_result(tool_name: str, tool_content: any, agent_input: dict) -> list:
    """处理工具返回结果"""

    if "web_search" in tool_name:
        tool_result, agent_input = web_search_jiuwen(agent_input, tool_content)
    elif tool_name == "local_search_tool":
        tool_result, agent_input = process_local_search_result(agent_input, tool_content)
    else:
        tool_result = json.loads(tool_content)
        result_dict = {
            "tool_name": tool_name,
            "content": tool_content,
        }
        agent_input["other_tool_record"].append(result_dict)

    return tool_result


def web_search_jiuwen(agent_input: dict, tool_content: any) -> (list, dict):
    """处理jiuwen搜索工具结果"""
    tool_content = json.loads(tool_content)
    engine = tool_content.get("search_engine", "")
    results = tool_content.get("search_results", "")
    if engine == "google":
        tool_result, agent_input = process_google_search_result(agent_input, results)
    elif engine == "tavily":
        tool_result, agent_input = process_tavily_search_result(agent_input, results)
    else:
        tool_result, agent_input = process_common_search_result(agent_input, results)

    return tool_result, agent_input


def process_tavily_search_result(agent_input: dict, tool_content: any) -> (list, dict):
    """Tavily搜索工具结果处理方法"""
    original_records = agent_input.get("web_page_search_record", [])
    if not isinstance(original_records, list):
        original_records = []
    tool_result = []
    try:
        tool_result = tool_content if isinstance(tool_content, list) else []
        added_records = []
        for item in tool_result:
            added_records.append(item)
        combined_records = original_records + added_records
        agent_input["web_page_search_record"] = remove_duplicate_items(combined_records)
    except Exception as e:
        agent_input["web_page_search_record"] = original_records
        if LogManager.is_sensitive():
            logger.error(f"[COLLECTOR FUNCTION] Error when get web search records")
        else:
            logger.error(f"[COLLECTOR FUNCTION] Error when get web search records '{e}': {tool_content}")

    return tool_result, agent_input


def process_google_search_result(agent_input: dict, tool_content: any) -> (list, dict):
    """Google Serper搜索工具结果处理方法"""
    original_records = agent_input.get("web_page_search_record", [])
    if not isinstance(original_records, list):
        original_records = []
    tool_result = []
    try:
        tool_result = tool_content if isinstance(tool_content, list) else []
        added_records = []
        for item in tool_result:
            if not isinstance(item, dict):
                continue
            new_item = {
                "type": "page",
                "title": item.get("title", "")[:MAX_SEARCH_CONTENT_LENGTH],
                "url": item.get("link", "")[:MAX_URL_LENGTH],
                "content": item.get("snippet", "")[:MAX_SEARCH_CONTENT_LENGTH]
            }
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


def process_common_search_result(agent_input: dict, tool_content: any) -> (list, dict):
    """标准搜索工具结果处理方法"""
    original_records = agent_input.get("web_page_search_record", [])
    if not isinstance(original_records, list):
        original_records = []
    tool_result = []
    try:
        tool_result = tool_content if isinstance(tool_content, list) else []
        added_records = []
        for item in tool_result:
            new_item = {
                "type": "page",
                "title": item.get("title", "")[:MAX_SEARCH_CONTENT_LENGTH],
                "url": item.get("url", "")[:MAX_URL_LENGTH],
                "content": item.get("content", "")[:MAX_SEARCH_CONTENT_LENGTH]
            }
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


def process_local_search_result(agent_input: dict, tool_content: any) -> (list, dict):
    """本地搜索工具结果处理方法"""

    tool_content = json.loads(tool_content)
    results = tool_content.get("search_results", "")
    tool_result, agent_input = process_local_search_common(agent_input, results)
    agent_input["local_text_search_record"] = remove_duplicate_items(agent_input["local_text_search_record"])

    return tool_result, agent_input


def process_local_search_common(agent_input: dict, tool_content: any) -> (list, dict):
    """标准搜索工具结果处理方法"""
    original_records = agent_input.get("local_text_search_record", [])
    if not isinstance(original_records, list):
        original_records = []
    tool_result = []
    try:
        tool_result = tool_content if isinstance(tool_content, list) else []
        added_records = []
        for item in tool_result:
            knowledge_base_id = item.get("knowledge_base_id", "")
            file_id = item.get("file_id", "")
            result = {
                "type": "text",
                "url": f"localdataset://result//{knowledge_base_id}//{file_id}",
                "title": item.get("document_name", "")[:MAX_SEARCH_CONTENT_LENGTH],
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
    """去除重复的搜索结果"""
    seen = set()
    unique_items = []

    for item in items:
        if isinstance(item, dict) and ('title' in item and 'url' in item):
            key = (item['title'], item['url'])
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
