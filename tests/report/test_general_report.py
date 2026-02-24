from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from openjiuwen_deepsearch.algorithm.report.config import ReportFormat
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Outline,
    Section,
    Report,
    SubReport,
    SubReportContent,
)
from openjiuwen_deepsearch.common.common_constants import CHINESE


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
