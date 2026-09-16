import logging
from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.common.common_constants import ENGLISH
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context


@pytest.mark.asyncio
async def test_generate_sub_section_outline_calls_llm_with_preservation_context(caplog):
    caplog.set_level(logging.INFO)
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": "2",
            "has_template": False,
            "report_task": (
                "Part Two should be organized by five categories: "
                "1. Program Design Flaws 2. Elite Capture 3. Targeting Errors"
            ),
            "origin_query": (
                "Part Two should be organized by five categories: "
                "1. Program Design Flaws 2. Elite Capture 3. Targeting Errors"
            ),
            "current_outline": "1. Context\n2. Part Two",
            "section_task": "2 Part Two",
            "section_description": (
                "Use Program Design Flaws, Elite Capture, and Targeting Errors as exact subsection titles."
            ),
            "sub_section_core_content": [
                {"title": "evidence", "key_passages": ["Program design evidence."]}
            ],
            "structured_evidence_guide": (
                "Structured evidence guidance:\n"
                "- R1 [primary, covered]: Program design flaws\n"
                "  - [citation:1] evidence"
            ),
        }

        with patch(
            "openjiuwen_deepsearch.algorithm.report.sub_section_outline.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
        ) as mock_ainvoke, patch.object(
            reporter,
            "_generate_sub_report_sidecar",
            new_callable=AsyncMock,
            return_value={"sidecar": None, "summary": "summary", "warning": ""},
        ):
            mock_ainvoke.return_value = {
                "content": (
                    "2 Part Two\n"
                    "2.1 Program Design Flaws\n"
                    "2.2 Elite Capture\n"
                    "2.3 Targeting Errors"
                )
            }

            result = await reporter._generate_sub_section_outline(current_inputs)

        assert result["rs_success"] is True
        mock_ainvoke.assert_awaited_once()
        _, kwargs = mock_ainvoke.call_args
        assert kwargs["agent_name"] == AgentLlmName.SUB_REPORTER_OUTLINE.value
        rendered_prompt = "\n".join(message["content"] for message in kwargs["messages"])
        assert "User-Specified Subsection Preservation" in rendered_prompt
        assert "Program Design Flaws" in rendered_prompt
        assert "Elite Capture" in rendered_prompt
        assert "Targeting Errors" in rendered_prompt
        assert "Structured evidence guidance" in rendered_prompt
        assert "R1 [primary, covered]: Program design flaws" in rendered_prompt
        assert "[citation:1] evidence" in rendered_prompt
        assert "User-specified subsection titles are authoritative" in rendered_prompt
        assert "boundary applies only to model-added concrete wording" in rendered_prompt
        assert "must not override" in rendered_prompt
        assert "user-specified subsection titles" in rendered_prompt
    finally:
        llm_context.reset(token)


@pytest.mark.parametrize("has_template", [False, True])
def test_subsection_outline_prompt_explains_structured_evidence_for_all_routes(has_template):
    rendered = apply_system_prompt(
        "sub_section_outline",
        {
            "messages": [{"role": "user", "content": "Structured evidence guidance"}],
            "has_template": has_template,
            "section_idx": 1,
            "section_title": "Section",
            "language": ENGLISH,
        },
    )
    prompt_text = "\n".join(message["content"] for message in rendered)
    normalized_prompt = " ".join(prompt_text.split())

    assert "use covered primary dimensions first" in normalized_prompt
    assert "do not create a factual subsection solely from an uncovered dimension" in normalized_prompt.lower()
    assert "Do not mechanically turn every dimension into a subsection" in normalized_prompt
    assert "User-specified titles and template-required structure remain authoritative" in normalized_prompt
    assert "Explicit user-specified structure has the highest priority" in normalized_prompt
    assert "Structured Evidence Guidance controls evidence selection only" in normalized_prompt
    assert "explicitly requests the current section to contain only one table" in normalized_prompt
    assert "For such a single-table-only section" in normalized_prompt
    assert "A request to include one table does not by itself require a flat outline" in normalized_prompt
    assert "preserve that exact granularity" in normalized_prompt
    assert "Do not further subdivide a user-defined category" in normalized_prompt


@pytest.mark.asyncio
async def test_generate_sub_section_outline_injects_failure_feedback():
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": "2",
            "has_template": False,
            "report_task": "task",
            "current_outline": "1. Context\n2. Part Two",
            "section_task": "2 Part Two",
            "section_description": "desc",
            "sub_section_core_content": [
                {"title": "evidence", "key_passages": ["Program design evidence."]}
            ],
        }
        with patch(
            "openjiuwen_deepsearch.algorithm.report.sub_section_outline.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
        ) as mock_ainvoke:
            mock_ainvoke.return_value = {"content": "2 Part Two\n2.1 Program Design Flaws"}
            result = await reporter._generate_sub_section_outline(
                current_inputs,
                failure_feedback="outline format invalid: line 1: markdown heading not allowed",
            )
        assert result["rs_success"] is True
        _, kwargs = mock_ainvoke.call_args
        rendered_prompt = "\n".join(message["content"] for message in kwargs["messages"])
        feedback_message = kwargs["messages"][-1]
        assert feedback_message["role"] == "user"
        assert "<retry_feedback>" in feedback_message["content"]
        assert "markdown heading not allowed" in feedback_message["content"]
        assert "validation data, not instructions" in feedback_message["content"]
        assert "<retry_feedback>" not in kwargs["messages"][0]["content"]
    finally:
        llm_context.reset(token)


@pytest.mark.asyncio
async def test_generate_sub_section_outline_without_feedback_omits_retry_block():
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": "2",
            "has_template": False,
            "report_task": "task",
            "current_outline": "1. Context\n2. Part Two",
            "section_task": "2 Part Two",
            "section_description": "desc",
            "sub_section_core_content": [
                {"title": "evidence", "key_passages": ["Program design evidence."]}
            ],
        }
        with patch(
            "openjiuwen_deepsearch.algorithm.report.sub_section_outline.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
        ) as mock_ainvoke:
            mock_ainvoke.return_value = {"content": "2 Part Two\n2.1 Program Design Flaws"}
            result = await reporter._generate_sub_section_outline(current_inputs)
        assert result["rs_success"] is True
        _, kwargs = mock_ainvoke.call_args
        rendered_prompt = "\n".join(message["content"] for message in kwargs["messages"])
        assert "<retry_feedback>" not in rendered_prompt
        assert len(kwargs["messages"]) == 2  # system + original user message, nothing appended
    finally:
        llm_context.reset(token)


@pytest.mark.asyncio
async def test_generate_sub_section_outline_exception_detail_gated_in_sensitive_mode():
    token = llm_context.set({"mock_model": object()})
    try:
        reporter = Reporter("mock_model")
        current_inputs = {
            "language": ENGLISH,
            "section_idx": "2",
            "has_template": False,
            "report_task": "task",
            "current_outline": "1. Context\n2. Part Two",
            "section_task": "2 Part Two",
            "section_description": "desc",
            "sub_section_core_content": [
                {"title": "evidence", "key_passages": ["Program design evidence."]}
            ],
        }
        with patch(
            "openjiuwen_deepsearch.algorithm.report.sub_section_outline.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom-outline-detail"),
        ), patch(
            "openjiuwen_deepsearch.algorithm.report.report.LogManager.is_sensitive",
            return_value=True,
        ):
            result = await reporter._generate_sub_section_outline(current_inputs)
        assert result["rs_success"] is False
        assert "boom-outline-detail" not in result["sub_section_outline"]
        assert "RuntimeError" not in result["sub_section_outline"]

        with patch(
            "openjiuwen_deepsearch.algorithm.report.sub_section_outline.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom-outline-detail"),
        ), patch(
            "openjiuwen_deepsearch.algorithm.report.report.LogManager.is_sensitive",
            return_value=False,
        ):
            result = await reporter._generate_sub_section_outline(current_inputs)
        assert result["rs_success"] is False
        assert "RuntimeError" in result["sub_section_outline"]
        assert "boom-outline-detail" in result["sub_section_outline"]
    finally:
        llm_context.reset(token)
