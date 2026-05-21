import pytest

from openjiuwen_deepsearch.algorithm.user_feedback_processor.section_locator import (
    locate_enclosing_numbered_major_block,
    locate_section,
)


@pytest.mark.parametrize(
    ("report", "needle", "expected_heading_prefix"),
    [
        (
            "# 标题\n\n## 第一章\nA\n\n## 第二章\n这里是选中内容\n更多内容\n",
            "选中内容",
            "## 第二章",
        ),
        (
            "# 标题\n\n"
            "## 第二章\n"
            "章节前言\n\n"
            "### 2.1 小节\n"
            "这里是选中内容\n"
            "更多内容\n\n"
            "### 2.2 小节\n"
            "别的内容\n",
            "选中内容",
            "### 2.1 小节",
        ),
    ],
    ids=["h2_encloses_selection", "prefers_deepest_enclosing_heading"],
)
def test_locate_section_selects_smallest_enclosing_block(report, needle, expected_heading_prefix):
    start = report.index(needle)
    end = start + len(needle)

    section = locate_section(report, start, end)

    assert section.section_text.startswith(expected_heading_prefix)
    assert section.section_start_offset == report.index(expected_heading_prefix)


def test_locate_section_falls_back_to_full_report_when_no_heading():
    report = "纯文本报告"

    section = locate_section(report, 0, 2)

    assert section.section_text == report


def test_locate_enclosing_numbered_major_block_returns_containing_h1_block():
    report = (
        "# 1. 第一大章\n"
        "前言\n"
        "## 1.1 已有小节\n"
        "已有内容\n"
        "## 1.2 目标小节\n"
        "目标内容\n"
        "# 2. 第二大章\n"
        "其他内容"
    )
    start = report.index("目标内容")
    end = start + len("目标内容")

    block = locate_enclosing_numbered_major_block(report, start, end)

    assert block is not None
    assert block.title == "1. 第一大章"
    assert block.heading == "# 1. 第一大章"
    assert block.level == 1
    assert block.block_start == 0
    assert block.block_end == report.index("# 2. 第二大章")


def test_locate_enclosing_numbered_major_block_ignores_unnumbered_h1():
    report = "# 第一大章\n## 1.1 目标小节\n目标内容\n# 2. 第二大章\n其他内容"
    start = report.index("目标内容")
    end = start + len("目标内容")

    block = locate_enclosing_numbered_major_block(report, start, end)

    assert block is None
