from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import InitializeStateNode

pytestmark = pytest.mark.unit


class _Runtime:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def get_global_state(self, key: str) -> Any:
        return self.state.get(key)

    def update_global_state(self, values: dict[str, Any]) -> None:
        self.state.update(values)


@pytest.mark.asyncio
async def test_initialize_state_node_writes_initial_state_file(
    monkeypatch, tmp_log_dir: Path, base_state
) -> None:
    async def _fake_run_initialize_state(*args, **kwargs):
        return {
            "init_state": base_state,
            "messages": [{"role": "assistant", "content": "ready"}],
            "total_input_tokens": 12,
            "total_output_tokens": 7,
        }

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.run_initialize_state",
        _fake_run_initialize_state,
    )
    runtime = _Runtime(
        {
            "query": "Where is the Eiffel Tower?",
            "config": {"llm_config": {"max_tries": 2}},
            "log_dir": str(tmp_log_dir),
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
    )

    node = InitializeStateNode()
    result = await node._do_invoke({}, runtime, None)

    assert result is None
    assert runtime.get_global_state("init_state").id == base_state.id
    payload = json.loads((tmp_log_dir / "initial_state.json").read_text(encoding="utf-8"))
    assert payload["id"] == base_state.id
    assert payload["messages"][0]["content"] == "ready"


def test_initialize_state_pre_handle_requires_query(tmp_log_dir: Path) -> None:
    runtime = _Runtime({"query": "", "config": {}, "log_dir": str(tmp_log_dir)})
    node = InitializeStateNode()

    with pytest.raises(Exception):
        node._pre_handle({}, runtime, None)
