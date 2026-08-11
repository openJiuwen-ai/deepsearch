# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import json
import logging
import re
from typing import Any, Dict, Tuple

from openjiuwen_deepsearch.algorithm.search_agent.deepsearch_agent import unwrap_workflow_result_payload
from openjiuwen_deepsearch.algorithm.search_nodes.llm_utils import RunLLMConfig, run_llm
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import State
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

QUESTION_CLUES_REF_PATTERN = r"\[([0-9]+_[a-z0-9_]+)\]"


def parse_and_validate_init_state(content: str):
    try:
        content = re.sub(
            r"<(think|reasoning|thinking)\b[^>]*>.*?</\1>",
            "",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        init_state_dict = unwrap_workflow_result_payload(json.loads(content), "answer_variable")
        if "variables" in init_state_dict and "state" not in init_state_dict:
            init_state_dict["state"] = init_state_dict.pop("variables")
        init_state = State(**init_state_dict)
    except Exception as e:
        raise CustomValueException(
            StatusCode.INIT_STATE_FAILED.code,
            StatusCode.INIT_STATE_FAILED.errmsg.format(e=e),
        ) from e

    if not init_state:
        raise CustomValueException(
            StatusCode.INIT_STATE_FAILED.code,
            StatusCode.INIT_STATE_FAILED.errmsg.format(e="invalid or empty state"),
        )

    init_state.id = "0"
    init_state.depth = 0

    return init_state


async def run_initialize_state(
    llm_config: dict,
    query: str,
    *,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
) -> Dict[str, Any]:
    context_vars = {
        "messages": [
            {
                "role": "user",
                "content": ("Please apply the above instructions to the following query:\n" f"{query}"),
            }
        ]
    }
    llm_result, _, input_tokens, output_tokens = await run_llm(
        RunLLMConfig(
            config=llm_config,
            prompt_template_file="deepsearch_initialize_state",
            context_vars=context_vars,
            need_stream_out=False,
            agent_name=AgentLlmName.INITIAL_STATE.value,
        )
    )
    total_input_tokens = total_input_tokens + input_tokens
    total_output_tokens = total_output_tokens + output_tokens
    content = llm_result.get("content") if isinstance(llm_result, dict) else llm_result
    init_state = parse_and_validate_init_state(content)
    logger.info(
        "[initialize_state] parsed State: %s",
        "***" if LogManager.is_sensitive() else init_state,
    )
    messages = context_vars["messages"] + [{"role": "assistant", "content": content}]
    return dict(
        init_state=init_state,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        success=True,
        messages=messages,
    )
