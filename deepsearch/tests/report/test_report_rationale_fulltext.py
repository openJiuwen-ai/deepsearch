"""Tests for report_rationale_fulltext.py core functions.

Covers: filter_passages_by_coverage, dedup_passages_by_rationale,
select_top_urls_by_frequency, split_passages_by_url, build_classified_content.
"""

import pytest

from openjiuwen_deepsearch.algorithm.report.report_rationale_fulltext import (
    filter_passages_by_coverage,
    dedup_passages_by_rationale,
    select_top_urls_by_frequency,
    split_passages_by_url,
    build_classified_content,
    enrich_fulltext_for_section,
)
from openjiuwen_deepsearch.algorithm.report.report_rationale_fulltext import FullTextEvidence


def test_enrich_fulltext_for_section_keeps_required_target_without_coverage_score():
    required = {
        "url": "https://example.com/required",
        "title": "Required paper",
        "original_content": "Required full text",
    }

    result = enrich_fulltext_for_section(
        passages={"selected": [], "raw": [required]},
        context={
            "rationales": [],
            "coverage_result": {},
            "required_documents": [required],
        },
        section_idx=1,
    )

    assert result["required_target_citation_indexes"] == [1]
    assert result["classified_content"][0]["index"] == 1
    assert result["classified_content"][0]["title"] == "Required paper"
    assert result["structured_evidence_guide"] == ""


@pytest.mark.parametrize(
    "key_passages",
    [
        ["First requested-paper passage.", "Second requested-paper passage."],
        "First requested-paper passage.\nSecond requested-paper passage.",
    ],
)
def test_enrich_fulltext_uses_normalized_required_target_key_passages(key_passages):
    required = {
        "url": "https://example.com/required-key-passages",
        "title": "Required paper",
        "key_passages": key_passages,
    }

    result = enrich_fulltext_for_section(
        passages={"selected": [], "raw": [required]},
        context={
            "rationales": [],
            "coverage_result": {},
            "required_documents": [required],
        },
        section_idx=1,
    )

    assert result["fulltext_evidences"][0].original_content == (
        "First requested-paper passage.\nSecond requested-paper passage."
    )


def test_enrich_fulltext_guide_reuses_promoted_fulltext_citation_index():
    passage = {
        "doc_url": "https://example.com/paper",
        "doc_title": "Promoted paper",
        "passage_text": "Evidence supporting the analysis dimension.",
        "original_content": "Complete paper text.",
    }
    coverage_result = {
        "filtered_passages": [passage],
        "coverage_matrix": {"passage_0": {"R1": 0.9}},
    }

    result = enrich_fulltext_for_section(
        passages={"selected": [passage], "raw": [passage]},
        context={
            "rationales": [{"id": "R1", "description": "Analysis dimension"}],
            "coverage_result": coverage_result,
        },
        section_idx=1,
    )

    assert result["fulltext_count"] == 1
    assert "[citation:1] Promoted paper (coverage: 0.90)" in result[
        "structured_evidence_guide"
    ]
    assert "[citation:]" not in result["structured_evidence_guide"]


# ---------- Fixtures ----------

def _passage(idx, url=None, title=None, text=None, scores=None, original_content=None):
    """Create a minimal passage dict for testing."""
    return {
        "doc_url": url or f"https://example.com/{idx}",
        "doc_title": title or f"doc-{idx}",
        "passage_text": text or f"passage-{idx}",
        "original_content": original_content or f"full-content-{idx}",
        "scores": scores or {},
    }


def _rationale(rid, desc):
    """Create a rationale dict."""
    return {"id": rid, "description": desc, "type": "factual"}


def _coverage_result(passages, matrix, dimension_scores=None):
    """Create a coverage_result dict."""
    return {
        "filtered_passages": passages,
        "coverage_matrix": matrix,
        "dimension_scores": dimension_scores or {},
    }


# ---------- filter_passages_by_coverage ----------

def test_filter_passages_keeps_high_coverage():
    """保留覆盖度 >= 0.15 的段落（默认阈值）。"""
    passages = [
        _passage(0),
        _passage(1),
        _passage(2),
    ]
    rationales = [_rationale("r1", "test")]
    matrix = {
        "passage_0": {"r1": 0.8},
        "passage_1": {"r1": 0.5},
        "passage_2": {"r1": 0.1},  # 低于默认阈值
    }
    coverage = _coverage_result(passages, matrix)

    filtered = filter_passages_by_coverage(passages, rationales, coverage, threshold=0.15)

    # passage_2 被过滤（coverage=0.1 < 0.15）
    assert len(filtered) == 2


def test_filter_passages_no_coverage_data():
    """无覆盖数据时返回原列表。"""
    passages = [_passage(i) for i in range(3)]
    rationales = [_rationale("r1", "test")]

    # coverage_result 为 None
    result = filter_passages_by_coverage(passages, rationales, None)
    assert len(result) == 3

    # coverage_matrix 为空
    result = filter_passages_by_coverage(passages, rationales, {"coverage_matrix": {}})
    assert len(result) == 3


# ---------- dedup_passages_by_rationale ----------

def test_dedup_passages_removes_duplicates():
    """高度相似的段落被去重，保留高分。"""
    passages = [
        _passage(0, text="This is a test passage about machine learning."),
        _passage(1, text="This is a test passage about machine learning!"),  # 高度相似
        _passage(2, text="Completely different content about deep learning."),
    ]
    rationales = [_rationale("r1", "test")]
    matrix = {
        "passage_0": {"r1": 0.9},
        "passage_1": {"r1": 0.5},
        "passage_2": {"r1": 0.7},
    }
    dimension_scores = {
        "passage_0": {"r1": {"coverage": 0.9, "reliability": 0.9, "data_density": 0.9, "total_score": 0.9}},
        "passage_1": {"r1": {"coverage": 0.5, "reliability": 0.5, "data_density": 0.5, "total_score": 0.5}},
        "passage_2": {"r1": {"coverage": 0.7, "reliability": 0.7, "data_density": 0.7, "total_score": 0.7}},
    }
    coverage = _coverage_result(passages, matrix, dimension_scores)

    deduped = dedup_passages_by_rationale(passages, rationales, coverage)

    # passage_0 和 passage_1 高度相似，去重后应该少于 3 个
    assert len(deduped) <= 3


def test_dedup_passages_empty_input():
    """空输入返回空列表。"""
    result = dedup_passages_by_rationale([], [], {})
    assert result == []


def test_dedup_passages_similarity_threshold_70():
    """相似度 0.70 阈值：高于 0.70 的去重，低于 0.70 的保留。"""
    passages = [
        _passage(0, text="Carbon steel corrosion inhibition in HCl solution"),
        _passage(1, text="Carbon steel corrosion inhibition in HCl solutions"),  # 极高相似
        _passage(2, text="Aluminum alloy passivation behavior in NaCl environment"),  # 低相似
    ]
    rationales = [_rationale("r1", "test")]
    matrix = {
        "passage_0": {"r1": 0.9},
        "passage_1": {"r1": 0.5},
        "passage_2": {"r1": 0.7},
    }
    dimension_scores = {
        "passage_0": {"r1": {"coverage": 0.9, "reliability": 0.9, "data_density": 0.9, "total_score": 0.9}},
        "passage_1": {"r1": {"coverage": 0.5, "reliability": 0.5, "data_density": 0.5, "total_score": 0.5}},
        "passage_2": {"r1": {"coverage": 0.7, "reliability": 0.7, "data_density": 0.7, "total_score": 0.7}},
    }
    coverage = _coverage_result(passages, matrix, dimension_scores)

    deduped = dedup_passages_by_rationale(
        passages, rationales, coverage,
        similarity_threshold=0.70, top_k_per_rationale=15,
    )
    # passage_0 和 passage_1 高度相似 -> passage_1 被去重
    # passage_2 不相似 -> 保留
    assert len(deduped) == 2


def test_dedup_passages_top_k_per_rationale_15():
    """top_k_per_rationale=15 时超过 15 个段落只保留 15 个。"""
    diverse_texts = [
        "Corrosion inhibition mechanisms of organic compounds on carbon steel surfaces",
        "Electrochemical impedance spectroscopy analysis of inhibitor film formation",
        "Quantum chemical studies on adsorption behavior of triazole derivatives",
        "Weight loss measurements for different inhibitor concentrations in acidic media",
        "Surface morphology characterization via scanning electron microscopy images",
        "Potentiodynamic polarization curves for various inhibitor molecular structures",
        "Hydrophobic chain length effects on inhibitor adsorption free energy values",
        "Synergistic effects between halide ions and organic inhibitor molecular complexes",
        "Temperature dependence of corrosion rate and inhibitor efficiency relationship",
        "Molecular dynamics simulation of inhibitor film self-assembly on iron substrate",
        "Atomic force microscopy topography of inhibited versus uninhibited metal surface",
        "Fourier transform infrared spectroscopy identification of surface functional groups",
        "Contact angle measurements correlating with hydrophobicity and inhibition efficiency",
        "Density functional theory calculations of HOMO LUMO orbital energy gap parameters",
        "Electrochemical noise analysis for localized corrosion monitoring with inhibitors",
        "X-ray photoelectron spectroscopy depth profiling of inhibitor adsorption layers",
        "Thermogravimetric analysis of inhibitor thermal stability under heating conditions",
        "Scanning vibrating electrode technique mapping of local galvanic current density",
        "Ellipsometry measurement of inhibitor film thickness growth kinetics over time",
        "Electrochemical quartz crystal microbalance monitoring of inhibitor mass uptake",
    ]
    passages = [_passage(i, text=diverse_texts[i]) for i in range(20)]
    rationales = [_rationale("r1", "test")]
    matrix = {f"passage_{i}": {"r1": 1.0 - i * 0.01} for i in range(20)}
    dimension_scores = {
        f"passage_{i}": {"r1": {"coverage": 1.0, "reliability": 1.0, "data_density": 1.0, "total_score": 1.0 - i * 0.01}}
        for i in range(20)
    }
    coverage = _coverage_result(passages, matrix, dimension_scores)

    deduped = dedup_passages_by_rationale(
        passages, rationales, coverage,
        similarity_threshold=0.70, top_k_per_rationale=15,
    )
    assert len(deduped) == 15


# ---------- select_top_urls_by_frequency ----------

def test_select_top_urls_returns_most_frequent():
    """返回出现频次最高的 URL。"""
    passages = [
        _passage(0, url="https://a.com"),
        _passage(1, url="https://a.com"),
        _passage(2, url="https://a.com"),
        _passage(3, url="https://b.com"),
        _passage(4, url="https://b.com"),
        _passage(5, url="https://c.com"),
    ]

    result = select_top_urls_by_frequency(passages, top_n=2)

    # 返回字典列表 {"url": ..., "frequency": ...}
    assert len(result) == 2
    urls = [r["url"] for r in result]
    assert "https://a.com" in urls
    assert "https://b.com" in urls


def test_select_top_urls_empty_input():
    """空输入返回空列表。"""
    result = select_top_urls_by_frequency([], top_n=5)
    assert result == []


# ---------- split_passages_by_url ----------

def test_split_passages_separates_correctly():
    """全文覆盖的 URL 对应段落被移除。"""
    passages = [
        _passage(0, url="https://a.com"),
        _passage(1, url="https://a.com"),
        _passage(2, url="https://b.com"),
        _passage(3, url="https://c.com"),
    ]
    fetched_urls = {"https://a.com"}  # 需要是 set

    removed, remaining = split_passages_by_url(passages, fetched_urls)

    # a.com 的段落被移到 removed
    assert len(removed) == 2
    assert len(remaining) == 2
    remaining_urls = [p["doc_url"] for p in remaining]
    assert "https://a.com" not in remaining_urls
    assert "https://b.com" in remaining_urls
    assert "https://c.com" in remaining_urls


def test_split_passages_empty_fetched_urls():
    """无全文 URL 时所有段落保留在 remaining。"""
    passages = [_passage(i) for i in range(3)]

    removed, remaining = split_passages_by_url(passages, set())

    assert len(remaining) == 3
    assert len(removed) == 0


# ---------- build_classified_content ----------

def test_build_classified_content_combines_inputs():
    """全文和段落正确拼接为 classified 格式。"""
    fulltext_evidences = [
        FullTextEvidence(
            url="https://full.com",
            doc_title="Fulltext Doc",
            doc_time="2024-01-01",
            original_content="Full text content here.",
            coverage_scores={"r1": {"coverage": 0.8}},
            citation_index=1,
            fetch_success=True,
        )
    ]
    remaining_passages = [
        _passage(10, url="https://passage.com", title="Passage Doc", text="Passage content."),
    ]

    classified = build_classified_content(fulltext_evidences, remaining_passages)

    # 至少有全文和段落的组合
    assert len(classified) >= 1
    # 第一个应该是全文（index 从 1 开始）
    assert classified[0]["index"] == 1
    assert classified[0]["is_fulltext"] is True


def test_build_classified_content_empty_fulltext():
    """无全文时只有段落。"""
    passages = [_passage(i) for i in range(3)]

    classified = build_classified_content([], passages)

    assert len(classified) == 3
    # index 从 1 开始
    assert classified[0]["index"] == 1
    assert classified[0]["is_fulltext"] is False


def test_build_classified_content_empty_inputs():
    """空输入返回空列表。"""
    result = build_classified_content([], [])
    assert result == []


def test_build_classified_content_data_density_fulltext():
    """全文条目的 data_density 取文档级顶层字段（评估时按文档整体评估）。"""
    evidence = FullTextEvidence(
        url="https://full.com",
        doc_title="Fulltext Doc",
        doc_time="2024-01-01",
        original_content="Full text content here.",
        reliability=0.8,
        data_density=0.95,
        coverage_scores={
            "r1": {"coverage": 0.9},
            "r2": {"coverage": 0.5},
        },
        citation_index=1,
        fetch_success=True,
    )

    classified = build_classified_content([evidence], [])

    assert len(classified) == 1
    # 文档级顶层字段，而非按 rationale 取最大
    assert classified[0]["data_density"] == 0.95
    assert classified[0]["reliability"] == 0.8


def test_build_classified_content_data_density_passage():
    """段落的 data_density 直接取 passage 顶层字段（评估时按段落整体评估）。"""
    passages = [
        _passage(
            1,
            url="https://p.com",
            title="P",
            text="text",
            scores={
                "r1": {"coverage": 0.9},
            },
        ),
    ]
    passages[0]["reliability"] = 0.6
    passages[0]["data_density"] = 0.85

    classified = build_classified_content([], passages)

    assert classified[0]["data_density"] == 0.85
    assert classified[0]["reliability"] == 0.6


def test_build_classified_content_data_density_missing():
    """passage 顶层无 data_density 时归 0，不抛异常。"""
    passages = [_passage(1, url="https://p.com", title="P", text="text", scores={"r1": {"coverage": 0.5}})]

    classified = build_classified_content([], passages)

    assert classified[0]["data_density"] == 0.0
    assert classified[0]["reliability"] == 0.0


def test_build_classified_content_passage_has_both_text_fields():
    """段落项同时有 passage_text（切片）和 original_content（全文）。"""
    passages = [
        _passage(
            1,
            url="https://p.com",
            title="P",
            text="CPI上涨0.8%",
            original_content="CPI上涨0.8%，PPI下降1.2%，GDP增长5%...",
        ),
    ]

    classified = build_classified_content([], passages)

    assert len(classified) == 1
    item = classified[0]
    assert item["is_fulltext"] is False
    assert item["passage_text"] == "CPI上涨0.8%"
    assert item["original_content"] == "CPI上涨0.8%，PPI下降1.2%，GDP增长5%..."


def test_build_classified_content_fulltext_has_no_passage_text():
    """全文项没有 passage_text，只有 original_content。"""
    evidence = FullTextEvidence(
        url="https://full.com",
        doc_title="Fulltext Doc",
        doc_time="2024-01-01",
        original_content="Full text content here.",
        coverage_scores={"r1": {"coverage": 0.8}},
        citation_index=1,
        fetch_success=True,
    )

    classified = build_classified_content([evidence], [])

    assert len(classified) == 1
    item = classified[0]
    assert item["is_fulltext"] is True
    assert "passage_text" not in item
    assert item["original_content"] == "Full text content here."


def test_build_classified_content_passage_text_independent_per_passage():
    """同 URL 不同段落的 passage_text 各自独立，original_content 相同。"""
    passages = [
        _passage(1, url="https://same.com", title="Same", text="段落A", original_content="全文"),
        _passage(2, url="https://same.com", title="Same", text="段落B", original_content="全文"),
    ]

    classified = build_classified_content([], passages)

    assert len(classified) == 2
    assert classified[0]["passage_text"] == "段落A"
    assert classified[1]["passage_text"] == "段落B"
    assert classified[0]["original_content"] == "全文"
    assert classified[1]["original_content"] == "全文"
