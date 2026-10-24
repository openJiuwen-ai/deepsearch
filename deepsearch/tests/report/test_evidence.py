import logging
import json
from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report import (
    Reporter,
    ensure_exact_target_documents,
)
from openjiuwen_deepsearch.common.common_constants import ENGLISH
from openjiuwen_deepsearch.config.config import ServiceConfig
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context


def _report_doc(idx: int, *, url: str | None = None, content: str | None = None) -> dict:
    return {
        "title": f"doc-{idx}",
        "url": url or f"https://example.com/{idx}",
        "original_content": content or f"content-{idx}",
        "key_passages": [f"passage-{idx}"],
        "scores": {"relevance": 9, "answerability": 9, "authority": 9, "data_density": 9},
    }


def test_requirement_mapping_and_adaptive_rationale_config_defaults_to_disabled():
    assert ServiceConfig().professional_report_adaptive_rationales is False


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
            "section_description": "Compare regional outcomes across historical periods and explain the differences",
            "report_task": "task",
            "original_query": "Compare the topic across multiple regions and time periods",
            "section_format_requirements": ["include a comparison table"],
            "professional_report_adaptive_rationales": True,
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
                    return {"content": "[]"}
                if len(calls) == 2:
                    return {"content": '{"rationales": "invalid"}'}
                requirements = [{"id": f"q{i}", "description": f"Requirement {i}"} for i in range(1, 11)]
                rationales = [
                    {"id": f"r{i}", "description": f"Evidence dimension {i}", "type": "qualitative",
                     "priority": "primary", "covers": [f"q{i}"]}
                    for i in range(1, 11)
                ]
                return {"content": json.dumps({"requirements": requirements, "rationales": rationales})}
                # Valid payload is constructed above with a full checklist.
            mock_ainvoke.side_effect = side_effect
            rationales, last_error = await reporter._generate_section_rationales(current_inputs)
        assert rationales and last_error == ""
        assert len(calls) == 3
        assert all([m["role"] for m in attempt] == ["system", "user"] for attempt in calls)
        assert calls[0][0] == calls[1][0] == calls[2][0]
        assert all("企业经营与行业分析" in attempt[-1]["content"] for attempt in calls)
        assert "Task complexity assessment: medium" in calls[0][-1]["content"]
        assert "Generate between 10 and 15 rationales" in calls[0][-1]["content"]
        assert "Section format requirements:" in calls[0][-1]["content"]
        assert "include a comparison table" in calls[0][-1]["content"]
        assert "not a JSON object" in calls[1][-1]["content"]
        assert "not a JSON object" not in calls[2][-1]["content"]
        assert "not a list" in calls[2][-1]["content"]
        assert '{"rationales": "invalid"}' not in calls[2][-1]["content"]
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
async def test_generate_section_rationales_uses_legacy_flow_when_feature_flag_is_off():
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "section_idx": 1,
            "section_task": "A narrowly scoped chapter",
            "section_description": "Describe one topic",
            "report_task": "task",
        }
        legacy_output = {
            "rationales": [
                {"id": "source-r1", "description": "One evidence dimension", "priority": "primary"}
            ]
        }
        with patch(
            "openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
            return_value={"content": json.dumps(legacy_output)},
        ) as mock_ainvoke:
            rationales, error = await reporter._generate_section_rationales(current_inputs)

        assert error == ""
        assert rationales == [
            {"id": "r1", "description": "One evidence dimension", "priority": "primary"}
        ]
        assert "rationale_requirements" not in current_inputs
        assert "rationale_coverage_diagnostics" not in current_inputs
        prompt_messages = mock_ainvoke.await_args.kwargs["messages"]
        assert "Original user query:" not in prompt_messages[-1]["content"]
        assert "Task complexity assessment:" not in prompt_messages[-1]["content"]
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
async def test_generate_section_rationales_network_retry_reuses_request_and_keeps_error_in_log(caplog):
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
        assert calls[1] is calls[0]
        assert "<retry_feedback>" not in retry_prompt
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
        assert calls[1] is calls[0]
        assert "<retry_feedback>" not in feedback_message["content"]
        assert "LLM call failed" not in feedback_message["content"]
        assert "boom-provider-secret" not in feedback_message["content"]
        # logs still carry the full detail for diagnostics
        assert "boom-provider-secret" in caplog.text
    finally:
        llm_context.reset(token)
