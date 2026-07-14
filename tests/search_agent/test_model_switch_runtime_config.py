from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen_deepsearch.config.config import AgentConfig, SearchWorkflowConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Result
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepSearchAgent

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("field", ["jina_api_key", "serper_api_key"])
def test_agent_config_rejects_retired_search_fetch_fields(field: str) -> None:
    with pytest.raises(ValueError, match="Use web_search_engine_config and web_fetch_provider_config"):
        AgentConfig.model_validate({field: "retired-key"})


def _make_agent(tmp_path: Path, model_name: str, query: str) -> DeepSearchAgent:
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
    agent.agent_config = AgentConfig.model_validate(cfg)
    agent.per_question_params = agent.agent_config.search_workflow_per_question_params.model_copy(
        update={
            "max_workers": 1,
            "retry_count_on_empty_action_space": 0,
            "time_limit": 120,
            "actions_explored_limit": 0,
            "fail_limit": 0,
            "answer_mode_top_k": 1,
            "provide_best_guess": False,
        }
    )
    agent.search_config = SearchWorkflowConfig()
    agent.query = query
    log_dir = tmp_path / f"result_{query}"
    (log_dir / "Action").mkdir(parents=True, exist_ok=True)
    (log_dir / "Result").mkdir(parents=True, exist_ok=True)
    agent.log_dir = str(log_dir)
    agent.action_pool.log_dir = str(log_dir)
    agent.time_limit = 120
    agent.tool_map = {}
    return agent


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

    agent_a = _make_agent(tmp_path, model_a, "sequential_a")
    agent_b = _make_agent(tmp_path, model_b, "sequential_b")

    final_a = await agent_a._run_internal()
    final_b = await agent_b._run_internal()

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

    agent_a = _make_agent(tmp_path, model_a, "overlap_a")
    agent_b = _make_agent(tmp_path, model_b, "overlap_b")

    final_a, final_b = await asyncio.gather(agent_a._run_internal(), agent_b._run_internal())

    assert final_a.prediction == f"answer-{model_a}"
    assert final_b.prediction == f"answer-{model_b}"
    for wf in ("init_state_1", "find_action_1", "state_creation_1"):
        wf_models = {m for w, m in seen if w == wf}
        assert wf_models == {model_a, model_b}
