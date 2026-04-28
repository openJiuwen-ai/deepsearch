from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen_deepsearch.config.config import AgentConfig, SearchWorkflowConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepSearchAgent

pytestmark = pytest.mark.unit


def test_deep_search_agent_setup_log_directory_creates_expected_dirs(
    monkeypatch, tmp_path: Path
) -> None:
    agent = DeepSearchAgent()
    agent.agent_config = AgentConfig()
    agent.search_config = SearchWorkflowConfig()
    agent.per_question_params = agent.agent_config.search_workflow_per_question_params.model_copy(
        update={"time_limit": 123}
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.LogManager.get_log_dir",
        lambda: str(tmp_path),
    )

    agent.setup_log_directory("result_case")

    assert (tmp_path / "result_case" / "Action").exists()
    assert (tmp_path / "result_case" / "Result").exists()
    assert agent.action_pool.log_dir == str(tmp_path / "result_case")


def test_deep_search_agent_build_agent_initializes_workflow_factories() -> None:
    agent = DeepSearchAgent()
    agent.agent_config = AgentConfig()
    agent.search_config = SearchWorkflowConfig()
    agent.log_dir = "/tmp/deepsearch"

    agent._build_agent()

    assert agent.agent is not None
    assert hasattr(agent.agent, "agent_config")
    wf_ids = {getattr(s, "id", None) for s in agent.agent.agent_config.workflows}
    assert wf_ids >= {"init_state", "find_action", "state_creation"}
