# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""测试 HTML 与 DOCX 共享的报告转换管线。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

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
