from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from openjiuwen_deepsearch.config.config import (
    ArxivScholarlyConfig,
    PubMedScholarlyConfig,
    ScholarlySearchConfig,
    SemanticScholarConfig,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import SearchStartNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("config_type", "official_url"),
    [
        (PubMedScholarlyConfig, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"),
        (ArxivScholarlyConfig, "https://export.arxiv.org/api/query"),
        (
            SemanticScholarConfig,
            "https://api.semanticscholar.org/graph/v1/paper/search",
        ),
    ],
)
def test_scholarly_provider_search_url_accepts_only_normalized_official_endpoint(
    config_type,
    official_url,
) -> None:
    assert config_type(search_url="").search_url == ""
    assert config_type(search_url=f"  {official_url}/  ").search_url == official_url


@pytest.mark.parametrize(
    ("config_type", "unsafe_url"),
    [
        (PubMedScholarlyConfig, "http://eutils.ncbi.nlm.nih.gov/entrez/eutils"),
        (PubMedScholarlyConfig, "http://127.0.0.1/entrez/eutils"),
        (PubMedScholarlyConfig, "https://eutils.ncbi.nlm.nih.gov.evil.test/entrez/eutils"),
        (PubMedScholarlyConfig, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/extra"),
        (ArxivScholarlyConfig, "http://export.arxiv.org/api/query"),
        (ArxivScholarlyConfig, "http://10.0.0.1/api/query"),
        (ArxivScholarlyConfig, "https://export.arxiv.org.evil.test/api/query"),
        (ArxivScholarlyConfig, "https://export.arxiv.org/api/query?redirect=localhost"),
        (SemanticScholarConfig, "http://api.semanticscholar.org/graph/v1/paper/search"),
        (SemanticScholarConfig, "http://169.254.169.254/latest/meta-data"),
        (
            SemanticScholarConfig,
            "https://api.semanticscholar.org.evil.test/graph/v1/paper/search",
        ),
        (
            SemanticScholarConfig,
            "https://api.semanticscholar.org/graph/v1/paper/search#internal",
        ),
    ],
)
def test_scholarly_provider_search_url_rejects_non_official_endpoint(
    config_type,
    unsafe_url,
) -> None:
    with pytest.raises(ValidationError, match="official endpoint"):
        config_type(search_url=unsafe_url)


class _Runtime:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {}

    def update_global_state(self, values: dict[str, Any]) -> None:
        self.state.update(values)

    def get_global_state(self, key: str) -> Any:
        return self.state.get(key)


class _NonDeepcopyableRuntimeValue:
    def __deepcopy__(self, memo):
        raise TypeError("runtime value cannot be deep-copied")

    def __repr__(self) -> str:
        return "<runtime-value>"


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


@pytest.mark.asyncio
async def test_search_start_node_redacts_scholarly_api_keys_from_logs(
    agent_config_dict,
    search_config_dict,
    caplog,
) -> None:
    runtime = _Runtime()
    secret = "scholarly-log-secret"
    agent_config = {
        **agent_config_dict,
        "scholarly_search_config": {
            "pubmed": {"search_api_key": bytearray(secret.encode("utf-8"))},
        },
    }

    with caplog.at_level(
        logging.INFO,
        logger="openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes",
    ):
        await _node().invoke(
            {
                "workflow_name": "init_state_workflow",
                "agent_config": agent_config,
                "search_config": search_config_dict,
            },
            runtime,
            None,
        )

    assert secret not in caplog.text
    assert "search_api_key': '***'" in caplog.text


@pytest.mark.asyncio
async def test_search_start_node_logging_does_not_deepcopy_runtime_config_values(
    agent_config_dict,
    search_config_dict,
) -> None:
    runtime = _Runtime()
    agent_config = {
        **agent_config_dict,
        "runtime_value": _NonDeepcopyableRuntimeValue(),
    }

    await _node().invoke(
        {
            "workflow_name": "init_state_workflow",
            "agent_config": agent_config,
            "search_config": search_config_dict,
        },
        runtime,
        None,
    )

    assert runtime.get_global_state("config") is not None


@pytest.mark.asyncio
async def test_stream_workflow_config_keeps_valid_scholarly_key_types() -> None:
    captured_inputs = {}

    async def fake_run_agent_streaming(*, agent, inputs):
        captured_inputs.update(inputs)
        if False:
            yield None

    agent = object.__new__(DeepresearchAgent)
    agent.agent = None
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.Runner.run_agent_streaming",
        side_effect=fake_run_agent_streaming,
    ):
        async for _ in agent._consume_stream_chunks(
            conversation_id="conversation-1",
            message="query",
            decoded_template="",
            interrupt_feedback="",
            session_agent_config={
                "scholarly_search_config": {
                    "fetch_full_text": False,
                    "max_full_text_results_per_query": 2,
                    "pubmed": {"search_api_key": bytearray(b"secret")},
                },
            },
        ):
            pass

    scholarly = captured_inputs["agent_config"]["scholarly_search_config"]
    parsed = ScholarlySearchConfig.model_validate(scholarly)
    assert parsed.fetch_full_text is False
    assert parsed.max_full_text_results_per_query == 2
    assert parsed.pubmed.search_api_key == bytearray()
    assert "secret" not in repr(captured_inputs)
