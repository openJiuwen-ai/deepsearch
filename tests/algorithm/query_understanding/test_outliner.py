from unittest.mock import Mock, AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.outliner import Outliner, create_outline_tool
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline

# 定义测试数据
test_data = {
    'max_outline_retry_num': 2,
    'messages': [{'content': '中国汽车产业结构', 'name': '', 'role': 'user'}]
}

outline_response = Outline(
    language="zh-CN",
    title="中国汽车产业结构",
    thought="中国汽车产业结构分析",
    sections=[
        {
            'title': '1. 中国汽车产业概述',
            'description': '中国汽车产业概述',
            'is_core_section': False
        }
    ]
)

tool_name = create_outline_tool(1).card.name
tool_call_id = '123'
functioncall_response = {
    'content': '',
    'name': None,
    'raw_content': None,
    'reason_content': None,
    'role': 'assistant',
    'tool_calls': [
        {
            'args': {
                'language': 'zh-CN',
                'sections': [
                    {
                        'description': '中国汽车产业概述',
                        'title': '1. 中国汽车产业概述',
                        'is_core_section': False
                    },
                ],
                'thought': '中国汽车产业结构分析',
                'title': '中国汽车产业结构'
            },
            'id': tool_call_id,
            'name': tool_name,
            'type': 'tool_call'
        }
    ],
    'usage_metadata': None
}


# 测试用例
class TestOutliner:

    @pytest.fixture
    def mock_llm(self):
        return Mock()

    @pytest.fixture
    def setup_outliner(self, mock_llm):
        with patch('openjiuwen_deepsearch.algorithm.query_understanding.outliner.llm_context', return_value=mock_llm):
            outliner = Outliner("test", "outliner")
        return outliner

    @pytest.mark.asyncio
    async def test_generate_outline_success(self, setup_outliner, mock_llm):
        """测试成功生成大纲"""
        mock_llm_response = {
            'current_outline': outline_response,
            'success_flag': True,
            'error_msg': ''
        }

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.outliner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                return_value=functioncall_response
        ):
            result = await setup_outliner.generate_outline(test_data)

        assert result == mock_llm_response

    @pytest.mark.asyncio
    async def test_generate_outline_failure(self, setup_outliner, mock_llm):
        """测试生成大纲失败"""
        mock_llm_response = {
            'current_outline': {},
            'success_flag': False,
            'error_msg': '[211800]Error when Outliner generate an outline: TestMessage'
        }

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.outliner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("TestMessage")
        ):
            result = await setup_outliner.generate_outline(test_data)

        assert result == mock_llm_response
   