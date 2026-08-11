from __future__ import annotations

import pytest

from openjiuwen_deepsearch.algorithm.search_nodes.run_action import (
    RunActionConfig,
    run_action,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode

pytestmark = pytest.mark.unit


def _params(base_action, *, strategy: str = "fail", retrieval_tool_only: bool = False):
    return RunActionConfig(
        llm_config={"model_name": "mock"},
        config={
            "use_candidate_strength": True,
            "discovered_clues_mode": "report",
            "context_limit_reached_strategy": strategy,
            "retrieval_settings": {"top_k": 10, "top_k_multiply_factor": 8},
        },
        action=base_action.model_dump(),
        state=base_action.state.model_dump(),
        query=base_action.question,
        messages=[{"role": "user", "content": "q"}],
        new_found_evidence_ids=[],
        validate_new_states=True,
        validate_answer=True,
        action_start_time=0.0,
        retrieval_tool_only=retrieval_tool_only,
        retrieval_settings={"top_k": 10, "top_k_multiply_factor": 8},
        context_limit_reached_strategy=strategy,
        total_input_tokens=1,
        total_output_tokens=2,
    )


@pytest.mark.asyncio
async def test_run_run_action_success_path(monkeypatch, base_action) -> None:
    async def _fake_run_llm(*args, **kwargs):
        return "<state>{}</state>", None, 3, 4

    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.search_nodes.run_action.run_llm", _fake_run_llm
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.search_nodes.run_action.apply_system_prompt",
        lambda *args, **kwargs: [{"role": "system", "content": "s"}],
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.search_nodes.run_action.parse_and_apply_llm_result_safe",
        lambda *args, **kwargs: ("state", {"messages": []}, {"k": "v"}),
    )

    out = await run_action(_params(base_action))

    assert out["success"] is True
    assert out["mode"] == "state"
    assert out["data"] == {"messages": []}
    assert out["total_input_tokens"] == 4
    assert out["total_output_tokens"] == 6


@pytest.mark.asyncio
async def test_run_action_retry_exhausted_returns_failure(monkeypatch, base_action) -> None:
    async def _raise_retry(*args, **kwargs):
        raise CustomValueException(
            StatusCode.AGENT_RETRY_FAILED_ALL_ATTEMPTS.code,
            "all retries exhausted",
        )

    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.search_nodes.run_action.run_llm", _raise_retry
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.search_nodes.run_action.apply_system_prompt",
        lambda *args, **kwargs: [{"role": "system", "content": "s"}],
    )

    out = await run_action(_params(base_action))

    assert out["success"] is False
    assert out["next_node"] == "end_node"
    assert "retries exhausted" in out["error"]
    assert isinstance(out["messages"], list)


@pytest.mark.asyncio
async def test_run_action_context_limit_reduced_retrieval_strategy(
    monkeypatch, base_action
) -> None:
    async def _raise_context_limit(*args, **kwargs):
        raise CustomValueException(
            StatusCode.LLM_CALL_FAILED.code,
            "context length exceeded",
        )

    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.search_nodes.run_action.run_llm", _raise_context_limit
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.search_nodes.run_action.apply_system_prompt",
        lambda *args, **kwargs: [{"role": "system", "content": "s"}],
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.search_nodes.run_action._is_context_limit_error",
        lambda *args, **kwargs: True,
    )

    out = await run_action(
        _params(base_action, strategy="reduced_retrieval_request", retrieval_tool_only=True)
    )

    assert out["success"] is False
    assert out["try_again"] is True
    assert out["retrieval_settings"]["top_k"] == 5
    assert out["retrieval_settings"]["top_k_multiply_factor"] == 4
