from openjiuwen_deepsearch.algorithm.report.compact_doc_info import (
    build_structured_evidence_guide,
)
from openjiuwen_deepsearch.algorithm.report.report import Reporter


def test_build_structured_evidence_guide_maps_selected_docs_to_dimensions():
    first = {
        "index": 1,
        "title": "Market report",
        "key_passages": ["Market reached 10 billion.", "Growth was 20%.", "Unused third passage."],
    }
    second = {
        "index": 2,
        "title": "Risk report",
        "key_passages": ["Costs remain high."],
    }
    rationales = [
        {"id": "R1", "description": "Market size", "priority": "primary"},
        {"id": "R2", "description": "Technical risks", "priority": "supplementary"},
        {"id": "R3", "description": "Policy impact", "priority": "primary"},
    ]
    coverage_result = {
        "filtered_docs": [first, second],
        "coverage_matrix": {
            "doc_0": {"R1": 0.9, "R2": 0.1, "R3": 0.0},
            "doc_1": {"R1": 0.2, "R2": 0.4, "R3": 0.0},
        },
    }

    guide = build_structured_evidence_guide(
        [first, second], rationales, coverage_result,
        selected_doc_keys=["doc_0", "doc_1"],
    )

    assert "R1 [primary, covered]: Market size" in guide
    assert "[citation:1] Market report (coverage: 0.90)" in guide
    assert "Market reached 10 billion." not in guide
    assert "Growth was 20%." not in guide
    assert "R2 [supplementary, weak]: Technical risks" in guide
    assert "[citation:2] Risk report (coverage: 0.40)" in guide
    assert "R3 [primary, uncovered]: Policy impact" in guide


def test_build_structured_evidence_guide_returns_empty_without_matrix_rows():
    doc = {"index": 1, "title": "Document", "key_passages": ["Evidence"]}

    assert build_structured_evidence_guide(
        [doc],
        [{"id": "R1", "description": "Dimension"}],
        {"filtered_docs": [doc], "coverage_matrix": {}},
        selected_doc_keys=["doc_0"],
    ) == ""


def test_build_structured_evidence_guide_limits_each_rationale_to_top_three_citations():
    docs = [
        {"index": index + 1, "title": f"Document {index + 1}", "key_passages": ["unused"]}
        for index in range(4)
    ]
    guide = build_structured_evidence_guide(
        docs,
        [{"id": "R1", "description": "Market size", "priority": "primary"}],
        {
            "coverage_matrix": {
                "doc_0": {"R1": 0.4},
                "doc_1": {"R1": 0.9},
                "doc_2": {"R1": 0.7},
                "doc_3": {"R1": 0.8},
            },
        },
        selected_doc_keys=["doc_0", "doc_1", "doc_2", "doc_3"],
    )

    assert "[citation:2] Document 2 (coverage: 0.90)" in guide
    assert "[citation:4] Document 4 (coverage: 0.80)" in guide
    assert "[citation:3] Document 3 (coverage: 0.70)" in guide
    assert "[citation:1] Document 1" not in guide
    assert "unused" not in guide


def test_build_structured_evidence_guide_returns_empty_for_misaligned_doc_keys(caplog):
    caplog.set_level("WARNING")
    doc = {"index": 1, "title": "Document", "key_passages": ["Evidence"]}

    assert build_structured_evidence_guide(
        [doc],
        [{"id": "R1", "description": "Dimension"}],
        {
            "filtered_docs": [doc],
            "coverage_matrix": {"doc_0": {"R1": 0.9}},
        },
        selected_doc_keys=[],
    ) == ""
    assert "selected docs and stable keys are misaligned" in caplog.text


def test_log_structured_evidence_build_keeps_info_diagnostics_compact(caplog):
    caplog.set_level("INFO")
    Reporter._log_structured_evidence_build(
        section_idx=2,
        rationales=[{"id": "R1", "description": "Market size"}],
        coverage_result={"coverage_matrix": {"doc_0": {"R1": 0.9}}},
        classified_doc_infos=[{
            "index": 1,
            "title": "Market report",
            "url": "https://example.com/market",
            "key_passages": ["Market reached 10 billion."],
            "original_content": "FULL CONTENT MUST NOT BE LOGGED",
        }],
        structured_evidence_guide=(
            "Structured evidence guidance:\n"
            "- R1 [primary, covered]: Market size"
        ),
    )

    assert "[structured_evidence][build]" in caplog.text
    assert "rationales=1" in caplog.text
    assert "covered=1" in caplog.text
    assert "weak=0" in caplog.text
    assert "uncovered=0" in caplog.text
    assert "guide_hash=" in caplog.text
    assert "coverage_result=" not in caplog.text
    assert "classified_doc_infos=" not in caplog.text
    assert "structured_evidence_guide=" not in caplog.text
    assert "Market reached 10 billion." not in caplog.text
    assert "https://example.com/market" not in caplog.text
    assert "FULL CONTENT MUST NOT BE LOGGED" not in caplog.text


def test_log_structured_evidence_target_checks_exact_guide_and_citation_blocks(caplog):
    caplog.set_level("INFO")
    Reporter._log_structured_evidence_target(
        section_idx=3,
        target="sub_report",
        llm_input=[{
            "role": "user",
            "content": (
                "# Structured Evidence Guidance\nwrong guide\n"
                "# Collected Evidence\n[citation:1 begin]evidence"
            ),
        }],
        structured_evidence_guide="Structured evidence guidance:\n- expected guide",
    )

    assert "guide_present=true" in caplog.text
    assert "exact_guide_in_input=false" in caplog.text
    assert "citation_blocks=1" in caplog.text
    assert "balanced_citation_blocks=false" in caplog.text
    assert "expected guide" not in caplog.text


def test_log_structured_evidence_target_accepts_zero_expected_citation_blocks(caplog):
    caplog.set_level("INFO")
    Reporter._log_structured_evidence_target(
        section_idx=2,
        target="sub_report",
        llm_input=[{
            "role": "user",
            "content": "# Collected Evidence\n\n# References\n[]",
        }],
        structured_evidence_guide="",
        expected_citation_blocks=0,
    )

    assert "citation_blocks=0" in caplog.text
    assert "balanced_citation_blocks=true" in caplog.text
