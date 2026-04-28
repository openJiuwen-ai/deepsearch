from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen_deepsearch.config.config import AgentConfig, SearchWorkflowConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Result
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepSearchAgent

pytestmark = pytest.mark.unit


def _make_agent(tmp_log_dir) -> DeepSearchAgent:
    agent = DeepSearchAgent()
    agent.agent_config = AgentConfig()
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
    agent.query = "capital question"
    agent.log_dir = str(tmp_log_dir)
    agent.time_limit = 120
    agent.tool_map = {}
    agent.action_pool.log_dir = str(tmp_log_dir)
    return agent


@pytest.mark.asyncio
async def test_run_internal_terminates_on_fail_limit(
    monkeypatch, tmp_log_dir, base_action, base_state
) -> None:
    agent = _make_agent(tmp_log_dir)
    agent.per_question_params.fail_limit = 1

    async def _fake_run_workflow(*, workflow, inputs):
        if workflow == "init_state_1":
            return SimpleNamespace(result={"init_state": base_state, "total_input_tokens": 0, "total_output_tokens": 0})
        if workflow == "find_action_1":
            return SimpleNamespace(result={"actions": [base_action], "total_input_tokens": 0, "total_output_tokens": 0})
        raise AssertionError(f"unexpected workflow: {workflow}")

    async def _fake_state_creation_workflow(*args, **kwargs):
        return SimpleNamespace(
            result={
                "result": None,
                "config": {"fail_count": 1},
                "total_input_tokens": 0,
                "total_output_tokens": 0,
            }
        )

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.Runner.run_workflow",
        _fake_run_workflow,
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent.run_state_creation_workflow",
        _fake_state_creation_workflow,
    )

    final = await agent._run_internal()

    assert final.termination == "fail_limit"


@pytest.mark.asyncio
async def test_run_internal_terminates_when_action_pool_depleted(
    monkeypatch, tmp_log_dir, base_state
) -> None:
    agent = _make_agent(tmp_log_dir)
    agent.per_question_params.retry_count_on_empty_action_space = 0

    async def _fake_run_workflow(*, workflow, inputs):
        if workflow == "init_state_1":
            return SimpleNamespace(result={"init_state": base_state, "total_input_tokens": 0, "total_output_tokens": 0})
        if workflow == "find_action_1":
            return SimpleNamespace(result={"actions": [], "total_input_tokens": 0, "total_output_tokens": 0})
        raise AssertionError(f"unexpected workflow: {workflow}")

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.Runner.run_workflow",
        _fake_run_workflow,
    )

    final = await agent._run_internal()

    assert final.termination == "action_pool_depleted"


@pytest.mark.asyncio
async def test_run_internal_returns_answer_termination(
    monkeypatch, tmp_log_dir, base_action, base_state
) -> None:
    agent = _make_agent(tmp_log_dir)

    async def _fake_run_workflow(*, workflow, inputs):
        if workflow == "init_state_1":
            return SimpleNamespace(result={"init_state": base_state, "total_input_tokens": 0, "total_output_tokens": 0})
        if workflow == "find_action_1":
            return SimpleNamespace(result={"actions": [base_action], "total_input_tokens": 0, "total_output_tokens": 0})
        raise AssertionError(f"unexpected workflow: {workflow}")

    async def _fake_state_creation_workflow(*args, **kwargs):
        return SimpleNamespace(
            result={
                "result": Result(
                    previous_action_id=base_action.id,
                    messages=[{"role": "assistant", "content": "answer"}],
                    new_states=[],
                    found_answer="Paris",
                ),
                "config": {"fail_count": 0},
                "total_input_tokens": 0,
                "total_output_tokens": 0,
            }
        )

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.Runner.run_workflow",
        _fake_run_workflow,
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent.run_state_creation_workflow",
        _fake_state_creation_workflow,
    )

    final = await agent._run_internal()

    assert final.termination == "answer"
    assert final.prediction == "Paris"
