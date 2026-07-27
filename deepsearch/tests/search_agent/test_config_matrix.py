from __future__ import annotations

from typing import Any

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import SearchStartNode

pytestmark = pytest.mark.unit


class _Runtime:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {}

    def update_global_state(self, values: dict[str, Any]) -> None:
        self.state.update(values)

    def get_global_state(self, key: str) -> Any:
        return self.state.get(key)


def _node() -> SearchStartNode:
    return SearchStartNode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workflow_name, expected_timeout, expected_max_tries",
    [
        ("init_state_workflow", 600, None),
        ("find_action_workflow", 600, 10),
        ("state_creation_workflow", 1200, 20),
    ],
)
async def test_search_start_node_applies_workflow_specific_llm_defaults(
    workflow_name,
    expected_timeout,
    expected_max_tries,
    agent_config_dict,
    search_config_dict,
) -> None:
    runtime = _Runtime()
    ac = {
        **agent_config_dict,
        "llm_config": {
            **agent_config_dict.get("llm_config", {}),
            "general": {
                **agent_config_dict.get("llm_config", {}).get("general", {}),
                "model_name": "m1",
                "model_type": "openai",
                "base_url": "https://example.com",
                "api_key": bytearray(b"x"),
            },
        },
        "retrieval_settings": {"top_k": 7},
        "log_dir": "/tmp/run-x",
        "fail_count": 4,
    }
    inputs = {
        "workflow_name": workflow_name,
        "agent_config": ac,
        "search_config": search_config_dict,
    }

    await _node().invoke(inputs, runtime, None)
    config = runtime.get_global_state("config")

    llm = config["llm_config"]["general"]
    assert llm["timeout"] == expected_timeout
    assert llm["append_think_tags_to_messages"] is (
        workflow_name == "state_creation_workflow"
    )
    if expected_max_tries is not None:
        assert llm["max_tries"] == expected_max_tries
    if workflow_name == "state_creation_workflow":
        assert config["retrieval_settings"]["top_k"] == 7
        assert config["log_dir"] == "/tmp/run-x"
        assert config["fail_count"] == 4


@pytest.mark.asyncio
async def test_search_start_node_rejects_unknown_workflow(agent_config_dict, search_config_dict) -> None:
    runtime = _Runtime()
    with pytest.raises(Exception):
        await _node().invoke(
            {
                "workflow_name": "unknown_workflow",
                "agent_config": agent_config_dict,
                "search_config": search_config_dict,
            },
            runtime,
            None,
        )
