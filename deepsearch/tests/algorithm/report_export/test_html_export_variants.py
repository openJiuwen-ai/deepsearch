# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""测试统一 HTML 导出器的页面变体。"""

from __future__ import annotations

from bs4 import BeautifulSoup


def test_html_export_variants_share_mermaid_rendering_and_limit_styled_dom(tmp_path) -> None:
    """普通与美化变体应共享 Mermaid 转换，仅美化变体增加报告语义结构。

    Args:
        tmp_path: pytest 提供的临时目录。
    """
    from openjiuwen_deepsearch.algorithm.report_export.html_export import (
        ConvertOptions,
        convert_md_to_html,
    )

    source = tmp_path / "report.md"
    standard_target = tmp_path / "standard.html"
    styled_target = tmp_path / "styled.html"
    source.write_text(
        "# 报告\n\n"
        "```mermaid\n"
        "xychart-beta\n"
        '    x-axis ["收入", "利润"]\n'
        '    y-axis "亿元" 0 --> 10\n'
        "    bar [8, 3]\n"
        "```",
        encoding="utf-8",
    )

    convert_md_to_html(source, standard_target)
    convert_md_to_html(
        source,
        styled_target,
        options=ConvertOptions(page_variant="styled"),
    )

    standard = BeautifulSoup(standard_target.read_text(encoding="utf-8"), "html.parser")
    styled = BeautifulSoup(styled_target.read_text(encoding="utf-8"), "html.parser")

    assert standard.select_one(".mermaid-wrap svg.chart-svg") is not None
    assert styled.select_one(".mermaid-wrap svg.chart-svg") is not None
    assert standard.select_one("main.report-shell") is None
    assert styled.select_one("main.report-shell") is not None


def test_html_export_variants_preserve_clickable_report_toc(tmp_path) -> None:
    """普通与美化 HTML 都应保留目录链接及对应章节 ID。"""
    from openjiuwen_deepsearch.algorithm.report_export.html_export import (
        ConvertOptions,
        convert_md_to_html,
    )

    source = tmp_path / "report.md"
    source.write_text(
        "# 报告\n\n"
        "# 目录\n\n"
        "[第一章](#chapter-1)\n\n"
        "[第二章](#chapter-2)\n\n"
        "# 第一章\n\n正文。\n\n"
        "# 第二章\n",
        encoding="utf-8",
    )

    variants = (
        (tmp_path / "standard.html", None),
        (tmp_path / "styled.html", ConvertOptions(page_variant="styled")),
    )
    for target, options in variants:
        convert_md_to_html(source, target, options=options)
        html = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
        for index in (1, 2):
            link = html.select_one(f'a[href="#chapter-{index}"]')
            assert link is not None
            assert link.find_parent("li") is None
            target = html.select_one(f'h1#chapter-{index}')
            assert target is not None
            assert target.find_parent("p") is None


def test_html_export_places_generated_chapter_ids_on_h1(tmp_path) -> None:
    """生成的章节 ID 应直接位于 H1 上，不产生额外空段落。"""
    from openjiuwen_deepsearch.algorithm.report_export.html_export import convert_md_to_html

    source = tmp_path / "report.md"
    target = tmp_path / "report.html"
    source.write_text(
        "# 报告\n\n"
        "# 目录\n\n[1. 第一章](#chapter-1)\n\n"
        "# 1. 第一章\n\n正文\n",
        encoding="utf-8",
    )

    convert_md_to_html(source, target)
    soup = BeautifulSoup(target.read_text(encoding="utf-8"), "html.parser")
    heading = soup.select_one("h1#chapter-1")

    assert heading is not None
    assert soup.select_one("a#chapter-1") is None
    assert heading.find_parent("p") is None
