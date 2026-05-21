import json
from unittest.mock import Mock, AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.outliner import (
    Outliner,
    check_tool_call,
    create_outline_tool,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline, Section


async def _make_async_iter(chunks: list):
    """Helper function to create an async iterator from a list of chunks."""
    for chunk in chunks:
        yield chunk

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
        Section(
            id="1",
            title="1. 中国汽车产业概述",
            description="中国汽车产业概述",
            is_core_section=False,
        )
    ],
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
    def test_check_tool_call_sections_must_be_list(self):
        """check_tool_call 验证 sections """
        tool = create_outline_tool(1)
        tool_calls = [
            {
                'args': {
                    'language': 'zh-CN',
                    'sections': 'invalid-sections',
                    'thought': 'test thought',
                    'title': 'test title'
                },
                'name': tool.card.name,
            }
        ]

        with pytest.raises(CustomValueException, match='Sections is not a list'):
            check_tool_call(tool, tool_calls)

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

    @pytest.mark.asyncio
    async def test_generate_outline_with_runtime_api_tool(self, setup_outliner, mock_llm):
        """测试 outliner 场景会合并并执行运行时 API 工具"""
        custom_input = {
            **test_data,
            "api_tools_config": {
                "query_understanding_tools": [
                    {
                        "tool_id": "tool-1",
                        "name": "runtime_outline_tool",
                        "description": "Runtime outline tool",
                        "path": "https://example.com/outline",
                        "http_method": "post",
                        "request_params": [
                            {
                                "name": "title",
                                "description": "outline title",
                                "send_method": "body",
                                "required": True,
                            },
                            {
                                "name": "language",
                                "description": "language",
                                "send_method": "body",
                                "required": False,
                            }
                        ],
                    }
                ]
            }
        }
        custom_response = {
            **functioncall_response,
            'tool_calls': [
                {
                    'args': {
                        'language': 'zh-CN',
                        'title': '运行时大纲'
                    },
                    'id': tool_call_id,
                    'name': 'runtime_outline_tool',
                    'type': 'tool_call'
                }
            ],
        }
        mock_http_response = Mock()
        mock_http_response.headers = {}
        mock_http_response.encoding = "utf-8"
        mock_http_response.raise_for_status = Mock()
        json_data = json.dumps({
            "code": 0,
            "message": "ok",
            "data": {
                "language": "zh-CN",
                "title": "运行时大纲",
                "thought": "Generated by runtime api",
                "sections": [],
            }
        }).encode("utf-8")
        mock_http_response.aiter_bytes = Mock(return_value=_make_async_iter([json_data]))
        
        mock_stream_cm = Mock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_http_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_client = Mock()
        mock_client.stream = Mock(return_value=mock_stream_cm)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.outliner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                return_value=custom_response
        ) as mock_invoke, patch(
                'openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.validate_runtime_request_url',
                return_value=None
        ), patch(
                'openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.httpx.AsyncClient',
                return_value=mock_client
        ):
            result = await setup_outliner.generate_outline(custom_input)

        tools = mock_invoke.await_args.kwargs["tools"]
        tool_names = [
            getattr(tool, "name", tool["name"] if isinstance(tool, dict) else None)
            for tool in tools
        ]
        assert result["success_flag"] is True
        assert result["current_outline"].title == "运行时大纲"
        assert tool_name in tool_names
        assert "runtime_outline_tool" in tool_names

