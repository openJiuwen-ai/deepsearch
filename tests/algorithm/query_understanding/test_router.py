import pytest
import json
from unittest.mock import Mock, AsyncMock, patch
from jiuwen_deepsearch.algorithm.query_understanding.router import classify_query

# 定义测试数据
test_data = {
    'query': '中国汽车产业结构',
    'language': 'zh-CN'
}

# 定义模拟的响应
mock_response = {
    'content': '分类成功',
    'tool_calls': [
        {
            'function': 'send_to_planner',
            'args': {
                'query_title': '中国汽车产业结构',
                'language': 'zh-CN'
            }
        }
    ]
}

# 定义模拟的错误响应
mock_error_response = {
    'content': '分类失败',
    'tool_calls': []
}

# 测试用例
class TestRouter:

    @pytest.fixture
    def mock_llm(self):
        return Mock()

    @pytest.fixture
    def setup_router(self, mock_llm):
        return mock_llm

    @pytest.mark.asyncio
    async def test_classify_query_success(self, setup_router):
        """测试成功分类查询"""
        expected_result = {
            "go_deepsearch": True,
            "lang": "zh-CN",
            "llm_result": "",
            "error_msg": ""
        }

        with patch(
                'jiuwen_deepsearch.algorithm.query_understanding.router.llm_context',
                return_value=setup_router
        ), patch(
                'jiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                return_value=mock_response
        ):
            result = await classify_query(test_data)

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_classify_query_failure(self, setup_router):
        """测试分类查询失败"""
        expected_result = {
            "go_deepsearch": False,
            "lang": "zh-CN",
            "llm_result": "",
            "error_msg": "[211704]Error when EntryNode classify the query: TestMessage"
        }

        with patch(
                'jiuwen_deepsearch.algorithm.query_understanding.router.llm_context',
                return_value=setup_router
        ), patch(
                'jiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("TestMessage")
        ):
            result = await classify_query(test_data)

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_classify_query_no_tool_calls(self, setup_router):
        """测试分类查询没有工具调用"""
        expected_result = {
            "go_deepsearch": False,
            "lang": "zh-CN",
            "llm_result": "分类失败",
            "error_msg": ""
        }

        with patch(
                'jiuwen_deepsearch.algorithm.query_understanding.router.llm_context',
                return_value=setup_router
        ), patch(
                'jiuwen_deepsearch.utils.common_utils.llm_utils.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                return_value=mock_error_response
        ):
            result = await classify_query(test_data)

        assert result == expected_result