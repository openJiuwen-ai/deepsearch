import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Union

from pydantic import BaseModel

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Action,
    Result,
    SearchFinalResult,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def format_action_for_log(action: Any) -> str:
    """One-line ``action_id`` and proposal direction for logs (honors sensitive mode)."""
    ad = to_dict_safe(action) if action is not None else {}
    if LogManager.is_sensitive():
        return "action_id=*** direction=***"
    aid = ad.get("id", "")
    prop = ad.get("proposal") or {}
    direction = prop.get("direction", "") if isinstance(prop, dict) else ""
    if isinstance(direction, str) and len(direction) > 120:
        direction = direction[:117] + "..."
    return "action_id=%s direction=%s" % (aid, direction or "")


def to_dict_safe(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        return obj.model_dump()
    return obj


def to_json_safe(obj):
    try:
        if isinstance(obj, BaseModel):
            return to_json_safe(obj.model_dump())

        if isinstance(obj, Enum):
            return obj.value

        if isinstance(obj, dict):
            return {k: to_json_safe(v) for k, v in obj.items()}

        if isinstance(obj, (list, tuple, set)):
            return [to_json_safe(v) for v in obj]

        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj

        if isinstance(obj, (bytes, bytearray)):
            return "***"

        return str(obj)

    except RecursionError:
        return "<recursion limit exceeded>"


def ensure_api_keys_bytearray(agent_config: dict) -> dict:
    def to_ba(v):
        return bytearray(v, encoding="utf-8") if isinstance(v, str) else v

    def convert_api_keys_recursive(d: dict) -> None:
        for k, v in d.items():
            if isinstance(v, dict):
                convert_api_keys_recursive(v)
            elif k == "api_key" and isinstance(v, str):
                d[k] = to_ba(v)

    if not agent_config:
        return {}
    if "llm_config" in agent_config and isinstance(agent_config["llm_config"], dict):
        k = agent_config["llm_config"].get("api_key")
        if k is not None:
            agent_config["llm_config"]["api_key"] = to_ba(k)
    for key in ("jina_api_key", "serper_api_key", "embedder_api_key"):
        if key in agent_config and agent_config.get(key) is not None:
            agent_config[key] = to_ba(agent_config[key])
        search_workflow_milvus_config = agent_config.get("search_workflow_milvus_config", {})
        if search_workflow_milvus_config and search_workflow_milvus_config.get(key) is not None:
            search_workflow_milvus_config[key] = to_ba(search_workflow_milvus_config[key])

    swc = agent_config.get("search_workflow")
    if isinstance(swc, dict):
        convert_api_keys_recursive(swc)

    return agent_config


def strip_quotes(s: str) -> str:
    """Remove optional leading/trailing quote characters (from config/env)."""
    if not s:
        return ""
    s = s.strip()
    for q in ('"', "'"):
        if len(s) >= 2 and s[0] == q and s[-1] == q:
            return s[1:-1].strip()
    return s


def coerce_api_keys_in_dict(d: dict) -> None:
    """Recursively convert string api_key values to bytearray in a nested dict."""
    for k, v in d.items():
        if isinstance(v, dict):
            coerce_api_keys_in_dict(v)
        elif "api_key" in k and isinstance(v, str):
            d[k] = bytearray(v, encoding="utf-8")


def expand_env_vars(text: str) -> str:
    """Replace ${VAR} or $VAR patterns with env var values."""

    def _replacer(m):
        var = m.group(1) or m.group(2)
        return os.environ.get(var, m.group(0))

    return re.sub(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z_0-9]*)", _replacer, text)


def load_search_config(path: str) -> dict:
    """Load a JSON search config file, expanding ${ENV_VAR} references."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    return json.loads(expand_env_vars(raw))


def _save_result(
    config: dict,
    action: Action | dict,
    result_to_save: Result | dict,
    time_taken: float,
) -> dict:
    id_ = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S%f")[:-3]
    action = to_dict_safe(action)
    saved_from_error_dict = False
    if isinstance(result_to_save, dict):
        if "Early termination" in result_to_save["termination"]:
            return config
        result_file_name = f"error_result_{id_}_{uuid.uuid4().hex}.json"
        config["fail_count"] += 1
        result_to_save["messages"].append({"role": "user", "content": result_to_save["termination"]})
        saved_from_error_dict = True
        result_to_save = Result(
            messages=result_to_save["messages"],
            new_states=[],
            found_answer=None,
            previous_action_id=action.get("id", ""),
        )
    else:
        result_file_name = (
            f"answer_result_{id_}_{uuid.uuid4().hex}.json"
            if result_to_save.found_answer
            else f"result_{id_}_{uuid.uuid4().hex}.json"
        )

    if config["log_dir"]:
        result_file = os.path.join(config["log_dir"], "Result", result_file_name)

        payload = {
            "previous_state": action["state"],
            "previous_action": action["proposal"]["direction"],
            "result": result_to_save,
            "time_taken": time_taken,
        }

        safe_payload = to_json_safe(payload)

        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(safe_payload, f, indent=2, ensure_ascii=False)
        _action_execution_result = (
            "answer" if result_to_save.found_answer else "error" if saved_from_error_dict else "new_state"
        )
        result_abs = os.path.abspath(result_file)
        aid = action.get("id")
        adir = (action.get("proposal") or {}).get("direction", "")
        if not LogManager.is_sensitive() and isinstance(adir, str) and len(adir) > 120:
            adir = adir[:117] + "..."
        log_msg = "[_save_result] action_id=%s action_execution_result=%s result_file=%s " "action_direction=%s" % (
            aid,
            _action_execution_result,
            result_abs,
            "***" if LogManager.is_sensitive() else (adir or ""),
        )
        if _action_execution_result == "error":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)
    return config


class Termination(Enum):
    ANSWER = ("answer", "Found final answer")
    TIME_LIMIT = ("time_limit", "Time limit exceeded")
    TIMEOUT_ANSWER = ("timeout_answer", "Time limit exceeded but returning best collected answer")
    TIMEOUT_GUESS = ("timeout_guess", "Time limit exceeded; returning best-guess candidate from completed actions")
    ACTIONS_EXPLORED_LIMIT = ("actions_explored_limit", "Actions explored limit reached")
    FAIL_LIMIT = ("fail_limit", "Fail limit reached")
    ACTION_POOL_DEPLETED = ("action_pool_depleted", "Action pool depleted and max retries exceeded")

    def __init__(self, key: str, log_message: str) -> None:
        self.key: str = key
        self.log_message: str = log_message

    def __str__(self) -> str:
        return self.key


@dataclass
class SaveSearchFinalResultConfig:
    question: str
    termination: Termination
    messages: List[Dict] | None = None
    prediction: str | None = None
    gold_answer: str | None = None
    retrieved_evidence_ids: List[str] | None = None
    params: dict | None = None
    config: dict | None = None


def _save_and_return_search_final_result(
    save_config: SaveSearchFinalResultConfig,
) -> SearchFinalResult:
    params = save_config.params or {}
    config = save_config.config or {}
    retrieved_evidence_ids = save_config.retrieved_evidence_ids or []
    completion_time = time.time() - params.get("start_time", 0)

    final_result = SearchFinalResult(
        question=save_config.question,
        messages=save_config.messages,
        current_date_time=datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S%f")[:-3],
        prediction=save_config.prediction,
        gold_answer=save_config.gold_answer,
        termination=str(save_config.termination),
        completion_time=completion_time,
        config=config,
        retrieved_evidence_ids=retrieved_evidence_ids,
    )
    if params.get("log_dir"):
        with open(
            os.path.join(params.get("log_dir"), "final_result.json"),
            "w",
            encoding="utf-8",
        ) as f:
            result_dict = final_result.model_dump()

            json.dump(to_json_safe(result_dict), f, indent=2, ensure_ascii=False)
    return final_result
