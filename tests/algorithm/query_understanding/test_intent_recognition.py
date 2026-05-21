# -*- coding: UTF-8 -*-
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    IntentRecognitionResult,
    recognize_report_intent,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import ResearchIntent


@pytest.fixture
def sample_tool_response():
    return {
        "tool_calls": [
            {
                "name": "emit_report_intent",
                "args": {
                    "research_query": "AI Agent trends",
                    "section_count": 5,
                    "audience_role": "研发负责人",
                    "tone": "analytical",
                    "report_type": "deep_research",
                    "include_url": ["https://example.com/a", "https://example.com/b"],
                    "exclude_url": [],
                    "include_domains": [],
                    "exclude_domains": [],
                },
                 "id": "tc1",
                "type": "tool_call",
            }
        ],
        "content": "",
    }


@pytest.mark.asyncio
async def test_recognize_report_intent_success(sample_tool_response):
    mock_llm = Mock()
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": mock_llm},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=sample_tool_response,
    ):
        result = await recognize_report_intent(
            {
                "original_query": "写报告：AI Agent\nhttps://example.com/a",
                "llm_model_name": "basic",
                "messages": [],
            }
        )

    assert isinstance(result, IntentRecognitionResult)
    assert result.research_query == "AI Agent trends"
    assert result.research_intent.section_count == 5
    assert result.research_intent.audience_role == "研发负责人"
    assert result.research_intent.tone == "analytical"
    assert result.research_intent.report_type == "deep_research"
    assert "https://example.com/a" in result.research_intent.include_url
    assert "example.com" in result.research_intent.include_domains


@pytest.mark.asyncio
async def test_recognize_report_intent_no_tool_calls_fallback():
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value={"tool_calls": [], "content": ""},
    ):
        q = "原始问题全文"
        result = await recognize_report_intent({"original_query": q, "llm_model_name": "basic"})

    assert result.original_query == q
    assert result.research_query == q
    assert result.research_intent == ResearchIntent()


@pytest.mark.asyncio
async def test_recognize_report_intent_exception_fallback():
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        side_effect=RuntimeError("llm down"),
    ):
        q = "fallback text"
        result = await recognize_report_intent({"original_query": q, "llm_model_name": "basic"})

    assert result.research_query == q
    assert result.research_intent.section_count is None


@pytest.mark.asyncio
async def test_empty_original_query():
    result = await recognize_report_intent({"original_query": "", "llm_model_name": "basic"})
    assert result.research_query == ""
    assert result.research_intent == ResearchIntent()


@pytest.mark.asyncio
async def test_normalize_invalid_section_count():
    bad_response = {
        "tool_calls": [
            {
                "args": {
                    "research_query": "topic",
                    "section_count": -1,
                    "include_url": ["https://x.y/z"],
                },
            }
        ]
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=bad_response,
    ):
        result = await recognize_report_intent(
            {"original_query": "topic", "llm_model_name": "basic"}
        )

    assert result.research_intent.section_count is None
    assert "x.y" in result.research_intent.include_domains
