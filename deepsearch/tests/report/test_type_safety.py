"""Tests for LLM output type-safety guards in the report pipeline.

Covers edge cases where LLM returns malformed types (non-dict JSON,
string numbers, None passages, non-list containers, etc.) that would
cause TypeError/AttributeError crashes without the defensive guards
added in evidence.py, report_rationale_fulltext.py, compact_doc_info.py,
report_utils.py, and report.py.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen_deepsearch.algorithm.report.evidence import _normalize_rationales
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.algorithm.report.report_rationale_fulltext import (
    filter_passages_by_coverage,
    dedup_passages_by_rationale,
)
from openjiuwen_deepsearch.algorithm.report.compact_doc_info import (
    build_structured_evidence_guide,
)
from openjiuwen_deepsearch.algorithm.report.report_utils import (
    XYChartMermaidGenerator,
    PieChartMermaidGenerator,
    TimelineChartMermaidGenerator,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context


# ---------- helpers ----------

def _make_reporter():
    with patch.object(Reporter, "__init__", lambda self, name: None):
        reporter = Reporter.__new__(Reporter)
        reporter._llm = MagicMock()
        return reporter


def _doc(idx, title=None, url=None):
    return {
        "doc_title": title or f"doc-{idx}",
        "doc_url": url or f"https://example.com/{idx}",
        "passage_text": f"passage-{idx}",
    }


def _rationale(rid, desc):
    return {"id": rid, "description": desc, "type": "factual"}


def _raw_doc(idx, content=None):
    return {
        "doc_id": f"doc-{idx}",
        "title": f"article-{idx}",
        "url": f"https://example.com/{idx}",
        "source": "example.com",
        "publish_time": "2025-01-01",
        "doc_time": "2025-01-01",
        "original_content": content or f"This is the content of article {idx}. " * 20,
    }


# =====================================================================
# _normalize_rationales — non-dict elements, non-str descriptions
# =====================================================================

def test_normalize_rationales_skips_non_dict_elements():
    """Non-dict rationale elements (int, str, None) are filtered out, not passed through."""
    rationales = [
        {"id": "r1", "description": "valid", "type": "factual"},
        42,
        "not a dict",
        None,
        {"id": "r2", "description": "also valid", "type": "factual"},
    ]
    result = _normalize_rationales(rationales, max_rationales=15)
    # Non-dict elements must be removed, not kept in the list
    assert all(isinstance(r, dict) for r in result)
    assert len(result) == 2
    # IDs are renumbered sequentially (r1, r2 — no gaps from removed elements)
    ids = [r.get("id") for r in result]
    assert ids == ["r1", "r2"]


def test_normalize_rationales_handles_non_str_description():
    """Non-str description (int, None) is coerced to str, not crashed on."""
    rationales = [
        {"id": "r1", "description": 12345, "type": "factual"},
        {"id": "r2", "description": None, "type": "factual"},
    ]
    result = _normalize_rationales(rationales, max_rationales=15)
    assert all(isinstance(r["description"], str) for r in result)


def test_normalize_rationales_priority_filter_ignores_non_dict():
    """Priority filtering skips non-dict elements without AttributeError."""
    rationales = [
        {"id": "r1", "description": "primary one", "priority": "primary"},
        "garbage",
        {"id": "r2", "description": "extra", "priority": "supplementary"},
    ]
    # max_rationales=1 forces priority filtering path
    result = _normalize_rationales(rationales, max_rationales=1)
    assert len(result) == 1
    assert result[0]["id"] == "r1"


@pytest.mark.asyncio
async def test_generate_rationales_non_list_field():
    """LLM returning rationales as a non-list (string) triggers retry, not silent empty."""
    reporter = _make_reporter()
    mock_llm_result = MagicMock()
    # rationales is a string, not a list
    mock_llm_result.get.return_value = '{"rationales": "not a list"}'

    with patch("openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.evidence.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            rationales, error = await reporter._generate_section_rationales(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
            )

    # Should trigger ValueError -> retry -> fail after max retries, not silent empty success
    assert rationales == []
    assert error != ""  # error message should be set


# =====================================================================
# _select_by_rationale_coverage — string scores, non-dict rationales

def test_select_by_rationale_coverage_string_scores():
    """String-type coverage scores in coverage_matrix are handled via safe_float."""
    reporter = _make_reporter()
    docs = [_doc(0, "high"), _doc(1, "low")]
    rationales = [_rationale("r1", "test")]
    coverage_result = {
        "filtered_passages": docs,
        "coverage_matrix": {
            "passage_0": {"r1": "0.9"},  # string instead of float
            "passage_1": {"r1": "0.1"},
        },
    }

    selected, stats = Reporter._select_by_rationale_coverage(
        docs, rationales, coverage_result, top_k=2,
    )
    assert len(selected) >= 1
    # High-coverage doc should be first
    assert selected[0]["doc_title"] == "high"


def test_select_by_rationale_coverage_bad_string_scores():
    """Non-numeric string scores (e.g. 'high') are safely converted to 0.0."""
    reporter = _make_reporter()
    docs = [_doc(0, "bad"), _doc(1, "good")]
    rationales = [_rationale("r1", "test")]
    coverage_result = {
        "filtered_passages": docs,
        "coverage_matrix": {
            "passage_0": {"r1": "not_a_number"},  # safe_float → 0.0
            "passage_1": {"r1": 0.8},
        },
    }

    selected, stats = Reporter._select_by_rationale_coverage(
        docs, rationales, coverage_result, top_k=2,
    )
    assert len(selected) >= 1


def test_select_by_rationale_coverage_non_dict_rationale_elements():
    """Non-dict rationale elements are filtered, not crashed on."""
    docs = [_doc(0, "test")]
    rationales = [
        {"id": "r1", "description": "valid"},
        42,  # non-dict element
        "garbage",
    ]
    coverage_result = {
        "filtered_passages": docs,
        "coverage_matrix": {"passage_0": {"r1": 0.9}},
    }

    selected, stats = Reporter._select_by_rationale_coverage(
        docs, rationales, coverage_result, top_k=1,
    )
    assert len(selected) == 1


# =====================================================================
# _extract_and_score_documents — non-list documents/passages, None passages
# =====================================================================

@pytest.mark.asyncio
async def test_extract_and_score_non_list_documents():
    """LLM returning documents as a dict (not list) doesn't crash."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, content="test content")]

    mock_llm_result = MagicMock()
    # documents is a dict, not a list
    mock_llm_result.get.return_value = '{"documents": {"key": "value"}}'

    with patch("openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.evidence.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    # Should not crash; degraded path returns empty coverage_matrix
    assert result["coverage_matrix"] == {}


@pytest.mark.asyncio
async def test_extract_and_score_non_list_passages():
    """LLM returning passages as None for a doc doesn't crash the batch."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, content="test content")]

    mock_llm_result = MagicMock()
    # passages is null (key exists, value is None)
    mock_llm_result.get.return_value = '{"documents": [{"doc_index": 0, "passages": null}]}'

    with patch("openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.evidence.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    # Should not crash; no passages extracted
    assert result["coverage_matrix"] == {}


@pytest.mark.asyncio
async def test_extract_and_score_non_dict_doc_result():
    """LLM returning a non-dict doc_result (int) is skipped, not crashed on."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, content="test content")]

    mock_llm_result = MagicMock()
    # documents contains a non-dict element (int)
    mock_llm_result.get.return_value = '{"documents": [42, {"doc_index": 0, "passages": [{"text": "valid", "rationale_ids": ["r1"], "scores": {"r1": {"coverage": 0.9}}}]}]}'

    with patch("openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.evidence.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    assert error == ""
    # The valid doc's passage should be extracted
    assert "passage_0" in result["coverage_matrix"]


@pytest.mark.asyncio
async def test_extract_and_score_llm_returns_json_array():
    """LLM returning a JSON array (not object) triggers retry, not crash."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, content="test content")]

    mock_llm_result = MagicMock()
    # LLM returns a JSON array instead of an object
    mock_llm_result.get.return_value = '[1, 2, 3]'

    with patch("openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.evidence.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    # Should degrade gracefully
    assert result["coverage_matrix"] == {}


# =====================================================================
# filter_passages_by_coverage — non-dict coverage_result/coverage_matrix
# =====================================================================

def test_filter_passages_by_coverage_non_dict_coverage_result():
    """coverage_result as a list (not dict) returns original passages."""
    passages = [_doc(0, "test")]
    result = filter_passages_by_coverage(passages, [_rationale("r1", "test")], [1, 2, 3])
    assert result == passages


def test_filter_passages_by_coverage_non_dict_coverage_matrix():
    """coverage_matrix as a list (not dict) returns original passages."""
    passages = [_doc(0, "test")]
    coverage_result = {"coverage_matrix": ["not", "a", "dict"], "filtered_passages": passages}
    result = filter_passages_by_coverage(passages, [_rationale("r1", "test")], coverage_result)
    assert result == passages


def test_filter_passages_by_coverage_string_scores():
    """String-type scores in coverage_matrix are converted via safe_float."""
    passages = [_doc(0, "high"), _doc(1, "low")]
    coverage_result = {
        "filtered_passages": passages,
        "coverage_matrix": {
            "passage_0": {"r1": "0.9"},
            "passage_1": {"r1": "0.1"},
        },
    }
    result = filter_passages_by_coverage(passages, [_rationale("r1", "test")], coverage_result)
    # High-coverage passage should be kept
    assert len(result) >= 1
    assert result[0]["doc_title"] == "high"


# =====================================================================
# dedup_passages_by_rationale — non-dict coverage_matrix
# =====================================================================

def test_dedup_passages_non_dict_coverage_matrix():
    """dedup with coverage_matrix as a list doesn't crash."""
    passages = [
        {"_passage_key": "passage_0", "doc_title": "a", "passage_text": "content a"},
        {"_passage_key": "passage_1", "doc_title": "b", "passage_text": "content b"},
    ]
    coverage_result = {"coverage_matrix": ["list", "not", "dict"], "filtered_passages": passages}
    # Should not crash; dedup proceeds with empty coverage_matrix
    result = dedup_passages_by_rationale(passages, [_rationale("r1", "test")], coverage_result)
    assert len(result) >= 1


# =====================================================================
# build_structured_evidence_guide — non-dict coverage_matrix
# =====================================================================

def test_build_structured_evidence_guide_non_dict_coverage_matrix():
    """Non-dict coverage_matrix returns empty string, not crash."""
    passage = {"index": 1, "doc_title": "test", "key_passages": ["text"]}
    rationales = [_rationale("r1", "test")]
    coverage_result = {"coverage_matrix": ["list", "not", "dict"], "filtered_passages": [passage]}

    guide = build_structured_evidence_guide(
        [passage], rationales, coverage_result,
        selected_passage_keys=["passage_0"],
    )
    assert guide == ""


# =====================================================================
# Chart generators — non-dict JSON, non-str unit, non-list records
# =====================================================================

def test_xy_chart_generator_non_dict_json():
    """XYChart with JSON array (not object) raises ValueError, not AttributeError."""
    with pytest.raises(ValueError, match="bar/line chart"):
        XYChartMermaidGenerator.generate_from_json("[1, 2, 3]")


def test_xy_chart_generator_non_str_unit():
    """XYChart with non-str unit (int) doesn't crash on .strip()."""
    data = {"image_type": "bar", "unit": 123, "records": [["a", 10], ["b", 20]]}
    # Should not crash; unit is coerced to str
    result = XYChartMermaidGenerator.generate_from_json(json.dumps(data))
    assert "xychart-beta" in result


def test_xy_chart_generator_non_list_records():
    """XYChart with non-list records (int) raises ValueError, not TypeError."""
    data = {"image_type": "bar", "unit": "个", "records": 42}
    with pytest.raises(ValueError, match="records are required"):
        XYChartMermaidGenerator.generate_from_json(json.dumps(data))


def test_pie_chart_generator_non_dict_json():
    """PieChart with JSON array raises ValueError, not AttributeError."""
    with pytest.raises(ValueError, match="pie chart"):
        PieChartMermaidGenerator.generate_from_json('"not a dict"')


def test_pie_chart_generator_non_str_unit():
    """PieChart with non-str unit (int) doesn't crash on .strip()."""
    data = {"image_type": "pie", "unit": 100, "records": [["a", 10], ["b", 20]]}
    result = PieChartMermaidGenerator.generate_from_json(json.dumps(data))
    assert "pie" in result


def test_timeline_chart_generator_non_dict_json():
    """Timeline with JSON scalar raises ValueError, not AttributeError."""
    with pytest.raises(ValueError, match="timeline"):
        TimelineChartMermaidGenerator.generate_from_json('42')


# =====================================================================
# _compute_y_range — string values in list
# =====================================================================

def test_compute_y_range_with_string_values():
    """String numbers in values list are converted via safe_float, no crash."""
    vmin, vmax = XYChartMermaidGenerator._compute_y_range(["10.5", "20.3", "15.0"], "bar")
    # Function applies nice-number padding; just verify no crash and vmin <= vmax
    assert vmin <= vmax
    assert isinstance(vmin, float)
    assert isinstance(vmax, float)


def test_compute_y_range_with_mixed_types():
    """Mixed str/float/bad values don't crash; bad_value → 0.0 via safe_float."""
    vmin, vmax = XYChartMermaidGenerator._compute_y_range(["10.5", 20.3, "bad_value"], "bar")
    assert vmin <= vmax
    assert isinstance(vmin, (int, float))
    assert isinstance(vmax, (int, float))


def test_compute_y_range_empty_list():
    """Empty values list returns default range."""
    vmin, vmax = XYChartMermaidGenerator._compute_y_range([], "bar")
    assert vmin == 0.0
    assert vmax == 1.0


# =====================================================================
# report.py — non-int max_generate_retry_num
# =====================================================================

@pytest.mark.asyncio
async def test_generate_sub_report_string_retry_num():
    """Non-int max_generate_retry_num (string, None, 0, non-numeric) is handled
    without crash, exercising the actual guard at report.py:499."""
    reporter = _make_reporter()
    captured = []

    async def _mock_outline(self, inputs, section_idx, max_attempt_num):
        captured.append(max_attempt_num)
        return False, "test error"

    bg = [{"text": "background knowledge content"}]
    with patch.object(Reporter, "_get_background_knowledge_contents",
                      return_value=bg):
        with patch.object(Reporter, "_generate_outline_with_retry", new=_mock_outline):
            # String "3" → int("3") = 3
            await reporter.generate_sub_report(
                {"section_idx": 1, "max_generate_retry_num": "3",
                 "sub_report_background_knowledge": bg})
            assert captured[-1] == 3

            # None → default 3
            await reporter.generate_sub_report(
                {"section_idx": 2, "max_generate_retry_num": None,
                 "sub_report_background_knowledge": bg})
            assert captured[-1] == 3

            # 0 → max(int(0), 1) = 1 (not 3, since `or` is no longer used)
            await reporter.generate_sub_report(
                {"section_idx": 3, "max_generate_retry_num": 0,
                 "sub_report_background_knowledge": bg})
            assert captured[-1] == 1

            # Non-numeric string "abc" → ValueError → fallback 3
            await reporter.generate_sub_report(
                {"section_idx": 4, "max_generate_retry_num": "abc",
                 "sub_report_background_knowledge": bg})
            assert captured[-1] == 3


# =====================================================================
# LLM content .strip() guards — non-str content doesn't crash
# =====================================================================

def test_str_strip_guard_on_non_str_llm_content():
    """str() wrapping before .strip() prevents AttributeError on non-str LLM content.

    The pattern `str(llm_output.get("content") or "").strip()` is used at:
    - visualization.py:229, 277, 352, 411, 707
    - visualization_insertion.py:201, 399
    - report_parts.py:210
    This test verifies the guard pattern works on various non-str types.
    """
    # int content
    assert str(12345 or "").strip() == "12345"
    # None content
    assert str(None or "").strip() == ""
    # list content (truthy, non-str)
    assert str([1, 2] or "").strip() == "[1, 2]"
    # dict content (truthy, non-str)
    assert str({"a": 1} or "").strip() == "{'a': 1}"
    # float content
    assert str(3.14 or "").strip() == "3.14"
    # bool content
    assert str(True or "").strip() == "True"


# =====================================================================
# evidence.py _extract_batch — non-list documents/passages in logging
# =====================================================================

@pytest.mark.asyncio
async def test_extract_batch_non_list_documents_logging():
    """_extract_batch with documents as int doesn't crash len() in logging path."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, content="test content")]

    mock_llm_result = MagicMock()
    # documents is an int, not a list
    mock_llm_result.get.return_value = '{"documents": 42}'

    with patch("openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.evidence.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    # Should not crash; degraded path returns empty coverage_matrix
    assert result["coverage_matrix"] == {}


@pytest.mark.asyncio
async def test_extract_batch_non_list_passages_in_logging():
    """_extract_batch with passages as string doesn't crash len() in logging path."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, content="test content")]

    mock_llm_result = MagicMock()
    # documents has a doc with passages as a string (truthy, non-list)
    mock_llm_result.get.return_value = '{"documents": [{"doc_index": 0, "passages": "not a list"}]}'

    with patch("openjiuwen_deepsearch.algorithm.report.evidence.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.evidence.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    # Should not crash; passages is not a list, so no passages extracted
    assert result["coverage_matrix"] == {}
