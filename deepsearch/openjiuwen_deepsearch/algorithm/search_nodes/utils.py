import copy
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Action,
    Result,
    SearchFinalResult,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.run_telemetry import emit, runtime_correlation_from

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


_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-goog-api-key",
    }
)


def _is_sensitive_config_key(name: str) -> bool:
    lk = name.lower()
    if lk in (
        "api_key",
        "apikey",
        "token",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "consumer_secret",
        "private_key",
        "authorization",
    ):
        return True
    if lk.endswith("_api_key") or lk.endswith("_apikey"):
        return True
    if lk.endswith("_token") and not lk.endswith("_tokens"):
        return True
    if "api_key" in lk:
        return True
    return False


def anonymize_config_for_logging(obj: Any) -> Any:
    """Deep-copy ``obj`` and replace credential-like values for logs / persisted SearchFinalResult."""
    if obj is None:
        return None
    if isinstance(obj, (bytes, bytearray)):
        return "***"
    if isinstance(obj, dict):
        name_raw = obj.get("name")
        if isinstance(name_raw, str) and name_raw.lower() in _SENSITIVE_HEADER_NAMES and "value" in obj:
            out: Dict[str, Any] = {}
            for k, v in obj.items():
                if k == "value":
                    out[k] = "***"
                else:
                    out[k] = anonymize_config_for_logging(v)
            return out
        out = {}
        for k, v in obj.items():
            ks = k if isinstance(k, str) else str(k)
            if _is_sensitive_config_key(ks):
                out[ks] = "***"
            else:
                out[ks] = anonymize_config_for_logging(v)
        return out
    if isinstance(obj, (list, tuple)):
        seq = [anonymize_config_for_logging(x) for x in obj]
        return type(obj)(seq) if isinstance(obj, tuple) else seq
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
    for key in ("embedder_api_key",):
        if key in agent_config and agent_config.get(key) is not None:
            agent_config[key] = to_ba(agent_config[key])
        search_workflow_milvus_config = agent_config.get("search_workflow_milvus_config", {})
        if search_workflow_milvus_config and search_workflow_milvus_config.get(key) is not None:
            search_workflow_milvus_config[key] = to_ba(search_workflow_milvus_config[key])

    web_fetch_provider_config = agent_config.get("web_fetch_provider_config")
    if isinstance(web_fetch_provider_config, dict):
        convert_api_keys_recursive(web_fetch_provider_config)

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
    runtime: Any = None,
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
        num_new_states = 0
        new_state_ids: List[str] = []
        if not saved_from_error_dict:
            ns = getattr(result_to_save, "new_states", None) or []
            num_new_states = len(ns)
            for s in ns:
                if hasattr(s, "id"):
                    new_state_ids.append(str(s.id))
                elif isinstance(s, dict):
                    new_state_ids.append(str(s.get("id", "")))
        has_answer = bool(
            (not saved_from_error_dict)
            and getattr(result_to_save, "found_answer", None)
        )
        if saved_from_error_dict:
            result_outcome: str = "fail"
        elif has_answer:
            result_outcome = "answer"
        elif num_new_states > 0:
            result_outcome = "new_states"
        else:
            result_outcome = "empty_patch"
        answer_preview: Optional[str] = None
        if has_answer and not LogManager.is_sensitive():
            fa = getattr(result_to_save, "found_answer", None)
            if isinstance(fa, str):
                answer_preview = fa[:500] + ("…" if len(fa) > 500 else "")
            elif fa is not None:
                answer_preview = str(fa)[:500]
        emit_payload: Dict[str, Any] = {
            "result_file": result_file_name,
            "action_execution_result": _action_execution_result,
            "result_outcome": result_outcome,
            "num_new_states": num_new_states,
            "new_state_ids": new_state_ids,
            "has_answer": has_answer,
            "saved_from_error": saved_from_error_dict,
            **runtime_correlation_from(runtime),
        }
        if answer_preview is not None:
            emit_payload["answer_preview"] = answer_preview
        emit(
            "action_result_saved",
            emit_payload,
            source="search_nodes._save_result",
            action_id=action.get("id"),
        )
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
    raw_config = save_config.config or {}
    config = anonymize_config_for_logging(copy.deepcopy(raw_config))
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
    emit(
        "search_final_result",
        {
            "termination": str(save_config.termination),
            "completion_time_sec": completion_time,
            "has_prediction": save_config.prediction is not None,
        },
        source="search_nodes.search_final_result",
        action_id=None,
    )
    return final_result
