# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Render supported report Mermaid chart syntax into static SVG markup."""

from __future__ import annotations

import html
import json
import math
import re


FRONTMATTER_RE = re.compile(r"^\s*---\s*\n.*?\n---\s*\n?", re.DOTALL)
NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?"
Y_AXIS_RE = re.compile(
    rf'^\s*y-axis\s+(?:"(?P<label>[^"]*)"\s+)?(?P<min>{NUMBER_PATTERN})\s*--?>\s*(?P<max>{NUMBER_PATTERN})\s*$'
)
X_AXIS_RE = re.compile(r"^\s*x-axis\s+(?P<values>\[.*\])\s*$")
SERIES_RE = re.compile(r"^\s*(?P<kind>bar|line)\s+(?P<values>\[.*\])\s*$")
PIE_ITEM_RE = re.compile(
    rf'^\s*"(?P<label>.*)"\s*:\s*(?P<value>{NUMBER_PATTERN})\s*$'
)
PALETTE = ("#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2")


def _strip_frontmatter(code: str) -> str:
    """Remove Mermaid YAML frontmatter before parsing its chart body.

    Args:
        code: 原始 Mermaid 源码。

    Returns:
        移除 YAML frontmatter 后的 Mermaid 主体。
    """
    return FRONTMATTER_RE.sub("", code.strip(), count=1).strip()


def _format_number(value: float) -> str:
    """Format an SVG axis value without unnecessary trailing zeroes.

    Args:
        value: 需要展示的数值。

    Returns:
        适用于 SVG 文本节点的数值字符串。
    """
    if abs(value) < 1e-9:
        return "0"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _svg_number(value: float) -> str:
    """Serialize a finite SVG coordinate with stable precision.

    Args:
        value: SVG 坐标或尺寸。

    Returns:
        坐标字符串。
    """
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _escape_text(value: object) -> str:
    """Escape text before inserting it into SVG markup.

    Args:
        value: 需要写入 SVG 的原始文本。

    Returns:
        HTML/SVG 转义后的文本。
    """
    return html.escape(str(value), quote=True)


def _truncate_vertical_axis_label(label: str, max_characters: int) -> str:
    """截断纵向图旋转标签以避免超出 SVG 左侧边界。

    Args:
        label: 原始类别标签。
        max_characters: 允许显示的最大字符数。

    Returns:
        未超限时返回原标签；超限时返回带 ASCII 省略号的显示文本。
    """
    if len(label) <= max_characters:
        return label
    if max_characters <= 3:
        return label[:max_characters]
    return f"{label[:max_characters - 3]}..."


def _load_json_array(raw_value: str) -> list[object]:
    """Load a non-empty JSON array from Mermaid chart source.

    Args:
        raw_value: Mermaid 方括号数组文本。

    Returns:
        未经元素类型验证的 JSON 数组。

    Raises:
        ValueError: 输入不是非空 JSON 数组时抛出。
    """
    parsed = json.loads(raw_value)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("chart array must be a non-empty list")
    return parsed


def _parse_text_array(raw_value: str) -> list[str]:
    """Parse Mermaid xychart category labels as non-empty text.

    Args:
        raw_value: Mermaid x-axis 方括号数组文本。

    Returns:
        去除首尾空白后的非空类目标签列表。

    Raises:
        ValueError: 数组元素不是非空字符串时抛出。
    """
    labels: list[str] = []
    for item in _load_json_array(raw_value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError("x-axis values must be non-empty strings")
        labels.append(item.strip())
    return labels


def _parse_number_array(raw_value: str) -> list[float]:
    """Parse Mermaid xychart series values as finite numbers.

    Args:
        raw_value: Mermaid 柱状或折线序列的方括号数组文本。

    Returns:
        有限浮点数列表。

    Raises:
        ValueError: 数组元素不是有限数值时抛出。
    """
    values: list[float] = []
    for item in _load_json_array(raw_value):
        if not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise ValueError("series values must be finite numbers")
        values.append(float(item))
    return values


def _read_svg_size(code: str, key: str, default: int, minimum: int, maximum: int) -> int:
    """Read a bounded numeric chart size from Mermaid frontmatter.

    Args:
        code: 含 Mermaid frontmatter 的完整源码。
        key: 要读取的配置键名。
        default: 未配置时使用的默认尺寸。
        minimum: 允许的最小尺寸。
        maximum: 允许的最大尺寸。

    Returns:
        限制在指定范围内的尺寸。
    """
    match = re.search(rf"^\s*{re.escape(key)}\s*:\s*(\d+)\s*$", code, re.MULTILINE)
    if match is None:
        return default
    return min(max(int(match.group(1)), minimum), maximum)


def _is_horizontal_xychart(code: str, body: str) -> bool:
    """Detect whether Mermaid xychart source requests horizontal bars.

    Args:
        code: 含 Mermaid frontmatter 的完整源码。
        body: 移除 frontmatter 后的 Mermaid 主体。

    Returns:
        使用 ``xychart-beta horizontal`` 或 ``horizontal: true`` 时返回 True。
    """
    chart_header = next((line.strip().lower() for line in body.splitlines() if line.strip()), "")
    if chart_header.startswith("xychart") and "horizontal" in chart_header.split()[1:]:
        return True
    return bool(
        re.search(
            r"^\s*horizontal\s*:\s*true\s*$",
            code,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )


def is_horizontal_xychart(code: str) -> bool:
    """判断 Mermaid xychart 源码是否声明为横向柱状图。

    Args:
        code: 含可选 frontmatter 的完整 Mermaid 源码。

    Returns:
        源码声明横向图时返回 True。
    """
    return _is_horizontal_xychart(code, _strip_frontmatter(code))


def _render_horizontal_xychart_svg(
    *,
    code: str,
    labels: list[str],
    values: list[float],
    y_label: str,
    y_min: float,
    y_max: float,
) -> str:
    """Render one horizontal Mermaid bar chart as static SVG.

    Args:
        code: 含 Mermaid frontmatter 的完整源码。
        labels: 类目标签。
        values: 与类目一一对应的数值。
        y_label: 数值轴单位或标题。
        y_min: 数值轴最小值。
        y_max: 数值轴最大值。

    Returns:
        水平条形图对应的 SVG 标记。
    """
    width = _read_svg_size(code, "width", max(620, 210 + len(labels) * 80), 420, 1080)
    requested_height = _read_svg_size(code, "height", 360, 280, 640)
    height = max(requested_height, min(640, 120 + len(labels) * 30))
    max_label_width = max(len(label) * 11 for label in labels)
    baseline_value = 0.0 if y_min <= 0 <= y_max else (y_min if y_min > 0 else y_max)
    category_label_x = max_label_width + 16
    negative_value_label_width = max(
        (len(_format_number(value)) * 8 for value in values if value < baseline_value),
        default=0,
    )
    # 负值标签位于柱体左侧，需在类别标签区与绘图区间预留独立 gutter。
    left = max(110, category_label_x + negative_value_label_width + 14)
    right, top, bottom = 30, 34, 54
    # 横向类目文本不旋转，保留完整标签空间并扩展画布，避免被 viewBox 裁切。
    width = max(width, int(left + right + 260))
    plot_width = width - left - right
    plot_height = height - top - bottom

    def value_to_x(value: float) -> float:
        """Map one chart value to its SVG x coordinate.

        Args:
            value: 需要映射的数值。

        Returns:
            相对于 SVG 左侧的 x 坐标。
        """
        return left + (value - y_min) * plot_width / (y_max - y_min)

    baseline_x = value_to_x(baseline_value)
    x_axis_y = top + plot_height
    parts = [
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" '
        'xmlns="http://www.w3.org/2000/svg">',
        f"<title>{_escape_text(y_label or '数据图表')}</title>",
        '<rect width="100%" height="100%" fill="#ffffff" rx="12"/>',
    ]

    for index in range(6):
        value = y_min + (y_max - y_min) * index / 5
        x = value_to_x(value)
        parts.append(
            f'<line class="chart-grid" x1="{_svg_number(x)}" y1="{top}" '
            f'x2="{_svg_number(x)}" y2="{_svg_number(x_axis_y)}" '
            'stroke="#e5e7eb" stroke-width="1" stroke-dasharray="3 3"/>'
        )
        parts.append(
            f'<text class="chart-axis-tick" x="{_svg_number(x)}" '
            f'y="{_svg_number(x_axis_y + 22)}" text-anchor="middle" '
            f'font-size="11" fill="#6b7280">{_escape_text(_format_number(value))}</text>'
        )

    parts.append(
        f'<line class="chart-zero-axis" x1="{_svg_number(baseline_x)}" y1="{top}" '
        f'x2="{_svg_number(baseline_x)}" y2="{_svg_number(x_axis_y)}" '
        'stroke="#64748b" stroke-width="1.25"/>'
    )
    if y_label:
        parts.append(
            f'<text x="{_svg_number(width - right)}" y="20" text-anchor="end" '
            f'font-size="12" fill="#475569">{_escape_text(y_label)}</text>'
        )

    step = plot_height / len(labels)
    bar_height = max(14, min(28, step * 0.62))
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        center_y = top + step * (index + 0.5)
        value_x = value_to_x(value)
        rect_x = min(value_x, baseline_x)
        rect_width = max(abs(baseline_x - value_x), 1)
        parts.append(
            f'<rect class="chart-bar" x="{_svg_number(rect_x)}" '
            f'y="{_svg_number(center_y - bar_height / 2)}" width="{_svg_number(rect_width)}" '
            f'height="{_svg_number(bar_height)}" fill="#2563eb" rx="3">'
            f"<title>{_escape_text(f'{label}: {_format_number(value)}')}</title></rect>"
        )
        parts.append(
            f'<text class="chart-category-label" x="{_svg_number(category_label_x)}" '
            f'y="{_svg_number(center_y + 4)}" text-anchor="end" '
            f'font-size="11" fill="#334155">{_escape_text(label)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def _render_xychart_svg(code: str, body: str) -> str | None:
    """Render Mermaid xychart-beta source as a static SVG chart.

    Args:
        code: 含 frontmatter 的完整 Mermaid 源码。
        body: 移除 frontmatter 后的 Mermaid 主体。

    Returns:
        渲染成功时返回 SVG 标记；源码不符合受支持格式时返回 None。
    """
    labels: list[str] | None = None
    values: list[float] | None = None
    series_kind: str | None = None
    y_label = ""
    y_min: float | None = None
    y_max: float | None = None

    for line in body.splitlines()[1:]:
        x_axis_match = X_AXIS_RE.match(line)
        if x_axis_match is not None:
            labels = _parse_text_array(x_axis_match.group("values"))
            continue

        y_axis_match = Y_AXIS_RE.match(line)
        if y_axis_match is not None:
            y_label = y_axis_match.group("label") or ""
            y_min = float(y_axis_match.group("min"))
            y_max = float(y_axis_match.group("max"))
            continue

        series_match = SERIES_RE.match(line)
        if series_match is not None:
            series_kind = series_match.group("kind")
            values = _parse_number_array(series_match.group("values"))

    if labels is None or values is None or series_kind is None:
        return None
    if y_min is None or y_max is None:
        return None
    if len(labels) != len(values) or y_min >= y_max:
        return None

    if is_horizontal_xychart(code) and series_kind == "bar":
        return _render_horizontal_xychart_svg(
            code=code,
            labels=labels,
            values=values,
            y_label=y_label,
            y_min=y_min,
            y_max=y_max,
        )

    width = _read_svg_size(code, "width", max(480, 140 + len(labels) * 95), 420, 1080)
    height = _read_svg_size(code, "height", 360, 280, 640)
    label_rotation_degrees = 32
    left, right, top = 66, 30, 34
    plot_width = width - left - right
    # 最左侧标签的锚点决定可用的旋转文本宽度，超长部分以 title 保留原文。
    first_label_x = left + plot_width / (2 * len(labels))
    max_display_width = max(
        44,
        (first_label_x - 8) / math.cos(math.radians(label_rotation_degrees)),
    )
    max_display_characters = max(4, math.floor(max_display_width / 11))
    display_labels = [
        _truncate_vertical_axis_label(label, max_display_characters) for label in labels
    ]
    display_label_markup = [
        _escape_text(display_label)
        + (
            f"<title>{_escape_text(label)}</title>"
            if display_label != label
            else ""
        )
        for label, display_label in zip(labels, display_labels, strict=True)
    ]
    max_label_width = max(len(label) * 11 for label in display_labels)
    # 旋转文本会向下延伸；底部边距必须覆盖显示文本的投影高度。
    bottom = max(
        80,
        math.ceil(38 + max_label_width * math.sin(math.radians(label_rotation_degrees))),
    )
    # 标签投影可超过生成器的固定高度；保留最小绘图区避免坐标反向。
    height = max(height, top + bottom + 180)
    plot_height = height - top - bottom

    def value_to_y(value: float) -> float:
        """Map one chart value to its SVG y coordinate.

        Args:
            value: 需要映射的数值。

        Returns:
            相对于 SVG 顶部的 y 坐标。
        """
        return top + (y_max - value) * plot_height / (y_max - y_min)

    baseline_value = 0.0 if y_min <= 0 <= y_max else (y_min if y_min > 0 else y_max)
    baseline_y = value_to_y(baseline_value)
    x_axis_y = top + plot_height
    x_label_y = x_axis_y + 30
    parts = [
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" '
        'xmlns="http://www.w3.org/2000/svg">',
        f"<title>{_escape_text(y_label or '数据图表')}</title>",
        '<rect width="100%" height="100%" fill="#ffffff" rx="12"/>',
    ]

    for index in range(6):
        value = y_max - (y_max - y_min) * index / 5
        y = value_to_y(value)
        parts.append(
            f'<line class="chart-grid" x1="{left}" y1="{_svg_number(y)}" '
            f'x2="{width - right}" y2="{_svg_number(y)}" stroke="#e5e7eb" '
            'stroke-width="1" stroke-dasharray="3 3"/>'
        )
        parts.append(
            f'<text class="chart-axis-tick" x="{left - 8}" y="{_svg_number(y + 4)}" text-anchor="end" '
            f'font-size="11" fill="#6b7280">{_escape_text(_format_number(value))}</text>'
        )

    parts.append(
        f'<line class="chart-zero-axis" x1="{left}" y1="{_svg_number(baseline_y)}" x2="{width - right}" '
        f'y2="{_svg_number(baseline_y)}" stroke="#64748b" stroke-width="1.25"/>'
    )
    if y_min < 0 < y_max:
        parts.append(
            f'<text class="chart-zero-tick" x="{left - 8}" y="{_svg_number(baseline_y + 4)}" '
            f'text-anchor="end" font-size="11" fill="#475569">0</text>'
        )
    if y_label:
        parts.append(
            f'<text x="{left}" y="20" font-size="12" fill="#475569">{_escape_text(y_label)}</text>'
        )

    step = plot_width / len(labels)
    if series_kind == "bar":
        bar_width = max(12, min(42, step * 0.55))
        for index, (label, display_markup, value) in enumerate(
            zip(labels, display_label_markup, values, strict=True)
        ):
            center_x = left + step * (index + 0.5)
            value_y = value_to_y(value)
            rect_y = min(value_y, baseline_y)
            rect_height = max(abs(baseline_y - value_y), 1)
            parts.append(
                f'<rect class="chart-bar" x="{_svg_number(center_x - bar_width / 2)}" '
                f'y="{_svg_number(rect_y)}" width="{_svg_number(bar_width)}" '
                f'height="{_svg_number(rect_height)}" fill="#2563eb" rx="3">'
                f"<title>{_escape_text(f'{label}: {_format_number(value)}')}</title></rect>"
            )
            parts.append(
                f'<text class="chart-category-label" x="{_svg_number(center_x)}" '
                f'y="{_svg_number(x_label_y)}" text-anchor="end" '
                f'font-size="11" fill="#334155" transform="rotate(-{label_rotation_degrees} '
                f'{_svg_number(center_x)} {_svg_number(x_label_y)})">'
                f"{display_markup}</text>"
            )
    else:
        points = []
        for index, value in enumerate(values):
            center_x = left + step * (index + 0.5)
            points.append(f"{_svg_number(center_x)},{_svg_number(value_to_y(value))}")
        parts.append(
            f'<polyline class="chart-line" points="{" ".join(points)}" fill="none" '
            'stroke="#2563eb" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        for index, (label, display_markup, value) in enumerate(
            zip(labels, display_label_markup, values, strict=True)
        ):
            center_x = left + step * (index + 0.5)
            center_y = value_to_y(value)
            parts.append(
                f'<circle class="chart-line-point" cx="{_svg_number(center_x)}" cy="{_svg_number(center_y)}" '
                'r="4" fill="#2563eb"><title>'
                f"{_escape_text(f'{label}: {_format_number(value)}')}</title></circle>"
            )
            parts.append(
                f'<text class="chart-category-label" x="{_svg_number(center_x)}" '
                f'y="{_svg_number(x_label_y)}" text-anchor="end" '
                f'font-size="11" fill="#334155" transform="rotate(-{label_rotation_degrees} '
                f'{_svg_number(center_x)} {_svg_number(x_label_y)})">'
                f"{display_markup}</text>"
            )

    parts.append("</svg>")
    return "".join(parts)


def _render_pie_svg(body: str) -> str | None:
    """Render Mermaid pie source as a static SVG chart.

    Args:
        body: 移除 frontmatter 后的 Mermaid 主体。

    Returns:
        渲染成功时返回 SVG 标记；源码不符合受支持格式时返回 None。
    """
    items: list[tuple[str, float]] = []
    for line in body.splitlines()[1:]:
        match = PIE_ITEM_RE.match(line)
        if match is None:
            continue
        value = float(match.group("value"))
        if value < 0 or not math.isfinite(value):
            return None
        items.append((match.group("label").strip(), value))
    total = sum(value for _, value in items)
    if len(items) < 2 or total <= 0:
        return None

    width = 760
    height = max(360, 72 + len(items) * 34)
    # 饼图与右侧图例作为一个整体居中，保留两者原有的 55px 间距。
    center_x, center_y, radius = 300.0, height / 2, 120.0
    legend_x = 475.0
    parts = [
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" '
        'xmlns="http://www.w3.org/2000/svg">',
        "<title>饼图</title>",
        '<rect width="100%" height="100%" fill="#ffffff" rx="12"/>',
    ]
    start_angle = -math.pi / 2
    for index, (label, value) in enumerate(items):
        sweep = value / total * math.tau
        end_angle = start_angle + sweep
        start_x = center_x + radius * math.cos(start_angle)
        start_y = center_y + radius * math.sin(start_angle)
        end_x = center_x + radius * math.cos(end_angle)
        end_y = center_y + radius * math.sin(end_angle)
        large_arc = 1 if sweep > math.pi else 0
        color = PALETTE[index % len(PALETTE)]
        title = _escape_text(f"{label}: {_format_number(value)}")
        if math.isclose(sweep, math.tau, rel_tol=0.0, abs_tol=1e-9):
            parts.append(
                f'<circle class="chart-pie-slice" cx="{_svg_number(center_x)}" '
                f'cy="{_svg_number(center_y)}" r="{radius}" fill="{color}" '
                f'stroke="#ffffff" stroke-width="2"><title>{title}</title></circle>'
            )
        elif sweep > 0:
            path = (
                f"M {_svg_number(center_x)} {_svg_number(center_y)} L {_svg_number(start_x)} {_svg_number(start_y)} "
                f"A {radius} {radius} 0 {large_arc} 1 {_svg_number(end_x)} {_svg_number(end_y)} Z"
            )
            parts.append(
                f'<path class="chart-pie-slice" d="{path}" fill="{color}" stroke="#ffffff" stroke-width="2">'
                f"<title>{title}</title></path>"
            )
        legend_y = 42 + index * 34
        parts.append(
            f'<rect x="{_svg_number(legend_x)}" y="{legend_y - 11}" width="14" height="14" '
            f'fill="{color}" rx="2"/>'
            f'<text x="{_svg_number(legend_x + 24)}" y="{legend_y}" font-size="13" fill="#334155">'
            f"{_escape_text(label)}</text>"
        )
        start_angle = end_angle
    parts.append("</svg>")
    return "".join(parts)


def _render_timeline_svg(body: str) -> str | None:
    """Render Mermaid timeline source as a static SVG chart.

    Args:
        body: 移除 frontmatter 后的 Mermaid 主体。

    Returns:
        渲染成功时返回 SVG 标记；源码不符合受支持格式时返回 None。
    """
    items: list[tuple[str, str]] = []
    for line in body.splitlines()[1:]:
        if ":" not in line:
            continue
        time_text, event_text = line.split(":", maxsplit=1)
        time_text = time_text.strip()
        event_text = event_text.strip().replace("<br>", " / ")
        if time_text and event_text:
            items.append((time_text, event_text))
    if not items:
        return None

    width = min(max(620, len(items) * 190), 1200)
    height = 270
    left, right, line_y = 64, 64, 120
    step = (width - left - right) / max(len(items) - 1, 1)
    parts = [
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" role="img" '
        'xmlns="http://www.w3.org/2000/svg">',
        "<title>时间轴</title>",
        '<rect width="100%" height="100%" fill="#ffffff" rx="12"/>',
        f'<line x1="{left}" y1="{line_y}" x2="{width - right}" y2="{line_y}" '
        'stroke="#94a3b8" stroke-width="3" stroke-linecap="round"/>',
    ]
    for index, (time_text, event_text) in enumerate(items):
        x = left + index * step
        event_y = 170 if index % 2 == 0 else 84
        parts.append(
            f'<circle class="chart-timeline-point" cx="{_svg_number(x)}" cy="{line_y}" r="8" '
            'fill="#2563eb" stroke="#dbeafe" stroke-width="5"/>'
            f'<text x="{_svg_number(x)}" y="{event_y - 20 if index % 2 == 0 else event_y + 26}" '
            'text-anchor="middle" font-size="12" font-weight="600" fill="#1e3a8a">'
            f"{_escape_text(time_text)}</text>"
            f'<text x="{_svg_number(x)}" y="{event_y}" text-anchor="middle" font-size="12" fill="#334155">'
            f"{_escape_text(event_text)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def render_mermaid_chart_as_svg(code: str) -> str | None:
    """Convert supported Mermaid chart source into safe static SVG markup.

    Args:
        code: Mermaid 代码块中的原始源码。

    Returns:
        受支持的 xychart、pie 或 timeline 图对应 SVG；其他 Mermaid 类型返回 None。
    """
    body = _strip_frontmatter(code)
    first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
    try:
        if first_line.startswith("xychart"):
            return _render_xychart_svg(code, body)
        if first_line == "pie":
            return _render_pie_svg(body)
        if first_line == "timeline":
            return _render_timeline_svg(body)
    except (ValueError, TypeError, OverflowError):
        return None
    return None
