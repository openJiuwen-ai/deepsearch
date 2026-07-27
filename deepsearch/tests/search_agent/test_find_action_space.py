from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import FindActionSpaceNode

pytestmark = pytest.mark.unit


class _Runtime:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def get_global_state(self, key: str) -> Any:
        return self.state.get(key)

    def update_global_state(self, values: dict[str, Any]) -> None:
        self.state.update(values)


def _action_files(log_dir: Path) -> list[Path]:
    return sorted((log_dir / "Action").glob("action_*.json"))


@pytest.mark.asyncio
async def test_find_action_space_node_success_creates_action_file(
    monkeypatch, tmp_log_dir: Path, base_action, base_result
) -> None:
    base_result.messages = [
        {"role": "assistant", "content": "first"},
        {"role": "assistant", "content": "latest"},
    ]

    async def _fake_run_find_action_space(*args, **kwargs):
        return {
            "actions": [base_action],
            "total_input_tokens": 22,
            "total_output_tokens": 11,
            "success": True,
        }

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.run_find_action_space",
        _fake_run_find_action_space,
    )
    runtime = _Runtime(
        {
            "query": base_action.question,
            "state": base_action.state,
            "result": base_result,
            "config": {"llm_config": {"max_tries": 2}},
            "log_dir": str(tmp_log_dir),
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
    )
    node = FindActionSpaceNode()

    actions = await node._do_invoke({}, runtime, None)

    assert actions is not None and len(actions) == 1
    assert actions[0].id == base_action.id
    assert runtime.get_global_state("actions")[0].id == base_action.id
    assert base_result.messages == [{"role": "assistant", "content": "latest"}]
    payload = json.loads(_action_files(tmp_log_dir)[0].read_text(encoding="utf-8"))
    assert payload["proposals"] == [base_action.proposal.direction]
    assert payload["action_ids"] == [base_action.id]


@pytest.mark.asyncio
async def test_find_action_space_node_failure_writes_error_action_file(
    monkeypatch, tmp_log_dir: Path, base_action
) -> None:
    async def _fake_run_find_action_space(*args, **kwargs):
        return {"success": False, "error": "llm down", "actions": []}

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.run_find_action_space",
        _fake_run_find_action_space,
    )
    runtime = _Runtime(
        {
            "query": base_action.question,
            "state": base_action.state,
            "result": None,
            "config": {"llm_config": {"max_tries": 2}},
            "log_dir": str(tmp_log_dir),
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
    )
    node = FindActionSpaceNode()

    result = await node._do_invoke({}, runtime, None)

    assert result is None
    payload = json.loads(_action_files(tmp_log_dir)[0].read_text(encoding="utf-8"))
    assert payload["proposals"] == ["Error: llm down"]
