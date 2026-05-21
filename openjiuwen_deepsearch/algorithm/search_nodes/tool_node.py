# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

from openjiuwen_deepsearch.algorithm.search_nodes.run_action import resolve_native_tool_call_name
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


async def _invoke_registered_tool(tool_map: dict, canonical_name: str, tool_args: dict):
    if canonical_name not in tool_map:
        raise CustomValueException(
            StatusCode.LOAD_EXTEND_TOOLS_FAILED.code,
            StatusCode.LOAD_EXTEND_TOOLS_FAILED.errmsg.format(tool_name=canonical_name),
        )

    tool = tool_map[canonical_name]

    if hasattr(tool, "acall"):
        return await tool.acall(tool_args)
    return await asyncio.to_thread(tool.call, tool_args)


async def execute_tool(execute_config: ExecuteToolConfig) -> Tuple[Any, List[Any]]:
    retrieval_only = "retrieve" in execute_config.tool_map and "web_search" not in execute_config.tool_map
    canonical = resolve_native_tool_call_name(execute_config.tool_name, retrieval_only)
    if not canonical or canonical not in execute_config.tool_map:
        allowed = ", ".join(sorted(execute_config.tool_map.keys()))
        raise CustomValueException(
            StatusCode.TOOL_EXEC_ERROR.code,
            StatusCode.TOOL_EXEC_ERROR.errmsg.format(
                e=f"Tool '{execute_config.tool_name}' is not allowed. Allowed tools: {allowed}."
            ),
        )

    tool_args = dict(execute_config.tool_args)

    if canonical == "web_fetch":
        tool_args["log_fetch"] = execute_config.config.get("log_fetch", False)
        tool_args["fetch_tool_model"] = (
            execute_config.config.get("llm_config", {}).get("general", {}).get("model_name", None)
        )
    elif canonical == "web_search":
        tool_args["log_search"] = execute_config.config.get("log_search", True)
    elif canonical == "retrieve":
        tool_args["top_k"] = execute_config.retrieval_settings.get("top_k", 5)
        tool_args["add_instruction"] = execute_config.retrieval_settings.get("add_instruction", True)
        tool_args["mode"] = execute_config.retrieval_settings.get("mode", "dense")
        tool_args["top_k_multiply_factor"] = execute_config.retrieval_settings.get("top_k_multiply_factor", 10)

    try:
        tool_result = await _invoke_registered_tool(execute_config.tool_map, canonical, tool_args)
    except Exception as e:
        raise CustomValueException(
            StatusCode.TOOL_EXEC_ERROR.code,
            StatusCode.TOOL_EXEC_ERROR.errmsg.format(e=e),
        ) from e

    if canonical == "web_fetch":
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

    if canonical == "retrieve":
        results, id_list = tool_result
        action_state = execute_config.action.get("state", {}) or {}
        existing_ids = set(action_state.get("retrieved_evidence_ids", []))
        for id_ in id_list:
            if id_ not in execute_config.new_found_evidence_ids and id_ not in existing_ids:
                execute_config.new_found_evidence_ids.append(id_)
        return results, execute_config.new_found_evidence_ids

    return tool_result, execute_config.new_found_evidence_ids
