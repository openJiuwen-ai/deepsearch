# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""基于纯 Python HTML 管线和 Pillow 图表渲染的 DOCX 转换。"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

from docx import Document

from openjiuwen_deepsearch.algorithm.report_export.conversion_utils import (
    MERMAID_BLOCK_RE,
    MermaidRenderStats,
    add_report_chapter_ids,
    normalize_docx_fonts,
    normalize_docx_tables,
    normalize_headings,
    preprocess_markdown_text,
    read_text_with_fallback,
    render_markdown_html_fragment,
)
from openjiuwen_deepsearch.algorithm.report_export.mermaid_preprocess import (
    MermaidRenderOptions,
    normalize_whitespace_and_units,
    preprocess_mermaid_code,
)
from openjiuwen_deepsearch.algorithm.report_export.mermaid_renderer import (
    render_preprocessed_mermaid_chart_as_png,
)
from openjiuwen_deepsearch.algorithm.report_export.word_utils import html_to_doc, set_global_styles


logger = logging.getLogger(__name__)

DOCX_HTML_TEMPLATE = """<!DOCTYPE html>
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
</head>
<body>
{content}
</body>
</html>
"""
DOCX_STYLE_MAP = {
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


def render_mermaid_png(code: str) -> bytes | None:
    """将已预处理 Mermaid 图表渲染为内存 PNG。

    Args:
        code: 已由 Mermaid 预处理器规范化的 Mermaid 源码。

    Returns:
        PNG 字节；不支持、无效或字体不可用时返回 None。
    """
    try:
        return render_preprocessed_mermaid_chart_as_png(code)
    except Exception as exc:
        logger.warning(
            "Mermaid PNG rendering raised; keeping the original Mermaid block. error=%s",
            exc,
        )
        return None


def replace_mermaid_blocks(
    content: str,
) -> tuple[str, MermaidRenderStats]:
    """Replace Mermaid fences with PNG image references for DOCX conversion.

    Args:
        content: 原始 Markdown 文本。

    Returns:
        tuple[str, MermaidRenderStats]: 替换后的 Markdown 文本和渲染统计。
    """
    stats = MermaidRenderStats()

    def _replace(match: re.Match[str]) -> str:
        stats.total += 1
        block_index = stats.total - 1
        try:
            raw_mermaid_code = match.group(1).strip()
            mermaid_code, supplement_markdown = preprocess_mermaid_code(
                raw_mermaid_code,
                MermaidRenderOptions(),
            )
            png_bytes = render_mermaid_png(mermaid_code)
            if png_bytes is not None:
                stats.success += 1
                supplement = f"\n\n{supplement_markdown}\n" if supplement_markdown.strip() else ""
                encoded = base64.b64encode(png_bytes).decode("ascii")
                return f"\n\n![diagram](data:image/png;base64,{encoded})\n{supplement}\n"

            logger.warning("Mermaid render failed; keeping the original code block.")
        except Exception as exc:
            logger.warning(
                "Mermaid block processing failed in DOCX conversion; keeping the original code block. "
                "block=%s error=%s",
                block_index,
                exc,
            )

        stats.failed += 1
        return match.group(0)

    return MERMAID_BLOCK_RE.sub(_replace, content), stats


def convert_md_to_docx(md_path: str | Path, docx_path: str | Path) -> None:
    """Convert Markdown into DOCX through the pure-Python HTML pipeline.

    Args:
        md_path: 输入 Markdown 文件路径。
        docx_path: 输出 DOCX 文件路径。

    Returns:
        None.

    Raises:
        FileNotFoundError: Markdown input does not exist.
    """
    md_file = Path(md_path).resolve()
    docx_file = Path(docx_path).resolve()
    start_time = time.perf_counter()
    logger.info("Starting Markdown to DOCX conversion input=%s output=%s", md_file, docx_file)
    if not md_file.exists():
        raise FileNotFoundError(f"Markdown file does not exist: {md_file}")

    docx_file.parent.mkdir(parents=True, exist_ok=True)
    content = read_text_with_fallback(md_file)
    content = normalize_whitespace_and_units(content)
    content = preprocess_markdown_text(content)
    content, mermaid_stats = replace_mermaid_blocks(content)
    # Mermaid fence 是否属于运行时契约必须在标题归一化前判断，后者会移除短缩进。
    content = normalize_headings(content)
    content = add_report_chapter_ids(content)
    html_content = render_markdown_html_fragment(content)
    html_text = DOCX_HTML_TEMPLATE.format(content=html_content)

    document = Document()
    set_global_styles(document)
    html_to_doc(document, html_text, DOCX_STYLE_MAP, base_path=docx_file.parent)
    document.save(docx_file)

    normalize_docx_fonts(docx_file)
    normalize_docx_tables(docx_file)
    logger.info(
        "Mermaid render stats: total=%s success=%s failed=%s",
        mermaid_stats.total,
        mermaid_stats.success,
        mermaid_stats.failed,
    )
    logger.info(
        "Completed Markdown to DOCX conversion input=%s output=%s docx_bytes=%s duration_ms=%.2f",
        md_file,
        docx_file,
        docx_file.stat().st_size if docx_file.exists() else 0,
        (time.perf_counter() - start_time) * 1000,
    )
