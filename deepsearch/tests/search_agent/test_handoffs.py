from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import RunActionNode, SearchEndNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Result

pytestmark = pytest.mark.unit


class _Runtime:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.outputs: dict[str, Any] = {}

    def get_global_state(self, key: str) -> Any:
        return self.state.get(key)

    def update_global_state(self, values: dict[str, Any]) -> None:
        self.state.update(values)

    def base(self) -> Any:
        outputs = self.outputs

        class _State:
            @staticmethod
            def set_outputs(payload: dict[str, Any]) -> None:
                outputs.update(payload)

        return SimpleNamespace(state=lambda: _State())


def test_run_action_post_handle_handoff_to_tool(base_action) -> None:
    runtime = _Runtime({})
    out = RunActionNode()._post_handle(
        {},
        {
            "success": True,
            "mode": "tool_calls",
            "data": {"tool_calls": [{"name": "web_search", "arguments": {"query": ["x"]}}], "messages": []},
            "config": {},
            "total_input_tokens": 1,
            "total_output_tokens": 1,
            "validate_new_states": False,
            "validate_answer": False,
        },
        runtime,
        None,
    )

    assert out == {"next_node": "tool"}
    assert runtime.get_global_state("pending_tool_calls")[0]["name"] == "web_search"


def test_run_action_post_handle_handoff_to_validate_for_state_result(base_action) -> None:
    runtime = _Runtime({})
    result = Result(
        previous_action_id=base_action.id,
        messages=[{"role": "assistant", "content": "state done"}],
        new_states=[],
        found_answer=None,
    )
    out = RunActionNode()._post_handle(
        {},
        {
            "success": True,
            "mode": "state",
            "data": result,
            "config": {},
            "total_input_tokens": 2,
            "total_output_tokens": 3,
            "validate_new_states": True,
            "validate_answer": False,
        },
        runtime,
        None,
    )

    assert out == {"next_node": "validate_new_state"}
    assert runtime.get_global_state("result").previous_action_id == base_action.id


@pytest.mark.asyncio
async def test_search_end_node_handoff_payload_for_state_creation(base_result) -> None:
    runtime = _Runtime(
        {
            "workflow_name": "state_creation_workflow",
            "result": base_result,
            "config": {"fail_count": 0},
            "messages": [{"role": "assistant", "content": "done"}],
            "total_input_tokens": 9,
            "total_output_tokens": 5,
        }
    )

    out = await SearchEndNode().invoke({}, runtime, None)

    assert "final_result" in out
    assert out["final_result"]["result"] == base_result
    assert out["final_result"]["total_input_tokens"] == 9
    assert out["final_result"]["total_output_tokens"] == 5
