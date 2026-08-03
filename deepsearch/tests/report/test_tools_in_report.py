from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report import (
    Reporter,
    VisualizationInsertRenderContext,
    _deduplicate_and_renumber_ref,
    _replace_citations_and_classified_index,
    _get_classified_infos,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline, Section, Plan, Step, StepType
from openjiuwen_deepsearch.algorithm.report.report_utils import (
    MarkdownOutlineRenumber,
)
from openjiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH


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


@pytest.mark.parametrize("content, refs, lang, expected", [
    # 中文引用
    ("这是正文", ["参考A", "参考B"], CHINESE,
     "这是正文\n## 参考文章\n[1] 参考A\n[2] 参考B"),

    # 英文引用
    ("This is content", ["Ref A", "Ref B"], ENGLISH,
     "This is content\n## References\n[1] Ref A\n[2] Ref B"),

    # 没有引用
    ("正文内容", [], CHINESE, "正文内容"),

    # 没有正文但有引用（返回空字符串）
    ("", ["Ref A"], ENGLISH, ""),

    # 未知语言 → 默认走英文逻辑
    ("Contenu", ["Réf A"], "fr",
     "Contenu\n## References\n[1] Réf A"),
])
def test_add_references(content, refs, lang, expected):
    result = Reporter.add_references(content, refs, lang)
    assert result == expected


def test_apply_visualization_insertions_escapes_image_title_html():
    context = VisualizationInsertRenderContext(
        report_lines=["第一段\n", "第二段\n"],
        insertions=[{"after_row": 1, "index": 1}],
        mermaid_map={1: "graph TD\nA-->B"},
        title_meta_map={
            1: {
                "image_title": '<img src=x onerror="alert(1)">',
                "citation_index": 7,
            }
        },
        newline="\n",
        language=CHINESE,
    )

    result = Reporter._apply_visualization_insertions(context)

    assert '<img src=x onerror="alert(1)">' not in result
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;[citation:7]" in result


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", new_callable=AsyncMock)
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_with_llm_returns_content(mock_llm_cls, mock_ainvoke_llm):
    # 准备 mock
    # mock ainvoke_llm_with_stats 返回值
    mock_ainvoke_llm.return_value = {"content": "mocked response"}
    # mock LLMWrapper 实例
    mock_llm_instance = MagicMock()
    mock_llm_cls.return_value = mock_llm_instance

    # 初始化被测试对象
    reporter = Reporter("basic")
    reporter.gen_report_context = {}

    # 调用被测函数
    result = await reporter._generate_with_llm(
        task_type="abstract",
        prompt="report_abstract_markdown",
        content="test content"
    )

    # 断言返回值正确
    assert result == "mocked response"

    # 断言 ainvoke_llm_with_stats 被正确调用
    mock_ainvoke_llm.assert_awaited_once()
    args, kwargs = mock_ainvoke_llm.call_args
    assert kwargs["agent_name"] is not None
    assert any(msg["role"] == "user" for msg in kwargs["messages"])


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", new_callable=AsyncMock)
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_with_llm_rejects_unknown_task_type(mock_llm_cls, mock_ainvoke_llm):
    mock_llm_instance = MagicMock()
    mock_llm_cls.return_value = mock_llm_instance

    reporter = Reporter("basic")
    reporter.gen_report_context = {}

    with pytest.raises(KeyError, match="Unsupported report task type"):
        await reporter._generate_with_llm(
            task_type="summary",
            prompt="report_abstract_markdown",
            content="test content"
        )

    mock_ainvoke_llm.assert_not_awaited()


@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
def test_set_context_variables_none(mock_llm_cls):
    reporter = Reporter("basic")
    result = reporter._set_context_variables(None)
    assert result is False
    assert reporter.gen_report_context is None


@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
def test_set_context_variables_dict(mock_llm_cls):
    reporter = Reporter("basic")
    ctx = {"foo": "bar"}
    result = reporter._set_context_variables(ctx)
    assert result is True
    assert reporter.gen_report_context == ctx


def test_deduplicate_and_renumber_with_ref_empty_input():
    text = ""
    result, mapping = _deduplicate_and_renumber_ref(text)
    assert result == ""
    assert mapping == {}


def test_deduplicate_and_renumber_with_ref_single_reference():
    text = "[1] First reference"
    result, mapping = _deduplicate_and_renumber_ref(text)
    assert result == "[1] First reference"
    assert mapping == {"1-1": 1}


def test_deduplicate_and_renumber_with_ref_duplicate_references_same_paragraph():
    text = "[1] First reference\n[2] First reference"
    result, mapping = _deduplicate_and_renumber_ref(text)
    # 去重后只保留一个
    assert result == "[1] First reference"
    # 两个 key 都映射到同一个编号
    assert mapping == {"1-1": 1, "1-2": 1}


def test_deduplicate_and_renumber_with_multiple_paragraphs_and_sections():
    text = "[1] First reference\n\n[1] Second reference\n[2] First reference"
    result, mapping = _deduplicate_and_renumber_ref(text)
    # 应该有两个不同的引用
    assert "[1] First reference" in result
    assert "[2] Second reference" in result
    # 映射应区分段落
    assert mapping["1-1"] == 1  # 第一段第一条
    assert mapping["3-1"] == 2  # 第三段第一条（任何一个\n都算作开始了一个新的段落）
    assert mapping["3-2"] == 1  # 第三段第二条重复了第一段的内容


def test_deduplicate_and_renumber_with_ignore_lines_without_reference():
    text = "This is not a ref\n[1] Valid reference"
    result, mapping = _deduplicate_and_renumber_ref(text)
    assert result == "[1] Valid reference"
    assert mapping == {"1-1": 1}


@pytest.mark.parametrize("paragraphs, classified_contents, ref_map, expected", [
    # 测试用例1：正常情况
    (
            ["This is a paragraph [citation:1].", "Another paragraph [citation:2]."],
            [
                [{"index": 1, "content": "First citation"}],
                [{"index": 2, "content": "Second citation"}]
            ],
            {"1-1": 10, "2-2": 20},
            (["This is a paragraph [citation:10].", "Another paragraph [citation:20]."], [
                [{"index": 10, "content": "First citation"}],
                [{"index": 20, "content": "Second citation"}]
            ])
    ),

    # 测试用例2：没有引用映射
    (
            ["This is a paragraph [citation:1].", "Another paragraph [citation:2]."],
            [
                [{"index": 1, "content": "First citation"}],
                [{"index": 2, "content": "Second citation"}]
            ],
            {},
            (["This is a paragraph [citation:1].", "Another paragraph [citation:2]."], [
                [{"index": 1, "content": "First citation"}],
                [{"index": 2, "content": "Second citation"}]
            ])
    ),

    # 测试用例3：没有分类内容
    (
            ["This is a paragraph [citation:1].", "Another paragraph [citation:2]."],
            [],
            {"1-1": 10, "2-2": 20},
            (["This is a paragraph [citation:1].", "Another paragraph [citation:2]."], [])
    ),

    # 测试用例4：空段落及分类内容
    (
            [],
            [],
            {"1-1": 10, "2-2": 20},
            ([], [])
    )
])
def test_replace_citations_and_classified_index(paragraphs, classified_contents, ref_map, expected):
    result = _replace_citations_and_classified_index(paragraphs, classified_contents, ref_map)
    assert result == expected


# 测试 _get_classified_infos 函数
@pytest.mark.parametrize(
    "selected_docs, marginal_values, expected_infos, expected_docs",
    [
        # selected_docs is empty
        ([], [], {}, []),

        # single match
        (
                [{"doc_url": "http://a.com", "doc_title": "A", "passage_text": "passageA"}],
                [0.5],
                {"references": ["[A](http://a.com)"], "core_content_list": ["Document 1 key passages:\n- passageA"]},
                [{"doc_url": "http://a.com", "doc_title": "A", "passage_text": "passageA"}],
        ),

        # two selected variants (from different URLs)
        (
                [
                    {"doc_url": "http://a.com", "doc_title": "A", "passage_text": "passageA"},
                    {"doc_url": "http://b.com", "doc_title": "B", "passage_text": "passageB"},
                ],
                [0.5, 0.4],
                {
                    "references": [
                        "[A](http://a.com)",
                        "[B](http://b.com)"
                    ],
                    "core_content_list": [
                        "Document 1 key passages:\n- passageA",
                        "Document 2 key passages:\n- passageB",
                    ]
                },
                [
                    {"doc_url": "http://a.com", "doc_title": "A", "passage_text": "passageA"},
                    {"doc_url": "http://b.com", "doc_title": "B", "passage_text": "passageB"},
                ],
        ),
        (
                [
                    {
                        "doc_url": "https://example.test/",
                        "doc_title": "x](javascript:alert(1)) [safe",
                        "passage_text": "",
                    }
                ],
                [0.5],
                {
                    "references": [
                        "[x\\]\\(javascript:alert\\(1\\)\\) \\[safe](https://example.test/)"
                    ],
                    "core_content_list": ["Document 1 key passages:\n[]"],
                },
                [
                    {
                        "doc_url": "https://example.test/",
                        "doc_title": "x](javascript:alert(1)) [safe",
                        "passage_text": "",
                    }
                ],
        ),
        (
                [{"doc_url": "javascript:alert(2)", "doc_title": "benign", "passage_text": "passageB"}],
                [0.5],
                {
                    "references": ["benign (javascript:alert\\(2\\))"],
                    "core_content_list": ["Document 1 key passages:\n- passageB"],
                },
                [{"doc_url": "javascript:alert(2)", "doc_title": "benign", "passage_text": "passageB"}],
        ),
    ],
)
def test_get_classified_infos(selected_docs, marginal_values, expected_infos, expected_docs):
    classified_infos, classified_doc_infos = _get_classified_infos(selected_docs, marginal_values)

    assert classified_infos == expected_infos
    assert classified_doc_infos == expected_docs


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


# ---------------------------------------------------------------------------
# export_outline_without_plans: strip plans from outline for LLM input
# ---------------------------------------------------------------------------

def _make_outline_dict():
    """Build a minimal outline dict with thought, plans, and step_result."""
    return {
        "id": "test-outline",
        "language": "zh-CN",
        "thought": "outline reasoning process",
        "title": "Test Report",
        "sections": [
            {
                "id": "1",
                "title": "Chapter One",
                "description": "desc one",
                "format_requirements": [],
                "is_core_section": True,
                "parent_ids": [],
                "relationships": [],
                "plans": [
                    {
                        "id": "1-1",
                        "language": "zh-CN",
                        "title": "Plan 1",
                        "thought": "plan thought",
                        "is_research_completed": True,
                        "steps": [
                            {
                                "id": "1-1-1",
                                "type": "info_collecting",
                                "title": "Step 1",
                                "description": "collect data",
                                "step_result": "X" * 5_000,
                                "evaluation": "good",
                            },
                        ],
                    },
                ],
                "section_focus": "market_size",
                "focus_dimensions": ["size", "growth"],
            },
            {
                "id": "2",
                "title": "Chapter Two",
                "description": "desc two",
                "plans": [],
                "parent_ids": ["1"],
                "relationships": ["depends on"],
            },
        ],
    }


def test_export_outline_without_plans_strips_plans_from_dict():
    """export_outline_without_plans must remove 'plans' (with step_result) from dict input."""
    outline = _make_outline_dict()
    result = Reporter.export_outline_without_plans(outline)

    assert isinstance(result, dict)
    for sec in result.get("sections", []):
        assert "plans" not in sec or sec["plans"] == []
        assert "step_result" not in str(sec)

    # Title and section metadata are preserved
    assert result["title"] == "Test Report"
    assert result["sections"][0]["title"] == "Chapter One"
    assert result["sections"][0]["description"] == "desc one"
    assert result["sections"][1]["parent_ids"] == ["1"]


def test_export_outline_without_plans_with_empty_input():
    """None / unsupported types should be handled gracefully."""
    assert Reporter.export_outline_without_plans(None) is None
    assert Reporter.export_outline_without_plans("str") == "str"
    assert Reporter.export_outline_without_plans({}) == {}


def test_export_outline_without_plans_preserves_section_metadata():
    """Section metadata (focus, dimensions, parent_ids) must survive stripping."""
    outline = _make_outline_dict()
    result = Reporter.export_outline_without_plans(outline)
    sec0 = result["sections"][0]
    assert sec0["section_focus"] == "market_size"
    assert sec0["focus_dimensions"] == ["size", "growth"]
    assert sec0["is_core_section"] is True


def test_export_outline_without_plans_preserves_thought():
    """thought field should be preserved (not stripped by export_outline_without_plans)."""
    outline = _make_outline_dict()
    result = Reporter.export_outline_without_plans(outline)
    assert result.get("thought") == "outline reasoning process"


def test_export_outline_without_plans_with_outline_object():
    """export_outline_without_plans must handle Outline objects, returning Outline."""
    step = Step(
        type=StepType.INFO_COLLECTING,
        title="Step 1",
        description="desc",
        step_result="R" * 5_000,
        evaluation="eval",
    )
    plan = Plan(
        id="1-1",
        title="Plan 1",
        thought="plan thought",
        is_research_completed=True,
        steps=[step],
    )
    section = Section(
        id="1",
        title="Chapter One",
        description="desc one",
        plans=[plan],
        section_focus="market_size",
        focus_dimensions=["size"],
    )
    outline = Outline(
        thought="T" * 10_000,
        title="Test Report",
        sections=[section],
    )

    result = Reporter.export_outline_without_plans(outline)
    assert isinstance(result, Outline)
    assert result.title == "Test Report"
    # plans must be stripped
    for sec in result.sections:
        assert sec.plans == []
    # step_result must not leak
    assert "R" * 100 not in str(result)
