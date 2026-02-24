import re
import json
from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.report_template.template_utils import TemplateUtils


def test_pdf_base64_to_markdown_from_file():
    """Integration test: read base64 from JSON, convert to markdown and verify heading counts."""
    json_path = Path(__file__).parent / "template_utils_test_cases.json"
    if not json_path.exists():
        pytest.skip(f"No test JSON found at {json_path}; skipping integration test.")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    pdf_b64 = data.get("finance_report.pdf")
    if not pdf_b64:
        pytest.skip("No base64 string for finance_report.pdf in JSON.")

    # Set expected counts here
    expected_h1 = 1
    expected_h2 = 5
    expected_h3 = 10

    md = TemplateUtils.pdf_base64_to_markdown(pdf_b64)
    assert isinstance(md, str)

    h1 = h2 = h3 = 0
    for line in md.splitlines():
        m = re.match(r'^\s*(#+)\s+', line)
        if not m:
            continue
        lvl = len(m.group(1))
        if lvl == 1:
            h1 += 1
        elif lvl == 2:
            h2 += 1
        elif lvl == 3:
            h3 += 1

    assert h1 == expected_h1, f"H1 count mismatch: got {h1}, expected {expected_h1}"
    assert h2 == expected_h2, f"H2 count mismatch: got {h2}, expected {expected_h2}"
    assert h3 == expected_h3, f"H3 count mismatch: got {h3}, expected {expected_h3}"



def test_word_base64_to_markdown_from_file():
    """Integration test: read base64 from JSON, convert to markdown and verify heading counts."""
    json_path = Path(__file__).parent / "template_utils_test_cases.json"
    if not json_path.exists():
        pytest.skip(f"No test JSON found at {json_path}; skipping integration test.")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    docx_b64 = data.get("protein_report.docx")
    if not docx_b64:
        pytest.skip("No base64 string for protein_report.docx in JSON.")

    expected_h1 = 7
    expected_h2 = 8
    expected_h3 = 0

    md = TemplateUtils.word_base64_to_markdown(docx_b64)
    assert isinstance(md, str)

    h1 = h2 = h3 = 0
    for line in md.splitlines():
        m = re.match(r'^\s*(#+)\s+', line)
        if not m:
            continue
        lvl = len(m.group(1))
        if lvl == 1:
            h1 += 1
        elif lvl == 2:
            h2 += 1
        elif lvl == 3:
            h3 += 1

    assert h1 == expected_h1, f"DOCX H1 count mismatch: got {h1}, expected {expected_h1}"
    assert h2 == expected_h2, f"DOCX H2 count mismatch: got {h2}, expected {expected_h2}"
    assert h3 == expected_h3, f"DOCX H3 count mismatch: got {h3}, expected {expected_h3}"


def test_postprocess_structure_keep_content():
    """Unit test for TemplateUtils.postprocess_structure_keep_content.
    Covers two scenarios:
    - single H1 present: H2 should be promoted to H1, deep (>=H4) sections removed but sibling
        content preserved as appropriate.
    - no single H1: H3+ sections and their content should be removed while H2 sections kept.
    """
    # Case 1: single H1 present
    input_md = "\n".join([
        "# Title",
        "## Section 1",
        "Some paragraph.",
        "### Subsection 1.1",
        "Detail of subsection.",
        "#### Sub-subsection",
        "More details.",
        "## Section 2",
        "Paragraph 2",
    ])

    out = TemplateUtils.postprocess_structure_keep_content(input_md)
    lines = [l for l in out.splitlines() if l.strip() != ""]

    # H1 (original Title) should be removed; first H2 promoted to H1
    assert lines[0].startswith("# Section 1")
    # paragraph under Section 1 should be present
    assert any("Some paragraph." == l for l in lines)
    # Subsection promoted to H2
    assert any(l.startswith("## ") and "Subsection 1.1" in l for l in lines)
    # Content inside the skipped deep section (####) should be removed
    assert "More details." not in out
    # Section 2 should be present and promoted to H1
    assert any(l.startswith("# Section 2") for l in lines)

    # Case 2: no single H1 (h1_count != 1) -> H3+ sections and their content removed
    input2 = "\n".join([
        "## A",
        "### a1",
        "Content a1",
        "## B",
        "Content B",
    ])

    out2 = TemplateUtils.postprocess_structure_keep_content(input2)
    # The H3 heading and its content should be removed
    assert "### a1" not in out2
    assert "Content a1" not in out2
    # H2 sections should remain
    assert "## A" in out2
    assert "## B" in out2

def test_postprocess_structure():
    """Unit test for TemplateUtils.postprocess_structure.
    Covers:
    - h1_count == 1: original H1 removed, H2->H1 and H3->H2; content text dropped.
    - h1_count == 0: H2->H1 and H3->H2; content text dropped..
    - h1_count > 1: H3 headings dropped; H1 and H2 preserved.
    """
    # Case 1: h1_count == 1
    input_md = "\n".join([
        "# Document Title",
        "## Intro",
        "Intro paragraph.",
        "### Details",
        "Detail line",
        "## Conclusion",
        "Final paragraph",
    ])

    out = TemplateUtils.postprocess_structure(input_md)
    # H1 removed and first H2 promoted to H1
    assert out.splitlines()[0].startswith("# Intro")
    # H3 promoted to H2
    assert any(line.startswith("## ") and "Details" in line for line in out.splitlines())
    # Content removed
    assert "Intro paragraph." not in out
    assert "Final paragraph" not in out

    # Case 2: h1_count == 0
    input2 = "\n".join([
        "## A",
        "### a1",
        "Content a1",
        "## B",
        "Content B",
    ])

    out2 = TemplateUtils.postprocess_structure(input2)

    # heading transform
    assert "# A" in out2
    assert "# B" in out2
    assert "## a1" in out2
    # Content removed
    assert "Content a1" not in out2
    assert "Content B" not in out2

    # Case 3: h1_count > 1
    input3 = "\n".join([
        "# A",
        "# B",
        "## b1",
        "### b1-1",
        "content",
    ])

    out3 = TemplateUtils.postprocess_structure(input3)

    # H1 and H2 preserved
    assert "# A" in out3
    assert "# B" in out3
    assert "## b1" in out3
    # H3 removed
    assert "### b1-1" not in out3
    # content removed
    assert "content" not in out3
