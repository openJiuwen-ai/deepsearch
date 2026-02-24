# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils import llm_utils
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


async def _dummy_func(**kwargs):
    return kwargs


def _create_function_tool():
    card = ToolCard(
        id="send_to_planner",
        name="send_to_planner",
        description="send_to_planner",
        input_params={
            "type": "object",
            "properties": {
                "query_title": {
                    "type": "string",
                    "description": "The title of the query to be handed off."
                },
                "language": {
                    "type": "string",
                    "description": "The user's detected language locale."
                }
            },
            "required": ["query_title", "language"]
        }
    )
    send_to_planner = LocalFunction(
        card=card,
        func=_dummy_func
    )
    return send_to_planner


async def classify_query(inputs: dict) -> (bool, str):
    """
        Query routing: Determine whether to enter the deep (re)search process.

        Args:
        context: Current agent context
        config: Current session configuration

        Returns:
            bool: whether to enter the deep (re)search process.
            str: language locale.
    """
    logger.info(f"[classify_query] Begin query classification operation.")

    prompts = apply_system_prompt("entry", inputs)

    error_msg = ""
    try:
        llm = llm_context.get().get(inputs.get("llm_model_name"))
        response = await llm_utils.ainvoke_llm_with_stats(llm,
                                                          prompts,
                                                          llm_type="basic",
                                                          agent_name=NodeId.ENTRY.value,
                                                          tools=[_create_function_tool().card.tool_info()],
                                                          need_stream_out=False)
        tool_calls = response.get('tool_calls', [])

    except Exception as e:
        error_msg = f"[{StatusCode.ENTRY_GENERATE_ERROR.code}]{StatusCode.ENTRY_GENERATE_ERROR.errmsg}: {e}"
        response = {}
        tool_calls = None
        logger.error(error_msg)

    if tool_calls:
        if not LogManager.is_sensitive():
            logger.debug("[classify_query] algorithm tool_calls output: %s.", tool_calls)
        else:
            logger.debug("[classify_query] get algorithm tool_calls output.")
        return {
            "go_deepsearch": True,
            "lang": response.get('tool_calls', [])[0].get("args", {}).get("language", "zh-CN"),
            "llm_result": "",
            "error_msg": error_msg
        }

    if not LogManager.is_sensitive():
        logger.debug("[classify_query] algorithm content output: %s.", response.get("content", ""))
    else:
        logger.debug("[classify_query] get algorithm content output.")
    return {
        "go_deepsearch": False,
        "lang": "zh-CN",
        "llm_result": response.get("content", ""),
        "error_msg": error_msg
    }
