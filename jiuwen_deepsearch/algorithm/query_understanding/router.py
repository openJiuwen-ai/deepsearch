# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging

from openjiuwen.core.utils.tool.function.function import LocalFunction
from openjiuwen.core.utils.tool.param import Param
from jiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from jiuwen_deepsearch.utils.constants_utils.runtime_contextvars import llm_context
from jiuwen_deepsearch.utils.common_utils import llm_utils
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from jiuwen_deepsearch.common.status_code import StatusCode

logger = logging.getLogger(__name__)


def _create_function_tool():
    send_to_planner = LocalFunction(
        name="send_to_planner",
        description="send_to_planner",
        params=[
            Param(name="query_title",
                  description="The title of the query to be handed off.",
                  param_type="string",
                  required=True),
            Param(name="language",
                  description="The user's detected language locale.",
                  param_type="string",
                  required=True)
        ],
        func=None
    )
    return send_to_planner


async def classify_query(inputs: dict) -> (bool, str):
    """
        Query routing: Determine whether to enter the deep (re)search process.

        Args:
        context: Current agent context
        config: Current runtime configuration

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
                                                          tools=[_create_function_tool().get_tool_info()],
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
