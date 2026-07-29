# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""Preprocess Mermaid source blocks for report export."""

from __future__ import annotations

import re
import unicodedata
import warnings
from dataclasses import dataclass


LATIN_UNITS = [
    "TB/s", "GB/s", "MB/s", "KB/s",
    "TB", "GB", "MB", "KB",
    "PFLOPS", "TFLOPS", "GFLOPS",
    "FLOPS", "FLOP",
    "FP16", "FP8", "FP4", "NVFP4",
    "W", "kW", "MW", "GW", "V", "A",
    "Hz", "kHz", "MHz", "GHz",
    "nm", "μm", "mm",
    "GPU", "CPU", "DPU", "LPU",
    "Token", "token",
]

CHINESE_UNITS = [
    "万亿美元", "亿美元", "美元", "亿元",
    "太瓦时", "瓦时",
    "吉瓦", "兆瓦", "千瓦",
    "万人", "万台", "倍", "%",
]
TIMELINE_NOTES_TITLE = "时间轴说明"
ELLIPSIS = "…"
TIMELINE_SPLIT_RE = re.compile(r"[，；、,;]")


def _compile_unit_pattern(units: list[str]) -> str:
    """Compile a length-sorted regex alternation for units.

    Args:
        units: 需要拼接的单位列表。

    Returns:
        str: 可嵌入正则表达式的 alternation 文本。
    """
    return "|".join(sorted(map(re.escape, units), key=len, reverse=True))


LATIN_UNITS_PATTERN = _compile_unit_pattern(LATIN_UNITS)
CHINESE_UNITS_PATTERN = _compile_unit_pattern(CHINESE_UNITS)
NUMBER_PATTERN = r"[-+]?(?:\d+(?:,\d{3})*|\d+|\.\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
XYCHART_NUMBER_RE = re.compile(
    r"""
    [-+]?
    (?:
        (?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?
        |
        \.\d+
    )
    (?:[eE][-+]?\d+)?
    """,
    re.VERBOSE,
)
Y_AXIS_RE = re.compile(
    r"""
    ^
    (?P<indent>\s*)
    y-axis
    (?:\s+"(?P<label_quoted>[^"]*)")?
    (?:\s+(?P<label_bare>(?!-->)[^\s"][^\r\n]*?))?
    (?:\s+(?P<min>[-+]?(?:\d+(?:\.\d+)?|\.\d+)))?
    \s*-->?\s*
    (?P<max>[-+]?(?:\d+(?:\.\d+)?|\.\d+))
    \s*$
    """,
    re.VERBOSE,
)


@dataclass(slots=True)
class MermaidRenderOptions:
    """Control Mermaid preprocessing behavior.

    Attributes:
        timeline_max_label_len: 时间轴标签最大显示宽度。
        scale_xychart: 是否启用 xychart 工程量级缩放。
        warn_on_invalid_number: 是否对非法 xychart 数值告警。
        show_xychart_value_labels: 是否允许下游叠加 xychart 数值标签。
    """

    timeline_max_label_len: int = 18
    scale_xychart: bool = True
    warn_on_invalid_number: bool = True
    show_xychart_value_labels: bool = True


@dataclass(slots=True)
class XyChartSeriesMetadata:
    """Describe one parsed xychart series.

    Attributes:
        index: Mermaid 渲染后序列索引。
        kind: 系列类型，例如 `bar`、`line`、`area`。
        values: 解析后的数值列表。
        display_values: 叠加到图上的展示文本。
    """

    index: int
    kind: str
    values: list[float]
    display_values: list[str]


@dataclass(slots=True)
class XyChartMetadata:
    """Store parsed xychart metadata for post-render annotations.

    Attributes:
        series: 图表中所有可标注的序列。
        chart_orientation: 图表方向，`vertical` 或 `horizontal`。
    """

    series: list[XyChartSeriesMetadata]
    chart_orientation: str = "vertical"


def normalize_whitespace_and_units(text: str) -> str:
    """Normalize whitespace and number-unit spacing for Mermaid text.

    Args:
        text: 原始 Mermaid 或 Markdown 文本。

    Returns:
        str: 归一化后的文本。
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    replacements = {
        "\u00a0": " ",
        "\u3000": " ",
        "端到-end": "端到端",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[ \t]+([，。；：！？、）》】])", r"\1", text)
    text = re.sub(r"([（《【])[ \t]+", r"\1", text)
    text = re.sub(rf"({NUMBER_PATTERN})\s*({LATIN_UNITS_PATTERN})\b", r"\1 \2", text)
    text = re.sub(rf"({NUMBER_PATTERN})\s+({CHINESE_UNITS_PATTERN})", r"\1\2", text)
    return text


def looks_like_mermaid_timeline(lines: list[str]) -> bool:
    """Check whether Mermaid lines look like a timeline diagram.

    Args:
        lines: Mermaid 源码按行拆分后的列表。

    Returns:
        bool: 是否为 timeline 图。
    """
    return any(line.strip().startswith("timeline") for line in lines)


def looks_like_mermaid_xychart(lines: list[str]) -> bool:
    """Check whether Mermaid lines look like an xychart diagram.

    Args:
        lines: Mermaid 源码按行拆分后的列表。

    Returns:
        bool: 是否为 xychart 图。
    """
    return any(line.strip().startswith("xychart") for line in lines)


def _display_width(text: str) -> int:
    """Calculate display width with East Asian wide character awareness.

    Args:
        text: 需要测量的文本。

    Returns:
        int: 估算后的显示宽度。
    """
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
    return width


def _trim_to_display_width(text: str, max_width: int) -> str:
    """Trim text to a target display width.

    Args:
        text: 原始文本。
        max_width: 允许的最大显示宽度。

    Returns:
        str: 截断后的文本。
    """
    if max_width <= 0:
        return ""

    parts: list[str] = []
    width = 0
    for ch in text:
        ch_width = 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
        if width + ch_width > max_width:
            break
        parts.append(ch)
        width += ch_width
    return "".join(parts)


def _compact_timeline_text(text: str) -> str:
    """Compact common verbose Chinese timeline phrasing.

    Args:
        text: 原始时间轴说明文本。

    Returns:
        str: 紧凑化后的文本。
    """
    compact = (
        text.replace("以及", "/")
        .replace("及", "/")
        .replace("和", "/")
        .replace("与", "/")
        .replace("相关", "")
        .replace("方面", "")
    )
    return re.sub(r"\s+", " ", compact).strip()


def smart_timeline_summary(text: str, max_len: int = 18) -> str:
    """Build a compact timeline label while preserving readability.

    Args:
        text: 原始说明文本。
        max_len: 目标最大显示宽度。

    Returns:
        str: 压缩后的时间轴文本。
    """
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return ""
    if _display_width(text) <= max_len:
        return text

    parts = [part.strip() for part in TIMELINE_SPLIT_RE.split(text) if part.strip()]
    candidate = parts[0] if parts else text
    if _display_width(candidate) <= max_len:
        return candidate

    compact = _compact_timeline_text(candidate)
    if _display_width(compact) <= max_len:
        return compact

    budget = max(max_len - _display_width(ELLIPSIS), 1)
    summary = _trim_to_display_width(compact, budget).rstrip("，；,. ")
    return f"{summary or _trim_to_display_width(text, budget)}{ELLIPSIS}"


def _build_timeline_notes(notes: list[tuple[str, str]]) -> str:
    """Render timeline truncation notes into Markdown.

    Args:
        notes: 需要展示原始说明的条目。

    Returns:
        str: Markdown 格式的时间轴补充说明。
    """
    if not notes:
        return ""

    lines = [f"**{TIMELINE_NOTES_TITLE}**", ""]
    for date_text, detail_text in notes:
        lines.append(f"- {date_text}: {detail_text}")
    return "\n".join(lines)


def _should_skip_timeline_entry(stripped: str) -> bool:
    """Check whether a timeline line should bypass label compaction.

    Args:
        stripped: 去除首尾空白后的行文本。

    Returns:
        bool: 是否跳过处理。
    """
    if not stripped or ":" not in stripped:
        return True
    return stripped.startswith("title ") or stripped.startswith("section ")


def preprocess_timeline_mermaid(code: str, *, max_len: int) -> tuple[str, str]:
    """Shorten timeline labels and emit a supplemental note block.

    Args:
        code: Mermaid timeline 源码。
        max_len: 时间轴标签最大显示宽度。

    Returns:
        tuple[str, str]: 处理后的 Mermaid 源码和补充说明 Markdown。
    """
    lines = code.splitlines()
    new_lines: list[str] = []
    notes: list[tuple[str, str]] = []
    in_timeline = False

    for raw_line in lines:
        line = raw_line.rstrip()
        if line.strip().startswith("timeline"):
            in_timeline = True
            new_lines.append(line)
            continue

        if not in_timeline:
            new_lines.append(line)
            continue

        stripped = line.strip()
        if _should_skip_timeline_entry(stripped):
            new_lines.append(line)
            continue

        left, right = stripped.split(":", 1)
        date_text = left.strip()
        detail_text = re.sub(r"\s+", " ", right.strip())
        if not date_text or not detail_text:
            new_lines.append(line)
            continue

        short_text = smart_timeline_summary(detail_text, max_len=max_len)
        indent = re.match(r"^\s*", raw_line).group(0)
        new_lines.append(f"{indent}{date_text} : {short_text}")

        if short_text != detail_text:
            notes.append((date_text, detail_text))

    return "\n".join(new_lines), _build_timeline_notes(notes)


def format_scaled_number(value: float) -> str:
    """Format scaled xychart values with compact decimal text.

    Args:
        value: 需要格式化的数值。

    Returns:
        str: 适合写回 Mermaid 的数值文本。
    """
    if abs(value - round(value)) < 1e-10:
        return str(int(round(value)))
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def parse_number_list(content: str, *, warn_on_invalid: bool = True) -> list[float]:
    """Parse Mermaid xychart numeric lists.

    Args:
        content: 方括号内的数值文本。
        warn_on_invalid: 是否对非法片段发出告警。

    Returns:
        list[float]: 成功解析的数值列表。
    """
    values: list[float] = []
    invalid_items: list[str] = []
    last_end = 0

    for match in XYCHART_NUMBER_RE.finditer(content):
        separator = content[last_end:match.start()]
        invalid = separator.strip(" \t,")
        if invalid:
            invalid_items.append(invalid)

        normalized = match.group(0).replace(",", "")
        try:
            values.append(float(normalized))
        except ValueError:
            invalid_items.append(match.group(0).strip())
        last_end = match.end()

    invalid_tail = content[last_end:].strip(" \t,")
    if invalid_tail:
        invalid_items.append(invalid_tail)

    if invalid_items and warn_on_invalid:
        warnings.warn(f"xychart 数值解析失败，已跳过: {invalid_items}", stacklevel=2)

    return values


def replace_number_list(line: str, new_values: list[float]) -> str:
    """Replace the first Mermaid number list with scaled values.

    Args:
        line: 原始 Mermaid 行文本。
        new_values: 替换后的数值列表。

    Returns:
        str: 替换后的 Mermaid 行文本。
    """
    formatted = ", ".join(format_scaled_number(value) for value in new_values)
    return re.sub(r"\[[^\]]*\]", f"[{formatted}]", line, count=1)


def choose_engineering_scale(max_abs: float) -> int:
    """Choose an engineering scale exponent for xychart values.

    Args:
        max_abs: 序列中的绝对值最大值。

    Returns:
        int: 需要缩放的 10 的指数。
    """
    if max_abs >= 1e12:
        return 12
    if max_abs >= 1e9:
        return 9
    if max_abs >= 1e6:
        return 6
    if max_abs >= 1e3:
        return 3
    return 0


def extract_xychart_metadata(code: str, *, warn_on_invalid: bool = True) -> XyChartMetadata:
    """Extract xychart series metadata for downstream SVG/PNG annotations.

    Args:
        code: Mermaid xychart 源码。
        warn_on_invalid: 是否对非法数值片段告警。

    Returns:
        XyChartMetadata: 可用于数值标签叠加的元数据。
    """
    chart_orientation = "horizontal" if is_horizontal_xychart(code) else "vertical"
    series: list[XyChartSeriesMetadata] = []

    for raw_line in code.splitlines():
        stripped = raw_line.strip()
        kind = next(
            (candidate for candidate in ("line", "bar", "area") if stripped.startswith(f"{candidate} ")),
            None,
        )
        if kind is None:
            continue

        match = re.search(r"\[([^\]]+)\]", stripped)
        if not match:
            continue

        values = parse_number_list(match.group(1), warn_on_invalid=warn_on_invalid)
        if not values:
            continue

        series.append(
            XyChartSeriesMetadata(
                index=len(series),
                kind=kind,
                values=values,
                display_values=[format_scaled_number(value) for value in values],
            )
        )

    return XyChartMetadata(series=series, chart_orientation=chart_orientation)


def is_horizontal_xychart(code: str) -> bool:
    """Detect whether a Mermaid xychart is horizontal.

    Args:
        code: Mermaid xychart 源码。

    Returns:
        bool: 是否为横向图表。
    """
    for line in code.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("xychart") and " horizontal" in f" {stripped}":
            return True

    frontmatter_match = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n?", code.strip(), flags=re.DOTALL)
    if not frontmatter_match:
        return False

    frontmatter = frontmatter_match.group(1)
    return bool(re.search(r"^\s*horizontal\s*:\s*true\s*$", frontmatter, flags=re.IGNORECASE | re.MULTILINE))


def preprocess_xychart_mermaid(code: str, *, warn_on_invalid: bool = True) -> str:
    """Scale large xychart values and adjust the y-axis label.

    Args:
        code: Mermaid xychart 源码。
        warn_on_invalid: 是否对非法数值告警。

    Returns:
        str: 处理后的 Mermaid xychart 源码。
    """
    lines = code.splitlines()
    series_indexes: list[int] = []
    series_values: list[float] = []

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped.startswith(("line ", "bar ", "area ")):
            continue

        match = re.search(r"\[([^\]]+)\]", stripped)
        if not match:
            continue

        values = parse_number_list(match.group(1), warn_on_invalid=warn_on_invalid)
        if not values:
            continue

        series_indexes.append(idx)
        series_values.extend(values)

    if not series_values:
        return code

    max_abs = max(abs(value) for value in series_values)
    scale_power = choose_engineering_scale(max_abs)
    if scale_power == 0:
        return code

    scale_factor = 10 ** scale_power
    new_lines = lines[:]

    for idx in series_indexes:
        match = re.search(r"\[([^\]]+)\]", new_lines[idx].strip())
        if not match:
            continue
        values = parse_number_list(match.group(1), warn_on_invalid=False)
        if not values:
            continue
        new_lines[idx] = replace_number_list(new_lines[idx], [value / scale_factor for value in values])

    scaled_min = min(series_values) / scale_factor
    scaled_max = max(series_values) / scale_factor
    y_axis_updated = False

    for index, raw_line in enumerate(new_lines):
        stripped = raw_line.strip()
        if not stripped.startswith("y-axis"):
            continue

        match = Y_AXIS_RE.match(raw_line)
        if not match:
            continue

        indent = match.group("indent") or ""
        label = match.group("label_quoted") or match.group("label_bare") or ""
        axis_min = match.group("min")
        axis_max = match.group("max")

        label = label.strip()
        label = f"{label} (x1e{scale_power})" if label else f"x1e{scale_power}"

        if axis_min is not None and axis_max is not None:
            new_min = float(axis_min) / scale_factor
            new_max = float(axis_max) / scale_factor
        else:
            padding = (scaled_max - scaled_min) * 0.1 if scaled_max != scaled_min else max(abs(scaled_max) * 0.1, 1)
            new_min = scaled_min if scaled_min < 0 else 0
            new_max = scaled_max + padding

        new_lines[index] = (
            f'{indent}y-axis "{label}" '
            f'{format_scaled_number(new_min)} --> {format_scaled_number(new_max)}'
        )
        y_axis_updated = True
        break

    if not y_axis_updated:
        padding = (scaled_max - scaled_min) * 0.1 if scaled_max != scaled_min else max(abs(scaled_max) * 0.1, 1)
        auto_min = scaled_min if scaled_min < 0 else 0
        auto_max = scaled_max + padding
        new_lines.append(
            f'y-axis "x1e{scale_power}" {format_scaled_number(auto_min)} --> {format_scaled_number(auto_max)}'
        )

    return "\n".join(new_lines)


def preprocess_mermaid_code(code: str, options: MermaidRenderOptions | None = None) -> tuple[str, str]:
    """Apply offline Mermaid preprocessing for timeline and xychart blocks.

    Args:
        code: 原始 Mermaid 源码。
        options: Mermaid 预处理选项；为空时使用默认值。

    Returns:
        tuple[str, str]: 处理后的 Mermaid 源码和可选补充说明 Markdown。
    """
    options = options or MermaidRenderOptions()
    normalized = normalize_whitespace_and_units(code)
    lines = normalized.splitlines()

    if looks_like_mermaid_timeline(lines):
        return preprocess_timeline_mermaid(
            normalized,
            max_len=options.timeline_max_label_len,
        )

    if looks_like_mermaid_xychart(lines) and options.scale_xychart:
        return preprocess_xychart_mermaid(
            normalized,
            warn_on_invalid=options.warn_on_invalid_number,
        ), ""

    return normalized, ""
