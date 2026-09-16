# -*- coding: UTF-8 -*-
"""Tests for passage-level pipeline: score passthrough, coverage filtering,
evidence guide, fulltext enrichment, and dedup.

Covers the test gaps identified in the code review:
- M1: passage_dict["scores"] passthrough to classified_content
- M2: build_evidence_atom writes scores to base dict
- M3: filter_passages_by_coverage fallback when all scores below threshold
- enrich_fulltext_for_section end-to-end (real L1/L2, no mock)
- build_references / build_core_content_list / build_structured_evidence_guide
"""
from unittest.mock import MagicMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report_rationale_fulltext import (
    _text_ngrams,
    filter_passages_by_coverage,
    dedup_passages_by_rationale,
    select_top_urls_by_frequency,
    split_passages_by_url,
    build_classified_content,
    build_core_content_list,
    build_references,
    FullTextEvidence,
    enrich_fulltext_for_section,
)
from openjiuwen_deepsearch.algorithm.report.compact_doc_info import (
    build_structured_evidence_guide,
)
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    build_evidence_atom,
    CollectorSourceStore,
)


# ---------- Helpers ----------

def _make_passage(idx, url=None, text=None, scores=None, reliability=0.0, data_density=0.0):
    return {
        "doc_url": url or f"https://example.com/{idx}",
        "doc_title": f"doc-{idx}",
        "doc_time": "2024-01-01",
        "passage_text": text or f"passage text {idx}",
        "scores": scores or {},
        "reliability": reliability,
        "data_density": data_density,
    }


def _make_coverage_result(passages, scores_per_passage):
    """Build a coverage_result dict consistent with _extract_and_score_documents."""
    coverage_matrix = {}
    dim_scores = {}
    for idx, (passage, scores) in enumerate(zip(passages, scores_per_passage)):
        key = f"passage_{idx}"
        cleaned = {rid: dim.get("coverage", 0.0) for rid, dim in scores.items()}
        dim_cleaned = dict(scores)
        coverage_matrix[key] = cleaned
        dim_scores[key] = dim_cleaned
    return {
        "coverage_matrix": coverage_matrix,
        "dimension_scores": dim_scores,
        "filtered_passages": passages,
    }


# ---------- _text_ngrams ----------

class TestTextNgrams:
    def test_cjk_3gram(self):
        grams = _text_ngrams("经济金融风险")
        assert "经济金" in grams
        assert "济金融" in grams

    def test_latin_unigram(self):
        grams = _text_ngrams("GDP growth rate")
        assert "gdp" in grams
        assert "growth" in grams
        assert "rate" in grams

    def test_latin_bigram(self):
        grams = _text_ngrams("GDP growth rate")
        assert "gdp growth" in grams
        assert "growth rate" in grams

    def test_latin_trigram(self):
        grams = _text_ngrams("GDP growth rate 2024")
        assert "growth rate 2024" in grams

    def test_latin_punctuation_stripped(self):
        grams = _text_ngrams("AI, machine learning!")
        assert "ai" in grams
        assert "machine" in grams
        assert "learning" in grams
        # Punctuation should NOT be part of tokens
        assert "ai," not in grams

    def test_empty_text(self):
        assert _text_ngrams("") == frozenset()

    def test_mixed_cjk_latin(self):
        grams = _text_ngrams("GDP 经济增长")
        assert "gdp" in grams
        assert "经济增" in grams  # CJK 3-gram from "经济增长"


# ---------- filter_passages_by_coverage ----------

class TestFilterPassagesByCoverage:
    def test_keeps_above_threshold(self):
        passages = [_make_passage(0), _make_passage(1)]
        coverage = _make_coverage_result(
            passages,
            [{"r1": {"coverage": 0.5}}, {"r1": {"coverage": 0.1}}],
        )
        result = filter_passages_by_coverage(passages, [{"id": "r1"}], coverage)
        assert len(result) == 1
        assert result[0] is passages[0]

    def test_fallback_when_all_below_threshold(self):
        passages = [_make_passage(i) for i in range(5)]
        coverage = _make_coverage_result(
            passages,
            [{"r1": {"coverage": 0.01}}] * 5,
        )
        result = filter_passages_by_coverage(passages, [{"id": "r1"}], coverage)
        # Fallback should keep top-5 by max coverage
        assert len(result) == 5

    def test_no_coverage_returns_original(self):
        passages = [_make_passage(0)]
        result = filter_passages_by_coverage(passages, [{"id": "r1"}], None)
        assert result == passages

    def test_empty_matrix_returns_original(self):
        passages = [_make_passage(0)]
        result = filter_passages_by_coverage(
            passages, [{"id": "r1"}], {"coverage_matrix": {}}
        )
        assert result == passages

    def test_fallback_top5_when_all_below_threshold(self):
        """P0-2: when ALL passages have max coverage < threshold, return top-5
        by max coverage (not empty). With 6 passages, exactly 5 are returned,
        sorted by coverage descending."""
        passages = [_make_passage(i) for i in range(6)]
        coverage = _make_coverage_result(
            passages,
            [{"r1": {"coverage": 0.01 + i * 0.01}} for i in range(6)],  # 0.01..0.06
        )
        result = filter_passages_by_coverage(passages, [{"id": "r1"}], coverage)
        # Fallback should keep top-5 by max coverage (not empty)
        assert len(result) == 5
        # Sorted by coverage descending: passage 5 (0.06) ... passage 1 (0.02)
        assert result[0] is passages[5]  # highest coverage
        assert result[4] is passages[1]
        # passage 0 (lowest coverage 0.01) should be excluded
        assert passages[0] not in result

    def test_sets_passage_key_field(self):
        """P1-5: filter_passages_by_coverage sets _passage_key on each passage dict."""
        passages = [_make_passage(i) for i in range(3)]
        coverage = _make_coverage_result(
            passages,
            [{"r1": {"coverage": 0.5}}, {"r1": {"coverage": 0.4}}, {"r1": {"coverage": 0.3}}],
        )
        result = filter_passages_by_coverage(passages, [{"id": "r1"}], coverage)
        assert len(result) == 3
        for idx, passage in enumerate(result):
            assert "_passage_key" in passage
            assert passage["_passage_key"] == f"passage_{idx}"


# ---------- build_classified_content (M1 passthrough) ----------

class TestClassifiedContentScores:
    """M1: Verify that passage scores flow through to classified_content."""

    def test_passage_scores_non_empty(self):
        """The core M1 assertion: passage scores must appear in classified items."""
        passages = [
            _make_passage(0, scores={"r1": {"coverage": 0.9, "reliability": 0.8}}),
        ]
        classified = build_classified_content([], passages)
        assert len(classified) == 1
        item = classified[0]
        assert item["is_fulltext"] is False
        assert item["scores"] == {"r1": {"coverage": 0.9, "reliability": 0.8}}

    def test_fulltext_scores_empty(self):
        """Fulltext items should have empty scores (by design)."""
        evidences = [
            FullTextEvidence(
                url="https://example.com/1",
                doc_title="Doc1",
                original_content="Full text content",
                citation_index=1,
            ),
        ]
        classified = build_classified_content(evidences, [])
        assert len(classified) == 1
        assert classified[0]["scores"] == {}

    def test_mixed_fulltext_passage_ordering(self):
        """Fulltext items come first (index 1..N), passages after."""
        evidences = [
            FullTextEvidence(url="https://a.com", doc_title="A", original_content="A", citation_index=1),
        ]
        passages = [
            _make_passage(0, url="https://b.com", scores={"r1": {"coverage": 0.5}}),
        ]
        classified = build_classified_content(evidences, passages)
        assert len(classified) == 2
        assert classified[0]["index"] == 1
        assert classified[0]["is_fulltext"] is True
        assert classified[1]["index"] == 2
        assert classified[1]["is_fulltext"] is False
        assert classified[1]["scores"] == {"r1": {"coverage": 0.5}}


# ---------- build_core_content_list (M6 truncation) ----------

class TestBuildCoreContentList:
    def test_fulltext_truncated_for_outline(self):
        long_content = "X" * 1000
        evidences = [
            FullTextEvidence(
                url="https://a.com",
                doc_title="A",
                original_content=long_content,
                citation_index=1,
            ),
        ]
        blocks = build_core_content_list(evidences, [])
        assert len(blocks) == 1
        # Truncated to 500 + "..."
        assert len(blocks[0]) < 600  # header + 500 + "..."

    def test_short_fulltext_not_truncated(self):
        short_content = "Short content"
        evidences = [
            FullTextEvidence(
                url="https://a.com",
                doc_title="A",
                original_content=short_content,
                citation_index=1,
            ),
        ]
        blocks = build_core_content_list(evidences, [])
        assert short_content in blocks[0]

    def test_passage_ordering(self):
        evidences = [
            FullTextEvidence(url="https://a.com", doc_title="A", original_content="A", citation_index=1),
        ]
        passages = [
            _make_passage(0, url="https://b.com", text="passage B"),
        ]
        blocks = build_core_content_list(evidences, passages)
        assert len(blocks) == 2
        assert "Document 1" in blocks[0]
        assert "Document 2" in blocks[1]


# ---------- build_references ----------

class TestBuildReferences:
    def test_url_not_deduped(self):
        """build_references must NOT deduplicate by URL.

        Each classified_content item gets its own reference entry to
        maintain 1:1 correspondence with citation indices. Deduplication
        is handled downstream by _deduplicate_and_renumber_ref.
        """
        evidences = [
            FullTextEvidence(url="https://a.com", doc_title="A", original_content="A", citation_index=1),
        ]
        passages = [
            _make_passage(0, url="https://a.com"),  # same URL as fulltext
            _make_passage(1, url="https://b.com"),
        ]
        refs = build_references(evidences, passages)
        # a.com should appear twice (fulltext + passage), b.com once
        assert len([r for r in refs if "a.com" in r]) == 2
        assert len([r for r in refs if "b.com" in r]) == 1
        assert len(refs) == 3

    def test_empty_inputs(self):
        refs = build_references([], [])
        assert refs == []


# ---------- build_structured_evidence_guide ----------

class TestStructuredEvidenceGuide:
    def test_covered_weak_uncovered(self):
        passages = [
            _make_passage(0, scores={"r1": {"coverage": 0.8}}),
            _make_passage(1, scores={"r1": {"coverage": 0.4}}),
            _make_passage(2, scores={"r1": {"coverage": 0.1}}),
        ]
        # Give them indices for citation display
        for i, p in enumerate(passages):
            p["index"] = i + 1
        coverage = _make_coverage_result(
            passages,
            [{"r1": {"coverage": 0.8}}, {"r1": {"coverage": 0.4}}, {"r1": {"coverage": 0.1}}],
        )
        guide = build_structured_evidence_guide(
            passages, [{"id": "r1", "priority": "primary", "description": "test"}],
            coverage,
            selected_passage_keys=["passage_0", "passage_1", "passage_2"],
        )
        # r1 max_coverage = 0.8 >= 0.6 → "covered"
        assert "covered" in guide
        # 0.8 and 0.4 are both >= 0.3, so both appear as evidence (top-3)
        # 0.1 < 0.3, so passage 2 does NOT appear
        assert "[citation:1]" in guide
        assert "[citation:2]" in guide
        assert "[citation:3]" not in guide

    def test_top3_evidence_shown(self):
        passages = [
            _make_passage(i, scores={"r1": {"coverage": 0.9 - i * 0.1}})
            for i in range(5)
        ]
        for i, p in enumerate(passages):
            p["index"] = i + 1
        coverage = _make_coverage_result(
            passages,
            [{"r1": {"coverage": 0.9 - i * 0.1}} for i in range(5)],
        )
        guide = build_structured_evidence_guide(
            passages, [{"id": "r1", "priority": "primary", "description": "test"}],
            coverage,
            selected_passage_keys=[f"passage_{i}" for i in range(5)],
        )
        # Should show top-3 passages (coverage >= 0.3: 0.9, 0.8, 0.7)
        citation_count = guide.count("[citation:")
        assert citation_count == 3

    def test_empty_passages_returns_empty(self):
        guide = build_structured_evidence_guide(
            [], [{"id": "r1"}], {"coverage_matrix": {}},
            selected_passage_keys=[],
        )
        assert guide == ""


# ---------- dedup_passages_by_rationale (m5 global scan) ----------

class TestDedupPassages:
    def test_per_rationale_dedup(self):
        """Near-duplicates within same rationale should be removed."""
        passages = [
            _make_passage(0, text="GDP growth rate is 5.2 percent this year"),
            _make_passage(1, text="GDP growth rate is 5.2 percent this year"),
        ]
        coverage = _make_coverage_result(
            passages,
            [{"r1": {"coverage": 0.9}}, {"r1": {"coverage": 0.85}}],
        )
        result = dedup_passages_by_rationale(
            passages, [{"id": "r1"}], coverage,
            similarity_threshold=0.70, top_k_per_rationale=15,
        )
        assert len(result) == 1

    def test_cross_rationale_global_dedup(self):
        """Near-duplicates surviving in different rationales are caught by
        the global dedup scan. The passage with higher total coverage is kept."""
        passages = [
            _make_passage(0, text="The inflation rate reached 3.5 percent in Q1"),
            _make_passage(1, text="The inflation rate reached 3.5 percent in Q1"),
        ]
        coverage = _make_coverage_result(
            passages,
            [
                {"r1": {"coverage": 0.9}, "r2": {"coverage": 0.0}},
                {"r1": {"coverage": 0.0}, "r2": {"coverage": 0.9}},
            ],
        )
        result = dedup_passages_by_rationale(
            passages, [{"id": "r1"}, {"id": "r2"}], coverage,
            similarity_threshold=0.70, top_k_per_rationale=15,
        )
        # Global dedup removes the cross-rationale duplicate
        assert len(result) == 1


# ---------- select_top_urls_by_frequency ----------

class TestSelectTopUrls:
    def test_url_frequency(self):
        passages = [
            _make_passage(0, url="https://a.com"),
            _make_passage(1, url="https://a.com"),
            _make_passage(2, url="https://b.com"),
        ]
        result = select_top_urls_by_frequency(passages, top_n=2)
        assert result[0]["url"] == "https://a.com"
        assert result[1]["url"] == "https://b.com"

    def test_top_n_limit(self):
        passages = [_make_passage(i, url=f"https://e{i}.com") for i in range(5)]
        result = select_top_urls_by_frequency(passages, top_n=3)
        assert len(result) == 3


# ---------- split_passages_by_url ----------

class TestSplitPassagesByUrl:
    def test_split(self):
        passages = [
            _make_passage(0, url="https://a.com"),
            _make_passage(1, url="https://b.com"),
        ]
        removed, remaining = split_passages_by_url(passages, {"https://a.com"})
        assert len(removed) == 1
        assert removed[0]["doc_url"] == "https://a.com"
        assert len(remaining) == 1
        assert remaining[0]["doc_url"] == "https://b.com"

    def test_empty_fetched_urls(self):
        passages = [_make_passage(0)]
        removed, remaining = split_passages_by_url(passages, set())
        assert len(removed) == 0
        assert len(remaining) == 1


# ---------- build_evidence_atom (M2 score passthrough) ----------

class TestBuildEvidenceAtom:
    """M2: Verify that evidence_scores are written to base dict."""

    def _mock_source_store(self):
        store = MagicMock(spec=CollectorSourceStore)
        store.write.return_value = True
        return store

    def test_score_passthrough_from_record_score(self):
        """record["score"] should become base["scores"]["relevance"]."""
        record = {
            "url": "https://example.com/test",
            "title": "Test Doc",
            "content": "Test content for verification",
            "score": 0.85,
        }
        store = self._mock_source_store()
        base, doc_info = build_evidence_atom(record, "test query", store)
        assert "scores" in base
        assert base["scores"]["relevance"] == 0.85
        assert "scores" in doc_info  # doc_info inherits from base

    def test_scores_passthrough_from_record_scores(self):
        """record["scores"] with valid relevance should be normalized and passed through."""
        record = {
            "url": "https://example.com/test",
            "title": "Test Doc",
            "content": "Test content",
            "scores": {"relevance": 0.9, "custom": 0.5},
        }
        store = self._mock_source_store()
        base, doc_info = build_evidence_atom(record, "test query", store)
        # relevance is clamped to [0,1]; other keys from dict are preserved
        assert base["scores"]["relevance"] == 0.9
        assert base["scores"]["custom"] == 0.5

    def test_no_score_returns_empty_scores(self):
        record = {
            "url": "https://example.com/test",
            "title": "Test Doc",
            "content": "Test content",
        }
        store = self._mock_source_store()
        base, _ = build_evidence_atom(record, "test query", store)
        # No score → relevance defaults to 0.0 (functionally equivalent to empty)
        assert base["scores"].get("relevance", 0) == 0


# ---------- enrich_fulltext_for_section end-to-end ----------

class TestEnrichFulltextForSection:
    """End-to-end test without mocking L1/L2/object-identity chain."""

    @pytest.mark.asyncio
    async def test_basic_pipeline(self):
        passages_data = [
            _make_passage(0, url="https://a.com", text="GDP growth is strong",
                          scores={"r1": {"coverage": 0.8}}),
            _make_passage(1, url="https://b.com", text="Inflation remains low",
                          scores={"r1": {"coverage": 0.5}}),
        ]
        raw_passages = [
            {"url": "https://a.com", "doc_title": "Doc A",
             "doc_time": "2024-01-01", "original_content": "Full content of doc A"},
        ]
        coverage = _make_coverage_result(
            passages_data,
            [{"r1": {"coverage": 0.8}}, {"r1": {"coverage": 0.5}}],
        )
        result = enrich_fulltext_for_section(
            passages={"selected": passages_data, "raw": raw_passages},
            context={"rationales": [{"id": "r1", "priority": "primary", "description": "test"}],
                      "coverage_result": coverage},
            section_idx=1,
            top_n=10,
        )
        assert "classified_content" in result
        assert "sub_section_core_content" in result
        assert "sub_section_references" in result
        assert "structured_evidence_guide" in result
        assert len(result["classified_content"]) > 0

    @pytest.mark.asyncio
    async def test_fallback_all_below_threshold(self):
        """M3: all passages below 0.15 should trigger fallback, not empty result."""
        passages_data = [
            _make_passage(i, text=f"passage {i}",
                          scores={"r1": {"coverage": 0.01}})
            for i in range(5)
        ]
        coverage = _make_coverage_result(
            passages_data,
            [{"r1": {"coverage": 0.01}}] * 5,
        )
        result = enrich_fulltext_for_section(
            passages={"selected": passages_data, "raw": []},
            context={"rationales": [{"id": "r1", "priority": "primary", "description": "test"}],
                      "coverage_result": coverage},
            section_idx=1,
            top_n=10,
        )
        # Should not be empty despite all scores < 0.15
        assert len(result["classified_content"]) > 0
        assert len(result["sub_section_core_content"]) > 0

    @pytest.mark.asyncio
    async def test_no_coverage_result(self):
        """When coverage_result is None, passages pass through L1 unchanged."""
        passages_data = [
            _make_passage(0, url="https://a.com", text="content"),
        ]
        result = enrich_fulltext_for_section(
            passages={"selected": passages_data, "raw": []},
            context={"rationales": [{"id": "r1", "priority": "primary", "description": "test"}],
                      "coverage_result": None},
            section_idx=1,
            top_n=10,
        )
        assert len(result["classified_content"]) > 0
