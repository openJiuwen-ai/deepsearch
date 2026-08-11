# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import copy
import logging
from typing import Any, Dict

from openjiuwen.core.runner.runner import Runner

from openjiuwen_deepsearch.algorithm.search_nodes.utils import anonymize_config_for_logging, to_dict_safe
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Result, Action

logger = logging.getLogger(__name__)


def unwrap_workflow_result_payload(result: Any, answer_key: str) -> Any:
    current = result
    for _ in range(8):
        if hasattr(current, "result") and not isinstance(current, dict):
            current = current.result
            continue
        break

    for _ in range(8):
        if not isinstance(current, dict):
            return current
        while answer_key in current:
            post = current[answer_key]
            if post is not None and isinstance(post, dict) and answer_key in post:
                current = post
            else:
                return current
        next_payload = None
        for key in ("final_result", "result", "data", "payload"):
            nested = current.get(key)
            if isinstance(nested, dict):
                next_payload = nested
                break
        if next_payload is None:
            return current
        current = next_payload
    return current


def parse_and_validate_init_state_result(init_result: Any) -> Dict[str, Any]:
    result = unwrap_workflow_result_payload(init_result, "init_state")
    if result is None:
        raise CustomValueException(
            StatusCode.AGENT_INIT_STATE_ERROR.code,
            StatusCode.AGENT_INIT_STATE_ERROR.errmsg,
        )
    if not isinstance(result, dict):
        raise CustomValueException(
            StatusCode.AGENT_INIT_STATE_ERROR.code,
            StatusCode.AGENT_INIT_STATE_ERROR.errmsg,
        )
    if "init_state" not in result:
        raise CustomValueException(
            StatusCode.AGENT_INIT_STATE_ERROR.code,
            StatusCode.AGENT_INIT_STATE_ERROR.errmsg,
        )
    return result


def parse_and_validate_find_action_result(actions_result: Any) -> Dict[str, Any]:
    result = unwrap_workflow_result_payload(actions_result, "actions")
    if result is None:
        raise CustomValueException(
            StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.code,
            StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.errmsg.format(e="result is None"),
        )
    if not isinstance(result, dict):
        raise CustomValueException(
            StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.code,
            StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.errmsg.format(e="result is not a dict"),
        )
    if "actions" not in result:
        raise CustomValueException(
            StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.code,
            StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.errmsg.format(e="missing 'actions' key"),
        )
    actions = result.get("actions", [])
    if not isinstance(actions, list):
        raise CustomValueException(
            StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.code,
            StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.errmsg.format(e="'actions' is not a list"),
        )
    for action in actions:
        if not isinstance(action, Action):
            raise CustomValueException(
                StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.code,
                StatusCode.AGENT_FIND_ACTION_RESULT_ERROR.errmsg.format(e="action in action pool must be an Action"),
            )
    return result


def parse_and_validate_state_creation_result(states: Any) -> Dict[str, Any]:
    result = unwrap_workflow_result_payload(states, "result")
    if result is None or not isinstance(result, dict):
        return {
            "result": None,
            "config": {},
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
    try:
        action_result = Result.model_validate(result)
    except Exception:
        action_result = result.get("result")

    return {
        "result": action_result,
        "config": anonymize_config_for_logging(copy.deepcopy(result.get("config", {}))),
        "total_input_tokens": result.get("total_input_tokens", 0),
        "total_output_tokens": result.get("total_output_tokens", 0),
    }
