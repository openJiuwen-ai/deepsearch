from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from jiuwen_deepsearch.algorithm.report.report import Reporter
from jiuwen_deepsearch.common.common_constants import CHINESE


@pytest.mark.asyncio
@patch("jiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", new_callable=AsyncMock)
@patch("jiuwen_deepsearch.algorithm.report.report.llm_context", new_callable=MagicMock)
async def test_generate_sub_report(mock_llm_cls, mock_ainvoke_llm):
    # 设置 mock 返回值
    # mock ainvoke_llm_with_stats 返回值(定义 side_effect 函数，根据输入参数返回不同结果)
    async def mock_ainvoke_llm_with_stats(llm, messages, llm_type: str = "basic", agent_name="AI", schema=None,
                                          tools=None, need_stream_out=False):
        # 遍历 messages 里的 dict，检查 content 字段
        if any("classification" in msg.get("content", "") for msg in messages):
            return {"content": '{\"chapter\": \"企业经营与行业分析\", \"core_content_url_list\": [\"fake_url\"]}'}
        elif any("subsection outline" in msg.get("content", "") for msg in messages):
            return {"content": "3 企业经营与行业分析\n3.1 经营风险评价\3.2 杠杆风险评估"}
        elif any("write the chapter" in msg.get("content", "") for msg in messages):
            return {"content": "fake subsection report content"}
        else:
            return {"content": "default response"}

    mock_ainvoke_llm.side_effect = mock_ainvoke_llm_with_stats

    reporter = Reporter("basic")
    current_inputs = dict(
        has_template=False,
        language=CHINESE,
        report_template='',
        report_style='scholarly',
        section_idx=3,
        report_task='XX有限公司尽职调查报告',
        section_task='企业经营与行业分析',
        section_iscore=True,
        section_description='fake section_description',
        doc_infos=[{'doc_time': '2024 8月', 'core_content': 'fake core_content', 'url': 'fake_url',
                    'title': 'XX有限公司 - 企业详情'}],
        gathered_info=[{'url': 'fake_url', 'title': 'XX有限公司 - 企业详情', 'content': 'fake content'}],
        sub_evaluation_details='',
        max_generate_retry_num=3,
        max_sub_report_evaluate_num=0
    )
    success, report, sub_report_content, classified_content = await reporter.generate_sub_report(current_inputs)

    assert success is True
