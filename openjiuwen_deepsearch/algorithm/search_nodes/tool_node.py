# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode


def format_tool_result_for_message(tool_result: Any) -> str:
    if isinstance(tool_result, str):
        return tool_result
    try:
        return json.dumps(tool_result, ensure_ascii=False, default=str)
    except Exception:
        return str(tool_result)


@dataclass
class ExecuteToolConfig:
    tool_map: Dict[str, Any]
    tool_name: str
    tool_args: Dict[str, Any]
    config: Dict[str, Any]
    retrieval_settings: Dict[str, Any]
    action: Dict[str, Any]
    new_found_evidence_ids: List[Any]


async def _call_custom_tool(tool_map: dict, tool_name: str, tool_args: dict):
    if "search" in tool_name.lower():
        tool_name = "web_search"
    elif "fetch" in tool_name.lower():
        tool_name = "web_fetch"
    elif "retrieve" in tool_name.lower():
        tool_name = "retrieve"

    if tool_name not in tool_map:
        raise CustomValueException(
            StatusCode.LOAD_EXTEND_TOOLS_FAILED.code,
            StatusCode.LOAD_EXTEND_TOOLS_FAILED.errmsg.format(tool_name=tool_name),
        )

    tool = tool_map[tool_name]

    if hasattr(tool, "acall"):
        return await tool.acall(tool_args)
    return await asyncio.to_thread(tool.call, tool_args)


async def execute_tool(execute_config: ExecuteToolConfig) -> Tuple[Any, List[Any]]:
    available_tools = list(execute_config.tool_map.keys())
    normalized_tool_name = execute_config.tool_name.lower()

    tool_args = dict(execute_config.tool_args)
    tool_matched = False

    if "fetch" in normalized_tool_name and "web_fetch" in available_tools:
        tool_matched = True
        tool_args["log_fetch"] = execute_config.config.get("log_fetch", False)
        tool_args["fetch_tool_model"] = (
            execute_config.config.get("llm_config", {}).get("general", {}).get("model_name", None)
        )
    elif "search" in normalized_tool_name and "web_search" in available_tools:
        tool_matched = True
        tool_args["log_search"] = execute_config.config.get("log_search", True)
    elif "retrieve" in normalized_tool_name and "retrieve" in available_tools:
        tool_matched = True
        tool_args["top_k"] = execute_config.retrieval_settings.get("top_k", 5)
        tool_args["add_instruction"] = execute_config.retrieval_settings.get("add_instruction", True)
        tool_args["mode"] = execute_config.retrieval_settings.get("mode", "dense")
        tool_args["top_k_multiply_factor"] = execute_config.retrieval_settings.get("top_k_multiply_factor", 10)

    if not tool_matched:
        friendly_tool_names = []
        if "web_search" in available_tools:
            friendly_tool_names.append("search")
        if "web_fetch" in available_tools:
            friendly_tool_names.append("fetch")
        if "retrieve" in available_tools:
            friendly_tool_names.append("retrieve")

        available_tools_str = ", ".join(friendly_tool_names)
        raise CustomValueException(
            StatusCode.TOOL_EXEC_ERROR.code,
            StatusCode.TOOL_EXEC_ERROR.errmsg.format(
                e=f"Tool '{execute_config.tool_name}' is not supported. Available tools: {available_tools_str}."
            ),
        )

    try:
        tool_result = await _call_custom_tool(execute_config.tool_map, execute_config.tool_name, tool_args)
    except Exception as e:
        raise CustomValueException(
            StatusCode.TOOL_EXEC_ERROR.code,
            StatusCode.TOOL_EXEC_ERROR.errmsg.format(e=e),
        ) from e

    if "fetch" in normalized_tool_name:
        if "url" not in tool_args or "goal" not in tool_args:
            logger.error(
                "[tool_node] fetch tool_args missing expected keys. "
                "Present keys: %s. tool_args: %s. tool_result: %s",
                list(tool_args.keys()),
                tool_args,
                tool_result,
            )
        execute_config.new_found_evidence_ids.append(
            {
                "url": tool_args.get("url"),
                "goal": tool_args.get("goal"),
            }
        )

    if "retrieve" in normalized_tool_name:
        results, id_list = tool_result
        action_state = execute_config.action.get("state", {}) or {}
        existing_ids = set(action_state.get("retrieved_evidence_ids", []))
        for id_ in id_list:
            if id_ not in execute_config.new_found_evidence_ids and id_ not in existing_ids:
                execute_config.new_found_evidence_ids.append(id_)
        return results, execute_config.new_found_evidence_ids

    return tool_result, execute_config.new_found_evidence_ids
