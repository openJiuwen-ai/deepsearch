import base64
import re
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
import markdown
import pytest

from server.deepsearch.core.manager.report_manager.conversion_utils import (
    normalize_docx_tables,
    postprocess_html,
    preprocess_markdown_text,
    protect_math_spans,
    restore_math_spans,
    wrap_html_tables,
)
from server.deepsearch.core.manager.report_manager.docx_export import convert_md_to_docx
from server.deepsearch.core.manager.report_manager.html_export import convert_md_to_html
from server.deepsearch.core.manager.report_manager.mermaid_offline import (
    ensure_mermaid_cli,
    render_mermaid_offline,
)
from server.deepsearch.core.manager.report_manager.mermaid_preprocess import (
    MermaidRenderOptions,
    extract_xychart_metadata,
    preprocess_mermaid_code,
)
from server.deepsearch.core.manager.report_manager.report_processor import ReportHtml, ReportWord
from server.deepsearch.core.manager.report_manager.word_utils import (
    _normalize_latex_for_omml,
    html_to_doc,
    set_global_styles,
)


TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO"
    "+/p9sAAAAASUVORK5CYII="
)


def test_set_global_styles_uses_compact_line_spacing():
    """Validate generated DOCX paragraphs use compact line spacing."""
    document = Document()

    set_global_styles(document)

    paragraph_format = document.styles["Normal"].paragraph_format
    assert paragraph_format.line_spacing_rule == WD_LINE_SPACING.MULTIPLE
    assert paragraph_format.line_spacing == 1.15


def test_ensure_mermaid_cli_returns_unavailable_when_missing(monkeypatch):
    """Validate Mermaid CLI detection when the executable is unavailable.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        None.
    """
    monkeypatch.delenv("MERMAID_MMDC_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "server.deepsearch.core.manager.report_manager.mermaid_offline.resolve_mmdc_path",
        lambda: None,
    )

    status = ensure_mermaid_cli()

    assert status.available is False


def test_render_mermaid_offline_returns_false_when_cli_missing(tmp_path, monkeypatch):
    """Validate Mermaid rendering fallback when Mermaid CLI is missing.

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest monkeypatch fixture。

    Returns:
        None.
    """
    monkeypatch.delenv("MERMAID_MMDC_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "server.deepsearch.core.manager.report_manager.mermaid_offline.resolve_mmdc_path",
        lambda: None,
    )

    ok = render_mermaid_offline(
        "graph TD\nA-->B",
        tmp_path / "diagram.svg",
        output_format="svg",
    )

    assert ok is False


def test_preprocess_mermaid_code_scales_xychart_and_extracts_metadata():
    """Validate xychart preprocessing keeps parity with the reference offline flow.

    Returns:
        None.
    """
    processed, supplement = preprocess_mermaid_code(
        "xychart-beta\n  bar [1200]\n",
        MermaidRenderOptions(),
    )
    metadata = extract_xychart_metadata(processed, warn_on_invalid=False)

    assert supplement == ""
    assert 'y-axis "x1e3"' in processed
    assert "bar [1.2]" in processed
    assert len(metadata.series) == 1
    assert metadata.series[0].display_values == ["1.2"]


def test_preprocess_markdown_text_strips_internal_citation_markers():
    """Validate export preprocessing hides internal citation control markers.

    Returns:
        None.
    """
    text = (
        "保留引用[checked_citation:4][[5]](https://example.com/source)\n\n"
        "移除旧标记[citation:2]"
    )

    processed = preprocess_markdown_text(text)

    assert "checked_citation" not in processed
    assert "[citation:2]" not in processed
    assert '[5]</a>' in processed


def test_wrap_html_tables_adds_centering_container_once():
    """Validate HTML table wrapping is idempotent.

    Returns:
        None.
    """
    html_text = "<p>intro</p><table><tr><td>A</td></tr></table>"

    processed = wrap_html_tables(html_text)
    processed_twice = wrap_html_tables(processed)

    assert '<div class="table-wrap"><table>' in processed
    assert processed.count('class="table-wrap"') == 1
    assert processed_twice.count('class="table-wrap"') == 1


def test_postprocess_html_wraps_tables_without_rewriting_svg():
    """Validate table wrapping does not mutate Mermaid SVG HTML.

    Returns:
        None.
    """
    html_text = (
        '<div class="mermaid-rendered"><svg viewBox="0 0 100 100"></svg></div>'
        "<table><tr><td>A</td></tr></table>"
    )

    processed = postprocess_html(html_text)

    assert 'viewBox="0 0 100 100"' in processed
    assert '<div class="table-wrap"><table>' in processed


def test_report_html_convert_from_markdown_wraps_tables():
    """Validate direct HTML conversion wraps Markdown tables.

    Returns:
        None.
    """
    html_text = ReportHtml.convert_from_markdown("| A | B |\n|---|---|\n| 1 | 2 |")

    assert 'class="table-wrap"' in html_text
    assert "<table>" in html_text


def test_report_html_convert_from_markdown_uses_shared_safe_math_handling():
    """Validate direct HTML conversion shares offline math/currency behavior."""
    html_text = ReportHtml.convert_from_markdown("变量 $G$ 保留为公式，价格 $4 和 $5 保持文本。")

    assert r"\(G\)" in html_text
    assert "$4 和 $5" in html_text
    assert "inlineMath: [['$', '$']" not in html_text


def test_report_word_convert_from_markdown_keeps_wrapped_tables():
    """Validate online DOCX conversion keeps tables wrapped for HTML centering.

    Returns:
        None.
    """
    doc = ReportWord.convert_from_markdown("| A | B |\n|---|---|\n| 1 | 2 |")

    assert len(doc.tables) == 1
    assert doc.tables[0].cell(0, 0).text == "A"
    assert doc.tables[0].cell(1, 1).text == "2"


def test_report_word_convert_from_markdown_uses_shared_safe_math_handling():
    """Validate direct DOCX conversion renders math without converting currency."""
    doc = ReportWord.convert_from_markdown("变量 $G$ 保留为公式，价格 $4 和 $5 保持文本。")
    paragraph_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)

    assert r"\(G\)" not in paragraph_text
    assert "$4 和 $5" in paragraph_text


def test_report_word_convert_from_html_handles_irregular_table_rows():
    """Validate HTML table conversion tolerates rows with different cell counts."""
    html_text = (
        '<div class="report-container">'
        "<table>"
        "<tr><th>A</th><th>B</th></tr>"
        "<tr><td>1</td><td>2</td><td>3</td></tr>"
        "</table>"
        "</div>"
    )

    doc = ReportWord._html_to_word(html_text)

    assert len(doc.tables) == 1
    assert len(doc.tables[0].columns) == 3
    assert doc.tables[0].cell(0, 2).text == ""
    assert doc.tables[0].cell(1, 2).text == "3"


def test_report_word_convert_from_html_limits_nested_block_depth():
    """Validate deeply nested HTML is flattened instead of recursing indefinitely."""
    html_text = (
        '<div class="report-container">'
        + "<div>" * 120
        + "<p>深层内容</p>"
        + "</div>" * 120
        + "</div>"
    )

    doc = ReportWord._html_to_word(html_text)

    assert any("深层内容" in paragraph.text for paragraph in doc.paragraphs)


def test_html_to_doc_embeds_relative_images_from_base_path(tmp_path):
    """Validate pure-Python HTML conversion embeds bundle-local image files."""
    image_dir = tmp_path / "charts"
    image_dir.mkdir()
    (image_dir / "chart_0.png").write_bytes(base64.b64decode(TINY_PNG_BASE64))
    document = Document()
    style_map = {
        "heading1": "heading 1",
        "heading2": "heading 2",
        "heading3": "heading 3",
        "heading4": "heading 4",
        "heading5": "heading 5",
        "heading6": "heading 6",
        "heading7": "heading 7",
        "heading8": "heading 8",
        "heading9": "heading 9",
        "paragraph": "Normal",
        "table": "Table Grid",
        "default": "Normal",
    }

    html_to_doc(
        document,
        '<div class="report-container"><p><img src="charts/chart_0.png" alt="Chart"></p></div>',
        style_map,
        base_path=tmp_path,
    )

    assert len(document.inline_shapes) == 1


def test_html_to_doc_limits_large_images_to_page_width(tmp_path):
    """Validate pure-Python DOCX conversion scales oversized images to fit the page."""
    image = pytest.importorskip("PIL.Image")
    image_dir = tmp_path / "charts"
    image_dir.mkdir()
    image_path = image_dir / "large_chart.png"
    image.new("RGB", (2400, 800), color="white").save(image_path)
    document = Document()
    style_map = {
        "heading1": "heading 1",
        "heading2": "heading 2",
        "heading3": "heading 3",
        "heading4": "heading 4",
        "heading5": "heading 5",
        "heading6": "heading 6",
        "heading7": "heading 7",
        "heading8": "heading 8",
        "heading9": "heading 9",
        "paragraph": "Normal",
        "table": "Table Grid",
        "default": "Normal",
    }

    html_to_doc(
        document,
        '<div class="report-container"><p><img src="charts/large_chart.png" alt="Chart"></p></div>',
        style_map,
        base_path=tmp_path,
    )

    max_width = document.sections[0].page_width - document.sections[0].left_margin - document.sections[0].right_margin
    assert document.inline_shapes[0].width <= max_width


def test_convert_md_to_docx_embeds_relative_images(tmp_path):
    """Validate pure-Python DOCX export embeds bundle-local image files."""
    md_path = tmp_path / "report.md"
    image_dir = tmp_path / "charts"
    image_dir.mkdir()
    (image_dir / "chart_0.png").write_bytes(base64.b64decode(TINY_PNG_BASE64))
    docx_path = tmp_path / "report.docx"
    md_path.write_text("# Title\n\n![Chart](charts/chart_0.png)\n", encoding="utf-8")

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    assert docx_path.exists()
    assert len(document.inline_shapes) == 1


def test_convert_md_to_docx_keeps_citations_as_superscript_links(tmp_path):
    """Validate DOCX export preserves citation links as superscript text."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text("正文需要引用[[1]](https://example.com/source)。\n", encoding="utf-8")

    convert_md_to_docx(md_path, docx_path)

    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    assert '<w:vertAlign w:val="superscript"/>' in document_xml
    assert "[1]" in document_xml


def test_convert_md_to_docx_keeps_code_blocks(tmp_path):
    """Validate DOCX export preserves fenced code block content."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text("# Title\n\n```python\nprint('hello')\n```\n", encoding="utf-8")

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    assert any("print('hello')" in paragraph.text for paragraph in document.paragraphs)


def test_convert_md_to_docx_keeps_citation_links_in_math_paragraphs(tmp_path):
    """Validate math paragraphs keep inline citation links and superscript formatting."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text("公式 $x^2$ 后引用[[1]](https://example.com/source)。\n", encoding="utf-8")

    convert_md_to_docx(md_path, docx_path)

    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
        document_rels = zip_file.read("word/_rels/document.xml.rels").decode("utf-8")
    assert '<w:vertAlign w:val="superscript"/>' in document_xml
    assert "[1]" in document_xml
    assert "https://example.com/source" in document_rels


def test_convert_md_to_docx_keeps_table_cell_links(tmp_path):
    """Validate DOCX export preserves hyperlinks inside table cells."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "| Source |\n|---|\n| [link](https://example.com/source) |\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    with zipfile.ZipFile(docx_path) as zip_file:
        document_rels = zip_file.read("word/_rels/document.xml.rels").decode("utf-8")
    assert document.tables[0].cell(1, 0).text == "link"
    assert "https://example.com/source" in document_rels


def test_convert_md_to_html_keeps_indented_markdown_list_items_out_of_code_blocks(tmp_path):
    """Validate report-style indented bullet lines render as lists, not code blocks."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "\n".join(
            [
                "![Chart](charts/chart.png)",
                "<font size=2>**图表**: 说明</font>[[1]](https://example.com/source)",
                "    - **逻辑二：软硬解耦与异构全栈底座打破算力垄断**。构建兼容异构芯片的软件栈至关重要[[1]](https://example.com/source)。",
            ]
        ),
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert "<pre><code>" not in html_text
    assert "<li>" in html_text
    assert "逻辑二：软硬解耦" in html_text


def test_convert_md_to_html_keeps_consecutive_indented_list_items_at_same_level(tmp_path):
    """Validate consecutive report-style indented bullets stay sibling list items."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "\n".join(
            [
                "Caption line",
                "    - first item",
                "    - second item",
            ]
        ),
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert "<pre><code>" not in html_text
    assert html_text.count("<ul>") == 1
    assert html_text.count("<li>") == 2
    assert "first item" in html_text
    assert "second item" in html_text


def test_convert_md_to_html_keeps_nested_list_level_across_chart_block(tmp_path):
    """Validate a chart inserted between nested items does not end the nested list."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "- **开源与闭源博弈的多维透视**：\n"
        "    - **地缘维度**：第一条\n"
        "\n"
        "![日本Top10](chart.png)\n"
        "<font size=2>**日本Top10**: 图注</font>\n"
        "    - **生态维度**：第二条\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    parent_item = next(
        item
        for item in soup.find_all("li")
        if "开源与闭源博弈的多维透视" in item.get_text()
    )
    nested_items = parent_item.find("ul", recursive=False).find_all("li", recursive=False)
    assert len(nested_items) == 2
    assert "地缘维度" in nested_items[0].get_text()
    assert "生态维度" in nested_items[1].get_text()
    assert nested_items[0].find("img", alt="日本Top10") is not None


def test_report_converters_keep_nested_list_level_across_font_description():
    """Validate a font description does not promote following nested items."""
    markdown = (
        "- **多维度协同分析**：\n"
        "\n"
        "<font size=2>**边缘智能体多维度协同分析**: 描述。</font>\n"
        "    - **技术维度**：内容。\n"
        "    - **经济维度**：内容。\n"
    )

    html = ReportHtml.convert_from_markdown(markdown)
    soup = BeautifulSoup(html, "html.parser")
    parent_item = next(item for item in soup.find_all("li") if "多维度协同分析" in item.get_text())
    nested_items = parent_item.find("ul", recursive=False).find_all("li", recursive=False)
    assert [item.get_text(strip=True) for item in nested_items] == [
        "技术维度：内容。",
        "经济维度：内容。",
    ]

    document = ReportWord.convert_from_markdown(markdown)
    paragraphs = {paragraph.text: paragraph for paragraph in document.paragraphs}
    parent = next(paragraph for paragraph in document.paragraphs if paragraph.text.startswith("多维度协同分析："))
    parent_num_pr = parent._p.pPr.numPr
    assert parent_num_pr.ilvl.val == 0
    for text in ("技术维度：内容。", "经济维度：内容。"):
        child_num_pr = paragraphs[text]._p.pPr.numPr
        assert child_num_pr.numId.val == parent_num_pr.numId.val
        assert child_num_pr.ilvl.val == 1


def test_html_to_doc_keeps_nested_list_items_as_separate_paragraphs():
    """Validate nested HTML lists do not collapse child item text into the parent paragraph."""
    document = Document()
    style_map = {
        "heading1": "heading 1",
        "heading2": "heading 2",
        "heading3": "heading 3",
        "heading4": "heading 4",
        "heading5": "heading 5",
        "heading6": "heading 6",
        "heading7": "heading 7",
        "heading8": "heading 8",
        "heading9": "heading 9",
        "paragraph": "Normal",
        "table": "Table Grid",
        "default": "Normal",
    }

    html_to_doc(
        document,
        (
            '<div class="report-container">'
            "<ul><li>first<ul><li>second</li></ul></li><li>third</li></ul>"
            "</div>"
        ),
        style_map,
    )

    assert [paragraph.text for paragraph in document.paragraphs] == ["first", "second", "third"]


def test_html_to_doc_indents_nested_list_items():
    """Validate DOCX paragraphs preserve nested list depth and native bullets."""
    document = Document()
    style_map = {
        "paragraph": "Normal",
        "default": "Normal",
    }

    html_to_doc(
        document,
        (
            '<div class="report-container">'
            "<ul><li>parent<ul><li>child</li></ul></li><li>sibling</li></ul>"
            "</div>"
        ),
        style_map,
    )

    parent, child, sibling = document.paragraphs
    assert parent.paragraph_format.left_indent is None
    assert child.paragraph_format.left_indent.pt == 18
    assert sibling.paragraph_format.left_indent is None
    parent_num_pr = parent._p.pPr.numPr
    child_num_pr = child._p.pPr.numPr
    sibling_num_pr = sibling._p.pPr.numPr
    assert parent_num_pr.numId.val == child_num_pr.numId.val == sibling_num_pr.numId.val
    assert parent_num_pr.ilvl.val == 0
    assert child_num_pr.ilvl.val == 1
    assert sibling_num_pr.ilvl.val == 0
    numbering_xml = document.part.numbering_part.element.xml
    assert 'w:numFmt w:val="bullet"' in numbering_xml
    assert 'w:lvlText w:val="•"' in numbering_xml
    assert 'w:lvlText w:val="◦"' in numbering_xml


def test_html_to_doc_uses_native_numbering_for_ordered_lists():
    """Validate ordered HTML lists become native multilevel Word numbering."""
    document = Document()

    html_to_doc(
        document,
        '<div class="report-container"><ol><li>first<ol><li>child</li></ol></li><li>second</li></ol></div>',
        {"paragraph": "Normal", "default": "Normal"},
    )

    first, child, second = document.paragraphs
    first_num_pr = first._p.pPr.numPr
    child_num_pr = child._p.pPr.numPr
    second_num_pr = second._p.pPr.numPr
    assert first_num_pr.numId.val == child_num_pr.numId.val == second_num_pr.numId.val
    assert first_num_pr.ilvl.val == 0
    assert child_num_pr.ilvl.val == 1
    assert second_num_pr.ilvl.val == 0
    numbering_xml = document.part.numbering_part.element.xml
    assert 'w:numFmt w:val="decimal"' in numbering_xml
    assert 'w:lvlText w:val="%1."' in numbering_xml


def test_report_table_css_preserves_global_width_and_centers_wrapped_tables():
    """Validate report CSS limits table centering changes to wrapped tables.

    Returns:
        None.
    """
    css_path = Path("server/deepsearch/core/manager/report_manager/css/style.css")
    css_text = css_path.read_text(encoding="utf-8")

    assert re.search(r"table\s*\{[^}]*width:\s*100%;", css_text, flags=re.DOTALL)
    assert re.search(
        r"\.table-wrap\s+table\s*\{[^}]*width:\s*auto;[^}]*max-width:\s*100%;[^}]*margin:\s*0\s+auto;",
        css_text,
        flags=re.DOTALL,
    )


def test_report_html_export_renders_mermaid_or_falls_back(tmp_path):
    """Validate HTML export renders Mermaid or preserves source as fallback.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": "# 标题\n\n```mermaid\ngraph TD\nA-->B\n```",
        "infer_messages": [],
        "chart_messages": [],
        "warning_info": "",
        "exception_info": "",
    }

    html_text = ReportHtml.convert_from_final_result(final_result, tmp_path)

    assert "<html" in html_text.lower()
    assert "标题" in html_text
    assert ("<svg" in html_text) or ("language-mermaid" in html_text)


def test_normalize_docx_tables_centers_tables_and_captions(tmp_path):
    """Validate DOCX table normalization centers table objects and captions.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    docx_path = tmp_path / "tables.docx"
    document = Document()
    document.add_paragraph("普通正文段落")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    document.add_paragraph("表2-1：合肥市“三电”系统核心企业的技术实力与市场表现")
    document.save(docx_path)

    normalize_docx_tables(docx_path)

    normalized = Document(docx_path)
    assert normalized.tables[0].alignment == WD_TABLE_ALIGNMENT.CENTER
    assert normalized.paragraphs[0].alignment is None
    assert normalized.paragraphs[-1].alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_convert_md_to_html_annotates_xychart_value_labels(tmp_path, monkeypatch):
    """Validate HTML export annotates xychart SVG output with value labels.

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest monkeypatch fixture。

    Returns:
        None.
    """
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "```mermaid\nxychart-beta\n  bar [1200]\n```",
        encoding="utf-8",
    )

    def _fake_render_mermaid_offline(code, output_path, **kwargs):
        del code, kwargs
        output_file = tmp_path / output_path.name
        output_file.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<g class="plot"><g class="bar-plot-0" fill="#374151">'
            '<rect x="10" y="10" width="20" height="30" />'
            "</g></g></svg>",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(
        "server.deepsearch.core.manager.report_manager.html_export.render_mermaid_offline",
        _fake_render_mermaid_offline,
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert "xychart-value-label" in html_text


def test_convert_md_to_html_keeps_legacy_font_caption_separate_from_following_list(tmp_path):
    """Validate legacy font captions do not absorb following bullet lists."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "![图表示例](chart.png)\n"
        "<font size=2>**图表示例**: 图注说明</font>[[1]](https://example.com)\n"
        "- **技术维度**：第一条\n"
        "- **经济维度**：第二条\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert ".figure-caption {" in html_text
    assert "width: 100%;" in html_text
    assert "text-align: center;" in html_text
    assert '<div class="figure-caption">' in html_text
    assert "<ul>" in html_text
    assert "<li><strong>技术维度</strong>" in html_text


def test_convert_md_to_html_separates_paragraph_from_following_bullets_without_blank_line(tmp_path):
    """Validate list items render after a paragraph even when the source misses a blank line."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "在代际价值观层面，这一人群展现出强烈的矛盾统一体特征：\n"
        "- **求稳与求变并存**：第一条\n"
        "- **务实与悦己交织**：第二条\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert "<p>在代际价值观层面，这一人群展现出强烈的矛盾统一体特征：</p>" in html_text
    assert "<ul>" in html_text
    assert "<li><strong>求稳与求变并存</strong>：第一条</li>" in html_text


def test_convert_md_to_html_separates_paragraph_from_following_table_without_blank_line(tmp_path):
    """Validate pipe tables render after a paragraph even when the source misses a blank line."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "表2-1梳理了测试数据：\n"
        "| 列1 | 列2 |\n"
        "| --- | --- |\n"
        "| A | B |\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert "<p>表2-1梳理了测试数据：</p>" in html_text
    assert '<div class="table-wrap"><table>' in html_text
    assert "| 列1 | 列2 |" not in html_text


def test_convert_md_to_html_centers_table_display(tmp_path):
    """Validate exported HTML uses centered table presentation styles."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "| 列1 | 列2 |\n"
        "| --- | --- |\n"
        "| A | B |\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert "table {" in html_text
    assert "margin: 16px auto 24px;" in html_text
    assert "width: fit-content;" in html_text
    assert "max-width: 100%;" in html_text
    assert "th[style], td[style] {" in html_text
    assert "text-align: center !important;" in html_text
    assert "text-align: center;" in html_text


def test_report_docx_export_creates_docx_file(tmp_path):
    """Validate DOCX export writes a pure-Python generated file into the bundle.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": "# Title\n\nPlain text.",
        "infer_messages": [],
        "chart_messages": [],
        "warning_info": "",
        "exception_info": "",
    }

    docx_path = ReportWord.convert_from_final_result(final_result, tmp_path)

    assert docx_path.exists()
    document = Document(docx_path)
    assert any(paragraph.text == "Title" for paragraph in document.paragraphs)
    assert any(paragraph.text == "Plain text." for paragraph in document.paragraphs)


def test_convert_md_to_docx_normalizes_headings_fonts_and_tables(tmp_path, monkeypatch):
    """Validate DOCX export uses heading/font/table post-processing flow.

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest monkeypatch fixture。

    Returns:
        None.
    """
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text("1. Heading\n", encoding="utf-8")

    font_calls = {"count": 0}
    table_calls = {"count": 0}

    monkeypatch.setattr(
        "server.deepsearch.core.manager.report_manager.docx_export.normalize_docx_fonts",
        lambda *_args, **_kwargs: font_calls.__setitem__("count", font_calls["count"] + 1),
        raising=False,
    )
    monkeypatch.setattr(
        "server.deepsearch.core.manager.report_manager.docx_export.normalize_docx_tables",
        lambda *_args, **_kwargs: table_calls.__setitem__("count", table_calls["count"] + 1),
        raising=False,
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    assert any(paragraph.text == "1 Heading" for paragraph in document.paragraphs)
    assert font_calls["count"] == 1
    assert table_calls["count"] == 1


def test_convert_md_to_docx_preserves_short_bold_spans_in_long_chinese_summary(tmp_path):
    """Validate DOCX export keeps inline bold spans in long Chinese summary paragraphs."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "# 摘要\n\n"
        "都市年轻职场人群（22-35岁）在职业周期演进与高居住成本制约下呈现显著分化，"
        "其核心生存图景由**69.6%**企业新招毕业生硕士占比更高、一线城市高达**45.6%**"
        "的房租负担率及**79.2%**企业离职率低于**10%**的求稳心态共同刻画；该群体深陷"
        "工作高压（**31.5%**日工作超10小时）、时间剥夺（北京单程通勤**47分钟**）、社交"
        "萎缩（超三成频繁孤独）与生活品质坍塌（超**90%**受亚健康影响）交织的痛点因果网，"
        "驱动其行为向情绪补剂常态化（近九成为情绪买单）、社交模块化（**54.4%**有搭子）"
        "与技术双刃剑（超**56%**日常使用GenAI但超六成担忧失业）三大策略演化；由此催生"
        "效率工具（**56.1%**愿为AI付费）、情绪价值（四成向AI倾诉）、零糖社交与零家务闭环"
        "等复合需求，其消费决策呈现极致折叠的精算师特质（比价工具使用率达**78%**）与为情绪"
        "溢价买单并存，复购核心由体验确证（**77.6%**因使用感好复购）与情绪持续供给驱动。\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    summary_paragraph = document.paragraphs[1]
    bold_runs = {run.text for run in summary_paragraph.runs if run.text and run.bold}

    assert "**" not in summary_paragraph.text
    assert {
        "69.6%",
        "45.6%",
        "79.2%",
        "10%",
        "31.5%",
        "47分钟",
        "90%",
        "54.4%",
        "56%",
        "56.1%",
        "78%",
        "77.6%",
    }.issubset(bold_runs)


def test_convert_md_to_docx_raises_file_not_found_for_missing_markdown(tmp_path):
    """Validate DOCX export still surfaces missing Markdown input."""
    with pytest.raises(FileNotFoundError):
        convert_md_to_docx(tmp_path / "missing.md", tmp_path / "report.docx")


def test_convert_md_to_docx_fallback_on_unconvertible_latex(tmp_path):
    """Validate DOCX export does not crash when a LaTeX formula cannot be converted.

    Instead, the raw formula text should be preserved in the output.
    This covers cases like deeply nested unbalanced left/right delimiters
    that latex2mathml rejects with ExtraLeftOrMissingRightError.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    # Nested unbalanced \left...\right — latex2mathml raises ExtraLeftOrMissingRightError
    # Use raw string to avoid \t/\f etc. being interpreted as escape chars
    md_path.write_text(
        r"# 测试" + "\n\n"
        r"$$\left[\left(x\right)\right) - y\right)$$" + "\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    # Should contain the raw formula text as fallback (with $$ delimiters preserved)
    assert any(r"\left" in paragraph.text for paragraph in document.paragraphs)


def test_convert_md_to_docx_keeps_valid_math_inline_and_block(tmp_path):
    """Validate DOCX export keeps well-formed LaTeX formulas converted to OMML.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "# 测试\n\n"
        "行内公式 $x^2 + y^2 = z^2$。\n\n"
        "$$E = mc^2$$\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    # OMML namespace should be present for converted formulas
    assert "http://schemas.openxmlformats.org/officeDocument/2006/math" in document_xml


def test_convert_md_to_docx_renders_binomial_power_display_math(tmp_path):
    """Validate DOCX export converts binomial expressions with outer powers."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "# Test\n\n"
        "$$\n"
        r"\sum_{k=1}^{\infty} \frac{21k-8}{k^3 \binom{2k}{k}^3} = \frac{\pi^2}{6}"
        "\n$$\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "$$" not in paragraph_text
    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    assert "<m:oMath" in document_xml


def test_convert_md_to_docx_renders_binomial_power_inline_math(tmp_path):
    """Validate inline DOCX math also normalizes binomial expressions."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "# Test\n\n"
        r"The inline choice formula is $\binom{n}{r}^2$ in text."
        "\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert r"$\binom{n}{r}^2$" not in paragraph_text
    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    assert "<m:oMath" in document_xml


def test_convert_md_to_docx_renders_aligned_display_math(tmp_path):
    """Validate DOCX export converts aligned display formulas."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "# Test\n\n"
        "$$\n"
        r"\begin{aligned} "
        r"\frac {\mathrm {d} x}{\mathrm {d} t}&=\sigma y-\sigma x,\\ "
        r"\frac {\mathrm {d} y}{\mathrm {d} t}&=\rho x-xz-y,\\ "
        r"\frac {\mathrm {d} z}{\mathrm {d} t}&=xy-\beta z. "
        r"\end{aligned}"
        "\n$$\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "$$" not in paragraph_text
    assert r"\begin{aligned}" not in paragraph_text
    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    assert "<m:oMath" in document_xml


def test_normalize_latex_for_omml_wraps_nested_grouped_command_powers():
    """Validate normalization reaches grouped powers nested inside grouped commands."""
    normalized = _normalize_latex_for_omml(r"\binom{\frac{1}{2}^3}{k}^2")

    assert normalized == r"{\binom{{\frac{1}{2}}^3}{k}}^2"


def test_html_export_contains_mathjax_script(tmp_path):
    """Validate HTML export includes MathJax script for formula rendering.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "# 测试\n\n"
        "公式：$$E = mc^2$$\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert "MathJax" in html_text
    assert "mathjax@3" in html_text
    assert "tex-mml-chtml" in html_text


def test_protect_math_spans_preserves_underscores_through_markdown():
    """Validate math placeholders survive Markdown conversion without emphasis injection.

    Underscores and asterisks inside ``$...$`` / ``$$...$$`` must not be turned
    into ``<em>``/``<strong>`` tags by Python-Markdown.

    Returns:
        None.
    """
    text = (
        "正文 $$\\mathcal{J}_{GRPO}(\\theta) = \\sum_{i=1}^G \\pi_\\theta$$ "
        "行内 $a_{i} * b_{j}$ 结束"
    )

    protected, formulas = protect_math_spans(text)
    assert len(formulas) == 2
    assert "$" not in protected  # 公式分隔符已被占位符取代

    rendered = markdown.markdown(protected, extensions=["extra"], output_format="html5")
    restored = restore_math_spans(rendered, formulas)

    assert "$$\\mathcal{J}_{GRPO}(\\theta) = \\sum_{i=1}^G \\pi_\\theta$$" in restored
    assert "\\(a_{i} * b_{j}\\)" in restored
    assert "<em>" not in restored
    assert "<strong>" not in restored


def test_protect_math_spans_ignores_code_spans_and_fenced_blocks():
    """Validate math placeholders do not rewrite code examples."""
    text = (
        "正文 $x_i$。\n\n"
        "`price = \"$5\"`\n\n"
        "```python\n"
        "formula = \"$x_i$\"\n"
        "```\n"
    )

    protected, formulas = protect_math_spans(text)

    assert formulas == ["$x_i$"]
    assert '`price = "$5"`' in protected
    assert 'formula = "$x_i$"' in protected


def test_protect_math_spans_skips_currency_but_keeps_simple_variables():
    """Validate inline math protection distinguishes currency from variables."""
    text = (
        r"成本为 $5 到 $10，收入约 $1,200.50，区间 amount $1,200 to $1,300，"
        r"变量 $G$、$H0$、$xyz$、$a, b, m$、$a, b, c, ...$、$(x,y,z)$ 和公式 "
        r"$G = (V, E)$、$P(1, 2, 1, (1, 0))$、$1/2$、$4*3$、$n!$、$5!$、"
        r"$|z|$、$|x-y|$、$f'(t)$、$f''(t)$、$f_i'(t)$、"
        r"$F(s) G(s)$、$F_0(s)\,G_1(s)$、$||x||$、$\|x\|$、$\lVert x \rVert$ 需要保留，"
        r"百分比 $79.29\%$、$79.29%$ 也需要保留，"
        r"转义美元 \$8 不处理。"
    )

    protected, formulas = protect_math_spans(text)

    assert formulas == [
        "$G$",
        "$H0$",
        "$xyz$",
        "$a, b, m$",
        "$a, b, c, ...$",
        "$(x,y,z)$",
        "$G = (V, E)$",
        "$P(1, 2, 1, (1, 0))$",
        "$1/2$",
        "$4*3$",
        "$n!$",
        "$5!$",
        "$|z|$",
        "$|x-y|$",
        "$f'(t)$",
        "$f''(t)$",
        "$f_i'(t)$",
        "$F(s) G(s)$",
        "$F_0(s)\\,G_1(s)$",
        "$||x||$",
        "$\\|x\\|$",
        "$\\lVert x \\rVert$",
        "$79.29\\%$",
        "$79.29%$",
    ]
    assert "$5 到 $10" in protected
    assert "$1,200.50" in protected
    assert "$1,200 to $1,300" in protected
    assert r"\$8" in protected


def test_html_export_uses_safe_mathjax_delimiters_for_currency_text(tmp_path):
    """Validate HTML MathJax does not reinterpret currency dollars as formulas."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "# 测试\n\n"
        "其中，$G$为组内采样数量，每一组的价格分别为$4和$5。\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert r"\(G\)" in html_text
    assert "$4和$5" in html_text
    assert "inlineMath: [['$', '$']" not in html_text


def test_convert_md_to_html_keeps_dollar_unit_table_intact(tmp_path):
    """Validate dollar unit labels do not break Markdown table parsing."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "| Provider | Price ($/GPU-hr) | Note |\n"
        "| :--- | :--- | :--- |\n"
        "| Thunder Compute | $1.38 | fixed on-demand price |\n"
        "\n"
        "Later text before a parameter mention.\n"
        "\n"
        "Hybrid sampling parameter ($q_r$) should render as math.\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_text, "html.parser")
    assert soup.find("table") is not None
    assert "Price ($/GPU-hr)" in soup.get_text()
    assert r"\(q_r\)" in html_text
    assert r"\(/GPU-hr" not in html_text


def test_convert_md_to_html_protects_numeric_indicator_math_and_currency(tmp_path):
    """Validate numeric indicator formulas are not mistaken for currency."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "| Term | Formula | Price |\n"
        "| :--- | :--- | :--- |\n"
        "| torque | $1(\\tau_t \\notin [\\tau_{min}, \\tau_{max}])$ | $1.38 |\n"
        "\n"
        "Each group costs $4 and $5.\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert r"\(1(\tau_t \notin [\tau_{min}, \tau_{max}])\)" in html_text
    assert "$1.38" in html_text
    assert "$4 and $5" in html_text


def test_convert_md_to_html_protects_comparison_math_before_markdown(tmp_path):
    """Validate comparison operators inside formulas do not skip math protection."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "| Term | Formula | Price |\n"
        "| :--- | :--- | :--- |\n"
        "| fall | $1(F^{left}_{feet}, F^{right}_{feet} < 1)$ | $1.38 |\n"
        "\n"
        "Scale uses $r_{t,i} < 0$ while escaped price \\$8 remains text.\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    soup_text = BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)
    assert r"\(1(F^{left}_{feet}, F^{right}_{feet} &lt; 1)\)" in html_text
    assert r"\(r_{t,i} &lt; 0\)" in html_text
    assert r"\(1(F^{left}_{feet}, F^{right}_{feet} < 1)\)" in soup_text
    assert r"\(r_{t,i} < 0\)" in soup_text
    assert "<em" not in html_text
    assert "$1.38" in html_text
    assert r"\$8" in html_text


def test_convert_md_to_html_protects_numeric_scientific_math(tmp_path):
    """Validate numeric-leading scientific formulas are not treated as currency."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "Training converges at $4 \\times 10^4$ samples. "
        "Plain prices $4 and $5 remain text.\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert r"\(4 \times 10^4\)" in html_text
    assert "$4 and $5" in html_text


def test_convert_md_to_html_protects_numeric_arithmetic_math(tmp_path):
    """Validate numeric arithmetic formulas are not treated as currency."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "Arithmetic formulas $2+2=4$, $4-3=1$, and $4−3=1$ render as math. "
        "Plain prices $4 and $5 remain text.\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert r"\(2+2=4\)" in html_text
    assert r"\(4-3=1\)" in html_text
    assert r"\(4−3=1\)" in html_text
    assert "$4 and $5" in html_text


def test_convert_md_to_html_escapes_formula_content_before_restore(tmp_path):
    """Validate formula text containing '<' does not become HTML tags."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "Compare $x<y$ and keep price $4 and $5 as text.\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    soup_text = BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)
    assert r"\(x&lt;y\)" in html_text
    assert r"\(x<y\)" in soup_text
    assert "$4 and $5" in soup_text


def test_convert_md_to_docx_renders_numeric_arithmetic_math(tmp_path):
    """Validate DOCX export converts numeric arithmetic formulas to OMML."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "Arithmetic formulas $2+2=4$, $4-3=1$, and $4−3=1$ render as math. "
        "Plain prices $4 and $5 remain text.\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "$2+2=4$" not in paragraph_text
    assert "$4-3=1$" not in paragraph_text
    assert "$4−3=1$" not in paragraph_text
    assert "$4 and $5" in paragraph_text
    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    assert document_xml.count("<m:oMath") >= 3


def test_convert_md_to_docx_preserves_formula_text_with_less_than(tmp_path):
    """Validate DOCX export does not lose formula content after '<'."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "Compare $x<y$ and keep price $4 and $5 as text.\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "$x<y$" not in paragraph_text
    assert "Compare" in paragraph_text
    assert "$4 and $5" in paragraph_text
    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    assert "<m:oMath" in document_xml
    assert "<m:t>x</m:t>" in document_xml
    assert "<m:t>y</m:t>" in document_xml


def test_convert_md_to_html_protects_equation_references(tmp_path):
    """Validate equation references wrapped in dollars render as inline math."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "The constraint follows $(Eq. 9)$ and the loss follows $(Equation 10)$.\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert r"\((Eq. 9)\)" in html_text
    assert r"\((Equation 10)\)" in html_text


def test_convert_md_to_html_protects_numeric_tuples_and_prime_variables(tmp_path):
    """Validate numeric tuples and prime variables render as inline math."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "Clamp range $(0.75, 1.5)$ and transformed variable $z'$ are formulas. "
        "Quoted text $foo'$ remains text.\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert r"\((0.75, 1.5)\)" in html_text
    assert r"\(z'\)" in html_text
    assert "$foo'" in html_text


def test_convert_md_to_html_keeps_function_call_math_distinct_from_closing_dollars(tmp_path):
    """Validate function-call formulas do not leave broken dollar delimiters."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "Advantage Function, $A(s,a)$, Value Network, $V(s)$, "
        "and $A(s,a) = Q(s,a) - V(s)$ are formulas. Price $1.38 remains text.\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert r"\(A(s,a)\)" in html_text
    assert r"\(V(s)\)" in html_text
    assert r"\(A(s,a) = Q(s,a) - V(s)\)" in html_text
    assert "$A(s,a)" not in html_text
    assert "$V(s)" not in html_text
    assert "$1.38" in html_text


def test_html_export_defines_bm_mathjax_macro(tmp_path):
    """Validate MathJax can render reports that use the common \\bm command."""
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        r"Policy $\pi_{\bm{\theta}}:\mathcal{O}\rightarrow\mathcal{A}$.",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert r"\(\pi_{\bm{\theta}}:\mathcal{O}\rightarrow\mathcal{A}\)" in html_text
    assert "bm: ['{\\\\boldsymbol{#1}}', 1]" in html_text


def test_convert_md_to_docx_preserves_currency_dollars_next_to_inline_math(tmp_path):
    """Validate DOCX conversion does not convert currency dollars as math."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "# 测试\n\n"
        "其中，$G$为组内采样数量，每一组的价格分别为$4和$5。\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "$4和$5" in paragraph_text
    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    assert "http://schemas.openxmlformats.org/officeDocument/2006/math" in document_xml
    assert r"\(G\)" not in document_xml


def test_convert_md_to_docx_renders_inline_math_without_currency_text(tmp_path):
    """Validate DOCX conversion renders normalized inline math without literal slashes."""
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text("# Test\n\nVariable $G$ should render as math.\n", encoding="utf-8")

    convert_md_to_docx(md_path, docx_path)

    document = Document(docx_path)
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert r"\(G\)" not in paragraph_text
    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    assert "<m:oMath" in document_xml


def test_html_export_preserves_underscore_math(tmp_path):
    """Validate HTML export keeps underscore-heavy formulas intact for MathJax.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    md_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    md_path.write_text(
        "# 测试\n\n"
        "$$\\sum_{i=1}^G \\pi_\\theta(o_i|q)$$\n",
        encoding="utf-8",
    )

    convert_md_to_html(md_path, html_path)

    html_text = html_path.read_text(encoding="utf-8")
    assert "\\sum_{i=1}^G \\pi_\\theta(o_i|q)" in html_text
    # 公式未被 Markdown 拆成斜体标签
    assert "<em>" not in html_text
    assert "<strong>" not in html_text


def test_docx_export_preserves_underscore_math(tmp_path):
    """Validate DOCX export handles underscore-heavy formulas without corruption.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    md_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    md_path.write_text(
        "# 测试\n\n"
        "$$x_{i}^{2} + y_{j}^{2} = z_{k}^{2}$$\n",
        encoding="utf-8",
    )

    convert_md_to_docx(md_path, docx_path)

    with zipfile.ZipFile(docx_path) as zip_file:
        document_xml = zip_file.read("word/document.xml").decode("utf-8")
    # 公式应转为 OMML，且未残留 Markdown 注入的斜体标签
    assert "http://schemas.openxmlformats.org/officeDocument/2006/math" in document_xml
