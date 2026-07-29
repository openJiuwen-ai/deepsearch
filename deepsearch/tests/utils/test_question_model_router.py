"""Unit tests for openjiuwen_deepsearch.utils.question_model_router (parse_bit + route_question_search_path)."""

import pytest
from unittest.mock import AsyncMock, patch

from openjiuwen_deepsearch.utils import question_model_router as qmr


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", None),
        ("   ", None),
        ("0", 0),
        ("1", 1),
        ("\n0\n", 0),
        ("The answer: 1", 1),
        ("prefix 0 suffix", 0),
        ("no binary here", None),
        ("2", None),
    ],
)
def test_parse_bit(text, expected):
    assert qmr.parse_bit(text) == expected


def test_system_prompt_file_loaded():
    assert isinstance(qmr.QUESTION_ROUTER_SYSTEM_PROMPT, str)
    assert len(qmr.QUESTION_ROUTER_SYSTEM_PROMPT) > 50
    assert "0 or 1" in qmr.QUESTION_ROUTER_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_route_empty_question_no_llm_defaults_deepsearch():
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
    ) as mock_llm:
        out = await qmr.route_question_search_path("  \n", {"model": object(), "model_name": "m"})
    assert out == 1
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_route_respects_model_output_zero():
    async def fake_ainvoke(*_a, **_k):
        return {"content": "0"}

    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        side_effect=fake_ainvoke,
    ) as mock_llm:
        out = await qmr.route_question_search_path("Who won X in 2020?", {"model": object(), "model_name": "m"})
    assert out == 0
    assert mock_llm.call_count == 1


@pytest.mark.asyncio
async def test_route_respects_model_output_one():
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        new=AsyncMock(return_value={"content": "1"}),
    ):
        out = await qmr.route_question_search_path("long clue chain A then B then C", {"model": object(), "model_name": "m"})
    assert out == 1


@pytest.mark.asyncio
async def test_route_unparseable_model_output_defaults_deepsearch():
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        new=AsyncMock(return_value={"content": "maybe"}),
    ):
        out = await qmr.route_question_search_path("q", {"model": object(), "model_name": "m"})
    assert out == 1


@pytest.mark.asyncio
async def test_route_llm_exception_defaults_deepsearch():
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        new=AsyncMock(side_effect=RuntimeError("api down")),
    ):
        out = await qmr.route_question_search_path("q", {"model": object(), "model_name": "m"})
    assert out == 1


@pytest.mark.asyncio
async def test_route_passes_extra_body_to_llm():
    async def capture(*_a, **kwargs):
        assert kwargs.get("extra_body") == {"k": 1}
        return {"content": "1"}

    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        side_effect=capture,
    ):
        await qmr.route_question_search_path("q", {"model": object(), "model_name": "m"}, extra_body={"k": 1})


@pytest.mark.asyncio
async def test_route_messages_include_system_and_user():
    seen = {}

    async def capture(_llm, messages, **kwargs):
        seen["messages"] = messages
        return {"content": "0"}

    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats",
        side_effect=capture,
    ):
        await qmr.route_question_search_path("user question here", {"model": object(), "model_name": "m"})

    assert len(seen["messages"]) == 2
    assert seen["messages"][0]["role"] == "system"
    assert qmr.QUESTION_ROUTER_SYSTEM_PROMPT in seen["messages"][0]["content"]
    assert seen["messages"][1] == {"role": "user", "content": "user question here"}
