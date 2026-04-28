from __future__ import annotations

from typing import Any

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import RunActionNode

pytestmark = pytest.mark.unit


class _Runtime:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def get_global_state(self, key: str) -> Any:
        return self.state.get(key)

    def update_global_state(self, values: dict[str, Any]) -> None:
        self.state.update(values)


@pytest.mark.asyncio
async def test_run_action_node_llm_budget_exhausted_saves_error(
    monkeypatch, base_action
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_save_result(config, action, result_to_save, time_taken):
        calls.append({"config": config, "action": action, "result": result_to_save})
        return config

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes._save_result",
        _fake_save_result,
    )
    runtime = _Runtime(
        {
            "action_start_time": 0.0,
            "config": {"max_llm_calls_per_run": 0, "validator_agent": {}},
            "retrieval_settings": {},
            "new_found_evidence_ids": [],
            "messages": [{"role": "user", "content": "q"}],
            "action": base_action.model_dump(),
            "max_llm_calls_per_run": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
    )

    out = await RunActionNode()._do_invoke({}, runtime, None)

    assert out == {"next_node": "end_node"}
    assert len(calls) == 1
    assert "Exceeded number of llm calls" in calls[0]["result"]["termination"]


@pytest.mark.asyncio
async def test_run_action_node_try_again_routes_back_to_run_action(
    monkeypatch, base_action
) -> None:
    async def _fake_run_action(*args, **kwargs):
        return {"success": False, "try_again": True, "messages": [{"role": "user", "content": "trimmed"}]}

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.run_action",
        _fake_run_action,
    )
    runtime = _Runtime(
        {
            "action_start_time": 1.0,
            "config": {"max_llm_calls_per_run": 5, "validator_agent": {}},
            "retrieval_settings": {},
            "new_found_evidence_ids": ["a"],
            "messages": [{"role": "user", "content": "q"}],
            "action": base_action.model_dump(),
            "max_llm_calls_per_run": 5,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
    )

    out = await RunActionNode()._do_invoke({}, runtime, None)

    assert out == {"next_node": "run_action"}
    assert runtime.get_global_state("messages")[0]["content"] == "trimmed"


@pytest.mark.asyncio
async def test_run_action_node_failure_saves_error_result(monkeypatch, base_action) -> None:
    save_calls: list[dict[str, Any]] = []

    async def _fake_run_action(*args, **kwargs):
        return {
            "success": False,
            "error": "boom",
            "messages": [{"role": "assistant", "content": "failed turn"}],
            "next_node": "end_node",
        }

    def _fake_save_result(config, action, result_to_save, time_taken):
        save_calls.append(result_to_save)
        return config

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.run_action",
        _fake_run_action,
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes._save_result",
        _fake_save_result,
    )
    runtime = _Runtime(
        {
            "action_start_time": 0.0,
            "config": {"max_llm_calls_per_run": 3, "validator_agent": {}},
            "retrieval_settings": {},
            "new_found_evidence_ids": [],
            "messages": [{"role": "user", "content": "q"}],
            "action": base_action.model_dump(),
            "max_llm_calls_per_run": 3,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
    )

    out = await RunActionNode()._do_invoke({}, runtime, None)

    assert out == {"next_node": "end_node"}
    assert len(save_calls) == 1
    assert save_calls[0]["termination"] == "Error: boom"
