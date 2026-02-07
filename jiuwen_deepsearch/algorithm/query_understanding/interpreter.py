# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging

from jiuwen_deepsearch.utils.constants_utils.runtime_contextvars import llm_context
from jiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from jiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.common.status_code import StatusCode

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
    prompt = apply_system_prompt("generate_questions", current_inputs)
    try:
        llm = llm_context.get().get(current_inputs.get("llm_model_name"))
        response = await ainvoke_llm_with_stats(llm, prompt, llm_type="basic",
                                                agent_name=NodeId.GENERATE_QUESTIONS.value, need_stream_out=True)
        if not LogManager.is_sensitive():
            logger.debug("[query_interpreter] algorithm output: %s.", response.get("content"))
        else:
            logger.debug("[query_interpreter] get algorithm output.")
        return dict(result=response.get("content"))
    except Exception as e:
        err_msg = (f"[{StatusCode.INTERPRETATION_GENERATE_ERROR.code}]"
                   f"{StatusCode.INTERPRETATION_GENERATE_ERROR.errmsg}: {e}")
        if LogManager.is_sensitive():
            logger.error(f"[{StatusCode.INTERPRETATION_GENERATE_ERROR.code}]"
                         f"{StatusCode.INTERPRETATION_GENERATE_ERROR.errmsg}")
        else:
            logger.error(err_msg)
        return dict(exception_info=err_msg)
