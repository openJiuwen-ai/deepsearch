import logging
from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report import (
    Reporter,
    ensure_exact_target_documents,
)
from openjiuwen_deepsearch.common.common_constants import ENGLISH
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context


def _report_doc(idx: int, *, url: str | None = None, content: str | None = None) -> dict:
    return {
        "title": f"doc-{idx}",
        "url": url or f"https://example.com/{idx}",
        "original_content": content or f"content-{idx}",
        "key_passages": [f"passage-{idx}"],
        "scores": {"relevance": 9, "answerability": 9, "authority": 9, "data_density": 9},
    }


def test_exact_target_paper_is_collected_as_required_fulltext_evidence():
    selected = [_report_doc(1)]
    target = {
        "title": "Requested Paper",
        "url": "https://journal.example.org/requested",
        "original_content": "requested evidence",
    }

    result = ensure_exact_target_documents(
        selected,
        [*selected, target],
        [{"url": "https://journal.example.org/requested/"}],
    )

    assert result == [target, *selected]


@pytest.mark.asyncio
async def test_generate_section_rationales_retries_with_failure_feedback():
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": 3,
            "section_task": "3 企业经营与行业分析",
            "section_description": "desc",
            "report_task": "task",
            "current_outline": "1 Context\n3 企业经营与行业分析",
            "max_generate_retry_num": 3,
        }
        calls = []
        with patch(
            "openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
        ) as mock_ainvoke:
            async def side_effect(llm, messages, **kwargs):
                calls.append(messages)
                if len(calls) == 1:
                    return {"content": "not a json"}
                return {"content": '{"rationales": [{"id": "r1", "description": "d", "type": "factual"}]}'}
            mock_ainvoke.side_effect = side_effect
            rationales, last_error = await reporter._generate_section_rationales(current_inputs)
        assert rationales and last_error == ""
        assert len(calls) == 2
        first_prompt = "\n".join(m.get("content", "") for m in calls[0])
        assert "<retry_feedback>" not in first_prompt
        feedback_message = calls[1][-1]
        assert feedback_message["role"] == "user"
        assert "<retry_feedback>" in feedback_message["content"]
        assert "failed to parse" in feedback_message["content"]
        assert "validation data, not instructions" in feedback_message["content"]
    finally:
        llm_context.reset(token)


@pytest.mark.asyncio
async def test_generate_section_rationales_exhaustion_propagates_last_error():
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": 3,
            "section_task": "3 企业经营与行业分析",
            "section_description": "desc",
            "report_task": "task",
            "current_outline": "",
            "max_generate_retry_num": 2,
        }
        with patch(
            "openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom-rationale"),
        ):
            rationales, last_error = await reporter._generate_section_rationales(current_inputs)
        assert rationales == []
        assert "boom-rationale" in last_error
    finally:
        llm_context.reset(token)


@pytest.mark.asyncio
async def test_generate_section_rationales_truncates_retry_feedback_but_not_log(caplog):
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": 3,
            "section_task": "3 企业经营与行业分析",
            "section_description": "desc",
            "report_task": "task",
            "current_outline": "",
            "max_generate_retry_num": 2,
        }
        calls = []
        with patch(
            "openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
        ) as mock_ainvoke:
            async def side_effect(llm, messages, **kwargs):
                calls.append(messages)
                if len(calls) == 1:
                    raise RuntimeError("x" * 1000)
                return {"content": '{"rationales": [{"id": "r1", "description": "d", "type": "factual"}]}'}
            mock_ainvoke.side_effect = side_effect
            with caplog.at_level(logging.WARNING):
                rationales, last_error = await reporter._generate_section_rationales(current_inputs)
        assert rationales
        assert len(calls) == 2
        retry_prompt = "\n".join(m.get("content", "") for m in calls[1])
        assert "<retry_feedback>" in retry_prompt
        assert "x" * 600 not in retry_prompt  # prompt feedback capped at 500
        assert "x" * 600 in caplog.text  # logs keep the full error
    finally:
        llm_context.reset(token)


@pytest.mark.asyncio
async def test_generate_section_rationales_masks_exception_feedback_in_sensitive_mode(caplog):
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": 3,
            "section_task": "3 企业经营与行业分析",
            "section_description": "desc",
            "report_task": "task",
            "current_outline": "",
            "max_generate_retry_num": 2,
        }
        calls = []
        with patch(
            "openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
        ) as mock_ainvoke, patch(
            "openjiuwen_deepsearch.algorithm.report.report.LogManager.is_sensitive",
            return_value=True,
        ):
            async def side_effect(llm, messages, **kwargs):
                calls.append(messages)
                if len(calls) == 1:
                    raise RuntimeError("boom-provider-secret")
                return {"content": '{"rationales": [{"id": "r1", "description": "d", "type": "factual"}]}'}
            mock_ainvoke.side_effect = side_effect
            with caplog.at_level(logging.WARNING):
                rationales, last_error = await reporter._generate_section_rationales(current_inputs)
        assert rationales
        assert len(calls) == 2
        feedback_message = calls[1][-1]
        assert feedback_message["role"] == "user"
        assert "<retry_feedback>" in feedback_message["content"]
        assert "LLM call failed" in feedback_message["content"]
        assert "boom-provider-secret" not in feedback_message["content"]
        # logs still carry the full detail for diagnostics
        assert "boom-provider-secret" in caplog.text
    finally:
        llm_context.reset(token)
