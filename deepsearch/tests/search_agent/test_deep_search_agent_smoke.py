from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.search_agent.action_pool import ActionPool
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepSearchAgent

pytestmark = pytest.mark.unit


def test_deep_search_agent_setup_log_directory_creates_expected_dirs(
    monkeypatch, tmp_path: Path
) -> None:
    agent = DeepSearchAgent()
    action_pool = ActionPool()
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.LogManager.get_log_dir",
        lambda: str(tmp_path),
    )

    log_dir = agent.setup_log_directory("result_case", action_pool)

    assert (tmp_path / "result_case" / "Action").exists()
    assert (tmp_path / "result_case" / "Result").exists()
    assert log_dir == str(tmp_path / "result_case")
    assert action_pool.log_dir == str(tmp_path / "result_case")


def test_deep_search_agent_build_agent_initializes_workflow_factories(monkeypatch) -> None:
    monkeypatch.setattr(DeepSearchAgent, "_workflow_agent", None)

    agent_a = DeepSearchAgent()
    agent_b = DeepSearchAgent()

    agent_a._build_agent()
    agent_b._build_agent()

    assert agent_a.agent is not None
    assert agent_a.agent is agent_b.agent
    assert hasattr(agent_a.agent, "agent_config")
    wf_ids = {getattr(s, "id", None) for s in agent_a.agent.agent_config.workflows}
    assert wf_ids >= {"init_state", "find_action", "state_creation"}
