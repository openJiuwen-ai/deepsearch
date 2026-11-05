"""Orchestrator-level integration tests (mocked Runner / state_creation)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen_deepsearch.config.config import AgentConfig, SearchWorkflowConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Action,
    ActionProposal,
    Result,
    State,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepSearchAgent

pytestmark = pytest.mark.integration


class ExposedDeepSearchAgent(DeepSearchAgent):
    """用于测试的类，公开受保护的方法以遵循 G.CLS.11 规则"""

    async def run_internal(self, *args, **kwargs):
        return await super()._run_internal(*args, **kwargs)


def _make_agent(tmp_log_dir: Path, **pqp_updates: Any):
    agent = DeepSearchAgent()
    agent_config = AgentConfig()
    base_pqp = agent_config.search_workflow_per_question_params.model_copy(
        update={
            "max_workers": 2,
            "retry_count_on_empty_action_space": 0,
            "time_limit": 300,
            "actions_explored_limit": 0,
            "fail_limit": 0,
            "answer_mode_top_k": 1,
            "provide_best_guess": False,
        }
    )
    per_question_params = base_pqp.model_copy(update=pqp_updates)
    run_context = agent._create_run_context(
        agent_config=agent_config,
        per_question_params=per_question_params,
        search_config=SearchWorkflowConfig(),
        query="integration query",
        log_dir=str(tmp_log_dir),
        time_limit=120,
        tool_map={},
    )
    return agent, run_context


def _second_action(base_action: Action, *, action_id: str, strength: float, answer: str) -> Action:
    st = base_action.state.model_copy(
        update={
            "state": [
                base_action.state.state[0].model_copy(
                    update={"candidate": answer, "candidate_strength": strength}
                )
            ]
        }
    )
    return base_action.model_copy(
        update={
            "id": action_id,
            "proposal": ActionProposal(direction=f"dir-{action_id}", score=0.5),
            "state": st,
        }
    )


@pytest.mark.asyncio
async def test_actions_explored_limit_terminates(
    monkeypatch: pytest.MonkeyPatch, tmp_log_dir: Path, base_action, base_state
) -> None:
    agent, run_context = _make_agent(tmp_log_dir, actions_explored_limit=2, max_workers=2)
    a2 = base_action.model_copy(update={"id": "action-2", "proposal": ActionProposal(direction="b", score=0.5)})

    async def _fake_run_workflow(*, workflow: str, inputs: dict) -> SimpleNamespace:
        if workflow == "init_state_1":
            return SimpleNamespace(
                result={"init_state": base_state, "total_input_tokens": 0, "total_output_tokens": 0}
            )
        if workflow == "find_action_1":
            return SimpleNamespace(
                result={"actions": [base_action, a2], "total_input_tokens": 0, "total_output_tokens": 0}
            )
        raise AssertionError(workflow)

    async def _fake_state_creation(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            result={
                "result": None,
                "config": {},
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
        _fake_state_creation,
    )

    final = await agent._run_internal(run_context)
    assert final.termination == "actions_explored_limit"


@pytest.mark.asyncio
async def test_new_states_triggers_second_find_action_then_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_log_dir: Path, base_action, base_state
) -> None:
    agent, run_context = _make_agent(tmp_log_dir, max_workers=1)
    find_calls: list[Any] = []

    branch_state = base_state.model_copy(update={"id": "branch-1", "depth": 1})
    follow_action = base_action.model_copy(
        update={
            "id": "follow-up",
            "state": branch_state,
            "proposal": ActionProposal(direction="follow", score=0.6),
        }
    )

    async def _fake_run_workflow(*, workflow: str, inputs: dict) -> SimpleNamespace:
        if workflow == "init_state_1":
            return SimpleNamespace(
                result={"init_state": base_state, "total_input_tokens": 0, "total_output_tokens": 0}
            )
        if workflow == "find_action_1":
            find_calls.append(inputs.get("state"))
            if len(find_calls) == 1:
                return SimpleNamespace(
                    result={"actions": [base_action], "total_input_tokens": 0, "total_output_tokens": 0}
                )
            return SimpleNamespace(
                result={"actions": [follow_action], "total_input_tokens": 0, "total_output_tokens": 0}
            )
        raise AssertionError(workflow)

    async def _fake_state_creation(*args: Any, **kwargs: Any) -> SimpleNamespace:
        action = kwargs.get("action") or args[0]
        aid = action.get("id") if isinstance(action, dict) else action.id
        if aid == base_action.id:
            return SimpleNamespace(
                result={
                    "result": Result(
                        previous_action_id=str(aid),
                        messages=[{"role": "assistant", "content": "branch"}],
                        new_states=[branch_state],
                        found_answer=None,
                    ),
                    "config": {},
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        return SimpleNamespace(
            result={
                "result": Result(
                    previous_action_id=str(aid),
                    messages=[{"role": "assistant", "content": "done"}],
                    new_states=[],
                    found_answer="Lyon",
                ),
                "config": {},
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
        _fake_state_creation,
    )

    final = await agent._run_internal(run_context)
    assert len(find_calls) >= 2
    second_find_state = find_calls[1]
    second_id = (
        second_find_state.id
        if isinstance(second_find_state, State)
        else second_find_state["id"]
    )
    assert second_id == branch_state.id
    assert final.termination == "answer"
    assert final.prediction == "Lyon"


@pytest.mark.asyncio
async def test_answer_mode_top_k_returns_best_strength(
    monkeypatch: pytest.MonkeyPatch, tmp_log_dir: Path, base_action, base_state
) -> None:
    agent, run_context = _make_agent(tmp_log_dir, answer_mode_top_k=2, max_workers=2)
    weak = _second_action(base_action, action_id="weak", strength=0.2, answer="Paris")
    strong = _second_action(base_action, action_id="strong", strength=0.95, answer="Lyon")

    async def _fake_run_workflow(*, workflow: str, inputs: dict) -> SimpleNamespace:
        if workflow == "init_state_1":
            return SimpleNamespace(
                result={"init_state": base_state, "total_input_tokens": 0, "total_output_tokens": 0}
            )
        if workflow == "find_action_1":
            return SimpleNamespace(
                result={"actions": [weak, strong], "total_input_tokens": 0, "total_output_tokens": 0}
            )
        raise AssertionError(workflow)

    async def _fake_state_creation(*args: Any, **kwargs: Any) -> SimpleNamespace:
        action = kwargs.get("action") or args[0]
        aid = action.get("id") if isinstance(action, dict) else action.id
        ans = "Paris" if aid == "weak" else "Lyon"
        return SimpleNamespace(
            result={
                "result": Result(
                    previous_action_id=str(aid),
                    messages=[{"role": "assistant", "content": ans}],
                    new_states=[],
                    found_answer=ans,
                ),
                "config": {},
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
        _fake_state_creation,
    )

    final = await agent._run_internal(run_context)
    assert final.termination == "answer"
    assert final.prediction == "Lyon"


@pytest.mark.asyncio
async def test_fail_count_accumulates_one_per_failed_action(
    monkeypatch: pytest.MonkeyPatch, tmp_log_dir: Path, base_action, base_state
) -> None:
    """Each failed action must raise the global fail_count by exactly 1.

    Regression for the double-counting bug: the parent used to pass its
    cumulative ``fail_count`` into every state_creation sub-workflow AND add the
    (already-cumulative) returned value back with ``+=``. With ``fail_limit=3``
    that tripped termination after 2 failed actions instead of 3. Here the mock
    reproduces the real sub-workflow contract -- it returns the passed-in
    ``fail_count`` incremented by 1 -- so the bug would surface as an early
    termination.
    """
    _, run_context = _make_agent(tmp_log_dir, fail_limit=3, max_workers=1)
    agent = ExposedDeepSearchAgent()
    actions = [
        base_action.model_copy(
            update={"id": f"action-{i}", "proposal": ActionProposal(direction=f"d{i}", score=0.5)}
        )
        for i in range(5)
    ]
    state_creation_calls: list[int] = []

    async def _fake_run_workflow(*, workflow: str, inputs: dict) -> SimpleNamespace:
        if workflow == "init_state_1":
            return SimpleNamespace(
                result={"init_state": base_state, "total_input_tokens": 0, "total_output_tokens": 0}
            )
        if workflow == "find_action_1":
            return SimpleNamespace(
                result={"actions": actions, "total_input_tokens": 0, "total_output_tokens": 0}
            )
        if workflow == "state_creation_1":
            # Mirror algorithm/search_nodes/utils.py: the sub-workflow increments
            # the fail_count it was handed and echoes it back inside ``config``.
            passed_in = inputs.get("fail_count", 0)
            state_creation_calls.append(passed_in)
            return SimpleNamespace(
                result={
                    "result": None,
                    "config": {"fail_count": passed_in + 1},
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                }
            )
        raise AssertionError(workflow)

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.Runner.run_workflow",
        _fake_run_workflow,
    )

    final = await agent.run_internal(run_context)

    assert final.termination == "fail_limit"
    # Exactly 3 failed actions are needed to reach fail_limit=3 (one increment
    # each). The double-counting bug would terminate after only 2.
    assert len(state_creation_calls) == 3
    assert run_context.fail_count == 3
    # The parent must hand a per-action delta base of 0 to each sub-workflow,
    # not its running cumulative total.
    assert state_creation_calls == [0, 0, 0]


@pytest.mark.asyncio
async def test_answer_writes_final_result_json(
    monkeypatch: pytest.MonkeyPatch, tmp_log_dir: Path, base_action, base_state
) -> None:
    agent, run_context = _make_agent(tmp_log_dir)

    async def _fake_run_workflow(*, workflow: str, inputs: dict) -> SimpleNamespace:
        if workflow == "init_state_1":
            return SimpleNamespace(
                result={"init_state": base_state, "total_input_tokens": 0, "total_output_tokens": 0}
            )
        if workflow == "find_action_1":
            return SimpleNamespace(
                result={"actions": [base_action], "total_input_tokens": 0, "total_output_tokens": 0}
            )
        raise AssertionError(workflow)

    async def _fake_state_creation(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            result={
                "result": Result(
                    previous_action_id=base_action.id,
                    messages=[{"role": "assistant", "content": "ok"}],
                    new_states=[],
                    found_answer="Paris",
                ),
                "config": {},
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
        _fake_state_creation,
    )

    await agent._run_internal(run_context)
    out = tmp_log_dir / "final_result.json"
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["termination"] == "answer"
    assert data["prediction"] == "Paris"
