"""Tests for new document selection methods in report.py.

Covers: _optimize_document_set, _elbow_cutoff, _verify_coverage.
These are pure-algorithm methods (0 LLM calls).
"""

import asyncio
from unittest.mock import patch, MagicMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context


# ---------- Fixtures ----------

def _make_reporter():
    """Create a Reporter with a mock LLM (not used by algorithmic methods)."""
    with patch.object(Reporter, "__init__", lambda self, name: None):
        reporter = Reporter.__new__(Reporter)
        reporter._llm = MagicMock()
        return reporter


def _doc(idx, title=None, url=None):
    return {
        "title": title or f"doc-{idx}",
        "url": url or f"https://example.com/{idx}",
        "original_content": f"content-{idx}",
        "key_passages": [f"passage-{idx}"],
    }


def _rationale(rid, desc):
    return {"id": rid, "description": desc, "type": "factual"}


def _coverage_result(docs, matrix, reliability=None, noise=None):
    return {
        "filtered_docs": docs,
        "coverage_matrix": matrix,
        "reliability_scores": reliability or {},
        "noise_scores": noise or {},
    }


# ---------- _optimize_document_set ----------

def test_optimize_selects_high_coverage_docs_first():
    reporter = _make_reporter()
    docs = [_doc(0, "出口数据"), _doc(1, "目的国分析"), _doc(2, "无关内容")]
    rationales = [_rationale("r1", "出口数据"), _rationale("r2", "目的国")]
    matrix = {
        "doc_0": {"r1": 0.9, "r2": 0.1},
        "doc_1": {"r1": 0.1, "r2": 0.9},
        "doc_2": {"r1": 0.05, "r2": 0.05},
    }
    coverage = _coverage_result(docs, matrix, reliability={"doc_0": 0.8, "doc_1": 0.8, "doc_2": 0.5},
                                noise={"doc_0": 0.0, "doc_1": 0.0, "doc_2": 0.5})

    selected, values = reporter._optimize_document_set(docs, rationales, coverage, top_k=5)

    assert len(selected) >= 2
    # doc_0 and doc_1 should be selected before doc_2
    selected_titles = [d["title"] for d in selected]
    assert "出口数据" in selected_titles
    assert "目的国分析" in selected_titles
    # marginal values should be descending
    assert values == sorted(values, reverse=True)


def test_optimize_stops_when_marginal_value_zero():
    reporter = _make_reporter()
    docs = [_doc(0), _doc(1)]
    rationales = [_rationale("r1", "test")]
    matrix = {"doc_0": {"r1": 0.0}, "doc_1": {"r1": 0.0}}
    coverage = _coverage_result(docs, matrix)

    selected, values = reporter._optimize_document_set(docs, rationales, coverage, top_k=5)

    # With 0 coverage and some noise/untrustworthy penalty, marginal value <= 0
    assert len(selected) == 0


def test_optimize_respects_top_k_limit():
    reporter = _make_reporter()
    docs = [_doc(i) for i in range(15)]
    rationales = [_rationale("r1", "common topic")]
    matrix = {f"doc_{i}": {"r1": 0.5} for i in range(15)}
    coverage = _coverage_result(docs, matrix)

    selected, _ = reporter._optimize_document_set(docs, rationales, coverage, top_k=3)

    assert len(selected) <= 3


def test_optimize_penalty_for_noise_reduces_selection():
    reporter = _make_reporter()
    docs = [_doc(0, "good"), _doc(1, "noisy")]
    rationales = [_rationale("r1", "good")]
    matrix = {"doc_0": {"r1": 0.5}, "doc_1": {"r1": 0.5}}
    coverage = _coverage_result(
        docs, matrix,
        reliability={"doc_0": 0.9, "doc_1": 0.9},
        noise={"doc_0": 0.0, "doc_1": 0.8},
    )

    selected, values = reporter._optimize_document_set(docs, rationales, coverage, top_k=2)

    # doc_0 should have higher marginal value due to lower noise
    assert len(selected) >= 1
    assert selected[0]["title"] == "good"


def test_optimize_penalty_for_low_reliability():
    reporter = _make_reporter()
    docs = [_doc(0, "reliable"), _doc(1, "unreliable")]
    rationales = [_rationale("r1", "test")]
    matrix = {"doc_0": {"r1": 0.5}, "doc_1": {"r1": 0.5}}
    coverage = _coverage_result(
        docs, matrix,
        reliability={"doc_0": 0.9, "doc_1": 0.1},
    )

    selected, values = reporter._optimize_document_set(docs, rationales, coverage, top_k=2)

    assert len(selected) >= 1
    assert selected[0]["title"] == "reliable"


def test_optimize_empty_docs():
    reporter = _make_reporter()
    selected, values = reporter._optimize_document_set([], [], {}, top_k=5)
    assert selected == []
    assert values == []


def test_optimize_redundancy_penalty_prevents_duplicates():
    reporter = _make_reporter()
    # Two docs with identical content (high n-gram overlap)
    docs = [_doc(0, "出口数据 2024"), _doc(1, "出口数据 2024")]
    rationales = [_rationale("r1", "出口数据")]
    matrix = {"doc_0": {"r1": 0.8}, "doc_1": {"r1": 0.8}}
    coverage = _coverage_result(docs, matrix, reliability={"doc_0": 0.8, "doc_1": 0.8})

    selected, values = reporter._optimize_document_set(docs, rationales, coverage, top_k=5)

    # First doc has high marginal value, second has high redundancy penalty
    assert len(selected) >= 1
    if len(selected) == 2:
        # If both selected, second should have lower value
        assert values[1] < values[0]


# ---------- _elbow_cutoff ----------

def test_elbow_cutoff_short_list_returns_all():
    reporter = _make_reporter()
    docs = [_doc(0), _doc(1), _doc(2)]
    result = reporter._elbow_cutoff(docs, [0.9, 0.8, 0.7], top_k=10)
    assert len(result) == 3


def test_elbow_cutoff_detects_elbow():
    reporter = _make_reporter()
    docs = [_doc(i) for i in range(10)]
    # Sharp drop after index 3
    values = [1.0, 0.9, 0.8, 0.7, 0.05, 0.04, 0.03, 0.02, 0.01, 0.005]
    result = reporter._elbow_cutoff(docs, values, top_k=10)
    # Should cut around index 3+2=5
    assert len(result) <= 6
    assert len(result) >= 4


def test_elbow_cutoff_no_clear_elbow_returns_top_k():
    reporter = _make_reporter()
    docs = [_doc(i) for i in range(8)]
    # Gradual decline, no sharp drop
    values = [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
    result = reporter._elbow_cutoff(docs, values, top_k=5)
    assert len(result) == 5


def test_elbow_cutoff_empty_list():
    reporter = _make_reporter()
    assert reporter._elbow_cutoff([], [], top_k=5) == []


def test_elbow_cutoff_single_element():
    reporter = _make_reporter()
    result = reporter._elbow_cutoff([_doc(0)], [0.9], top_k=5)
    assert len(result) == 1


def test_elbow_cutoff_all_same_values():
    reporter = _make_reporter()
    docs = [_doc(i) for i in range(6)]
    values = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
    result = reporter._elbow_cutoff(docs, values, top_k=5)
    # diffs are all 0, max_diff = 0 which is not > 0.05, so no elbow
    assert len(result) == 5


def test_elbow_cutoff_coverage_aware_keeps_improving_doc():
    """After elbow, keep a doc that improves rationale coverage."""
    reporter = _make_reporter()
    docs = [_doc(i) for i in range(8)]
    # Sharp drop after index 2: elbow_idx=2, baseline cutoff=3
    values = [1.0, 0.9, 0.8, 0.05, 0.04, 0.03, 0.02, 0.01]
    rationales = [{"id": "r1", "description": "export data"}, {"id": "r2", "description": "destination"}]
    # Pre-elbow docs (0,1,2) cover r1 well, but r2 poorly.
    # Post-elbow doc 3 covers r2 better → should be kept.
    coverage_result = {
        "filtered_docs": docs,
        "coverage_matrix": {
            "doc_0": {"r1": 0.9, "r2": 0.1},
            "doc_1": {"r1": 0.8, "r2": 0.2},
            "doc_2": {"r1": 0.7, "r2": 0.1},
            "doc_3": {"r1": 0.1, "r2": 0.8},  # improves r2!
            "doc_4": {"r1": 0.1, "r2": 0.1},  # no improvement
            "doc_5": {"r1": 0.1, "r2": 0.1},
            "doc_6": {"r1": 0.1, "r2": 0.1},
            "doc_7": {"r1": 0.1, "r2": 0.1},
        },
        "reliability_scores": {},
        "noise_scores": {},
    }
    result = reporter._elbow_cutoff(
        docs, values, top_k=10,
        coverage_ctx={"coverage_result": coverage_result, "rationales": rationales},
    )
    # Baseline cutoff=3, doc 3 improves r2 → kept, rest don't improve
    assert len(result) == 4  # docs 0,1,2,3


def test_elbow_cutoff_coverage_aware_no_improvement():
    """After elbow, if no doc improves coverage, use baseline cutoff."""
    reporter = _make_reporter()
    docs = [_doc(i) for i in range(8)]
    values = [1.0, 0.9, 0.8, 0.05, 0.04, 0.03, 0.02, 0.01]
    rationales = [{"id": "r1", "description": "export data"}]
    coverage_result = {
        "filtered_docs": docs,
        "coverage_matrix": {
            "doc_0": {"r1": 0.9},
            "doc_1": {"r1": 0.8},
            "doc_2": {"r1": 0.7},
            "doc_3": {"r1": 0.1},  # no improvement over 0.9
            "doc_4": {"r1": 0.1},
            "doc_5": {"r1": 0.1},
            "doc_6": {"r1": 0.1},
            "doc_7": {"r1": 0.1},
        },
        "reliability_scores": {},
        "noise_scores": {},
    }
    result = reporter._elbow_cutoff(
        docs, values, top_k=10,
        coverage_ctx={"coverage_result": coverage_result, "rationales": rationales},
    )
    # Baseline cutoff=3, no improvement → stays at 3
    assert len(result) == 3


def test_elbow_cutoff_coverage_aware_non_contiguous_keeps():
    """Post-elbow docs are kept even if non-contiguous (no early stop)."""
    reporter = _make_reporter()
    docs = [_doc(i) for i in range(8)]
    values = [1.0, 0.9, 0.8, 0.05, 0.04, 0.03, 0.02, 0.01]
    rationales = [
        {"id": "r1", "description": "export data"},
        {"id": "r2", "description": "destination"},
        {"id": "r3", "description": "tariff"},
    ]
    coverage_result = {
        "filtered_docs": docs,
        "coverage_matrix": {
            "doc_0": {"r1": 0.9, "r2": 0.1, "r3": 0.1},
            "doc_1": {"r1": 0.8, "r2": 0.2, "r3": 0.1},
            "doc_2": {"r1": 0.7, "r2": 0.1, "r3": 0.1},
            "doc_3": {"r1": 0.1, "r2": 0.1, "r3": 0.1},  # no improvement
            "doc_4": {"r1": 0.1, "r2": 0.8, "r3": 0.1},  # improves r2
            "doc_5": {"r1": 0.1, "r2": 0.1, "r3": 0.1},  # no improvement (gap)
            "doc_6": {"r1": 0.1, "r2": 0.1, "r3": 0.9},  # improves r3 (non-contiguous!)
            "doc_7": {"r1": 0.1, "r2": 0.1, "r3": 0.1},
        },
        "reliability_scores": {},
        "noise_scores": {},
    }
    result = reporter._elbow_cutoff(
        docs, values, top_k=10,
        coverage_ctx={"coverage_result": coverage_result, "rationales": rationales},
    )
    # Pre-elbow: 0,1,2. Doc 4 improves r2, doc 6 improves r3 (non-contiguous)
    # Docs 3,5,7 don't improve anything but we don't stop — we keep iterating
    assert len(result) == 5  # docs 0,1,2,4,6


# ---------- _verify_coverage ----------

def test_verify_coverage_all_covered():
    reporter = _make_reporter()
    docs = [_doc(0, "出口"), _doc(1, "目的国")]
    rationales = [_rationale("r1", "出口数据"), _rationale("r2", "目的国")]
    matrix = {
        "doc_0": {"r1": 0.8, "r2": 0.1},
        "doc_1": {"r1": 0.1, "r2": 0.8},
    }
    coverage = _coverage_result(docs, matrix)

    result = reporter._verify_coverage(docs, rationales, coverage, section_idx=1)

    assert len(result["uncovered_rationales"]) == 0
    assert result["coverage_rate"] == 1.0
    assert result["limitations"] == []


def test_verify_coverage_some_uncovered():
    reporter = _make_reporter()
    docs = [_doc(0, "出口")]
    rationales = [_rationale("r1", "出口"), _rationale("r2", "目的国")]
    matrix = {"doc_0": {"r1": 0.9, "r2": 0.1}}
    coverage = _coverage_result(docs, matrix)

    result = reporter._verify_coverage(docs, rationales, coverage, section_idx=1)

    assert len(result["uncovered_rationales"]) == 1
    assert result["uncovered_rationales"][0]["id"] == "r2"
    assert result["coverage_rate"] == 0.5
    assert len(result["limitations"]) == 1
    assert "目的国" in result["limitations"][0]


def test_verify_coverage_weak_coverage():
    reporter = _make_reporter()
    docs = [_doc(0, "partial")]
    rationales = [_rationale("r1", "partial")]
    matrix = {"doc_0": {"r1": 0.4}}  # between 0.3 and 0.6 = weak
    coverage = _coverage_result(docs, matrix)

    result = reporter._verify_coverage(docs, rationales, coverage, section_idx=1)

    # 0.4 is in weak range [0.3, 0.6), not uncovered (< 0.3)
    assert len(result["weak_rationales"]) == 1
    assert len(result["uncovered_rationales"]) == 0
    assert result["coverage_rate"] == 1.0


def test_verify_coverage_empty_rationales():
    reporter = _make_reporter()
    result = reporter._verify_coverage([], [], {}, section_idx=1)
    assert result["coverage_rate"] == 1.0
    assert result["uncovered_rationales"] == []


def test_verify_coverage_empty_matrix():
    reporter = _make_reporter()
    docs = [_doc(0)]
    rationales = [_rationale("r1", "test")]
    coverage = _coverage_result(docs, {})

    result = reporter._verify_coverage(docs, rationales, coverage, section_idx=1)

    assert len(result["uncovered_rationales"]) == 1
    assert result["coverage_rate"] == 0.0


def test_verify_coverage_only_checks_selected_docs():
    """A rationale covered only by a non-selected doc should be 'uncovered'."""
    reporter = _make_reporter()
    all_docs = [_doc(0, "出口"), _doc(1, "目的国")]
    selected = [all_docs[0]]  # only doc 0 selected, doc 1 not in report
    rationales = [_rationale("r1", "出口数据"), _rationale("r2", "目的国")]
    matrix = {
        "doc_0": {"r1": 0.9, "r2": 0.1},
        "doc_1": {"r1": 0.1, "r2": 0.8},  # doc_1 covers r2 but is NOT selected
    }
    coverage = _coverage_result(all_docs, matrix)

    result = reporter._verify_coverage(selected, rationales, coverage, section_idx=1)

    # r2 should be uncovered because doc_1 (which covers r2) was not selected
    assert len(result["uncovered_rationales"]) == 1
    assert result["uncovered_rationales"][0]["id"] == "r2"
    assert result["coverage_rate"] == 0.5


# ---------- _evaluate_coverage_matrix (batch + parallel) ----------

def test_evaluate_coverage_matrix_single_batch():
    """When docs <= BATCH_SIZE, should use 1 batch."""
    reporter = _make_reporter()
    docs = [_doc(i, f"export data {i}") for i in range(8)]
    rationales = [_rationale("r1", "export data")]
    current_inputs = {"section_idx": 1, "section_task": "1 Export", "section_description": "desc"}

    async def fake_batch(batch_docs, batch_idx, *args):
        result = {
            "coverage_matrix": {f"doc_{i}": {"r1": 0.8} for i in range(len(batch_docs))},
            "reliability_scores": {f"doc_{i}": 0.9 for i in range(len(batch_docs))},
            "noise_scores": {f"doc_{i}": 0.1 for i in range(len(batch_docs))},
        }
        return result, batch_docs, ""

    with patch.object(reporter, "_eval_coverage_batch", side_effect=fake_batch):
        result, last_error = asyncio.run(
            reporter._evaluate_coverage_matrix(current_inputs, docs, rationales)
        )

    assert len(result["coverage_matrix"]) == 8
    assert result["coverage_matrix"]["doc_0"] == {"r1": 0.8}
    assert result["coverage_matrix"]["doc_7"] == {"r1": 0.8}
    assert len(result["filtered_docs"]) <= 8


def test_evaluate_coverage_matrix_multi_batch_merges_keys():
    """When docs > BATCH_SIZE, should split into batches and merge with offset."""
    reporter = _make_reporter()
    docs = [_doc(i, f"export data {i}") for i in range(25)]
    rationales = [_rationale("r1", "export data")]
    current_inputs = {"section_idx": 1, "section_task": "1 Export", "section_description": "desc"}

    # Track batch calls
    batch_calls = []

    async def fake_batch(batch_docs, batch_idx, *args):
        batch_calls.append((batch_idx, len(batch_docs)))
        result = {
            "coverage_matrix": {f"doc_{i}": {"r1": 0.5} for i in range(len(batch_docs))},
            "reliability_scores": {f"doc_{i}": 0.7 for i in range(len(batch_docs))},
            "noise_scores": {f"doc_{i}": 0.2 for i in range(len(batch_docs))},
        }
        return result, batch_docs, ""

    with patch.object(reporter, "_eval_coverage_batch", side_effect=fake_batch):
        result, last_error = asyncio.run(
            reporter._evaluate_coverage_matrix(current_inputs, docs, rationales)
        )

    # No truncation, all 25 docs kept (after n-gram filter)
    assert len(result["filtered_docs"]) <= 25
    # With BATCH_SIZE=15, 25 docs → 2 batches
    assert len(batch_calls) == 2
    # Batch 0: 15 docs, Batch 1: 10 docs
    assert batch_calls[0] == (0, 15)
    assert batch_calls[1] == (1, 10)

    # Merged keys should have global indices
    assert "doc_0" in result["coverage_matrix"]
    assert "doc_14" in result["coverage_matrix"]
    # Batch 1 local doc_0 → global doc_15
    assert "doc_15" in result["coverage_matrix"]
    assert "doc_24" in result["coverage_matrix"]


def test_evaluate_coverage_matrix_empty_docs():
    """Empty doc_infos should return empty dict."""
    reporter = _make_reporter()
    current_inputs = {"section_idx": 1, "section_task": "test", "section_description": ""}
    result, last_error = asyncio.run(
        reporter._evaluate_coverage_matrix(current_inputs, [], [_rationale("r1", "test")])
    )
    assert result == {}


def test_evaluate_coverage_matrix_empty_rationales():
    """Empty rationales should return empty dict."""
    reporter = _make_reporter()
    docs = [_doc(0, "test")]
    current_inputs = {"section_idx": 1, "section_task": "test", "section_description": ""}
    result, last_error = asyncio.run(
        reporter._evaluate_coverage_matrix(current_inputs, docs, [])
    )
    assert result == {}


def test_evaluate_coverage_matrix_batch_failure_continues():
    """If one batch fails, other batches' results should still be merged."""
    reporter = _make_reporter()
    docs = [_doc(i, f"export data {i}") for i in range(25)]
    rationales = [_rationale("r1", "export data")]
    current_inputs = {"section_idx": 1, "section_task": "1 Export", "section_description": "desc"}

    async def fake_batch(batch_docs, batch_idx, *args):
        if batch_idx == 0:
            return {}, batch_docs, "batch failed"  # First batch fails
        result = {
            "coverage_matrix": {f"doc_{i}": {"r1": 0.9} for i in range(len(batch_docs))},
            "reliability_scores": {f"doc_{i}": 0.8 for i in range(len(batch_docs))},
            "noise_scores": {f"doc_{i}": 0.1 for i in range(len(batch_docs))},
        }
        return result, batch_docs, ""

    with patch.object(reporter, "_eval_coverage_batch", side_effect=fake_batch):
        result, last_error = asyncio.run(
            reporter._evaluate_coverage_matrix(current_inputs, docs, rationales)
        )

    # Batch 0 failed → no doc_0..doc_14
    assert "doc_0" not in result["coverage_matrix"]
    # Batch 1 succeeded → doc_15..doc_24 present
    assert "doc_15" in result["coverage_matrix"]
    assert "doc_24" in result["coverage_matrix"]


def test_evaluate_coverage_matrix_all_batches_fail_degrades_with_error():
    """When every batch fails, degrade to an old-shape dict (empty matrix) and surface the error."""
    reporter = _make_reporter()
    docs = [_doc(i, f"export data {i}") for i in range(25)]
    rationales = [_rationale("r1", "export data")]
    current_inputs = {"section_idx": 1, "section_task": "1 Export", "section_description": "desc"}

    async def fake_batch(batch_docs, batch_idx, *args):
        return {}, batch_docs, f"batch {batch_idx} boom"

    with patch.object(reporter, "_eval_coverage_batch", side_effect=fake_batch):
        result, last_error = asyncio.run(
            reporter._evaluate_coverage_matrix(current_inputs, docs, rationales)
        )

    # Degrade path: old-shape truthy dict so the caller continues (chapter not lost)
    assert result["coverage_matrix"] == {}
    assert result["filtered_docs"]
    assert "boom" in last_error
