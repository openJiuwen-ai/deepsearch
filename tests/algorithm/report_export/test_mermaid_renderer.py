# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""测试公共报告导出层的 Mermaid 静态渲染能力。"""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET

from PIL import Image
import pytest


SVG_NS = "{http://www.w3.org/2000/svg}"


def test_mermaid_renderer_outputs_labeled_svg_and_png() -> None:
    """同一 Mermaid 图表应输出带数值标签的 SVG 和有效 PNG。"""
    from openjiuwen_deepsearch.algorithm.report_export.mermaid_renderer import (
        render_mermaid_chart_as_png,
        render_mermaid_chart_as_svg,
    )

    mermaid_code = """---
config:
    showDataLabel: true
---
xychart-beta
    x-axis ["收入", "利润"]
    y-axis "亿元" 0 --> 60
    bar [48, 21]
"""

    svg = render_mermaid_chart_as_svg(mermaid_code)
    png = render_mermaid_chart_as_png(mermaid_code)

    assert svg is not None
    assert 'class="chart-value-label"' in svg
    assert ">48<" in svg
    assert png is not None
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.width > 0
        assert image.height > 0


def test_mermaid_renderer_only_adds_labels_when_enabled() -> None:
    """数值标签应严格遵从 Mermaid 的 showDataLabel 配置。

    Returns:
        None.
    """
    from openjiuwen_deepsearch.algorithm.report_export.mermaid_renderer import (
        render_mermaid_chart_as_svg,
    )

    svg = render_mermaid_chart_as_svg(
        """xychart-beta
    x-axis ["A", "B"]
    y-axis "值" 0 --> 4
    bar [2, 3]
"""
    )

    assert svg is not None
    assert 'class="chart-value-label"' not in svg


@pytest.mark.parametrize(
    ("series", "axis", "expected_anchors"),
    [
        ("[-1, 80]", "-100 --> 100", ("end", "start")),
        ("[-1, -80]", "-100 --> 0", ("end", "end")),
        ("[-80, 80]", "-100 --> 100", ("end", "start")),
    ],
)
def test_horizontal_bar_labels_follow_source_orientation_and_baseline(
    series: str,
    axis: str,
    expected_anchors: tuple[str, str],
) -> None:
    """横向柱状图的短柱、全负值和混合值标签均应落在外端。"""
    from openjiuwen_deepsearch.algorithm.report_export.mermaid_renderer import (
        render_mermaid_chart_as_svg,
    )

    code = f"""---
config:
    showDataLabel: true
---
xychart-beta horizontal
    x-axis ["短柱", "长柱"]
    y-axis "值" {axis}
    bar {series}
"""

    svg = render_mermaid_chart_as_svg(code)
    assert svg is not None
    root = ET.fromstring(svg)
    bars = root.findall(f".//{SVG_NS}rect[@class='chart-bar']")
    labels = root.findall(f".//{SVG_NS}text[@class='chart-value-label']")
    assert len(bars) == len(labels) == 2

    for bar, label, expected_anchor in zip(bars, labels, expected_anchors, strict=True):
        bar_x = float(bar.attrib["x"])
        bar_width = float(bar.attrib["width"])
        label_x = float(label.attrib["x"])
        assert label.attrib["text-anchor"] == expected_anchor
        if expected_anchor == "end":
            assert label_x < bar_x
        else:
            assert label_x > bar_x + bar_width

def test_horizontal_chart_expands_viewbox_for_long_generated_category_labels() -> None:
    """生成器切换横向图后，长中文类别名不应落在 SVG viewBox 之外。"""
    from openjiuwen_deepsearch.algorithm.report.report_utils import XYChartMermaidGenerator
    from openjiuwen_deepsearch.algorithm.report_export.mermaid_renderer import (
        render_mermaid_chart_as_png,
        render_mermaid_chart_as_svg,
    )

    long_label = "长期重点投资项目进展情况及后续安排说明与风险评估建议"
    code = XYChartMermaidGenerator.generate_from_json(
        '{"image_type":"bar","unit":"亿元","records":['
        f'["{long_label}",12],["普通项目",8]]}}'
    )

    assert "horizontal: true" in code
    assert "xychart-beta horizontal" not in code
    svg = render_mermaid_chart_as_svg(code)
    png = render_mermaid_chart_as_png(code)

    assert svg is not None
    root = ET.fromstring(svg)
    category_label = root.find(f".//{SVG_NS}text[@class='chart-category-label']")
    assert category_label is not None
    label_x = float(category_label.attrib["x"])
    assert label_x - len(long_label) * 11 >= 0
    _, _, viewbox_width, viewbox_height = map(float, root.attrib["viewBox"].split())
    assert png is not None
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.width == round(viewbox_width * 2)
        assert image.height == round(viewbox_height * 2)


def test_all_negative_horizontal_labels_reserve_space_for_value_labels() -> None:
    """生成器产生的全负横向图中，类别和值标签的边界不应重叠。"""
    from openjiuwen_deepsearch.algorithm.report.report_utils import XYChartMermaidGenerator
    from openjiuwen_deepsearch.algorithm.report_export.mermaid_renderer import (
        render_mermaid_chart_as_svg,
    )

    code = XYChartMermaidGenerator.generate_from_json(
        json.dumps(
            {
                "image_type": "bar",
                "unit": "%",
                "records": [
                    ["第一产业年度实际同比增长率变化", -3],
                    ["第二产业年度实际同比增长率变化", -2],
                    ["第三产业年度实际同比增长率变化", -1],
                ],
            },
            ensure_ascii=False,
        )
    )

    assert "horizontal: true" in code
    assert "xychart-beta horizontal" not in code
    svg = render_mermaid_chart_as_svg(code)
    assert svg is not None
    root = ET.fromstring(svg)
    category_labels = root.findall(f".//{SVG_NS}text[@class='chart-category-label']")
    value_labels = root.findall(f".//{SVG_NS}text[@class='chart-value-label']")
    assert len(category_labels) == len(value_labels) == 3
    for category_label, value_label in zip(category_labels, value_labels, strict=True):
        category_right = float(category_label.attrib["x"])
        value_width = len(value_label.text or "") * 8
        value_left = float(value_label.attrib["x"]) - value_width
        assert category_right < value_left

def test_long_english_line_chart_keeps_points_and_labels_inside_viewbox() -> None:
    """生成器产生的长英文折线图不应生成负绘图区高度。"""
    from openjiuwen_deepsearch.algorithm.report.report_utils import XYChartMermaidGenerator
    from openjiuwen_deepsearch.algorithm.report_export.mermaid_renderer import (
        render_mermaid_chart_as_png,
        render_mermaid_chart_as_svg,
    )

    code = XYChartMermaidGenerator.generate_from_json(
        json.dumps(
            {
                "image_type": "line",
                "unit": "值",
                "records": [
                    [
                        "annual economic development growth trend comparison across major industries and regions performance",
                        1,
                    ],
                    [
                        "annual economic development growth trend comparison across key sectors and provinces performance",
                        2,
                    ],
                    [
                        "annual economic development growth trend comparison across important markets and cities performance",
                        3,
                    ],
                ],
            },
        )
    )

    assert "xychart-beta horizontal" not in code
    svg = render_mermaid_chart_as_svg(code)
    png = render_mermaid_chart_as_png(code)

    assert svg is not None
    root = ET.fromstring(svg)
    _, _, viewbox_width, viewbox_height = map(float, root.attrib["viewBox"].split())
    points = root.findall(f".//{SVG_NS}circle[@class='chart-line-point']")
    category_labels = root.findall(f".//{SVG_NS}text[@class='chart-category-label']")
    assert len(points) == len(category_labels) == 3
    for point in points:
        assert 0 <= float(point.attrib["cx"]) <= viewbox_width
        assert 0 <= float(point.attrib["cy"]) <= viewbox_height
    for label in category_labels:
        assert 0 <= float(label.attrib["x"]) <= viewbox_width
        assert 0 <= float(label.attrib["y"]) <= viewbox_height
        assert label.text is not None
        assert label.text.endswith("...")
        full_label = label.find(f"{SVG_NS}title")
        assert full_label is not None
        assert full_label.text is not None
        assert len(full_label.text) > len(label.text)

    assert png is not None
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.width == round(viewbox_width * 2)
        assert image.height == round(viewbox_height * 2)


@pytest.mark.parametrize(
    "items",
    [
        '"甲": 100\n    "乙": 0',
        '"甲": 0\n    "乙": 100',
        '"甲": 100\n    "乙": 0\n    "丙": 0',
    ],
)
def test_full_pie_slice_uses_a_complete_svg_shape(items: str) -> None:
    """合法的满圆饼图应以 SVG 完整图形表达，并可生成 PNG。"""
    from openjiuwen_deepsearch.algorithm.report_export.mermaid_renderer import (
        render_mermaid_chart_as_png,
        render_mermaid_chart_as_svg,
    )

    svg = render_mermaid_chart_as_svg(f"pie\n    {items}\n")
    png = render_mermaid_chart_as_png(f"pie\n    {items}\n")

    assert svg is not None
    root = ET.fromstring(svg)
    full_slices = root.findall(f".//{SVG_NS}circle[@class='chart-pie-slice']")
    assert len(full_slices) == 1
    assert png is not None
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        pie_center_x = round(image.width * 300 / 760)
        assert image.getpixel((pie_center_x, image.height // 2)) != (255, 255, 255)
