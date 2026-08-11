# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import logging
import re
import secrets
from typing import Any, Dict, List, Tuple

import json_repair

from openjiuwen_deepsearch.algorithm.search_agent.deepsearch_agent import unwrap_workflow_result_payload
from openjiuwen_deepsearch.algorithm.search_nodes.llm_utils import RunLLMConfig, run_llm
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Action,
    ActionProposal,
    Result,
    State,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def parse_and_build_actions(messages: list, state: State, query: str) -> List[Action]:

    llm_content = re.sub(r"<think>.*?</think>", "", messages[-1]["content"], flags=re.DOTALL).strip()
    try:
        data = unwrap_workflow_result_payload(json_repair.loads(llm_content), "action_proposals")
    except Exception as e:
        raise CustomValueException(
            StatusCode.FIND_ACTION_PARSE_ERROR.code,
            StatusCode.FIND_ACTION_PARSE_ERROR.errmsg.format(e=e),
        ) from e

    if not isinstance(data, dict):
        raise CustomValueException(
            StatusCode.FIND_ACTION_PARSE_ERROR.code,
            StatusCode.FIND_ACTION_PARSE_ERROR.errmsg.format(e="response is not a JSON object"),
        )

    actions_data = data.get("action_proposals", [])
    if not isinstance(actions_data, list):
        raise CustomValueException(
            StatusCode.FIND_ACTION_PARSE_ERROR.code,
            StatusCode.FIND_ACTION_PARSE_ERROR.errmsg.format(e="action_proposals is not a list"),
        )

    actions: List[Action] = []
    for i, item in enumerate(actions_data):
        try:
            if not isinstance(item, dict):
                raise TypeError(f"item at index {i} is not a dict")

            if "score" not in item:
                direction_text = str(item.get("direction", ""))
                score_match = re.search(r"[Ss]core[:\s]+([0-9.]+)", direction_text)
                if score_match:
                    try:
                        extracted_score = float(score_match.group(1))
                        item["score"] = max(0.0, min(1.0, extracted_score))
                    except (ValueError, AttributeError) as e:
                        raise ValueError(f"score at index {i} is not a float") from e
                else:
                    raise ValueError(f"score at index {i} is not found")

            proposal = ActionProposal(**item)
            actions.append(
                Action(
                    state=state,
                    proposal=proposal,
                    question=query,
                    id=f"{state.id}_{secrets.token_hex(3)}",
                    messages=messages,
                )
            )
        except Exception as e:
            raise CustomValueException(
                StatusCode.FIND_ACTION_PARSE_ERROR.code,
                StatusCode.FIND_ACTION_PARSE_ERROR.errmsg.format(e=f"action_proposals[{i}]: {e}"),
            ) from e

    return actions


async def run_find_action_space(
    llm_config: dict,
    config: dict,
    query: str,
    state: State,
    result: Result | None,
    *,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    max_tries: int = 4,
) -> Dict[str, Any]:
    context_vars = {
        "action_proposals_limit": config.get("action_proposals_limit", 5),
        "messages": [
            {
                "role": "user",
                "content": (
                    "Please apply the above instructions to the following query and state and result:\n"
                    f"Query: {query}\n"
                    f"State: {state}\n"
                    f"Result: {result.get_summary() if result else 'No result since empty state'}"
                ),
            }
        ],
    }
    algorithm_output = None
    while max_tries > 0:
        content = None
        try:
            llm_result, _, input_tokens, output_tokens = await run_llm(
                RunLLMConfig(
                    config=llm_config,
                    prompt_template_file="deepsearch_find_action_space",
                    context_vars=context_vars,
                    need_stream_out=False,
                    agent_name=AgentLlmName.FIND_ACTION_SPACE.value,
                )
            )
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            if llm_result is None:
                raise CustomValueException(
                    StatusCode.FIND_ACTION_PARSE_ERROR.code,
                    StatusCode.FIND_ACTION_PARSE_ERROR.errmsg.format(e="LLM returned None"),
                )
            content = llm_result.get("content") if isinstance(llm_result, dict) else llm_result
            messages = context_vars["messages"] + [{"role": "assistant", "content": content}]
            actions = parse_and_build_actions(messages, state, query)
            if not actions:
                logger.warning(
                    "[find_action_space] LLM returned empty action_proposals. LLM content: %s",
                    "***" if LogManager.is_sensitive() else content,
                )
                raise CustomValueException(
                    StatusCode.FIND_ACTION_PARSE_ERROR.code,
                    StatusCode.FIND_ACTION_PARSE_ERROR.errmsg.format(e="action_proposals is empty"),
                )
            logger.info(
                "[find_action_space] parsed ActionProposal: %s",
                "***" if LogManager.is_sensitive() else actions,
            )
            algorithm_output = dict(
                actions=actions,
                messages=list(messages),
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                success=True,
            )
            break
        except Exception as e:
            logger.warning(
                "Failed to find action: %s, retries left=%s",
                "*" if LogManager.is_sensitive() else e,
                max_tries - 1,
            )
            max_tries -= 1
            if max_tries == 0:
                logger.error("All retries exhausted, returning empty actions.")
                fail_messages: List[Dict[str, Any]] = list(
                    context_vars.get("messages") or []
                )
                if content is not None:
                    fail_messages = fail_messages + [
                        {"role": "assistant", "content": content}
                    ]
                algorithm_output = dict(
                    actions=[],
                    messages=fail_messages,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    success=False,
                    error=str(e),
                )
                break
            if content:
                context_vars["messages"].append({"role": "assistant", "content": content})
                context_vars["messages"].append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response failed to parse. Error: {e}\n"
                            "Please fix the issues and try again. "
                            "Output ONLY valid JSON matching the required schema."
                        ),
                    }
                )
            await asyncio.sleep(1)
    return algorithm_output
