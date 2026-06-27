# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""DOCX conversion based on pure-Python HTML rendering and Mermaid CLI."""

from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path

import markdown
from docx import Document

from server.deepsearch.core.manager.report_manager.conversion_utils import (
    MermaidRenderStats,
    enhance_image,
    make_safe_filename_component,
    normalize_docx_fonts,
    normalize_docx_tables,
    normalize_headings,
    postprocess_html,
    preprocess_markdown_text,
    protect_math_spans,
    read_text_with_fallback,
    restore_math_spans,
)
from server.deepsearch.core.manager.report_manager.mermaid_common import load_svg_markup
from server.deepsearch.core.manager.report_manager.mermaid_offline import render_mermaid_offline
from server.deepsearch.core.manager.report_manager.mermaid_preprocess import (
    MermaidRenderOptions,
    extract_xychart_metadata,
    looks_like_mermaid_xychart,
    normalize_whitespace_and_units,
    preprocess_mermaid_code,
)
from server.deepsearch.core.manager.report_manager.xychart_value_labels import (
    overlay_xychart_value_labels_on_png,
)
from server.deepsearch.core.manager.report_manager.word_utils import html_to_doc, set_global_styles


logger = logging.getLogger(__name__)

MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
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


def render_mermaid_png(
    code: str,
    output_path: str | Path,
    *,
    debug_base_path: Path | None = None,
    xychart_metadata=None,
) -> bool:
    """Render Mermaid into PNG and apply reference-style post-processing.

    Args:
        code: Mermaid 源码。
        output_path: PNG 输出路径。
        debug_base_path: 调试输出基础路径。
        xychart_metadata: 可选的 xychart 元数据。

    Returns:
        bool: 成功渲染返回 `True`，否则返回 `False`。
    """
    output_file = Path(output_path)
    try:
        success = render_mermaid_offline(
            code,
            output_file,
            output_format="png",
            debug_base_path=debug_base_path,
        )
    except Exception as exc:
        logger.warning(
            "Offline Mermaid PNG rendering raised; keeping the original Mermaid block. error=%s",
            exc,
        )
        return False

    if not success:
        return False

    try:
        enhance_image(str(output_file))
    except Exception as exc:  # pragma: no cover - best effort branch
        logger.warning(
            "Offline Mermaid PNG post-processing failed; using the raw rendered image. error=%s",
            exc,
        )

    if not xychart_metadata or not xychart_metadata.series:
        return True

    svg_path = output_file.parent / f".tmp_{output_file.stem}_{uuid.uuid4().hex}.svg"
    try:
        try:
            rendered_svg = render_mermaid_offline(
                code,
                svg_path,
                output_format="svg",
                debug_base_path=debug_base_path,
            )
        except Exception as exc:
            logger.warning(
                "Offline Mermaid SVG overlay rendering failed; using the PNG without labels. error=%s",
                exc,
            )
            return True

        if not rendered_svg:
            return True

        try:
            svg_markup = load_svg_markup(svg_path)
            overlay_xychart_value_labels_on_png(
                str(output_file),
                svg_markup,
                xychart_metadata,
            )
        except Exception as exc:  # pragma: no cover - best effort branch
            logger.warning(
                "Offline Mermaid PNG label overlay failed; using the PNG without labels. error=%s",
                exc,
            )
    finally:
        svg_path.unlink(missing_ok=True)

    return True


def replace_mermaid_blocks(
    content: str,
    tmp_dir: Path,
    *,
    asset_prefix: str,
    cleanup_paths: list[Path],
    debug_dir: Path,
    debug_stem: str,
) -> tuple[str, MermaidRenderStats]:
    """Replace Mermaid fences with PNG image references for DOCX conversion.

    Args:
        content: 原始 Markdown 文本。
        tmp_dir: 临时资源目录。
        asset_prefix: 临时资源名前缀。
        cleanup_paths: 待清理路径列表。
        debug_dir: Mermaid 调试目录。
        debug_stem: Mermaid 调试前缀。

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
            xychart_metadata = None
            if looks_like_mermaid_xychart(mermaid_code.splitlines()):
                xychart_metadata = extract_xychart_metadata(mermaid_code, warn_on_invalid=False)

            image_name = f"{asset_prefix}_mermaid_{block_index}.png"
            image_path = tmp_dir / image_name
            debug_base_path = debug_dir / f"{debug_stem}_mermaid_{block_index}"

            if render_mermaid_png(
                mermaid_code,
                image_path,
                debug_base_path=debug_base_path,
                xychart_metadata=xychart_metadata,
            ):
                cleanup_paths.append(image_path)
                stats.success += 1
                supplement = f"\n\n{supplement_markdown}\n" if supplement_markdown.strip() else ""
                return f"\n\n![diagram](<{image_name}>)\n{supplement}\n"

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
    safe_stem = make_safe_filename_component(docx_file.stem)
    temp_prefix = f".tmp_{safe_stem}_{uuid.uuid4().hex}"
    temp_html = docx_file.parent / f"{temp_prefix}.html"
    cleanup_paths: list[Path] = [temp_html]

    try:
        content = read_text_with_fallback(md_file)
        content = normalize_headings(content)
        content = normalize_whitespace_and_units(content)
        content = preprocess_markdown_text(content)
        content, mermaid_stats = replace_mermaid_blocks(
            content,
            docx_file.parent,
            asset_prefix=temp_prefix,
            cleanup_paths=cleanup_paths,
            debug_dir=docx_file.parent,
            debug_stem=docx_file.stem,
        )
        content, math_spans = protect_math_spans(content)
        html_body = markdown.markdown(
            content,
            extensions=["extra", "toc", "md_in_html"],
            output_format="html5",
        )
        html_body = restore_math_spans(html_body, math_spans)
        html_text = DOCX_HTML_TEMPLATE.format(content=postprocess_html(html_body))
        temp_html.write_text(
            html_text,
            encoding="utf-8",
            newline="\n",
        )

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
    finally:
        for cleanup_path in cleanup_paths:
            cleanup_path.unlink(missing_ok=True)
