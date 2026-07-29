from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from openjiuwen_deepsearch.algorithm.report.config import ReportFormat
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ChapterSidecar,
    Outline,
    ResearchIntent,
    Section,
    Report,
    SubReport,
    SubReportContent,
    build_research_intent_prompt_context,
)
from openjiuwen_deepsearch.common.common_constants import CHINESE


@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
def test_build_reporter_compact_context_selects_fields_by_target(mock_llm_cls):
    reporter = Reporter("basic")
    reporter.gen_report_context = {
        "report_task": "总报告任务",
        "current_report": Report(
            sub_reports=[
                SubReport(
                    section_id="2",
                    section_task="章节标题",
                    content=SubReportContent(
                        sub_report_content_summary="兼容摘要",
                        sub_report_chapter_sidecar=ChapterSidecar(
                            chapter_summary="结构化摘要",
                            key_findings=["发现1", "发现2", "发现3", "发现4"],
                            risk_points=["风险1"],
                        ),
                    ),
                )
            ]
        ),
    }

    abstract_context = reporter._build_reporter_compact_context("abstract")
    conclusion_context = reporter._build_reporter_compact_context("conclusion")

    assert "总报告任务" in abstract_context
    assert "2 章节标题" in abstract_context
    assert "结构化摘要" in abstract_context
    assert "发现1" in abstract_context
    assert "发现3" in abstract_context
    assert "发现4" not in abstract_context
    assert "风险1" not in abstract_context

    assert "发现4" in conclusion_context
    assert "风险1" in conclusion_context


@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
def test_build_reporter_compact_context_uses_summary_fallback_and_skips_empty(mock_llm_cls):
    reporter = Reporter("basic")
    reporter.gen_report_context = {
        "current_report": Report(
            sub_reports=[
                SubReport(
                    section_id=1,
                    section_task="可用章节",
                    content=SubReportContent(sub_report_content_summary="全文兜底摘要"),
                ),
                SubReport(section_id=2, section_task="空章节"),
            ]
        )
    }

    compact_context = reporter._build_reporter_compact_context("abstract")

    assert "1 可用章节" in compact_context
    assert "Summary (fallback)" in compact_context
    assert "全文兜底摘要" in compact_context
    assert "空章节" not in compact_context

    reporter.gen_report_context = {"current_report": Report(sub_reports=[SubReport()])}
    assert reporter._build_reporter_compact_context("abstract") == ""


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_report_falls_back_to_full_content_when_compact_context_is_empty(
    mock_llm_cls,
):
    reporter = Reporter("basic")
    reporter.gen_report_context = {
        "current_outline": Outline(thought="", title="报告标题"),
        "current_report": Report(sub_reports=[SubReport()]),
        "language": CHINESE,
    }
    sub_report_result = {
        "sub_reports_content": "完整拼接正文",
        "sub_references": "",
        "refreshed_all_classified_contents": [],
    }

    with patch.object(reporter, "_set_context_variables", return_value=True):
        with patch.object(
            reporter,
            "_process_sub_report",
            new_callable=AsyncMock,
            return_value=sub_report_result,
        ):
            with patch.object(
                reporter,
                "generate_abstract",
                new_callable=AsyncMock,
                return_value="摘要",
            ) as mock_generate_abstract:
                with patch.object(
                    reporter,
                    "generate_conclusion",
                    new_callable=AsyncMock,
                    return_value="结论",
                ) as mock_generate_conclusion:
                    success, _ = await reporter.generate_report({"language": CHINESE})

    assert success is True
    mock_generate_abstract.assert_awaited_once_with("完整拼接正文")
    mock_generate_conclusion.assert_awaited_once_with("完整拼接正文")


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
@patch.object(Reporter, "_generate_with_llm", new_callable=AsyncMock)
async def test_generate_abstract(mock_generate, mock_llm_cls):
    # 设置 mock 返回值
    mock_generate.return_value = "mocked abstract"

    reporter = Reporter("basic")
    result = await reporter.generate_abstract("test content")

    # 验证返回值
    assert result == "mocked abstract"

    # 验证 _generate_with_llm 调用参数
    mock_generate.assert_awaited_once()
    args, kwargs = mock_generate.call_args
    assert args[0] == "abstract"
    assert "report_abstract_markdown" in args[1]
    assert args[2] == "test content"


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
@patch.object(Reporter, "_generate_with_llm", new_callable=AsyncMock)
async def test_generate_conclusion(mock_generate, mock_llm_cls):
    # 设置 mock 返回值
    mock_generate.return_value = "mocked conclusion"

    reporter = Reporter("basic")
    result = await reporter.generate_conclusion("test content")

    # 验证返回值
    assert result == "mocked conclusion"

    # 验证 _generate_with_llm 调用参数
    mock_generate.assert_awaited_once()
    args, kwargs = mock_generate.call_args
    assert args[0] == "conclusion"
    assert "report_implications_and_recommendations_markdown" in args[1]
    assert args[2] == "test content"


@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
def test_set_context_variables_exposes_research_intent_prompt_context(mock_llm_cls):
    reporter = Reporter("basic")
    ok = reporter._set_context_variables(
        {
            "language": CHINESE,
            "report_type_policy": {"report_type": "professional"},
            "research_intent": ResearchIntent(
                task_type="comparison",
                required_dimensions=["growth", "dividend"],
                comparison_targets=["AIA", "Ping An"],
            ).model_dump(),
        }
    )

    assert ok is True
    expected = build_research_intent_prompt_context(
        ResearchIntent(
            task_type="comparison",
            required_dimensions=["growth", "dividend"],
            comparison_targets=["AIA", "Ping An"],
        )
    )
    for key, value in expected.items():
        assert reporter.gen_report_context[key] == value


def test_validate_sub_report_headings_match_outline_accepts_exact_outline():
    outline = """3 落地场景分化：消费级与企业级应用在中美欧的渗透深度对比
3.1 消费级场景渗透率量化对比：中美欧内容创作与教育娱乐差异
3.2 企业级应用成熟度评估：政企集采、混合办公与合规驱动分化
3.3 垂直行业落地深度分析：教育医疗增量与金融制造定制化路径
3.4 场景差异化归因分析：用户付费意愿与制度文化根源研判"""
    content = """# 3 落地场景分化：消费级与企业级应用在中美欧的渗透深度对比

## 3.1 消费级场景渗透率量化对比：中美欧内容创作与教育娱乐差异
正文一

## 3.2 企业级应用成熟度评估：政企集采、混合办公与合规驱动分化
正文二

## 3.3 垂直行业落地深度分析：教育医疗增量与金融制造定制化路径
正文三

## 3.4 场景差异化归因分析：用户付费意愿与制度文化根源研判
正文四
"""

    ok, reason = Reporter.validate_sub_report_headings_match_outline(content, outline)

    assert ok is True
    assert reason == ""


def test_validate_sub_report_headings_match_outline_rejects_duplicated_h2_block():
    outline = """3 落地场景分化：消费级与企业级应用在中美欧的渗透深度对比
3.1 消费级场景渗透率量化对比：中美欧内容创作与教育娱乐差异
3.2 企业级应用成熟度评估：政企集采、混合办公与合规驱动分化
3.3 垂直行业落地深度分析：教育医疗增量与金融制造定制化路径
3.4 场景差异化归因分析：用户付费意愿与制度文化根源研判"""
    content = """# 3 落地场景分化：消费级与企业级应用在中美欧的渗透深度对比

## 3.1 消费级场景渗透率量化对比：中美欧内容创作与教育娱乐差异
正文一

## 3.2 企业级应用成熟度评估：政企集采、混合办公与合规驱动分化
正文二

## 3.3 垂直行业落地深度分析：教育医疗增量与金融制造定制化路径
正文三

## 3.4 场景差异化归因分析：用户付费意愿与制度文化根源研判
正文四

## 3.1 消费级场景渗透率量化对比：中美欧内容创作与教育娱乐差异
重复正文一

## 3.2 企业级应用成熟度评估：政企集采、混合办公与合规驱动分化
重复正文二
"""

    ok, reason = Reporter.validate_sub_report_headings_match_outline(content, outline)

    assert ok is False
    assert "duplicate" in reason.lower() or "expected" in reason.lower()


def test_clean_markdown_headers_preserves_year_prefixed_titles():
    content = """# 2025年中美欧AI PC市场规模与增长动能对比基准

## 2025年中美欧AI PC出货量与渗透率量化对标
正文
"""

    cleaned = Reporter.clean_markdown_headers(content)

    assert "# 2025年中美欧AI PC市场规模与增长动能对比基准" in cleaned
    assert "## 2025年中美欧AI PC出货量与渗透率量化对标" in cleaned


def test_clean_markdown_headers_preserves_space_separated_year_and_age_titles():
    content = """# 1. 2025 年中美欧AI PC市场规模与增长动能对比基准

## 1.1 53 岁80kg中年人代谢衰退评估与每日热量需求测算
正文
"""

    cleaned = Reporter.clean_markdown_headers(content)
    cleaned_twice = Reporter.clean_markdown_headers(cleaned)

    assert "# 2025 年中美欧AI PC市场规模与增长动能对比基准" in cleaned
    assert "## 53 岁80kg中年人代谢衰退评估与每日热量需求测算" in cleaned
    assert cleaned_twice == cleaned


def test_clean_markdown_headers_preserves_space_separated_age_titles_for_h4():
    content = """#### 53 岁80kg中年人控卡原则
正文
"""

    cleaned = Reporter.clean_markdown_headers(content)

    assert "- **53 岁80kg中年人控卡原则**" in cleaned


def test_strip_leading_number_preserves_year_prefixed_titles():
    assert (
        Reporter.strip_leading_number("2025年中美欧AI PC市场规模与增长动能对比基准")
        == "2025年中美欧AI PC市场规模与增长动能对比基准"
    )


def test_strip_leading_number_preserves_space_separated_year_and_age_titles():
    assert (
        Reporter.strip_leading_number("2025 年中美欧AI PC市场规模与增长动能对比基准")
        == "2025 年中美欧AI PC市场规模与增长动能对比基准"
    )
    assert (
        Reporter.strip_leading_number("53 岁80kg中年人代谢衰退评估与每日热量需求测算")
        == "53 岁80kg中年人代谢衰退评估与每日热量需求测算"
    )


def test_clean_markdown_headers_still_strips_real_section_numbers():
    content = """# 1. 中美欧AI PC市场规模与增长动能对比基准

## 1.1 中美欧AI PC出货量与渗透率量化对标
正文
"""

    cleaned = Reporter.clean_markdown_headers(content)

    assert "# 中美欧AI PC市场规模与增长动能对比基准" in cleaned
    assert "## 中美欧AI PC出货量与渗透率量化对标" in cleaned


@pytest.mark.asyncio
@patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", new_callable=AsyncMock)
@patch("openjiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_report(mock_llm_cls, mock_ainvoke_llm):
    # 设置 mock 返回值
    # mock ainvoke_llm_with_stats 返回值(定义 side_effect 函数，根据输入参数返回不同结果)
    async def mock_ainvoke_llm_with_stats(llm, messages, llm_type: str = "basic", agent_name="AI", schema=None,
                                          tools=None, need_stream_out=False):
        # 遍历 messages 里的 dict，检查 content 字段
        if any("Abstract" in msg.get("content", "") for msg in messages):
            return {"content": 'Fake Abstract'}
        elif any("Conclusion" in msg.get("content", "") for msg in messages):
            return {"content": "Fake Conclusion"}
        elif any("User Role Judgment" in msg.get("content", "") for msg in messages):
            return {"content": '{"user_role": "Fake Role"}'}
        else:
            return {"content": "default response"}

    mock_ainvoke_llm.side_effect = mock_ainvoke_llm_with_stats

    reporter = Reporter("basic")
    current_inputs = dict(
        thread_id='default_session_id',
        report_style='scholarly',
        report_format=ReportFormat.MARKDOWN,
        current_outline=Outline(
            language='zh',
            thought='根据提供的模板结构，需生成一份针对XX有限公司的尽职调查报告大纲。严格遵循模板的章节层级与逻辑顺序，XXX',
            title='XX有限公司尽职调查报告',
            sections=[
                Section(title='企业基本情况分析', description='- 基础信息: fake description', is_core_section=True)]
        ),
        all_classified_contents=[
            [{'doc_time': '2023 Jun', 'source_authority': '该篇文章的信息来源权威性和可信度得分：8.0',
              'task_relevance': '该篇文章的内容与当前任务的相关性得分：9.0',
              'information_richness': '该篇文章的信息丰富程度与可答性得分：8.5',
              'url': 'http://fake_html_1', 'title': '环保持续|产品科技 - XX有限公司',
              'original_content': 'fake original_content',
              'index': 1},
             {'doc_time': '2023 Jun', 'source_authority': '该篇文章的信息来源权威性和可信度得分：7.5',
              'task_relevance': '该篇文章的内容与当前任务的相关性得分：9.0',
              'information_richness': '该篇文章的信息丰富程度与可答性得分：8.0',
              'url': 'http://fake_html_2',
              'title': 'XX有限公司 - 企业详情',
              'original_content': 'fake original_content',
              'index': 2}]],
        current_report=Report(
            id="test_report_id",
            report_task='XX有限公司尽职调查报告',
            sub_reports=[
                SubReport(
                    id="test_sub_report_id",
                    section_id=1,
                    section_task='企业基本情况分析',
                    content=SubReportContent(
                        sub_report_content_text="""# 1 企业基本情况分析

                        ## 1.1 基础信息
                        XX公司成立于2000年7月3日[citation:1][citation:2]。

                        ## 参考文章
                        [1] [环保持续|产品科技 - XX有限公司](http://fake_html_1)
                        [2] [XX有限公司 - 企业详情](http://fake_html_1)
                        """,
                        sub_report_content_summary='企业基本情况'
                    )
                )
            ]
        ),
        language=CHINESE,
        report_task='',
        max_evaluate_executed_num=0
    )
    success, report_str = await reporter.generate_report(current_inputs)

    assert success is True
