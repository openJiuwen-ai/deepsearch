from __future__ import annotations

from dataclasses import dataclass
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
    Variable,
)


@dataclass
class DummyRuntime:
    state: dict[str, Any]

    def get_global_state(self, key: str) -> Any:
        if "." not in key:
            return self.state.get(key)
        cur: Any = self.state
        for part in key.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = getattr(cur, part, None)
            if cur is None:
                return None
        return cur

    def update_global_state(self, values: dict[str, Any]) -> None:
        self.state.update(values)

    def base(self) -> Any:
        outputs: dict[str, Any] = {}

        class _State:
            @staticmethod
            def set_outputs(payload: dict[str, Any]) -> None:
                outputs.update(payload)

        return SimpleNamespace(state=lambda: _State())


@pytest.fixture
def tmp_log_dir(tmp_path: Path) -> Path:
    (tmp_path / "Action").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Result").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def base_state() -> State:
    return State(
        id="state-1",
        depth=0,
        answer_variable=1,
        retrieved_evidence_ids=["e1"],
        state=[
            Variable(
                id=1,
                type="City",
                question_clues=["capital of france"],
                discovered_clues=["eiffel tower"],
                candidate="Paris",
                candidate_strength=0.7,
            )
        ],
    )


@pytest.fixture
def base_action(base_state: State) -> Action:
    return Action(
        id="action-1",
        question="What is the capital of France?",
        state=base_state,
        proposal=ActionProposal(direction="Check official sources", score=0.8),
        messages=[{"role": "user", "content": "start"}],
    )


@pytest.fixture
def base_result(base_action: Action) -> Result:
    return Result(
        previous_action_id=base_action.id,
        messages=[{"role": "assistant", "content": "done"}],
        new_states=[],
        found_answer=None,
    )


@pytest.fixture
def agent_config_dict() -> dict[str, Any]:
    cfg = AgentConfig().model_dump()
    general = cfg.setdefault("llm_config", {}).setdefault("general", {})
    general["model_name"] = "mock-model"
    general["base_url"] = "https://example.com"
    general["api_key"] = bytearray(b"x")
    return cfg


@pytest.fixture
def search_config_dict() -> dict[str, Any]:
    return SearchWorkflowConfig().model_dump()
