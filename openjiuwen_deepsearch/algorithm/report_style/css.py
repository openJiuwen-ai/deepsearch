# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Normalize and inject model-generated CSS for a report."""

from __future__ import annotations

import re


CSS_FENCE_RE = re.compile(r"^```(?:css)?\s*\n(?P<css>.*?)\n?```\s*$", re.IGNORECASE | re.DOTALL)
OPENING_CSS_FENCE_RE = re.compile(r"^```(?:css)?[ \t]*\n", re.IGNORECASE)


def _strip_css_fence(raw_css: str) -> str:
    """Remove an optional Markdown CSS fence emitted by a model.

    Args:
        raw_css: LLM 返回的原始文本。

    Returns:
        str: 移除完整围栏或孤立开围栏后的 CSS 文本。
    """
    text = raw_css.strip()
    match = CSS_FENCE_RE.fullmatch(text)
    if match:
        return match.group("css").strip()
    # 模型在 token 截断时可能省略结尾的 ```；仅移除开头的 CSS 围栏，
    # 避免把正文中的 Markdown 围栏误认为样式内容。
    return OPENING_CSS_FENCE_RE.sub("", raw_css, count=1)


def normalize_css_output(raw_css: object) -> str:
    """Validate CSS type and emptiness while preserving its content.

    Args:
        raw_css: LLM `content` 字段中的原始 CSS 文本。

    Returns:
        str: 仅移除可选 Markdown CSS 围栏后的 CSS 原文。

    Raises:
        ValueError: CSS 不是字符串或去除围栏后为空时抛出。
    """
    if not isinstance(raw_css, str):
        raise ValueError("CSS must be a string")
    css = _strip_css_fence(raw_css)
    if not css.strip():
        raise ValueError("CSS is empty")
    return css


def inject_css(html: str, css: str) -> str:
    """Append generated CSS after the report's baseline stylesheet.

    Args:
        html: 完整的基础报告 HTML 文本。
        css: 已规整、按原文注入的 CSS 文本。

    Returns:
        str: 含有生成样式块的完整 HTML。

    Raises:
        ValueError: HTML 缺少 `</head>` 标签时抛出。
    """
    if "</head>" not in html:
        raise ValueError("HTML document has no head closing tag")
    style_tag = f'<style id="report-style-generated">\n{css}\n</style>\n'
    return html.replace("</head>", f"{style_tag}</head>", 1)
