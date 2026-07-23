from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openjiuwen.core.session import Config as WorkflowRuntimeConfig, WORKFLOW_EXECUTE_TIMEOUT, workflow_session_vars
from openjiuwen.core.session.constants import WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY

from openjiuwen_deepsearch.config.config import AgentConfig, PerQuestionParams, SearchWorkflowConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Result, SearchFinalResult
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepSearchAgent
from openjiuwen_deepsearch.utils.common_utils.security_utils import zero_secret as clear_secret

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("field", ["jina_api_key", "serper_api_key"])
def test_agent_config_rejects_retired_search_fetch_fields(field: str) -> None:
    with pytest.raises(ValueError, match="Use web_search_engine_config and web_fetch_provider_config"):
        AgentConfig.model_validate({field: "retired-key"})


def _make_agent(tmp_path: Path, model_name: str, query: str, *, time_limit: int = 120):
    agent = DeepSearchAgent()
    cfg = AgentConfig().model_dump()
    general = cfg.setdefault("llm_config", {}).setdefault("general", {})
    general.update(
        {
            "model_name": model_name,
            "model_type": "openai",
            "base_url": "https://example.com",
            "api_key": bytearray(b"x"),
        }
    )
    agent_config = AgentConfig.model_validate(cfg)
    per_question_params = agent_config.search_workflow_per_question_params.model_copy(
        update={
            "max_workers": 1,
            "retry_count_on_empty_action_space": 0,
            "time_limit": time_limit,
            "actions_explored_limit": 0,
            "fail_limit": 0,
            "answer_mode_top_k": 1,
            "provide_best_guess": False,
        }
    )
    search_config = SearchWorkflowConfig()
    log_dir = tmp_path / f"result_{query}"
    (log_dir / "Action").mkdir(parents=True, exist_ok=True)
    (log_dir / "Result").mkdir(parents=True, exist_ok=True)
    run_context = agent._create_run_context(
        agent_config=agent_config,
        per_question_params=per_question_params,
        search_config=search_config,
        query=query,
        log_dir=str(log_dir),
        time_limit=time_limit,
        tool_map={},
    )
    return agent, run_context


@pytest.mark.asyncio
async def test_sequential_runs_pass_runtime_model_config_to_all_subworkflows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base_state, base_action
) -> None:
    model_a = "qwen3-max"
    model_b = "Qwen/Qwen3-8B"
    expected_workflow_name = {
        "init_state_1": "init_state_workflow",
        "find_action_1": "find_action_workflow",
        "state_creation_1": "state_creation_workflow",
    }
    seen: list[tuple[str, str]] = []

    async def _fake_run_workflow(*, workflow: str, inputs: dict) -> SimpleNamespace:
        model_name = (
            inputs.get("agent_config", {})
            .get("llm_config", {})
            .get("general", {})
            .get("model_name")
        )
        assert model_name
        assert inputs.get("workflow_name") == expected_workflow_name[workflow]
        assert isinstance(inputs.get("search_config"), dict)
        seen.append((workflow, model_name))
        if workflow == "init_state_1":
            return SimpleNamespace(
                result={
                    "init_state": base_state.model_copy(deep=True),
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        if workflow == "find_action_1":
            state = base_state.model_copy(deep=True, update={"id": f"state-{model_name}"})
            action = base_action.model_copy(deep=True, update={"id": f"action-{model_name}", "state": state})
            return SimpleNamespace(
                result={
                    "actions": [action],
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        if workflow == "state_creation_1":
            return SimpleNamespace(
                result={
                    "result": Result(
                        previous_action_id=f"action-{model_name}",
                        messages=[{"role": "assistant", "content": f"answer-{model_name}"}],
                        new_states=[],
                        found_answer=f"answer-{model_name}",
                    ),
                    "config": {"fail_count": 0},
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        raise AssertionError(workflow)

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.Runner.run_workflow",
        _fake_run_workflow,
    )

    agent_a, run_context_a = _make_agent(tmp_path, model_a, "sequential_a")
    agent_b, run_context_b = _make_agent(tmp_path, model_b, "sequential_b")

    final_a = await agent_a._run_internal(run_context_a)
    final_b = await agent_b._run_internal(run_context_b)

    assert final_a.prediction == f"answer-{model_a}"
    assert final_b.prediction == f"answer-{model_b}"
    for wf in ("init_state_1", "find_action_1", "state_creation_1"):
        wf_models = [m for w, m in seen if w == wf]
        assert wf_models == [model_a, model_b]


@pytest.mark.asyncio
async def test_overlapping_runs_keep_runtime_model_config_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base_state, base_action
) -> None:
    model_a = "qwen3-max"
    model_b = "Qwen/Qwen3-8B"
    expected_workflow_name = {
        "init_state_1": "init_state_workflow",
        "find_action_1": "find_action_workflow",
        "state_creation_1": "state_creation_workflow",
    }
    seen: list[tuple[str, str]] = []

    async def _fake_run_workflow(*, workflow: str, inputs: dict) -> SimpleNamespace:
        model_name = (
            inputs.get("agent_config", {})
            .get("llm_config", {})
            .get("general", {})
            .get("model_name")
        )
        assert model_name
        assert inputs.get("workflow_name") == expected_workflow_name[workflow]
        assert isinstance(inputs.get("search_config"), dict)
        seen.append((workflow, model_name))
        await asyncio.sleep(0)
        if workflow == "init_state_1":
            return SimpleNamespace(
                result={
                    "init_state": base_state.model_copy(deep=True),
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        if workflow == "find_action_1":
            state = base_state.model_copy(deep=True, update={"id": f"state-{model_name}"})
            action = base_action.model_copy(deep=True, update={"id": f"action-{model_name}", "state": state})
            return SimpleNamespace(
                result={
                    "actions": [action],
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        if workflow == "state_creation_1":
            return SimpleNamespace(
                result={
                    "result": Result(
                        previous_action_id=f"action-{model_name}",
                        messages=[{"role": "assistant", "content": f"answer-{model_name}"}],
                        new_states=[],
                        found_answer=f"answer-{model_name}",
                    ),
                    "config": {"fail_count": 0},
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        raise AssertionError(workflow)

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.Runner.run_workflow",
        _fake_run_workflow,
    )

    agent_a, run_context_a = _make_agent(tmp_path, model_a, "overlap_a")
    agent_b, run_context_b = _make_agent(tmp_path, model_b, "overlap_b")

    final_a, final_b = await asyncio.gather(
        agent_a._run_internal(run_context_a),
        agent_b._run_internal(run_context_b),
    )

    assert final_a.prediction == f"answer-{model_a}"
    assert final_b.prediction == f"answer-{model_b}"
    for wf in ("init_state_1", "find_action_1", "state_creation_1"):
        wf_models = {m for w, m in seen if w == wf}
        assert wf_models == {model_a, model_b}


@pytest.mark.asyncio
async def test_overlapping_runs_keep_workflow_timeout_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base_state, base_action
) -> None:
    seen: list[tuple[str, str, float | None]] = []

    async def _fake_run_workflow(*, workflow: str, inputs: dict) -> SimpleNamespace:
        model_name = (
            inputs.get("agent_config", {})
            .get("llm_config", {})
            .get("general", {})
            .get("model_name")
        )
        timeout = WorkflowRuntimeConfig().get_env(WORKFLOW_EXECUTE_TIMEOUT)
        seen.append((workflow, model_name, timeout))
        await asyncio.sleep(0)
        if workflow == "init_state_1":
            return SimpleNamespace(
                result={
                    "init_state": base_state.model_copy(deep=True),
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        if workflow == "find_action_1":
            state = base_state.model_copy(deep=True, update={"id": f"state-{model_name}"})
            action = base_action.model_copy(deep=True, update={"id": f"action-{model_name}", "state": state})
            return SimpleNamespace(
                result={
                    "actions": [action],
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        if workflow == "state_creation_1":
            return SimpleNamespace(
                result={
                    "result": Result(
                        previous_action_id=f"action-{model_name}",
                        messages=[{"role": "assistant", "content": f"answer-{model_name}"}],
                        new_states=[],
                        found_answer=f"answer-{model_name}",
                    ),
                    "config": {"fail_count": 0},
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        raise AssertionError(workflow)

    async def _run_with_timeout(agent: DeepSearchAgent, run_context, time_limit: int):
        token = workflow_session_vars.set(
            {
                **dict(workflow_session_vars.get() or {}),
                WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY: str(time_limit),
            }
        )
        try:
            return await agent._run_internal(run_context)
        finally:
            workflow_session_vars.reset(token)

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.Runner.run_workflow",
        _fake_run_workflow,
    )

    agent_a, run_context_a = _make_agent(tmp_path, "qwen3-max", "timeout_a", time_limit=45)
    agent_b, run_context_b = _make_agent(tmp_path, "Qwen/Qwen3-8B", "timeout_b", time_limit=90)

    final_a, final_b = await asyncio.gather(
        _run_with_timeout(agent_a, run_context_a, 45),
        _run_with_timeout(agent_b, run_context_b, 90),
    )

    assert final_a.prediction == "answer-qwen3-max"
    assert final_b.prediction == "answer-Qwen/Qwen3-8B"
    timeouts_by_model = {(model_name, timeout) for _, model_name, timeout in seen}
    assert timeouts_by_model >= {("qwen3-max", 45.0), ("Qwen/Qwen3-8B", 90.0)}


@pytest.mark.asyncio
async def test_run_sets_workflow_timeout_and_preserves_caller_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cleared_secret_ids: list[int] = []

    class _DummyTool:
        def __init__(self, name: str, config: dict):
            self.name = name
            self.config = config

    class _DummyWebFetch(_DummyTool):
        def __init__(self, config: dict):
            super().__init__("web_fetch", config)

    class _DummyWebSearch(_DummyTool):
        def __init__(self, config: dict):
            super().__init__("web_search", config)

    async def _fake_run_internal(self, run_context):
        assert WorkflowRuntimeConfig().get_env(WORKFLOW_EXECUTE_TIMEOUT) == 45.0
        assert workflow_session_vars.get()["existing"] == "value"
        return SearchFinalResult(
            question=run_context.query,
            termination="answer",
            completion_time=0.0,
            current_date_time="2026-06-30T00:00:00Z",
            prediction="done",
            messages=[],
        )

    def _record_and_clear_secret(secret: bytearray) -> None:
        cleared_secret_ids.append(id(secret))
        clear_secret(secret)

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.LogManager.get_log_dir",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.create_llm_obj",
        lambda llm_config: {"model": object(), "model_name": llm_config.model_name},
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.WebFetch",
        _DummyWebFetch,
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.WebSearch",
        _DummyWebSearch,
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent._build_agent",
        lambda self: None,
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent._run_internal",
        _fake_run_internal,
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.zero_secret",
        _record_and_clear_secret,
    )

    agent_config = AgentConfig(
        llm_config={
            "general": {
                "model_name": "qwen3-max",
                "model_type": "openai",
                "base_url": "https://example.com",
                "api_key": bytearray(b"x"),
            }
        },
        search_workflow_per_question_params=PerQuestionParams(tool_map="search_fetch", time_limit=45),
        web_search_engine_config={
            "search_engine_name": "jina",
            "search_api_key": bytearray(b"search"),
        },
        web_fetch_provider_config={
            "provider_name": "jina",
            "api_key": bytearray(b"fetch"),
        },
    ).model_dump()

    token = workflow_session_vars.set({"existing": "value"})
    try:
        agent = DeepSearchAgent()
        chunks = [
            chunk
            async for chunk in agent.run(
                message="timeout query",
                conversation_id="timeout-run",
                agent_config=agent_config,
            )
        ]
        assert workflow_session_vars.get() == {"existing": "value"}
    finally:
        workflow_session_vars.reset(token)

    assert json.loads(chunks[0])["prediction"] == "done"
    assert len(cleared_secret_ids) == 4
    assert len(set(cleared_secret_ids)) == 4
    assert agent_config["web_search_engine_config"]["search_api_key"] == bytearray(b"search")
    assert agent_config["web_fetch_provider_config"]["api_key"] == bytearray(b"fetch")
