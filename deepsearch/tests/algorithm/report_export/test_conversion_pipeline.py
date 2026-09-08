# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""测试 HTML 与 DOCX 共享的报告转换管线。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from docx import Document
import pytest


def _run_generated_mermaid_report() -> str:
    """构造 run 图文并茂流程生成的 Mermaid 报告片段。

    Returns:
        含有第 0 列 Mermaid fence 的报告 Markdown。
    """
    from openjiuwen_deepsearch.algorithm.report.report import (
        Reporter,
        VisualizationInsertRenderContext,
    )
    from openjiuwen_deepsearch.algorithm.report.report_utils import XYChartMermaidGenerator

    mermaid_code = XYChartMermaidGenerator.generate_from_json(
        json.dumps(
            {
                "image_type": "bar",
                "unit": "亿元",
                "records": [["收入", 48], ["利润", 21]],
            },
            ensure_ascii=False,
        )
    )
    return Reporter._apply_visualization_insertions(
        VisualizationInsertRenderContext(
            report_lines=["# 报告\n", "\n", "正文。\n"],
            insertions=[{"after_row": 3, "index": 1}],
            mermaid_map={1: mermaid_code},
            title_meta_map={1: {"image_title": "经营指标", "citation_index": 0}},
            newline="\n",
            language="zh-CN",
        )
    )


def test_common_mermaid_fence_matches_only_run_contract() -> None:
    """公共 Mermaid fence 仅识别 run 产生的小写独立代码块。"""
    from openjiuwen_deepsearch.algorithm.report_export.conversion_utils import MERMAID_BLOCK_RE

    assert MERMAID_BLOCK_RE.search("```mermaid\r\nxychart-beta\r\n```") is not None
    assert MERMAID_BLOCK_RE.search("```Mermaid\nxychart-beta\n```") is None
    assert MERMAID_BLOCK_RE.search("  ```mermaid\nxychart-beta\n```") is None
    assert MERMAID_BLOCK_RE.search("说明 ```mermaid\nxychart-beta\n```") is None


@pytest.mark.parametrize(
    "markdown_text",
    [
        "```Mermaid\nxychart-beta\n    x-axis [\"A\"]\n    y-axis \"值\" 0 --> 2\n    bar [1]\n```",
        "  ```mermaid\nxychart-beta\n    x-axis [\"A\"]\n    y-axis \"值\" 0 --> 2\n    bar [1]\n```",
        "说明 ```mermaid\nxychart-beta\n    x-axis [\"A\"]\n    y-axis \"值\" 0 --> 2\n    bar [1]\n```",
    ],
)
def test_exporters_leave_non_contract_mermaid_fences_unrendered(
    markdown_text: str,
    tmp_path: Path,
) -> None:
    """HTML 与 DOCX 对非 run Mermaid fence 保持同样的源码回退语义。

    Args:
        markdown_text: 非运行时契约的 Mermaid Markdown。
        tmp_path: pytest 提供的临时目录。
    """
    from openjiuwen_deepsearch.algorithm.report_export.docx_export import convert_md_to_docx
    from openjiuwen_deepsearch.algorithm.report_export.html_export import convert_md_to_html

    markdown_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    docx_path = tmp_path / "report.docx"
    markdown_path.write_text(markdown_text, encoding="utf-8")

    convert_md_to_html(markdown_path, html_path)
    convert_md_to_docx(markdown_path, docx_path)

    assert "chart-svg" not in html_path.read_text(encoding="utf-8")
    assert len(Document(docx_path).inline_shapes) == 0


def test_render_markdown_html_fragment_preserves_shared_semantics() -> None:
    """公共 fragment 应保留公式、引用链接和表格包装语义。"""
    from openjiuwen_deepsearch.algorithm.report_export.conversion_utils import (
        render_markdown_html_fragment,
    )

    fragment = render_markdown_html_fragment(
        "公式 $x_1 + y_1$[[1]](https://example.com)\n\n"
        "| 指标 | 数值 |\n| --- | --- |\n| 收入 | 48 |\n"
    )

    assert "x_1 + y_1" in fragment
    assert '<sup class="citation">' in fragment
    assert 'target="_blank"' in fragment
    assert '<div class="table-wrap">' in fragment


def test_add_report_chapter_ids_matches_toc_and_ignores_fenced_headings() -> None:
    """导出层只给目录引用的真实 H1 添加章节 ID。"""
    from openjiuwen_deepsearch.algorithm.report_export.conversion_utils import (
        add_report_chapter_ids,
    )

    markdown_text = (
        "# 报告\n\n"
        "# 目录\n\n"
        "[1. 第一章](#chapter-1)\n\n"
        "[2. 第二章](#chapter-2)\n\n"
        "```markdown\n# 1. 第一章\n```\n\n"
        "# 1. 第一章\n\n正文\n\n"
        "# 2. 第二章\n"
    )

    converted = add_report_chapter_ids(markdown_text)

    assert "```markdown\n# 1. 第一章\n```" in converted
    assert "# 1. 第一章 {#chapter-1}" in converted
    assert "# 2. 第二章 {#chapter-2}" in converted
    assert add_report_chapter_ids(converted) == converted


def test_add_report_chapter_ids_supports_legacy_bulleted_toc() -> None:
    """Legacy bulleted TOCs should still receive stable H1 chapter IDs."""
    from openjiuwen_deepsearch.algorithm.report_export.conversion_utils import (
        add_report_chapter_ids,
    )

    markdown_text = (
        "# Report\n\n"
        "# Table of Contents\n\n"
        "- [1. First Chapter](#chapter-1)\n\n"
        '<a id="chapter-1"></a>\n'
        "# 1. First Chapter\n\nContent\n"
    )

    converted = add_report_chapter_ids(markdown_text)

    assert '<a id="chapter-1"></a>' not in converted
    assert "- [1. First Chapter](#chapter-1)" in converted
    assert "# 1. First Chapter {#chapter-1}" in converted


def test_add_report_chapter_ids_strips_anchor_line_after_h1() -> None:
    """锚点行在 H1 之后（_add_chapter_anchor_ids 产物）应被清理并转为 {#chapter-N}。"""
    from openjiuwen_deepsearch.algorithm.report_export.conversion_utils import (
        add_report_chapter_ids,
    )

    markdown_text = (
        "# 报告\n\n"
        "# 目录\n\n"
        "[1. 第一章](#chapter-1)\n\n"
        "[2. 第二章](#chapter-2)\n\n"
        '# 1. 第一章\n<a id="chapter-1"></a>\n\n正文一\n\n'
        '# 2. 第二章\n<a id="chapter-2"></a>\n\n正文二\n'
    )

    converted = add_report_chapter_ids(markdown_text)

    # 锚点行被清理
    assert '<a id="chapter-1"></a>' not in converted
    assert '<a id="chapter-2"></a>' not in converted
    # 标题行干净，{#chapter-N} 属性正确添加
    assert "# 1. 第一章 {#chapter-1}" in converted
    assert "# 2. 第二章 {#chapter-2}" in converted
    # 幂等：再调一次结果不变
    assert add_report_chapter_ids(converted) == converted


def test_html_export_strips_anchor_line_and_produces_clean_h1_ids(tmp_path: Path) -> None:
    """端到端：带锚点行的 markdown 转 HTML 后 h1 id 唯一且无残留锚点。"""
    from openjiuwen_deepsearch.algorithm.report_export.html_export import (
        ConvertOptions,
        convert_md_to_html,
    )

    markdown_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    markdown_path.write_text(
        "# 报告\n\n"
        "# 目录\n\n"
        "[1. 第一章](#chapter-1)\n\n"
        "[2. 第二章](#chapter-2)\n\n"
        '# 1. 第一章\n<a id="chapter-1"></a>\n\n正文一\n\n'
        '# 2. 第二章\n<a id="chapter-2"></a>\n\n正文二\n',
        encoding="utf-8",
    )

    convert_md_to_html(markdown_path, html_path, options=ConvertOptions())
    html = html_path.read_text(encoding="utf-8")

    # 每个章节只有一个 h1 id，无重复
    assert html.count('id="chapter-1"') == 1
    assert html.count('id="chapter-2"') == 1
    # 无残留 <a id> 标签
    assert '<a id="chapter-' not in html
    # TOC 链接存在
    assert 'href="#chapter-1"' in html
    assert 'href="#chapter-2"' in html


def test_docx_export_converts_report_toc_to_internal_links(tmp_path: Path) -> None:
    """DOCX 目录应链接到章节书签，而不是创建伪外部链接。"""
    from openjiuwen_deepsearch.algorithm.report_export.docx_export import convert_md_to_docx

    markdown_path = tmp_path / "report.md"
    docx_path = tmp_path / "report.docx"
    markdown_path.write_text(
        "# 报告\n\n"
        "# 目录\n\n"
        "[第一章](#chapter-1)\n\n"
        "[第二章](#chapter-2)\n\n"
        "# 第一章\n\n正文。\n\n"
        "# 第二章\n\n[外部链接](https://example.com)\n",
        encoding="utf-8",
    )

    convert_md_to_docx(markdown_path, docx_path)

    with zipfile.ZipFile(docx_path) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        relationships = archive.read("word/_rels/document.xml.rels").decode("utf-8")

    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    bookmark_names = {
        node.get(f"{{{word_ns}}}name")
        for node in document.findall(f".//{{{word_ns}}}bookmarkStart")
    }
    internal_links = [
        node
        for node in document.findall(f".//{{{word_ns}}}hyperlink")
        if node.get(f"{{{word_ns}}}anchor")
    ]

    assert {"chapter_1", "chapter_2"}.issubset(bookmark_names)
    assert {
        node.get(f"{{{word_ns}}}anchor") for node in internal_links
    } == {"chapter_1", "chapter_2"}
    assert all(node.get(f"{{{rel_ns}}}id") is None for node in internal_links)
    toc_paragraphs = [
        paragraph
        for paragraph in document.findall(f".//{{{word_ns}}}p")
        if paragraph.find(f".//{{{word_ns}}}hyperlink[@{{{word_ns}}}anchor]") is not None
    ]
    assert all(
        paragraph.find(f".//{{{word_ns}}}numPr") is None
        for paragraph in toc_paragraphs
    )
    assert "#chapter-1" not in relationships
    assert "#chapter-2" not in relationships
    assert "https://example.com" in relationships


def test_exporters_do_not_preprocess_mermaid_again_in_renderer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """导出器预处理 Mermaid 后，渲染层不应再次归一化其源码。

    Args:
        monkeypatch: pytest monkeypatch 夹具。
        tmp_path: pytest 提供的临时目录。
    """
    import openjiuwen_deepsearch.algorithm.report_export.mermaid_renderer as mermaid_renderer
    from openjiuwen_deepsearch.algorithm.report_export.docx_export import convert_md_to_docx
    from openjiuwen_deepsearch.algorithm.report_export.html_export import convert_md_to_html

    def _fail_if_preprocessed_again(*_args, **_kwargs):
        raise AssertionError("rendering an export Mermaid block must not preprocess it again")

    monkeypatch.setattr(mermaid_renderer, "preprocess_mermaid_code", _fail_if_preprocessed_again)
    markdown_path = tmp_path / "report.md"
    html_path = tmp_path / "report.html"
    docx_path = tmp_path / "report.docx"
    markdown_path.write_text(
        "```mermaid\nxychart-beta\n    x-axis [\"收入\", \"利润\"]\n"
        '    y-axis "亿元" 0 --> 60\n    bar [48, 21]\n```',
        encoding="utf-8",
    )

    convert_md_to_html(markdown_path, html_path)
    convert_md_to_docx(markdown_path, docx_path)

    assert "chart-svg" in html_path.read_text(encoding="utf-8")
    assert len(Document(docx_path).inline_shapes) == 1


def test_run_generated_final_result_renders_mermaid_in_html_and_docx(tmp_path: Path) -> None:
    """run 风格 final_result 的 Mermaid 应导出为 SVG 和 DOCX PNG。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    from openjiuwen_deepsearch.algorithm.report_export.docx_export import convert_md_to_docx
    from openjiuwen_deepsearch.algorithm.report_export.html_export import convert_md_to_html
    from openjiuwen_deepsearch.algorithm.report_export.report_bundle import build_report_bundle

    final_result = {
        "response_content": _run_generated_mermaid_report(),
        "infer_messages": [],
        "chart_messages": [],
    }
    bundle = build_report_bundle(final_result, tmp_path)
    html_path = bundle.root_dir / "report.html"
    docx_path = bundle.root_dir / "report.docx"

    convert_md_to_html(bundle.markdown_path, html_path)
    convert_md_to_docx(bundle.markdown_path, docx_path)

    assert "<svg" in html_path.read_text(encoding="utf-8")
    with zipfile.ZipFile(docx_path) as archive:
        media_names = [name for name in archive.namelist() if name.startswith("word/media/")]
        assert media_names
        assert archive.read(media_names[0])
