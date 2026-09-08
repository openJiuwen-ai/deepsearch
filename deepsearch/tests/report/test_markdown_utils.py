import pytest

from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.algorithm.report.report_utils import (
    MarkdownOutlineRenumber,
)


@pytest.mark.parametrize("input_str, expected", [
    ("第一章 Python入门", "Python入门"),  # 中文章节号
    ("第十2章 高级用法", "高级用法"),  # 中文+数字
    ("二、异常处理", "异常处理"),  # 中文序号
    ("3.4 数据结构", "数据结构"),  # 阿拉伯数字+点
    ("第九章", ""),  # 只有章节号，没有正文
    ("Chapter Intro", "Chapter Intro"),  # 无匹配前缀，保持原样
    # Year-range and digit-prefixed titles: must NOT strip year digits
    ("1.2025-2026年xxx", "1.2025-2026年xxx"),  # year after dot — not a section number
    ("1.1.2025-2026年xxx", "1.1.2025-2026年xxx"),  # hierarchical + year
    ("2025-2026年xxx", "2025-2026年xxx"),  # no section number
    ("3.14159分析", "3.14159分析"),  # decimal prefix
    # Standard section numbers with space: strip number, space consumed by \s*
    ("1. 2025-2026年xxx", " 2025-2026年xxx"),  # flat number + space + year (leading space from \s*)
    ("1.1 2025-2026年xxx", " 2025-2026年xxx"),  # hierarchical + space + year
    ("1.2 subsection title", " subsection title"),  # standard hierarchical
    ("1.1.2 deep subsection", " deep subsection"),  # three-level hierarchical
])
def test_strip_leading_number(input_str, expected):
    assert Reporter.strip_leading_number(input_str) == expected


@pytest.mark.parametrize(
    "input_md, expected",
    [
        # 一级标题去掉中文序号
        (
                "# 五、潜在挑战与风险管理策略建议",
                "# 潜在挑战与风险管理策略建议"
        ),
        # 二级标题去掉括号序号（英文括号）
        (
                "## (二) 方法论",
                "## 方法论"
        ),
        # 三级标题去掉括号序号（中文括号）
        (
                "### （一）研发孵化期",
                "### 研发孵化期"
        ),
        # 三级标题去掉数字序号
        (
                "### 1. 目标",
                "### 目标"
        ),
        # 三级标题去掉数字序号（带中文括号）
        (
                "### （1） 目标",
                "### 目标"
        ),
        # 三级标题去掉数字序号（带英文括号）
        (
                "### (1) 目标",
                "### 目标"
        ),
        # 四级标题转为无序列表
        (
                "#### 数据来源",
                "- **数据来源**"
        ),
        # 五级标题也转为无序列表
        (
                "##### 进一步细节",
                "- **进一步细节**"
        ),
        # 四级标题带数字
        (
                "#### 1.进一步细节",
                "- **进一步细节**"
        ),
        # 四级标题带数字、空格
        (
                "#### 1. 进一步细节",
                "- **进一步细节**"
        ),
        # 三四级标题结合
        (
                "### (二) 方法论\n#### 数据来源",
                "### 方法论\n- **数据来源**"
        ),
        # 普通文本保持不变
        (
                "这是正文",
                "这是正文"
        ),
    ]
)
def test_clean_markdown(input_md, expected):
    assert Reporter.clean_markdown_headers(input_md) == expected


@pytest.mark.parametrize("text, section_idx, expected", [
    # ✅ 合法场景
    ('5 财务分析\n5.1 三张报表分析框架\n5.2 关键财务比率分析\n5.3 同行业对比分析方法\n5.4 财务风险识别与评估', 5, True),

    # ✅ 合法场景：主章节 + 子章节从1开始
    ("1 主章节\n1.1 子章节一\n1.2 子章节二", 1, True),

    # ❌ 没有主章节
    ("1.1 子章节一\n1.2 子章节二", 1, False),

    # ❌ 主章节重复
    ("1 主章节\n1 主章节重复", 1, False),

    # ❌ 子章节不是从1开始
    ("1 主章节\n1.2 子章节二", 1, False),

    # ❌ 存在非法第三层格式
    ("1 主章节\n1.1 子章节一\n1.1.1 第三层", 1, False),

    # ❌ 存在纯数字行
    ("1 主章节\n123", 1, False),

    # ❌ 空文本
    ("", 1, False),
])
def test_is_valid_chapter_format(text, section_idx, expected):
    assert Reporter.is_valid_chapter_format(text, section_idx) == expected


def test_check_chapter_format_accepts_level1_only_outline():
    ok, reason = Reporter.check_chapter_format("1 市场概览", 1)
    assert ok is True, reason
    assert reason == ""


def test_check_chapter_format_accepts_date_range_in_title():
    """Dotted date ranges inside a title (e.g. 2010.3.12–2021.2.26) are not third-level numbering."""
    outline = (
        "2 数据汇总：VIX、GVZ、OVX日收盘价描述性统计（2010.3.12–2021.2.26）\n"
        "2.1 数据来源与样本区间说明（2010.3.12–2021.2.26，约2700个交易日）\n"
        "2.2 VIX、GVZ、OVX日收盘价描述性统计全样本汇总表"
    )
    ok, reason = Reporter.check_chapter_format(outline, 2)
    assert ok is True, reason
    assert reason == ""


def test_check_chapter_format_rejects_genuine_third_level_numbering():
    ok, reason = Reporter.check_chapter_format("2 主标题\n2.1.1 三级子节\n2.1 子节", 2)
    assert ok is False
    assert "third-level numbering" in reason


@pytest.mark.parametrize(
    "outline",
    [
        "1 市场概览\nHere is the requested outline:",
        "1 市场概览\n```",
        "1 市场概览\n正文说明",
        "说明文字\n1 市场概览",
        "1 市场概览\n2 错误章节",
        "1.1 子章节\n1 市场概览",
        "1 市场概览\n1.1",
        "1 市场概览\n1.1 现状\n1.3 趋势",
        "1 市场概览\n1.2 趋势\n1.1 现状",
        "1 市场概览\n1.1 现状\n1.1 趋势",
    ],
)
def test_check_chapter_format_rejects_extra_or_misordered_lines(outline):
    ok, reason = Reporter.check_chapter_format(outline, 1)
    assert ok is False
    assert reason


def test_check_chapter_format_returns_reason_for_markdown_heading():
    ok, reason = Reporter.check_chapter_format("## 1.1 子标题\n1 主标题", 1)
    assert ok is False
    assert "markdown heading" in reason


def test_check_chapter_format_returns_reason_for_digit_only_line():
    ok, reason = Reporter.check_chapter_format("1 主章节\n2025年市场规模", 1)
    assert ok is False
    assert "starts with digits" in reason


def test_check_chapter_format_accepts_level1_title_starting_with_year():
    outline = (
        "1 2025年中国低空经济全景透视：发展现状、技术成熟度与系统性风险评估\n"
        "1.1 政策跃迁与制度架构\n"
        "1.2 产业现状量化画像\n"
        "1.3 技术成熟度与核心瓶颈\n"
        "1.4 系统性风险矩阵"
    )
    ok, reason = Reporter.check_chapter_format(outline, 1)
    assert ok is True, reason
    assert reason == ""


# ---------------------------------------------------------------------------
# MarkdownOutlineRenumber tests — year-range and digit-prefixed titles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_md, expected_title_text",
    [
        # Year-range titles without section numbers: year must be preserved
        ("## 2025-2026年市场规模", "2025-2026年市场规模"),
        ("# 2025-2026年行业报告", "2025-2026年行业报告"),
        # Year-range titles with section number merged (no space): year preserved
        ("## 1.2025-2026年市场规模", "1.2025-2026年市场规模"),
        ("# 1.2025-2026年行业报告", "1.2025-2026年行业报告"),
        # Year-range titles with proper section number + space: year preserved
        ("## 1. 2025-2026年市场规模", "2025-2026年市场规模"),
        ("## 1.1 2025-2026年市场规模", "2025-2026年市场规模"),
        # Decimal / version-prefixed titles: digits preserved
        ("### 3.14159分析", "3.14159分析"),
        ("## 2.5G网络部署", "2.5G网络部署"),
        # Standard section numbering: title preserved
        ("## 1.1 市场规模分析", "市场规模分析"),
        ("# 1. 行业概览", "行业概览"),
        # Section number directly followed by title text (no space): number consumed
        ("## 1.1技术路线", "技术路线"),
        ("## 1.标题", "标题"),
        ("## 2.1市场规模分析", "市场规模分析"),
        ("### 3.1.2核心结论", "核心结论"),
    ],
)
def test_renumber_headers_preserves_title_text(input_md, expected_title_text):
    """renumber_headers must preserve title text, especially year/version digits."""
    renumber = MarkdownOutlineRenumber()
    result = renumber.renumber_headers(input_md)
    line = result.split("\n")[0]
    # Extract title text: everything after "# " or "## " or "### "
    import re as _re
    title_match = _re.match(r"^#{1,3}\s+(.*)", line)
    assert title_match is not None, f"Expected heading in result: {result!r}"
    actual_title = title_match.group(1).strip()
    assert expected_title_text in actual_title, (
        f"Expected title {expected_title_text!r} in heading {actual_title!r}"
    )


def test_renumber_headers_preserves_year_in_full_report():
    """Multi-heading report with year-range titles must not lose year digits."""
    content = (
        "# 行业概览\n"
        "## 2025-2026年市场规模分析\n"
        "正文\n"
        "## 1.1 技术成熟度评估\n"
        "正文\n"
        "### 3.14159精度对比\n"
        "正文\n"
    )
    renumber = MarkdownOutlineRenumber()
    result = renumber.renumber_headers(content)

    assert "2025-2026年市场规模分析" in result
    assert "技术成熟度评估" in result
    assert "3.14159精度对比" in result


def test_renumber_headers_standard_numbering_still_works():
    """Standard section numbering must be renumbered correctly."""
    content = (
        "# 总报告\n"
        "## 1 第一章\n"
        "## 1.1 子章节一\n"
        "## 1.2 子章节二\n"
        "## 2 第二章\n"
        "## 2.1 子章节三\n"
    )
    renumber = MarkdownOutlineRenumber()
    result = renumber.renumber_headers(content)
    lines = result.split("\n")

    # H1 gets "1.", H2 gets "1.1", "1.2", "1.3", "1.4", "1.5"
    assert lines[0].startswith("# 1.")
    assert "第一章" in lines[1]
    assert "子章节一" in lines[2]
    assert "子章节二" in lines[3]
    assert "第二章" in lines[4]
    assert "子章节三" in lines[5]


def test_clean_markdown_headers_preserves_year_range_heading():
    """clean_markdown_headers must not strip year-range digits from headings."""
    content = "# 2025-2026年市场规模\n\n## 1.1 2025-2026年出货量对比\n正文\n"
    cleaned = Reporter.clean_markdown_headers(content)
    assert "2025-2026年市场规模" in cleaned
    assert "2025-2026年出货量对比" in cleaned
