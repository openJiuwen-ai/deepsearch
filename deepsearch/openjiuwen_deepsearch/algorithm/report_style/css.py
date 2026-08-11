# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Normalize and inject model-generated CSS for a report."""

from __future__ import annotations

import re


CSS_FENCE_RE = re.compile(r"^```(?:css)?\s*\n(?P<css>.*?)\n?```\s*$", re.IGNORECASE | re.DOTALL)
OPENING_CSS_FENCE_RE = re.compile(r"^```(?:css)?[ \t]*\n", re.IGNORECASE)
CSS_RULE_RE = re.compile(r"(?P<selectors>[^{}]+)\{(?P<declarations>[^{}]*)\}", re.DOTALL)
CSS_DECLARATION_RE = re.compile(r"(?P<property>[-\w]+)\s*:\s*(?P<value>[^;]+)")
CSS_VARIABLE_RE = re.compile(r"(?P<name>--[-\w]+)\s*:\s*(?P<value>#[0-9a-fA-F]{3,6})\b")
HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b")
VAR_REFERENCE_RE = re.compile(r"var\(\s*(--[-\w]+)\s*\)")
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
MINIMUM_TITLE_CONTRAST_RATIO = 4.5
LIGHT_TITLE_COLOR = "#ffffff"
DARK_TITLE_COLOR = "#111827"
LIGHT_TITLE_RGB = (255, 255, 255)
DARK_TITLE_RGB = (17, 24, 39)


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


def _parse_hex_color(value: str) -> tuple[int, int, int] | None:
    """将 CSS 十六进制颜色转换为 RGB。

    Args:
        value: 仅包含十六进制颜色值的 CSS 文本。

    Returns:
        tuple[int, int, int] | None: RGB 三元组；格式不受支持时返回 None。
    """
    match = HEX_COLOR_RE.fullmatch(value.strip())
    if match is None:
        return None
    color = match.group(0)[1:]
    if len(color) == 3:
        color = "".join(component * 2 for component in color)
    return (
        int(color[0:2], 16),
        int(color[2:4], 16),
        int(color[4:6], 16),
    )


def _relative_luminance(color: tuple[int, int, int]) -> float:
    """计算 sRGB 颜色的相对亮度。

    Args:
        color: RGB 三元组。

    Returns:
        float: 符合 WCAG 定义的相对亮度。
    """
    components = []
    for component in color:
        normalized = component / 255
        components.append(
            normalized / 12.92
            if normalized <= 0.04045
            else ((normalized + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * components[0] + 0.7152 * components[1] + 0.0722 * components[2]


def _contrast_ratio(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    """计算两种不透明颜色的 WCAG 对比度。

    Args:
        first: 第一种 RGB 颜色。
        second: 第二种 RGB 颜色。

    Returns:
        float: 两种颜色的对比度，范围从 1 到 21。
    """
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter, darker = sorted((first_luminance, second_luminance), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _extract_css_variables(css: str) -> dict[str, str]:
    """提取可用于封面对比度检查的十六进制 CSS 变量。

    Args:
        css: 模型生成的 CSS 文本。

    Returns:
        dict[str, str]: 变量名称到原始十六进制颜色的映射。
    """
    return {match.group("name"): match.group("value") for match in CSS_VARIABLE_RE.finditer(css)}


def _resolve_color_value(value: str, variables: dict[str, str]) -> tuple[int, int, int] | None:
    """解析直接颜色或单个 CSS 变量引用。

    Args:
        value: CSS 声明中的颜色值。
        variables: 已提取的 CSS 变量映射。

    Returns:
        tuple[int, int, int] | None: 可解析时返回 RGB 三元组，否则返回 None。
    """
    cleaned_value = value.strip().removesuffix("!important").strip()
    variable_match = VAR_REFERENCE_RE.fullmatch(cleaned_value)
    if variable_match is not None:
        cleaned_value = variables.get(variable_match.group(1), "")
    return _parse_hex_color(cleaned_value)


def _iter_css_rules(css: str):
    """按 CSS 源码顺序遍历简单规则。

    Args:
        css: 模型生成的 CSS 文本。

    Yields:
        tuple[list[str], list[tuple[str, str]]]: 选择器列表和规则内的声明列表。
    """
    for rule_match in CSS_RULE_RE.finditer(css):
        selector_text = CSS_COMMENT_RE.sub("", rule_match.group("selectors"))
        selectors = [selector.strip() for selector in selector_text.split(",")]
        declarations = [
            (match.group("property").lower(), match.group("value").strip())
            for match in CSS_DECLARATION_RE.finditer(rule_match.group("declarations"))
        ]
        yield selectors, declarations


def _is_cover_selector(selector: str) -> bool:
    """判断选择器是否直接指向报告封面容器。

    Args:
        selector: 单个 CSS 选择器。

    Returns:
        bool: 仅选择 `.report-cover` 容器时返回 True。
    """
    return selector.strip() == ".report-cover"


def _title_selector_specificity(selector: str) -> int:
    """返回会命中封面 h1 的简化选择器优先级。

    Args:
        selector: 单个 CSS 选择器。

    Returns:
        int: 不命中返回 -1；全局 h1 返回 1；封面 h1 返回 11。
    """
    normalized = " ".join(selector.split())
    if normalized == "h1":
        return 1
    if ".report-cover" in normalized and re.search(r"(^|[\s>+~])h1\b", normalized):
        return 11
    return -1


def _extract_cover_background_colors(
    css: str,
    variables: dict[str, str],
) -> list[tuple[int, int, int]]:
    """提取最终封面背景声明中可解析的颜色。

    Args:
        css: 模型生成的 CSS 文本。
        variables: 已提取的 CSS 变量映射。

    Returns:
        list[tuple[int, int, int]]: 背景或渐变色标的 RGB 列表；无法判断时为空。
    """
    background_value: str | None = None
    for selectors, declarations in _iter_css_rules(css):
        if not any(_is_cover_selector(selector) for selector in selectors):
            continue
        for property_name, value in declarations:
            if property_name in {"background", "background-color"}:
                background_value = value

    if background_value is None:
        return []
    variable_match = VAR_REFERENCE_RE.fullmatch(background_value.strip())
    if variable_match is not None:
        background_value = variables.get(variable_match.group(1), "")
    background_colors = []
    for raw_color in HEX_COLOR_RE.findall(background_value):
        parsed_color = _parse_hex_color(raw_color)
        if parsed_color is not None:
            background_colors.append(parsed_color)
    return background_colors


def _has_cover_background_declaration(css: str) -> bool:
    """判断模型 CSS 是否实际覆盖了封面背景。

    Args:
        css: 模型生成的 CSS 文本。

    Returns:
        bool: `.report-cover` 存在 `background` 或 `background-color` 声明时返回 True。
    """
    for selectors, declarations in _iter_css_rules(css):
        if not any(_is_cover_selector(selector) for selector in selectors):
            continue
        if any(property_name in {"background", "background-color"} for property_name, _ in declarations):
            return True
    return False


def _extract_cover_title_color(
    css: str,
    variables: dict[str, str],
) -> tuple[int, int, int] | None:
    """按简化层叠规则提取封面标题的显式前景色。

    Args:
        css: 模型生成的 CSS 文本。
        variables: 已提取的 CSS 变量映射。

    Returns:
        tuple[int, int, int] | None: 可解析的有效标题色；没有显式标题色时返回 None。
    """
    title_color: tuple[int, int, int] | None = None
    title_specificity = -1
    inherited_cover_color: tuple[int, int, int] | None = None
    for selectors, declarations in _iter_css_rules(css):
        color_value = next(
            (value for property_name, value in declarations if property_name == "color"), None
        )
        if color_value is None:
            continue
        parsed_color = _resolve_color_value(color_value, variables)
        for selector in selectors:
            if _is_cover_selector(selector):
                inherited_cover_color = parsed_color
            specificity = _title_selector_specificity(selector)
            if specificity >= title_specificity and specificity >= 0:
                title_color = parsed_color
                title_specificity = specificity
    return title_color if title_specificity >= 0 else inherited_cover_color


def _append_title_color_override(css: str, color: str) -> str:
    """在模型 CSS 末尾追加高优先级封面标题色。

    Args:
        css: 模型生成的 CSS 文本。
        color: 已验证的十六进制标题色。

    Returns:
        str: 追加了标题色覆盖规则的 CSS。
    """
    return f"{css.rstrip()}\n\n.report-cover > h1 {{\n    color: {color} !important;\n}}\n"


def _append_title_backdrop(css: str) -> str:
    """在封面背景不可解析时为标题追加确定性的可读底板。

    Args:
        css: 模型生成的 CSS 文本。

    Returns:
        str: 追加了标题底板兜底规则的 CSS。
    """
    return (
        f"{css.rstrip()}\n\n.report-cover > h1 {{\n"
        "    color: #ffffff !important;\n"
        "    background-color: #0f172a !important;\n"
        "    padding: 0.12em 0.28em;\n"
        "    box-decoration-break: clone;\n"
        "    -webkit-box-decoration-break: clone;\n"
        "    text-shadow: 0 1px 2px rgb(0 0 0 / 25%);\n"
        "}\n"
    )


def append_cover_title_contrast_safeguard(css: str) -> str:
    """为低对比度或不可解析的封面标题追加可读性兜底样式。

    当封面背景可解析为纯色或仅含十六进制色标的渐变时，本函数会比较标题
    当前颜色与黑白候选色，必要时在 CSS 末尾覆盖为对比度更高的候选色。背景
    无法可靠解析时，改为给标题添加不透明深色底板，保证不依赖封面背景也可读。

    Args:
        css: 已规整的模型生成 CSS。

    Returns:
        str: 原 CSS 或追加封面标题可读性保护规则后的 CSS。
    """
    variables = _extract_css_variables(css)
    background_colors = _extract_cover_background_colors(css, variables)
    if not background_colors:
        if not _has_cover_background_declaration(css):
            return css
        return _append_title_backdrop(css)

    title_color = _extract_cover_title_color(css, variables)
    if title_color is not None and all(
        _contrast_ratio(title_color, background_color) >= MINIMUM_TITLE_CONTRAST_RATIO
        for background_color in background_colors
    ):
        return css

    title_color_candidates = (
        (LIGHT_TITLE_COLOR, LIGHT_TITLE_RGB),
        (DARK_TITLE_COLOR, DARK_TITLE_RGB),
    )
    best_color, _ = max(
        title_color_candidates,
        key=lambda candidate: min(
            _contrast_ratio(candidate[1], background_color)
            for background_color in background_colors
        ),
    )
    return _append_title_color_override(css, best_color)


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
