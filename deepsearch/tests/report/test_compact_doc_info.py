from openjiuwen_deepsearch.algorithm.report.compact_doc_info import (
    build_structured_evidence_guide,
)


def test_build_structured_evidence_guide_maps_selected_docs_to_dimensions():
    first = {
        "index": 1,
        "doc_title": "Market report",
        "key_passages": ["Market reached 10 billion.", "Growth was 20%.", "Unused third passage."],
    }
    second = {
        "index": 2,
        "doc_title": "Risk report",
        "key_passages": ["Costs remain high."],
    }
    rationales = [
        {"id": "R1", "description": "Market size", "priority": "primary"},
        {"id": "R2", "description": "Technical risks", "priority": "supplementary"},
        {"id": "R3", "description": "Policy impact", "priority": "primary"},
    ]
    coverage_result = {
        "filtered_passages": [first, second],
        "coverage_matrix": {
            "passage_0": {"R1": 0.9, "R2": 0.1, "R3": 0.0},
            "passage_1": {"R1": 0.2, "R2": 0.4, "R3": 0.0},
        },
    }

    guide = build_structured_evidence_guide(
        [first, second], rationales, coverage_result,
        selected_passage_keys=["passage_0", "passage_1"],
    )

    assert "R1 [primary, covered]: Market size" in guide
    assert "[citation:1] Market report (coverage: 0.90)" in guide
    assert "Market reached 10 billion." not in guide
    assert "Growth was 20%." not in guide
    assert "R2 [supplementary, weak]: Technical risks" in guide
    assert "[citation:2] Risk report (coverage: 0.40)" in guide
    assert "R3 [primary, uncovered]: Policy impact" in guide


def test_build_structured_evidence_guide_returns_empty_without_matrix_rows():
    doc = {"index": 1, "doc_title": "Document", "key_passages": ["Evidence"]}

    assert build_structured_evidence_guide(
        [doc],
        [{"id": "R1", "description": "Dimension"}],
        {"filtered_passages": [doc], "coverage_matrix": {}},
        selected_passage_keys=["passage_0"],
    ) == ""


def test_build_structured_evidence_guide_limits_each_rationale_to_top_three_citations():
    docs = [
        {"index": index + 1, "doc_title": f"Document {index + 1}", "key_passages": ["unused"]}
        for index in range(4)
    ]
    guide = build_structured_evidence_guide(
        docs,
        [{"id": "R1", "description": "Market size", "priority": "primary"}],
        {
            "coverage_matrix": {
                "passage_0": {"R1": 0.4},
                "passage_1": {"R1": 0.9},
                "passage_2": {"R1": 0.7},
                "passage_3": {"R1": 0.8},
            },
        },
        selected_passage_keys=["passage_0", "passage_1", "passage_2", "passage_3"],
    )

    assert "[citation:2] Document 2 (coverage: 0.90)" in guide
    assert "[citation:4] Document 4 (coverage: 0.80)" in guide
    assert "[citation:3] Document 3 (coverage: 0.70)" in guide
    assert "[citation:1] Document 1" not in guide
    assert "unused" not in guide


def test_build_structured_evidence_guide_returns_empty_for_misaligned_doc_keys(caplog):
    caplog.set_level("WARNING")
    doc = {"index": 1, "doc_title": "Document", "key_passages": ["Evidence"]}

    assert build_structured_evidence_guide(
        [doc],
        [{"id": "R1", "description": "Dimension"}],
        {
            "filtered_passages": [doc],
            "coverage_matrix": {"passage_0": {"R1": 0.9}},
        },
        selected_passage_keys=[],
    ) == ""
    assert "selected passages and stable keys are misaligned" in caplog.text
