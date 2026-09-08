# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.status_code import StatusCode, format_exception_info
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


async def query_interpreter(current_inputs: dict) -> dict:
    """
        Generate questions for user input for deep query interpretation

        Args:
            current_inputs: dict includes language and query

        Returns:
            str: generated questions
    """
    logger.info(f"Begin query interpretation operation.")
    logger.info(
        "[query_interpreter] input: query=%s language=%s report_type=%s entry_search_results_count=%d",
        "**" if LogManager.is_sensitive() else current_inputs.get("query"),
        current_inputs.get("language"),
        current_inputs.get("report_type"),
        len(current_inputs.get("entry_search_results") or []),
    )
    prompt = apply_system_prompt("generate_questions", current_inputs)
    try:
        llm = llm_context.get().get(current_inputs.get("llm_model_name"))
        response = await ainvoke_llm_with_stats(llm, prompt, llm_type="basic",
                                                agent_name=AgentLlmName.GENERATE_QUESTIONS.value, need_stream_out=True)
        if not LogManager.is_sensitive():
            logger.info("[query_interpreter] output: %s", response.get("content"))
        else:
            logger.info("[query_interpreter] got output (redacted).")
        return dict(result=response.get("content"))
    except Exception as e:
        err_msg = format_exception_info(StatusCode.INTERPRETATION_GENERATE_ERROR, e)
        if LogManager.is_sensitive():
            logger.error(f"[{StatusCode.INTERPRETATION_GENERATE_ERROR.code}]"
                         f"{StatusCode.INTERPRETATION_GENERATE_ERROR.errmsg}")
        else:
            logger.error(err_msg)
        return dict(exception_info=err_msg)
