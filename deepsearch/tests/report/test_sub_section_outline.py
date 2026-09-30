import logging
from datetime import date
from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.algorithm.report.sub_section_outline import (
    _validate_outline_years,
    _validate_user_time_constraints,
)
from openjiuwen_deepsearch.common.common_constants import ENGLISH
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TemporalScope,
    build_time_contract,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context


def _outline_evidence_cards():
    return [{
        "citation_id": 1,
        "evidence_type": "passage",
        "source_title": "evidence",
        "publish_time": "2024-01-01",
        "content_time": {"start": "2023-01-01", "end": "2023-12-31"},
        "rationale_refs": [{"rationale_id": "r1", "coverage_score": 0.9}],
        "passage_text": "Program design evidence.",
    }]


def test_outline_year_validation_uses_content_time_contract():
    contract = {
        "content_date_scope": {"start": "2021-01-01", "end": "2023-12-31"}
    }

    assert _validate_outline_years("2.1 2023 implementation", contract) == (True, "")
    valid, feedback = _validate_outline_years("2.1 2024 implementation", contract)
    assert not valid
    assert "2024" in feedback
    assert _validate_outline_years("2100 outlook", {"content_date_scope": {"end": "2099-12-31"}})[0] is False
    assert _validate_outline_years("2023", {"content_date_scope": {"start": "invalid"}}) == (True, "")


def test_outline_year_validation_rejects_full_year_broader_than_partial_scope():
    contract = {
        "content_date_scope": {"start": "2020-06-01", "end": "2021-06-30"}
    }

    valid, feedback = _validate_outline_years("1. 2020–2021 overview", contract)

    assert not valid
    assert "2020–2021" in feedback


@pytest.mark.parametrize("outline", [
    "1. 2020-21 overview",
    "1. 2020/21 overview",
])
def test_outline_year_validation_rejects_abbreviated_year_range_outside_scope(outline):
    contract = {
        "content_date_scope": {"start": "2020-01-01", "end": "2020-12-31"}
    }

    valid, feedback = _validate_outline_years(outline, contract)

    assert not valid
    assert "2020" in feedback


def test_outline_year_validation_accepts_abbreviated_range_crossing_century():
    contract = {
        "content_date_scope": {"start": "1999-01-01", "end": "2000-12-31"}
    }

    assert _validate_outline_years("1999-00 overview", contract) == (True, "")


def test_outline_year_validation_accepts_date_range_matching_partial_scope():
    contract = {
        "content_date_scope": {"start": "2020-06-01", "end": "2021-06-30"}
    }

    assert _validate_outline_years(
        "1. 2020-06-01 to 2021-06-30 events", contract
    ) == (True, "")


@pytest.mark.parametrize("separator", ["-", "/", "–", "—", "－", "至", "到", "~", "～", "to"])
def test_abbreviated_year_range_requires_both_years_within_scope(separator):
    title = f"2020{separator}21 overview"
    assert not _validate_outline_years(title, {
        "content_date_scope": {"start": "2020-01-01", "end": "2020-12-31"}
    })[0]
    assert _validate_outline_years(title, {
        "content_date_scope": {"start": "2020-01-01", "end": "2021-12-31"}
    }) == (True, "")


@pytest.mark.parametrize("title", ["2020-06 review", "2020/06 review"])
def test_numeric_month_is_checked_as_a_calendar_month(title):
    assert _validate_outline_years(title, {
        "content_date_scope": {"start": "2020-06-01", "end": "2020-06-30"}
    }) == (True, "")
    assert not _validate_outline_years(title, {
        "content_date_scope": {"start": "2020-07-01", "end": "2020-12-31"}
    })[0]
    assert not _validate_outline_years(title, {
        "content_date_scope": {"start": "2020-06-15", "end": "2020-06-30"}
    })[0]


@pytest.mark.parametrize("title", [
    "ISO 9001 quality management", "ISO/IEC 27001:2013 controls", "RFC 9110 protocol",
    "RTX 4090 performance", "GTX1080 performance", "600519 company review",
    "对ISO9001质量管理的分析", "分析RTX4090性能",
])
def test_identifiers_do_not_create_false_user_time_conflicts(title):
    contract = {"content_date_scope": {"start": "2020-01-01", "end": "2025-12-31"}}
    assert _validate_user_time_constraints({"section_task": title}, contract) == (True, "")
    # Excluding an identifier must not mask an actual out-of-scope year nearby.
    assert not _validate_user_time_constraints({"section_task": title + " in 2026"}, contract)[0]


@pytest.mark.parametrize(
    ("outline", "expected"),
    [
        ("1. 2020年6月政策进展", True),
        ("1. 2020年6月15日政策进展", True),
        ("1. 2020年5月政策进展", False),
        ("1. 2020年6月1日至2020年6月30日", True),
        ("1. 2020年7月1日至2020年6月30日", False),
    ],
)
def test_outline_year_validation_parses_chinese_month_and_date_ranges(outline, expected):
    contract = {
        "content_date_scope": {"start": "2020-06-01", "end": "2020-06-30"}
    }

    valid, _ = _validate_outline_years(outline, contract)

    assert valid is expected


@pytest.mark.parametrize(
    "outline",
    [
        "1. 2022-01-01 to 2020-01-01",
        "1. 2022–2020 overview",
    ],
)
def test_outline_year_validation_rejects_reversed_ranges(outline):
    contract = {
        "content_date_scope": {"start": "2019-01-01", "end": "2023-12-31"}
    }

    valid, _ = _validate_outline_years(outline, contract)

    assert not valid


def test_outline_year_validation_handles_year_zero_without_raising():
    contract = {
        "content_date_scope": {"start": "0001-01-01", "end": "9999-12-31"}
    }

    valid, feedback = _validate_outline_years("1. 0000 outlook", contract)

    assert not valid
    assert "0000" in feedback


def test_time_contract_contains_only_machine_readable_date_scopes():
    contract = build_time_contract(ResearchIntent(
        source_date_scope=TemporalScope(
            constraint_type="source_date", start_date=date(2020, 1, 1), end_date=date(2021, 12, 31),
        ),
        content_date_scope=TemporalScope(
            constraint_type="content_date", start_date=date(2018, 1, 1), end_date=date(2019, 12, 31),
        ),
    ))

    assert contract == {
        "source_date_scope": {"start": "2020-01-01", "end": "2021-12-31"},
        "content_date_scope": {"start": "2018-01-01", "end": "2019-12-31"},
    }


def test_outline_system_prompt_states_fixed_title_time_rules():
    messages = build_prompt_messages("sub_section_outline", {
        "section_idx": 1,
        "section_title": "Topic",
        "report_task": "Report",
        "core_context": "[]",
        "time_contract_json": "{}",
        "rationale_contract_json": "{}",
    })
    system_text = messages[0]["content"]

    assert "broader than `content_date_scope`" in system_text
    assert "corresponding known `content_time`" in system_text
    assert "unknown `content_time`" in system_text


def test_user_time_conflict_is_detected_before_outline_retry():
    ok, feedback = _validate_user_time_constraints(
        {"section_task": "1 Review of 2024"},
        {"content_date_scope": {"end": "2023-12-31"}},
    )

    assert not ok
    assert "user-specified time conflicts" in feedback


def test_out_of_scope_year_mentioned_as_exclusion_in_description_is_not_conflict():
    ok, feedback = _validate_user_time_constraints(
        {
            "section_task": "Market outlook",
            "section_description": "Exclude 2024 figures; focus on 2023 and earlier.",
        },
        {"content_date_scope": {"start": "2020-01-01", "end": "2023-12-31"}},
    )

    assert ok
    assert feedback == ""


def test_unrelated_global_outline_year_does_not_block_current_section():
    ok, feedback = _validate_user_time_constraints(
        {
            "section_task": "2 Current topic",
            "section_description": "Analyze the current topic",
            "current_outline": "1 Historical context 2018\n2 Current topic",
        },
        {"content_date_scope": {"start": "2020-01-01", "end": "2023-12-31"}},
    )

    assert ok
    assert feedback == ""


@pytest.mark.asyncio
async def test_exclusion_year_in_description_still_allows_outline_generation():
    reporter = Reporter.__new__(Reporter)
    inputs = {
        "section_idx": 1,
        "section_task": "1 Market outlook",
        "section_description": "Exclude 2024 figures; focus on 2023 and earlier.",
        "time_contract": {
            "content_date_scope": {"start": "2020-01-01", "end": "2023-12-31"}
        },
    }
    generated_outline = {"rs_success": True, "sub_section_outline": "1 Market outlook"}
    with (
        patch.object(
            reporter,
            "_generate_sub_section_outline",
            new=AsyncMock(return_value=generated_outline),
        ) as generate_outline,
        patch.object(reporter, "check_chapter_format", return_value=(True, "")),
    ):
        success, error = await reporter._generate_outline_with_retry(inputs, 1, 1)

    assert success
    assert error == ""
    assert inputs["sub_section_outline"] == "1 Market outlook"
    generate_outline.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_time_conflict_stops_before_requesting_an_outline():
    reporter = Reporter.__new__(Reporter)
    inputs = {
        "section_idx": 1,
        "section_task": "1 Review of 2024",
        "outline_evidence_cards": _outline_evidence_cards(),
        "time_contract": {"content_date_scope": {"end": "2023-12-31"}},
    }
    with patch.object(
        reporter, "_generate_sub_section_outline", new_callable=AsyncMock
    ) as generate_outline:
        success, error = await reporter._generate_outline_with_retry(inputs, 1, 2)

    assert not success
    assert "user-specified time conflicts" in error
    generate_outline.assert_not_awaited()


@pytest.mark.asyncio
async def test_generated_out_of_scope_year_retries_then_accepts_valid_outline():
    reporter = Reporter.__new__(Reporter)
    inputs = {
        "section_idx": 1,
        "section_task": "1 Topic",
        "outline_evidence_cards": _outline_evidence_cards(),
        "time_contract": {"content_date_scope": {"start": "2021-01-01", "end": "2023-12-31"}},
    }
    with patch.object(
        reporter,
        "_generate_sub_section_outline",
        new_callable=AsyncMock,
        side_effect=[
            {"rs_success": True, "sub_section_outline": "1 Topic\\n1.1 2024 events"},
            {"rs_success": True, "sub_section_outline": "1 Topic\\n1.1 2023 events"},
        ],
    ) as generate_outline:
        success, error = await reporter._generate_outline_with_retry(inputs, 1, 2)

    assert success
    assert error == ""
    assert inputs["sub_section_outline"] == "1 Topic\\n1.1 2023 events"
    assert "outside content_date_scope" in generate_outline.await_args_list[1].args[1]


@pytest.mark.asyncio
async def test_outline_network_retry_reuses_current_request():
    reporter = Reporter.__new__(Reporter)
    reporter._llm = object()
    inputs = {"section_idx": 1, "section_task": "1 Topic", "outline_evidence_cards": _outline_evidence_cards()}
    with patch(
        "openjiuwen_deepsearch.algorithm.report.sub_section_outline.ainvoke_llm_with_stats",
        new=AsyncMock(side_effect=[RuntimeError("network error"), {"content": "1 Topic"}]),
    ) as invoke:
        success, _ = await reporter._generate_outline_with_retry(inputs, 1, 2)
    assert success
    first, second = [call.kwargs["messages"] for call in invoke.await_args_list]
    assert first is second
    assert "network error" not in second[-1]["content"]


@pytest.mark.parametrize("network_first", [False, True])
@pytest.mark.asyncio
async def test_outline_empty_response_rebuilds_request_with_feedback(network_first):
    reporter = Reporter.__new__(Reporter)
    reporter._llm = object()
    inputs = {"section_idx": 1, "section_task": "1 Topic", "outline_evidence_cards": _outline_evidence_cards()}
    responses = [{"content": ""}, {"content": "1 Topic"}]
    if network_first:
        responses.insert(0, RuntimeError("network error"))
    with patch(
        "openjiuwen_deepsearch.algorithm.report.sub_section_outline.ainvoke_llm_with_stats",
        new=AsyncMock(side_effect=responses),
    ) as invoke:
        success, _ = await reporter._generate_outline_with_retry(inputs, 1, len(responses))
    assert success
    requests = [call.kwargs["messages"] for call in invoke.await_args_list]
    if network_first:
        assert requests[0] is requests[1]
    assert requests[-1] is not requests[-2]
    assert "<retry_feedback>" in requests[-1][-1]["content"]
    assert "LLM returned empty content" in requests[-1][-1]["content"]
    assert "network error" not in requests[-1][-1]["content"]


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
            "outline_evidence_cards": _outline_evidence_cards(),
            "rationale_contract": {"items": [{"rationale_id": "r1", "coverage_status": "covered"}]},
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
        assert "# Outline Evidence Cards" in rendered_prompt
        assert "Program design evidence." in rendered_prompt
        assert '"rationale_id": "r1"' in rendered_prompt
        assert "User-specified subsection titles are authoritative" in rendered_prompt
        assert "boundary applies only to model-added concrete wording" in rendered_prompt
        assert "must not override" in rendered_prompt
        assert "user-specified subsection titles" in rendered_prompt
    finally:
        llm_context.reset(token)


@pytest.mark.parametrize("has_template", [False, True])
def test_subsection_outline_prompt_explains_structured_evidence_for_all_routes(has_template):
    rendered = build_prompt_messages(
        "sub_section_outline",
        {
            "structured_evidence_guide": "Structured evidence guidance",
            "has_template": has_template,
            "section_idx": 1,
            "section_title": "Section",
            "language": ENGLISH,
        },
    )
    prompt_text = "\n".join(message["content"] for message in rendered)
    normalized_prompt = " ".join(prompt_text.split())

    assert "Prefer covered primary dimensions" in normalized_prompt
    assert "coverage_status" in normalized_prompt
    assert "`weak` means keep any model-added heading general" in normalized_prompt
    assert "`uncovered` means the dimension cannot justify a factual model-added heading" in normalized_prompt
    assert "but do not mechanically turn every dimension into a subsection" in normalized_prompt
    assert "User-specified titles and template-required structure remain authoritative" in normalized_prompt
    assert "Explicit user-specified structure has the highest priority" in normalized_prompt
    assert "The Rationale Contract controls evidence selection only" in normalized_prompt
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
            "outline_evidence_cards": _outline_evidence_cards(),
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
            "outline_evidence_cards": _outline_evidence_cards(),
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
            "outline_evidence_cards": _outline_evidence_cards(),
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
