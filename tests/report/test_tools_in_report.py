from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report import Reporter, _deduplicate_and_renumber_ref, \
    _replace_citations_and_classified_index, _get_classified_infos
from openjiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH


@pytest.mark.parametrize("input_str, expected", [
    ("第一章 Python入门", "Python入门"),  # 中文章节号
    ("第十2章 高级用法", "高级用法"),  # 中文+数字
    ("二、异常处理", "异常处理"),  # 中文序号
    ("3.4 数据结构", "数据结构"),  # 阿拉伯数字+点
    ("12-5 算法分析", "算法分析"),  # 阿拉伯数字+连字符
    ("第九章", ""),  # 只有章节号，没有正文
    ("Chapter Intro", "Chapter Intro"),  # 无匹配前缀，保持原样
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
    "doc_infos, urls, expected_infos, expected_docs",
    [
        # doc_infos为空
        ([], ["http://a.com"], {}, []),

        # urls为空
        ([{"url": "http://a.com", "title": "A", "core_content": "contentA"}], [], {}, []),

        # 单个匹配
        (
                [{"url": "http://a.com", "title": "A", "core_content": "contentA"}],
                ["http://a.com"],
                {"references": ["[A](http://a.com)"], "core_content_list": ["contentA"]},
                [{"url": "http://a.com", "title": "A", "core_content": "contentA"}],
        ),

        # urls里有两个地址，doc_infos里都有
        (
                [
                    {"url": "http://a.com", "title": "A", "core_content": "contentA"},
                    {"url": "http://b.com", "title": "B", "core_content": "contentB"},
                    {"url": "http://c.com", "title": "C", "core_content": "contentC"},
                ],
                ["http://a.com", "http://b.com"],
                {
                    "references": [
                        "[A](http://a.com)",
                        "[B](http://b.com)"
                    ],
                    "core_content_list": [
                        "contentA",
                        "contentB"
                    ]
                },
                [
                    {"url": "http://a.com", "title": "A", "core_content": "contentA"},
                    {"url": "http://b.com", "title": "B", "core_content": "contentB"},
                ],
        ),
    ],
)
def test_get_classified_infos(doc_infos, urls, expected_infos, expected_docs):
    classified_infos, classified_doc_infos = _get_classified_infos(doc_infos, urls)

    assert classified_infos == expected_infos
    assert classified_doc_infos == expected_docs
