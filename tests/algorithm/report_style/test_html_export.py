"""Test standalone Markdown to HTML export for report styling."""

import math

from bs4 import BeautifulSoup
import pytest

from openjiuwen_deepsearch.algorithm.report_style.export import html_export


def test_convert_md_to_html_keeps_report_content_and_local_asset_links(tmp_path, caplog):
    """Render report text while preserving local asset and inference links.

    Args:
        tmp_path: pytest 提供的临时目录。
        caplog: pytest 日志捕获夹具。
    """
    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    source.write_text(
        "# 标题\n\n# 摘要\n\n摘要内容\n\n# 1. 市场\n\n正文\n\n"
        "![图](charts/chart_1.png)\n\n[依据](infer/inference_7.html)",
        encoding="utf-8",
    )

    with caplog.at_level("INFO", logger=html_export.__name__):
        html_export.convert_md_to_html(source, target)

    html = target.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    shell = soup.select_one("main.report-shell")
    cover = soup.select_one("header.report-cover")
    abstract = soup.select_one("section.report-abstract")
    content = soup.select_one("div.report-content")
    sections = soup.select("section.report-section")

    assert '<h1 id="_1">标题</h1>' in html
    assert "正文" in html
    assert 'src="charts/chart_1.png"' in html
    assert 'href="infer/inference_7.html"' in html
    assert shell is not None
    assert cover is not None
    assert abstract is not None
    assert content is not None
    assert sections
    assert shell in cover.parents
    assert shell in abstract.parents
    assert shell in content.parents
    assert all(shell in section.parents for section in sections)
    assert abstract.get_text(" ", strip=True) == "摘要 摘要内容"
    assert sections[0].find("h1").find_next_sibling("p").get_text(strip=True) == "正文"
    assert ".report-shell {\n            width: 1280px;" in html
    assert ".report-section > h1" in html
    assert ".report-table th" in html
    assert "var(--report-table-header-background)" in html
    assert "@media" not in html
    assert str(tmp_path) not in caplog.text
    assert "Completed Markdown to HTML conversion html_bytes=" in caplog.text


def test_convert_md_to_html_keeps_unsupported_mermaid_as_source_without_runtime(tmp_path):
    """无法静态绘制的 Mermaid 图保留源码且不依赖浏览器运行时。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    source.write_text("```mermaid\ngraph TD\n  A --> B\n```", encoding="utf-8")

    html_export.convert_md_to_html(source, target)

    html = target.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    source_block = soup.select_one("pre > code.language-mermaid")

    assert source_block is not None
    assert source_block.get_text("\n", strip=True) == "graph TD\n  A --> B"
    assert "mermaid.esm.min.mjs" not in html


def test_convert_md_to_html_renders_xychart_mermaid_as_inline_svg(tmp_path):
    """将 xychart Mermaid 转换为可离线查看的内嵌 SVG。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    source.write_text(
        "```mermaid\nxychart-beta\n"
        '    x-axis ["商汤", "云从", "云天励飞"]\n'
        '    y-axis "毛利率" 0 --> 60\n'
        "    bar [42.9, 35.9, 20.9]\n```",
        encoding="utf-8",
    )

    html_export.convert_md_to_html(source, target)

    html = target.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    chart = soup.select_one(".mermaid-wrap svg.chart-svg")

    assert chart is not None
    assert len(chart.select("rect.chart-bar")) == 3
    assert "商汤" in chart.get_text()
    assert "毛利率" in chart.get_text()
    assert "mermaid.esm.min.mjs" not in html


@pytest.mark.parametrize(
    ("frontmatter", "chart_header"),
    [
        ("", "xychart-beta horizontal"),
        ("---\nconfig:\n    horizontal: true\n---\n", "xychart-beta"),
    ],
)
def test_convert_md_to_html_renders_horizontal_xychart_as_horizontal_bars(
    tmp_path,
    frontmatter,
    chart_header,
):
    """将两种横向 xychart 标记均转换为水平条形图。

    Args:
        tmp_path: pytest 提供的临时目录。
        frontmatter: Mermaid frontmatter 文本。
        chart_header: Mermaid xychart 标题行。
    """
    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    source.write_text(
        "```mermaid\n"
        f"{frontmatter}"
        f"{chart_header}\n"
        '    x-axis ["比亚迪汽车", "吉利汽车", "上汽通用五菱"]\n'
        '    y-axis "万辆" 0 --> 36\n'
        "    bar [29.59, 16.43, 9.98]\n```",
        encoding="utf-8",
    )

    html_export.convert_md_to_html(source, target)

    soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
    chart = soup.select_one(".mermaid-wrap svg.chart-svg")
    bars = chart.select("rect.chart-bar")
    category_labels = chart.select("text.chart-category-label")

    assert len(bars) == 3
    assert all(float(bar["width"]) > float(bar["height"]) for bar in bars)
    assert [label.get_text() for label in category_labels] == [
        "比亚迪汽车",
        "吉利汽车",
        "上汽通用五菱",
    ]
    assert all("rotate" not in label.attrs.get("transform", "") for label in category_labels)


def test_convert_md_to_html_distinguishes_zero_baseline_for_mixed_sign_bars(tmp_path):
    """混合正负值柱状图应标注零基线，最小刻度线保持普通网格线样式。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    source.write_text(
        "```mermaid\nxychart-beta\n"
        '    x-axis ["比亚迪纯电销量", "特斯拉纯电销量", "比亚迪海外销量"]\n'
        '    y-axis "%" -9 --> 150\n'
        "    bar [27.86, -8.6, 145]\n```",
        encoding="utf-8",
    )

    html_export.convert_md_to_html(source, target)

    soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
    chart = soup.select_one(".mermaid-wrap svg.chart-svg")
    zero_axis = chart.select_one("line.chart-zero-axis")
    zero_tick = chart.select_one("text.chart-zero-tick")
    min_grid = chart.select("line.chart-grid")[-1]

    assert zero_axis is not None
    assert zero_tick is not None
    assert zero_tick.get_text() == "0"
    assert min_grid["stroke-dasharray"] == "3 3"
    assert zero_axis["y1"] != min_grid["y1"]


def test_convert_md_to_html_reserves_viewbox_space_for_long_x_axis_labels(tmp_path):
    """为旋转的长中文横坐标标签保留足够的 SVG 底部空间。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    label = "研发投入占比"
    source.write_text(
        "```mermaid\nxychart-beta\n"
        f'    x-axis ["营业收入占比", "{label}", "国内市场占比", "海外市场占比"]\n'
        '    y-axis "%" 0 --> 60\n'
        "    bar [48, 51, 54, 27]\n```",
        encoding="utf-8",
    )

    html_export.convert_md_to_html(source, target)

    soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
    chart = soup.select_one(".mermaid-wrap svg.chart-svg")
    label_node = next(text for text in chart.select("text") if text.get_text() == label)
    viewbox_height = float(chart["viewbox"].split()[3])
    label_y = float(label_node["y"])
    label_width = len(label) * 11
    rotated_lower_edge = label_y + label_width * math.sin(math.radians(32)) + 4

    assert rotated_lower_edge <= viewbox_height


def test_convert_md_to_html_renders_line_mermaid_as_inline_svg(tmp_path):
    """将折线 Mermaid 转换为可离线查看的内嵌 SVG。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    source.write_text(
        "```mermaid\nxychart-beta\n"
        '    x-axis ["2023", "2024", "2025"]\n'
        '    y-axis "营收" 0 --> 60\n'
        "    line [20, 35, 52]\n```",
        encoding="utf-8",
    )

    html_export.convert_md_to_html(source, target)

    soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
    chart = soup.select_one(".mermaid-wrap svg.chart-svg")

    assert chart is not None
    assert chart.select_one("polyline.chart-line") is not None
    assert len(chart.select("circle.chart-line-point")) == 3


def test_convert_md_to_html_renders_pie_mermaid_as_inline_svg(tmp_path):
    """将饼图 Mermaid 转换为可离线查看的内嵌 SVG。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    source.write_text(
        "```mermaid\npie\n"
        '    "商汤 (42.9%)" : 42.9\n'
        '    "云从 (35.9%)" : 35.9\n'
        '    "云天励飞 (20.9%)" : 20.9\n```',
        encoding="utf-8",
    )

    html_export.convert_md_to_html(source, target)

    soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
    chart = soup.select_one(".mermaid-wrap svg.chart-svg")

    assert chart is not None
    assert len(chart.select("path.chart-pie-slice")) == 3
    assert "商汤 (42.9%)" in chart.get_text()


def test_convert_md_to_html_renders_timeline_mermaid_as_inline_svg(tmp_path):
    """将时间轴 Mermaid 转换为可离线查看的内嵌 SVG。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    source.write_text(
        "```mermaid\ntimeline\n"
        "    2024 : 发布第一代产品\n"
        "    2025 : 完成规模化部署\n```",
        encoding="utf-8",
    )

    html_export.convert_md_to_html(source, target)

    soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
    chart = soup.select_one(".mermaid-wrap svg.chart-svg")

    assert chart is not None
    assert len(chart.select("circle.chart-timeline-point")) == 2
    assert "完成规模化部署" in chart.get_text()


def test_convert_md_to_html_structures_h2_led_report_without_changing_links(tmp_path):
    """Export an h2-led report with cover, abstract, sections, and intact local links.

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    source = tmp_path / "legal-report.md"
    target = tmp_path / "legal-report.html"
    source.write_text(
        "## 法律意见书\n\n### 摘要\n\n摘要内容\n\n## 一、事实认定\n\n"
        "### （一）交易背景\n\n事实内容\n\n## 二、法律分析\n\n"
        "[依据](infer/inference_7.html)",
        encoding="utf-8",
    )

    html_export.convert_md_to_html(source, target)

    html = target.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    cover = soup.select_one("header.report-cover")
    abstract = soup.select_one("section.report-abstract")
    sections = soup.select(".report-content > section.report-section")

    assert cover.find("h2").get_text(strip=True) == "法律意见书"
    assert abstract.find("h3").get_text(strip=True) == "摘要"
    assert [section.find("h2").get_text(strip=True) for section in sections] == [
        "一、事实认定",
        "二、法律分析",
    ]
    assert sections[0].find("h3").get_text(strip=True) == "（一）交易背景"
    assert 'href="infer/inference_7.html"' in html


@pytest.mark.parametrize(
    ("markdown", "expected_text"),
    [
        ("## 标题", "标题"),
        ("说明", "说明"),
    ],
)
def test_convert_md_to_html_keeps_stable_section_when_cover_consumes_all_content(
    tmp_path, markdown, expected_text
):
    """仅含封面内容的 Markdown 导出仍生成稳定正文章节。

    Args:
        tmp_path: pytest 提供的临时目录。
        markdown: 待导出的最小 Markdown 文本。
        expected_text: 导出 HTML 中必须保留的原始文本。
    """
    source = tmp_path / "minimal-report.md"
    target = tmp_path / "minimal-report.html"
    source.write_text(markdown, encoding="utf-8")

    html_export.convert_md_to_html(source, target)

    soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")

    assert soup.select_one("body.report-page") is not None
    assert soup.select_one("main.report-shell") is not None
    assert soup.select_one("header.report-cover") is not None
    assert soup.select_one("div.report-content") is not None
    assert soup.select_one("div.report-content > section.report-section") is not None
    assert soup.select_one("body").get_text(strip=True) == expected_text
