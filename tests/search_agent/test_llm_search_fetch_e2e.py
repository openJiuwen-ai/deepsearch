"""Real LLM + search_fetch smoke (optional; requires env and RUN_LLM_TESTS=1).

Optional: set ``LLM_E2E_LOG_DIR`` to a directory path; logs are written under
``<that_dir>/<profile>/`` (e.g. ``gpt_mini_stack``). Otherwise logs use pytest's ``tmp_path``.

For ``small_qwen``, optional ``LLM_E2E_SMALL_QWEN_MODEL`` overrides the default OpenRouter model id
(still OpenRouter ``qwen/...`` style).
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.search_nodes.utils import coerce_api_keys_in_dict, load_search_config
from openjiuwen_deepsearch.config.config import AgentConfig, Config, SearchWorkflowConfig, ServiceConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

pytestmark = pytest.mark.llm

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LLM_E2E_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "llm_e2e"

_LLM_SLOT_KEYS = frozenset({"general", "plan_understanding", "info_collecting", "writing_checking"})
# Matches benchmarking/qwen_config.json shape; slug must be OpenRouter-valid (hyphenated 2.5 path).
_DEFAULT_SMALL_QWEN = "qwen/qwen3.5-35b-a3b"
_QWEN_THINK_EXTRA_BODY = {"enable_thinking": True}

# E2E prompt (search_fetch); keep in sync with assertions / docs.
_LLM_E2E_QUESTION = (
    "What Canadian actor starred in a film released on June 14th, 2011?"
)


def _resolve_llm_e2e_log_dir(tmp_path: Path, profile: str) -> Path:
    """Log root for LLM e2e: tmp by default, or LLM_E2E_LOG_DIR/<profile> if set."""
    override = os.getenv("LLM_E2E_LOG_DIR", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        safe = root / profile
        safe.mkdir(parents=True, exist_ok=True)
        return safe
    safe = tmp_path / "logs"
    safe.mkdir(parents=True, exist_ok=True)
    return safe


def _require_llm_env() -> None:
    if os.getenv("RUN_LLM_TESTS", "").strip() != "1":
        pytest.skip("Set RUN_LLM_TESTS=1 to run llm-marked tests")
    missing = [k for k in ("OPENROUTER_API_KEY", "JINA_API_KEY", "SERPER_API_KEY") if not os.getenv(k, "").strip()]
    if missing:
        pytest.skip("Missing API keys for llm tests: " + ", ".join(missing))


def _to_ba(val) -> bytearray:
    if isinstance(val, bytearray):
        return val
    if val is None:
        return bytearray(b"")
    return bytearray(str(val), encoding="utf-8")


def _resolve_benchmark_json(json_name: str) -> Path:
    """Prefer tracked test fixtures, then repo ``benchmarking/`` (may be gitignored locally)."""
    cand = _LLM_E2E_FIXTURE_DIR / json_name
    if cand.is_file():
        return cand
    legacy = _REPO_ROOT / "benchmarking" / json_name
    if legacy.is_file():
        return legacy
    pytest.skip(
        "LLM e2e JSON not found: %s (tried %s and %s)"
        % (json_name, cand, legacy)
    )


def _merge_raw_llm_into_agent(agent: dict, raw_llm: dict) -> None:
    """Merge benchmark ``llm_config`` into ``AgentConfig``-shaped ``llm_config`` (nested by slot)."""
    if not raw_llm:
        return
    dest = agent.setdefault("llm_config", {})
    for slot in _LLM_SLOT_KEYS:
        if slot in raw_llm and isinstance(raw_llm[slot], dict):
            dest[slot] = {**(dest.get(slot) or {}), **raw_llm[slot]}
    for k, v in raw_llm.items():
        if k in _LLM_SLOT_KEYS:
            continue
        gen = dest.setdefault("general", {})
        if isinstance(gen, dict):
            gen[k] = v


def _set_sw_agent_llm_general(sw: dict, agent_key: str, updates: dict) -> None:
    block = dict(sw[agent_key])
    llm = dict(block.get("llm_config") or {})
    gen = dict(llm.get("general") or {})
    upd = dict(updates)
    if "extra_body" in upd:
        eb = upd.pop("extra_body") or {}
        gen["extra_body"] = {**dict(gen.get("extra_body") or {}), **dict(eb)}
    gen.update(upd)
    llm["general"] = gen
    block["llm_config"] = llm
    sw[agent_key] = block


def _build_configs(json_name: str, profile: str) -> tuple[dict, dict]:
    path = _resolve_benchmark_json(json_name)
    raw = load_search_config(str(path))
    cfg = Config()
    agent = cfg.agent_config.model_dump()
    service = cfg.service_config.model_dump()

    _merge_raw_llm_into_agent(agent, dict(raw.get("llm_config") or {}))

    sw = deepcopy(service["search_workflow"])
    for key in ("action_sampling", "init_state_agent", "state_creation_agent", "find_action_agent"):
        if key in raw:
            sw[key] = raw[key]
    coerce_api_keys_in_dict(sw)
    service["search_workflow"] = SearchWorkflowConfig.model_validate(sw).model_dump()

    pqp = dict(agent["search_workflow_per_question_params"])
    for k, v in raw.get("per_question_params", {}).items():
        pqp[k] = v
    pqp["tool_map"] = "search_fetch"
    pqp.update(
        {
            "actions_explored_limit": 24,
            "max_workers": 1,
            "time_limit": 300,
            "answer_mode_top_k": 1,
            "fail_limit": 2,
            "retry_count_on_empty_action_space": 1,
            "provide_best_guess": False,
        }
    )
    agent["search_workflow_per_question_params"] = pqp

    sf = raw.get("search_fetch_config", {})
    agent["jina_api_key"] = _to_ba(sf.get("jina_api_key"))
    agent["serper_api_key"] = _to_ba(sf.get("serper_api_key"))
    agent["web_fetch_provider_config"] = {
        "provider_name": "jina",
        "api_key": _to_ba(sf.get("jina_api_key")),
    }

    agent["search_mode"] = "search"

    if profile == "gpt_mini_stack":
        sw2 = deepcopy(service["search_workflow"])
        _set_sw_agent_llm_general(sw2, "init_state_agent", {"model_name": "gpt-5-mini"})
        service["search_workflow"] = SearchWorkflowConfig.model_validate(sw2).model_dump()
    elif profile == "small_qwen":
        # Align with benchmarking/qwen_config.json: OpenRouter + thinking flags; swap in a small model.
        small = (os.getenv("LLM_E2E_SMALL_QWEN_MODEL") or "").strip() or _DEFAULT_SMALL_QWEN
        g = agent.setdefault("llm_config", {}).setdefault("general", {})
        g["model_name"] = small
        g["model_type"] = "openai"
        g["base_url"] = "https://openrouter.ai/api/v1"
        g["extra_body"] = dict(_QWEN_THINK_EXTRA_BODY)
        g["append_think_tags_to_messages"] = True

        sw2 = deepcopy(service["search_workflow"])
        qwen_updates = {
            "model_name": small,
            "extra_body": {**_QWEN_THINK_EXTRA_BODY},
            "append_think_tags_to_messages": True,
        }
        _set_sw_agent_llm_general(sw2, "init_state_agent", qwen_updates)
        _set_sw_agent_llm_general(sw2, "state_creation_agent", qwen_updates)
        _set_sw_agent_llm_general(sw2, "find_action_agent", qwen_updates)

        service["search_workflow"] = SearchWorkflowConfig.model_validate(sw2).model_dump()
    else:
        raise AssertionError(profile)

    orch = os.getenv("OPENROUTER_API_KEY", "").strip()
    gen = agent.setdefault("llm_config", {}).setdefault("general", {})
    if orch:
        gen["api_key"] = _to_ba(orch)
        gen.setdefault("base_url", "https://openrouter.ai/api/v1")
        gen.setdefault("model_type", "openai")
    if profile == "gpt_mini_stack":
        gen["model_name"] = "gpt-5-mini"

    coerce_api_keys_in_dict(agent)
    validated_agent = AgentConfig.model_validate(agent).model_dump()
    validated_service = ServiceConfig.model_validate(service).model_dump()
    return validated_agent, validated_service


@pytest.mark.parametrize(
    "json_name,profile",
    [
        ("search_config_router_questions.json", "gpt_mini_stack"),
        ("qwen_config.json", "small_qwen"),
    ],
)
@pytest.mark.asyncio
async def test_real_search_fetch_simple_question(
    json_name: str, profile: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _require_llm_env()

    safe = _resolve_llm_e2e_log_dir(tmp_path, profile)
    monkeypatch.setattr(LogManager, "_SAFE_BASE", str(safe.resolve()))
    LogManager._initialized = False
    LogManager.init(
        log_dir=str(safe),
        level="INFO",
        max_bytes=10 * 1024 * 1024,
        backup_count=2,
        is_sensitive=False,
    )

    agent_cfg, service_cfg = _build_configs(json_name, profile)
    factory = AgentFactory()
    agent = factory.create_agent(agent_cfg)

    chunks: list[str] = []
    run_agent_cfg = deepcopy(agent_cfg)
    run_agent_cfg["service_config"] = service_cfg
    async for chunk in agent.run(
        message=_LLM_E2E_QUESTION,
        conversation_id=f"llm_{profile}",
        agent_config=run_agent_cfg,
        report_template="",
        interrupt_feedback="",
    ):
        chunks.append(chunk)

    assert chunks, "expected at least one streamed chunk"
    last = json.loads(chunks[-1])
    assert "termination" in last
    assert last.get("prediction") or last.get("termination") != ""

    run_dir = safe / f"result_llm_{profile}"
    final_path = run_dir / "final_result.json"
    if final_path.is_file():
        disk = json.loads(final_path.read_text(encoding="utf-8"))
        assert disk.get("question")
        assert disk.get("termination")
