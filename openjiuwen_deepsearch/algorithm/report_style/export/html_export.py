# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""HTML conversion for report export bundles."""

from __future__ import annotations

import html
import logging
import re
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import markdown

from openjiuwen_deepsearch.algorithm.report_style.export.conversion_utils import (
    postprocess_html,
    preprocess_markdown_text,
    protect_math_spans,
    read_text_with_fallback,
    render_mermaid_supplement,
    restore_math_spans,
)
from openjiuwen_deepsearch.algorithm.report_style.export.chart_svg import render_mermaid_chart_as_svg
from openjiuwen_deepsearch.algorithm.report_style.export.mermaid_preprocess import (
    MermaidRenderOptions,
    normalize_whitespace_and_units,
    preprocess_mermaid_code,
)
from openjiuwen_deepsearch.algorithm.report_style.structure import decorate_report_html


logger = logging.getLogger(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        :root {{
            --text: #222;
            --muted: #666;
            --border: #e5e7eb;
            --bg-soft: #f6f8fa;
            --link: #2563eb;
            --report-section-title: #1e3a5f;
            --report-table-header-background: #1e3a5f;
            --report-table-header-text: #ffffff;
        }}

        * {{
            box-sizing: border-box;
        }}

        html {{
            -webkit-text-size-adjust: 100%;
            text-rendering: optimizeLegibility;
        }}

        body {{
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                         "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB",
                         "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", sans-serif;
            line-height: 1.8;
            color: var(--text);
            background: #fff;
            word-break: break-word;
            overflow-wrap: anywhere;
        }}

        .report-shell {{
            width: 1280px;
            margin: 48px auto;
        }}

        h1, h2, h3, h4, h5, h6 {{
            line-height: 1.35;
            margin-top: 1.6em;
            margin-bottom: 0.7em;
        }}

        h1 {{
            padding-bottom: 0.3em;
            border-bottom: 1px solid var(--border);
        }}

        .report-abstract > h1 {{
            margin-top: 0;
        }}

        .report-section > h1 {{
            color: var(--report-section-title);
            border-bottom: 2px solid var(--border);
            margin-top: 2.2em;
            padding-bottom: 0.45em;
        }}

        p {{
            margin: 0.9em 0;
        }}

        a {{
            color: var(--link);
            text-decoration: none;
        }}

        a:hover {{
            text-decoration: underline;
        }}

        img {{
            max-width: 100%;
            height: auto;
            display: block;
            margin: 20px auto 12px;
        }}

        .figure-caption {{
            display: block;
            width: 100%;
            text-align: center;
            color: var(--muted);
            font-size: 0.95rem;
            margin: 0.2rem auto 1.4rem;
        }}

        .figure-caption p {{
            margin: 0.2rem 0;
        }}

        .citation {{
            vertical-align: super;
            font-size: 0.78em;
            line-height: 0;
            white-space: nowrap;
        }}

        .citation a {{
            color: var(--muted);
            text-decoration: none;
        }}

        .citation a:hover {{
            color: var(--link);
            text-decoration: underline;
        }}

        .citation + .citation {{
            margin-left: 0.18em;
        }}

        pre {{
            background: var(--bg-soft);
            padding: 16px;
            border-radius: 10px;
            overflow-x: auto;
            border: 1px solid var(--border);
        }}

        code {{
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
        }}

        p code, li code, td code, th code {{
            background: #f3f4f6;
            padding: 0.12em 0.35em;
            border-radius: 6px;
        }}

        .table-wrap {{
            width: 100%;
            overflow-x: auto;
            margin: 16px 0 24px;
            text-align: center;
        }}

        table {{
            border-collapse: collapse;
            width: fit-content;
            max-width: 100%;
            margin: 16px auto 24px;
            display: table;
        }}

        .table-wrap table {{
            width: auto;
            max-width: 100%;
            margin: 0 auto;
        }}

        th, td {{
            border: 1px solid var(--border);
            padding: 10px 12px;
            text-align: center;
            vertical-align: top;
        }}

        th[style], td[style] {{
            text-align: center !important;
        }}

        th {{
            background: #f8fafc;
        }}

        /* 表格单元格自身必须持有前景和背景色，不能只依赖 thead 背景。 */
        .report-table th {{
            background-color: var(--report-table-header-background);
            color: var(--report-table-header-text);
        }}

        ul, ol {{
            padding-left: 1.5em;
        }}

        blockquote {{
            margin: 1em 0;
            padding: 0.2em 1em;
            color: var(--muted);
            border-left: 4px solid var(--border);
            background: #fafafa;
        }}

        hr {{
            border: 0;
            border-top: 1px solid var(--border);
            margin: 2em 0;
        }}

        .mermaid-wrap {{
            width: 100%;
            overflow-x: auto;
            overflow-y: hidden;
            margin: 24px 0 12px;
            padding-bottom: 8px;
        }}

        .mermaid-rendered {{
            min-width: max-content;
            text-align: center;
        }}

        .mermaid-rendered svg {{
            height: auto;
            display: block;
            margin: 0 auto;
            max-width: 100%;
        }}

        .timeline-notes {{
            margin: 10px 0 24px;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 10px;
            background: #fafafa;
            font-size: 0.96rem;
        }}

        .timeline-notes p {{
            margin: 0 0 8px;
            font-weight: 600;
        }}

        .timeline-notes ul {{
            margin: 0;
            padding-left: 1.4em;
        }}

        .timeline-notes li {{
            margin: 0.45em 0;
        }}

        /* Math formula rendering with KaTeX */
        .katex-display {{
            margin: 1em 0;
            overflow-x: auto;
            overflow-y: hidden;
        }}
    </style>
    <!-- KaTeX CSS for LaTeX math formula rendering -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css" />
</head>
<body>
{content}
<!-- KaTeX for rendering LaTeX math formulas ($...$ / $$...$$) -->
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {{
    /* Escape currency-like $ signs (e.g. $4, $5, $1.38) so KaTeX auto-render
       does not pair them as inline-math delimiters. The $ signs are restored
       after rendering, leaving the visible output unchanged. */
    var PH = '\uFF04';
    var MATH_OPS = '\\+-*/!%^_{{}}=';
    function hasMathOp(s) {{
        for (var j = 0; j < s.length; j++) {{
            if (MATH_OPS.indexOf(s[j]) !== -1) return true;
        }}
        return false;
    }}
    function escapeCurrencyDollars() {{
        var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        var nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        nodes.forEach(function(node) {{
            var text = node.nodeValue;
            if (text.indexOf('$') === -1) return;
            var out = '';
            var modified = false;
            var i = 0;
            while (i < text.length) {{
                if (text[i] === '$' && i + 1 < text.length &&
                        text[i + 1] >= '0' && text[i + 1] <= '9') {{
                    var next$ = text.indexOf('$', i + 1);
                    if (next$ === -1 || next$ - i > 50 ||
                            !hasMathOp(text.substring(i + 1, next$))) {{
                        out += PH;
                        modified = true;
                        i++;
                        continue;
                    }}
                }}
                out += text[i];
                i++;
            }}
            if (modified) node.nodeValue = out;
        }});
    }}
    escapeCurrencyDollars();
    renderMathInElement(document.body, {{
        delimiters: [
            {{left: '$$', right: '$$', display: true}},
            {{left: '$', right: '$', display: false}},
        ],
        macros: {{
            '\\bm': '\\boldsymbol{{#1}}'
        }},
        throwOnError: false
    }});
    var restoreWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (restoreWalker.nextNode()) {{
        var n = restoreWalker.currentNode;
        if (n.nodeValue.indexOf(PH) !== -1) {{
            n.nodeValue = n.nodeValue.split(PH).join('$');
        }}
    }}
}});
</script>
</body>
</html>
"""

MERMAID_BLOCK_RE = re.compile(r"(?ms)^```[ \t]*mermaid[ \t]*\r?\n(.*?)\r?\n```[ \t]*$")


@dataclass(slots=True)
class ConvertOptions:
    """Control HTML export conversion behavior.

    Attributes:
        timeline_max_label_len: 时间轴标签最大显示宽度。
        scale_xychart: 是否启用 xychart 工程量级缩放。
        warn_on_invalid_number: 是否对 xychart 非法数值告警。
        title: 输出 HTML 标题。
    """

    timeline_max_label_len: int = 18
    scale_xychart: bool = True
    warn_on_invalid_number: bool = True
    title: str = "Document"


def replace_mermaid_blocks(text: str, options: ConvertOptions) -> str:
    """Replace Mermaid code fences with static SVG or readable source blocks.

    Args:
        text: 原始 Markdown 文本。
        options: HTML 转换选项。

    Returns:
        str: 受支持图表的静态 SVG 包装，或保留 Mermaid 源码的 HTML 块。
    """
    def _render_supplement(supplement_markdown: str) -> str:
        """Render optional notes emitted while preprocessing a Mermaid block.

        Args:
            supplement_markdown: Mermaid 预处理生成的补充说明。

        Returns:
            str: 转换后的补充说明 HTML；失败时为空字符串。
        """
        if not supplement_markdown.strip():
            return ""
        try:
            return render_mermaid_supplement(supplement_markdown)
        except Exception as exc:
            logger.warning(
                "Mermaid supplement rendering failed in HTML export conversion; skipping supplement. "
                "error=%s",
                exc,
            )
            return ""

    def _replace(match: re.Match[str]) -> str:
        """Convert one fenced Mermaid block into static SVG or source HTML.

        Args:
            match: Mermaid 代码块的正则匹配结果。

        Returns:
            str: 内嵌 SVG 图表或可读 Mermaid 源码块。
        """
        raw_mermaid_code = match.group(1).strip()
        mermaid_code = raw_mermaid_code
        supplement_markdown = ""
        try:
            mermaid_code, supplement_markdown = preprocess_mermaid_code(
                raw_mermaid_code,
                MermaidRenderOptions(
                    timeline_max_label_len=options.timeline_max_label_len,
                    scale_xychart=options.scale_xychart,
                    warn_on_invalid_number=options.warn_on_invalid_number,
                ),
            )
        except Exception as exc:
            logger.warning(
                "Mermaid block preprocessing failed in HTML export conversion; using the original source. "
                "error=%s",
                exc,
            )

        escaped = html.escape(mermaid_code)
        supplement_html = _render_supplement(supplement_markdown)
        svg_markup = render_mermaid_chart_as_svg(mermaid_code)
        if svg_markup is not None:
            return (
                '\n<div class="mermaid-wrap"><div class="mermaid-rendered">'
                f"{svg_markup}</div></div>{supplement_html}\n"
            )
        return f'\n<pre><code class="language-mermaid">{escaped}</code></pre>{supplement_html}\n'

    return MERMAID_BLOCK_RE.sub(_replace, text)


def preprocess_markdown(
    text: str,
    options: ConvertOptions,
) -> str:
    """Apply Markdown preprocessing before HTML conversion.

    Args:
        text: 原始 Markdown 文本。
        options: HTML 转换选项。

    Returns:
        str: 预处理后的 Markdown 文本。
    """
    text = normalize_whitespace_and_units(text)
    text = preprocess_markdown_text(text)
    return replace_mermaid_blocks(text, options)


def convert_md_to_html(
    input_md: str | Path,
    output_html: str | Path,
    *,
    options: ConvertOptions | None = None,
) -> None:
    """Convert Markdown into HTML with static Mermaid SVG rendering.

    Args:
        input_md: 输入 Markdown 文件路径。
        output_html: 输出 HTML 文件路径。
        options: HTML 转换选项。

    Returns:
        None.
    """
    options = options or ConvertOptions()
    input_path = Path(input_md)
    output_path = Path(output_html)
    start_time = time.perf_counter()
    logger.debug("Starting Markdown to HTML conversion input=%s output=%s", input_path, output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Markdown file does not exist: {input_path}")
    if input_path.suffix.lower() != ".md":
        warnings.warn(f"Input file does not look like Markdown: {input_path.name}", stacklevel=2)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    md_content = read_text_with_fallback(input_path)
    md_content = preprocess_markdown(md_content, options)
    md_content, math_spans = protect_math_spans(md_content)
    html_body = markdown.markdown(
        md_content,
        extensions=["extra", "toc", "md_in_html"],
        output_format="html5",
    )
    html_body = restore_math_spans(html_body, math_spans)
    full_html = decorate_report_html(
        HTML_TEMPLATE.format(
            title=html.escape(options.title, quote=True),
            content=postprocess_html(html_body),
        )
    )
    output_path.write_text(full_html, encoding="utf-8", newline="\n")
    logger.info(
        "Completed Markdown to HTML conversion html_bytes=%s duration_ms=%.2f",
        output_path.stat().st_size if output_path.exists() else 0,
        (time.perf_counter() - start_time) * 1000,
    )
