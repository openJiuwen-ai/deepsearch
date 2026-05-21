import json
from unittest.mock import Mock, AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.planner import Planner, PlannerResult, create_plan_tool
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Plan, StepType


async def _make_async_iter(chunks: list):
    """Helper function to create an async iterator from a list of chunks."""
    for chunk in chunks:
        yield chunk

# 定义测试数据
test_data = {
    'input': 'test input',
    'section_idx': 1,
    'plan_executed_num': 0,
    'max_plan_executed_num': 3,
}

# 定义模拟的 Plan 响应
plan_response = Plan(
    language="zh-CN",
    title="Test Plan",
    thought="This is a test thought",
    is_research_completed=False,
    steps=[
        {
            'title': 'Step 1',
            'description': 'Description 1',
            'type': StepType.INFO_COLLECTING.value,
            'step_result': None
        }
    ]
)
# 定义模拟的 functioncall 响应
tool_name = create_plan_tool(test_data, 'planner').card.name
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
                'is_research_completed': False,
                'language': 'zh-CN',
                'steps': [
                    {
                        'description': 'Description 1',
                        'title': 'Step 1',
                        'type': 'info_collecting'
                    },
                ],
                'thought': 'This is a test thought',
                'title': 'Test Plan'
            },
            'id': tool_call_id,
            'name': tool_name,
            'type': 'tool_call'
        }
    ],
    'usage_metadata': None
}

# 定义模拟的 functioncall 执行结果
functioncall_result = {
    'name': tool_name,
    'role': 'tool',
    'content': plan_response.model_dump_json(),
    'tool_call_id': tool_call_id
}


# 测试类
class TestPlanner:
    @pytest.fixture
    def mock_llm(self):
        return Mock()

    @pytest.fixture
    def setup_planner(self, mock_llm):
        with patch('openjiuwen_deepsearch.algorithm.query_understanding.planner.llm_context', return_value=mock_llm):
            planner = Planner()
        return planner

    @pytest.mark.asyncio
    async def test_generate_plan_success(self, setup_planner, mock_llm):
        """测试成功生成计划"""
        mock_llm_response = PlannerResult(
            plan_success=True,
            plan=plan_response,
            response_messages=[functioncall_response, functioncall_result],
            error_msg='')

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                return_value=functioncall_response
        ):
            result = await setup_planner.generate_plan(test_data)

        assert result == mock_llm_response

    @pytest.mark.asyncio
    async def test_generate_plan_retry_failure(self, setup_planner, mock_llm):
        """测试重试失败的情况"""
        mock_llm_response = PlannerResult(
            plan_success=False,
            plan=None,
            response_messages=[],
            error_msg='section_idx: 1 | Round 1/3 | Error when Planner generating a plan. retry (1/1).error: Test Exception'
        )

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("Test Exception")
        ):
            result = await setup_planner.generate_plan(test_data)

        assert result == mock_llm_response

    @pytest.mark.asyncio
    async def test_generate_plan_exception(self, setup_planner, mock_llm):
        """测试生成计划时发生异常的情况"""
        mock_llm_response = PlannerResult(
            plan_success=False,
            plan=None,
            response_messages=[],
            error_msg='section_idx: 1 | Round 1/3 | Error when Planner generating a plan. retry (1/1).error: Test Exception'
        )

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("Test Exception")
        ):
            result = await setup_planner.generate_plan(test_data)

        assert result == mock_llm_response

    @pytest.mark.asyncio
    async def test_generate_plan_max_retries(self, setup_planner, mock_llm):
        """测试达到最大重试次数的情况"""
        mock_llm_response = PlannerResult(
            plan_success=False,
            plan=None,
            response_messages=[],
            error_msg='section_idx: 1 | Round 1/3 | Error when Planner generating a plan. retry (3/3).error: Test Exception'
        )

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("Test Exception")
        ):
            # 设置最大重试次数为3
            setup_planner.config.max_retry_num = 3
            result = await setup_planner.generate_plan(test_data)

        assert result == mock_llm_response

    @pytest.mark.asyncio
    async def test_generate_plan_with_runtime_api_tool(self, setup_planner, mock_llm):
        """测试 planner 场景会合并并执行运行时 API 工具"""
        custom_input = {
            **test_data,
            "api_tools_config": {
                "query_understanding_tools": [
                    {
                        "tool_id": "tool-1",
                        "name": "runtime_plan_tool",
                        "description": "Runtime planner tool",
                        "path": "https://example.com/plan",
                        "http_method": "post",
                        "request_params": [
                            {
                                "name": "title",
                                "description": "plan title",
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
            "tool_calls": [
                {
                    "args": {
                        "language": "zh-CN",
                        "title": "Runtime Plan"
                    },
                    "id": tool_call_id,
                    "name": "runtime_plan_tool",
                    "type": "tool_call"
                }
            ]
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
                "title": "Runtime Plan",
                "thought": "Generated by runtime api",
                "is_research_completed": True,
                "steps": [],
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
                'openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                return_value=custom_response
        ) as mock_invoke, patch(
                'openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.validate_runtime_request_url',
                return_value=None
        ), patch(
                'openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.httpx.AsyncClient',
                return_value=mock_client
        ):
            result = await setup_planner.generate_plan(custom_input)

        tools = mock_invoke.await_args.kwargs["tools"]
        tool_names = [
            getattr(tool, "name", tool["name"] if isinstance(tool, dict) else None)
            for tool in tools
        ]
        assert result.plan_success is True
        assert result.plan.title == "Runtime Plan"
        assert result.response_messages[1]["name"] == "runtime_plan_tool"
        assert tool_name in tool_names
        assert "runtime_plan_tool" in tool_names

    @pytest.mark.asyncio
    async def test_generate_plan_rejects_steps_exceeding_max_step_num(self, setup_planner, mock_llm):
        """planner should fail when the model returns more steps than allowed."""
        excessive_response = {
            **functioncall_response,
            "tool_calls": [
                {
                    **functioncall_response["tool_calls"][0],
                    "args": {
                        **functioncall_response["tool_calls"][0]["args"],
                        "steps": [
                            {
                                "description": f"Description {idx}",
                                "title": f"Step {idx}",
                                "type": "info_collecting"
                            }
                            for idx in range(3)
                        ],
                    },
                }
            ],
        }

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                return_value=excessive_response
        ):
            result = await setup_planner.generate_plan({**test_data, "max_step_num": 2})

        assert result.plan_success is False
        assert result.plan is None
        assert "steps" in result.error_msg.lower()
