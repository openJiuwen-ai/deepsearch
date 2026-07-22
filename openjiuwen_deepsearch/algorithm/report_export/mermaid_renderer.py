# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""将项目生成的 Mermaid 图表确定性渲染为 SVG 或内存 PNG。"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from openjiuwen_deepsearch.algorithm.report_export.chart_svg import (
    is_horizontal_xychart,
    render_mermaid_chart_as_svg as _render_svg,
)
from openjiuwen_deepsearch.algorithm.report_export.mermaid_preprocess import (
    MermaidRenderOptions,
    preprocess_mermaid_code,
)


FONT_PATH = (
    Path(__file__).resolve().parents[1]
    / "chart_generation"
    / "fonts"
    / "kt_font.ttf"
)
NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?")
SERIES_RE = re.compile(r"^\s*(?:bar|line)\s+\[([^\]]+)]\s*$", re.MULTILINE)
SVG_NS = "{http://www.w3.org/2000/svg}"


def _format_number(value: float) -> str:
    """将图表数值格式化为稳定的简短文本。

    Args:
        value: 待格式化的有限数值。

    Returns:
        去除无意义尾零的数值文本。
    """
    if abs(value) < 1e-9:
        return "0"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _show_data_labels(code: str) -> bool:
    """读取 Mermaid frontmatter 中的数值标签开关。

    Args:
        code: Mermaid 源码。

    Returns:
        配置明确开启 `showDataLabel` 时返回 True。
    """
    return bool(
        re.search(
            r"^\s*showDataLabel\s*:\s*true\s*$",
            code,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )


def _series_values(code: str) -> list[float]:
    """提取第一组受支持的 xychart 序列值。

    Args:
        code: 已完成工程量级预处理的 Mermaid 源码。

    Returns:
        第一组柱状或折线序列；无法解析时返回空列表。
    """
    match = SERIES_RE.search(code)
    if match is None:
        return []
    return [float(value) for value in NUMBER_RE.findall(match.group(1))]


def _horizontal_baseline_x(root: ET.Element) -> float | None:
    """读取横向图 SVG 中已计算好的数值轴基线位置。

    Args:
        root: 图表 SVG 的根节点。

    Returns:
        横向零轴的 x 坐标；图表未提供竖直基线时返回 None。
    """
    axis = root.find(f".//{SVG_NS}line[@class='chart-zero-axis']")
    if axis is None:
        return None
    x1 = float(axis.get("x1", "nan"))
    x2 = float(axis.get("x2", "nan"))
    if not math.isfinite(x1) or not math.isfinite(x2) or not math.isclose(x1, x2):
        return None
    return x1


def _add_value_labels(svg_markup: str, code: str) -> str:
    """按 SVG 中已计算的几何位置添加 xychart 数值标签。

    Args:
        svg_markup: 确定性渲染器生成的 SVG。
        code: 已完成预处理的 Mermaid 源码。

    Returns:
        添加标签后的 SVG；图形与数据不匹配时返回原 SVG。
    """
    if not _show_data_labels(code):
        return svg_markup

    values = _series_values(code)
    if not values:
        return svg_markup
    root = ET.fromstring(svg_markup)
    bars = root.findall(f".//{SVG_NS}rect[@class='chart-bar']")
    points = root.findall(f".//{SVG_NS}circle[@class='chart-line-point']")
    shapes = bars or points
    if len(shapes) != len(values):
        return svg_markup

    horizontal = bool(bars) and is_horizontal_xychart(code)
    baseline_x = _horizontal_baseline_x(root) if horizontal else None
    if horizontal and baseline_x is None:
        return svg_markup
    for shape, value in zip(shapes, values, strict=True):
        if shape.tag.endswith("rect"):
            x = float(shape.get("x", "0"))
            y = float(shape.get("y", "0"))
            width = float(shape.get("width", "0"))
            height = float(shape.get("height", "0"))
            if horizontal:
                label_y = y + height / 2 + 4
                # 柱体左右端与实际 SVG 基线比较，避免假设数值基线恒为 0。
                if x + width <= baseline_x:
                    label_x = x - 6
                    anchor = "end"
                else:
                    label_x = x + width + 6
                    anchor = "start"
            else:
                label_x = x + width / 2
                label_y = y - 7 if value >= 0 else y + height + 15
                anchor = "middle"
        else:
            label_x = float(shape.get("cx", "0"))
            label_y = float(shape.get("cy", "0")) - 9
            anchor = "middle"

        label = ET.Element(
            f"{SVG_NS}text",
            {
                "class": "chart-value-label",
                "x": f"{label_x:.2f}".rstrip("0").rstrip("."),
                "y": f"{label_y:.2f}".rstrip("0").rstrip("."),
                "text-anchor": anchor,
                "font-size": "11",
                "font-weight": "600",
                "fill": "#1f2937",
            },
        )
        label.text = _format_number(value)
        root.append(label)

    ET.register_namespace("", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def render_preprocessed_mermaid_chart_as_svg(code: str) -> str | None:
    """将已预处理的 Mermaid 图表渲染为安全的内联 SVG。

    Args:
        code: 已由 Mermaid 预处理器规范化的 Mermaid 代码块源码。

    Returns:
        渲染成功时返回 SVG；不支持或无效时返回 None。
    """
    svg_markup = _render_svg(code)
    if svg_markup is None:
        return None
    try:
        return _add_value_labels(svg_markup, code)
    except (ET.ParseError, TypeError, ValueError):
        return svg_markup


def render_mermaid_chart_as_svg(code: str) -> str | None:
    """将受支持的 Mermaid 图表渲染为安全的内联 SVG。

    Args:
        code: Mermaid 代码块源码。

    Returns:
        渲染成功时返回 SVG；不支持或无效时返回 None。
    """
    processed_code, _ = preprocess_mermaid_code(
        code,
        MermaidRenderOptions(warn_on_invalid_number=False),
    )
    return render_preprocessed_mermaid_chart_as_svg(processed_code)


def _color(value: str | None, default: str = "#000000") -> tuple[int, int, int, int]:
    """将 SVG 颜色转换为 Pillow RGBA 颜色。

    Args:
        value: SVG 颜色文本。
        default: 缺失或透明之外的默认颜色。

    Returns:
        Pillow 可用的 RGBA 元组。
    """
    if value in {None, "none"}:
        value = default
    rgb = ImageColor.getrgb(value)
    return (*rgb[:3], 255)


def _font(size: int) -> ImageFont.FreeTypeFont:
    """加载报告图表使用的仓库内置中文字体。

    Args:
        size: 字体像素尺寸。

    Returns:
        Pillow FreeType 字体对象。

    Raises:
        OSError: 内置字体不存在或无法加载时抛出。
    """
    return ImageFont.truetype(str(FONT_PATH), size=size)


def _draw_text(draw: ImageDraw.ImageDraw, node: ET.Element, scale: float) -> None:
    """按 SVG 文本节点的位置和锚点绘制文本。

    Args:
        draw: Pillow 绘图上下文。
        node: SVG text 节点。
        scale: SVG 坐标到 PNG 像素的缩放倍数。
    """
    # SVG title 仅用于浏览器悬浮提示；PNG 应只绘制 text 节点的可见内容。
    text = (node.text or "").strip()
    if not text:
        return
    x = float(node.get("x", "0")) * scale
    y = float(node.get("y", "0")) * scale
    font_size = max(9, round(float(node.get("font-size", "12")) * scale))
    font = _font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    anchor = node.get("text-anchor")
    if anchor == "middle":
        x -= width / 2
    elif anchor == "end":
        x -= width
    draw.text((x, y - font_size), text, font=font, fill=_color(node.get("fill", "#334155")))


def _draw_pie_path(draw: ImageDraw.ImageDraw, node: ET.Element, scale: float) -> None:
    """将确定性饼图 SVG 路径近似为 Pillow 多边形。

    Args:
        draw: Pillow 绘图上下文。
        node: SVG path 节点。
        scale: SVG 坐标到 PNG 像素的缩放倍数。
    """
    numbers = [float(value) for value in NUMBER_RE.findall(node.get("d", ""))]
    if len(numbers) < 11:
        return
    center_x, center_y, start_x, start_y = numbers[:4]
    radius = numbers[4]
    large_arc = int(numbers[7])
    sweep_flag = int(numbers[8])
    end_x, end_y = numbers[9:11]
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(end_y - center_y, end_x - center_x)
    delta = end_angle - start_angle
    if sweep_flag and delta <= 0:
        delta += math.tau
    elif not sweep_flag and delta >= 0:
        delta -= math.tau
    if large_arc and abs(delta) < math.pi:
        delta += math.copysign(math.tau, delta or 1)
    points = [(center_x * scale, center_y * scale)]
    segments = max(12, round(abs(delta) * radius / 8))
    for index in range(segments + 1):
        angle = start_angle + delta * index / segments
        points.append(
            (
                (center_x + radius * math.cos(angle)) * scale,
                (center_y + radius * math.sin(angle)) * scale,
            )
        )
    draw.polygon(points, fill=_color(node.get("fill")))


def _rasterize_svg(svg_markup: str, *, scale: float = 2.0) -> bytes:
    """使用 Pillow 绘制公共 SVG 场景并返回 PNG 字节。

    Args:
        svg_markup: 公共静态渲染器生成的 SVG。
        scale: PNG 相对 SVG viewBox 的像素倍率。

    Returns:
        PNG 文件字节。

    Raises:
        ValueError: SVG 缺少有效 viewBox 时抛出。
        OSError: 内置字体无法加载时抛出。
    """
    root = ET.fromstring(svg_markup)
    viewbox = [float(value) for value in root.get("viewBox", "").split()]
    if len(viewbox) != 4 or viewbox[2] <= 0 or viewbox[3] <= 0:
        raise ValueError("chart SVG has no valid viewBox")
    image = Image.new("RGBA", (round(viewbox[2] * scale), round(viewbox[3] * scale)), "white")
    draw = ImageDraw.Draw(image)

    for node in root.iter():
        tag = node.tag.rsplit("}", maxsplit=1)[-1]
        if tag == "rect":
            x = float(node.get("x", "0")) * scale
            y = float(node.get("y", "0")) * scale
            width_value = node.get("width", "0")
            height_value = node.get("height", "0")
            if width_value == "100%":
                width = image.width
            else:
                width = float(width_value) * scale
            if height_value == "100%":
                height = image.height
            else:
                height = float(height_value) * scale
            draw.rounded_rectangle(
                (x, y, x + width, y + height),
                radius=float(node.get("rx", "0")) * scale,
                fill=_color(node.get("fill", "#ffffff")),
            )
        elif tag == "line":
            draw.line(
                (
                    float(node.get("x1", "0")) * scale,
                    float(node.get("y1", "0")) * scale,
                    float(node.get("x2", "0")) * scale,
                    float(node.get("y2", "0")) * scale,
                ),
                fill=_color(node.get("stroke", "#94a3b8")),
                width=max(1, round(float(node.get("stroke-width", "1")) * scale)),
            )
        elif tag == "polyline":
            points = [
                tuple(float(part) * scale for part in pair.split(","))
                for pair in node.get("points", "").split()
            ]
            if points:
                draw.line(
                    points,
                    fill=_color(node.get("stroke", "#2563eb")),
                    width=max(1, round(float(node.get("stroke-width", "1")) * scale)),
                    joint="curve",
                )
        elif tag == "circle":
            cx = float(node.get("cx", "0")) * scale
            cy = float(node.get("cy", "0")) * scale
            radius = float(node.get("r", "0")) * scale
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=_color(node.get("fill")))
        elif tag == "path":
            _draw_pie_path(draw, node, scale)
        elif tag == "text":
            _draw_text(draw, node, scale)

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_preprocessed_mermaid_chart_as_png(code: str) -> bytes | None:
    """将已预处理的 Mermaid 图表渲染为内存 PNG。

    Args:
        code: 已由 Mermaid 预处理器规范化的 Mermaid 代码块源码。

    Returns:
        PNG 字节；不支持、无效或字体不可用时返回 None。
    """
    svg_markup = render_preprocessed_mermaid_chart_as_svg(code)
    if svg_markup is None:
        return None
    try:
        return _rasterize_svg(svg_markup)
    except (ET.ParseError, OSError, TypeError, ValueError):
        return None


def render_mermaid_chart_as_png(code: str) -> bytes | None:
    """将受支持的 Mermaid 图表渲染为内存 PNG。

    Args:
        code: Mermaid 代码块源码。

    Returns:
        PNG 字节；不支持、无效或字体不可用时返回 None。
    """
    processed_code, _ = preprocess_mermaid_code(
        code,
        MermaidRenderOptions(warn_on_invalid_number=False),
    )
    return render_preprocessed_mermaid_chart_as_png(processed_code)
