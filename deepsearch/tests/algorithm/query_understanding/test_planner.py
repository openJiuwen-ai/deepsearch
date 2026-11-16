import json
from unittest.mock import Mock, AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages
from openjiuwen_deepsearch.algorithm.query_understanding.planner import Planner, PlannerResult, create_plan_tool
from openjiuwen_deepsearch.common.status_code import StatusCode, format_exception_info
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
    def test_planner_prompt_separates_stable_system_from_query_and_date(self):
        messages = build_prompt_messages(
            "planner",
            {"query": "测试查询", "current_date": "2026-09-14"},
        )

        assert "测试查询" not in messages[0]["content"]
        assert "2026-09-14" not in messages[0]["content"]
        assert "测试查询" in messages[1]["content"]
        assert "2026-09-14" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_generate_plan_preserves_prior_messages_and_current_query(self, setup_planner):
        prior = [{"role": "user", "content": "已有研究任务"}]
        current_inputs = {
            "messages": prior,
            "query": "当前章节研究问题",
            "language": "zh-CN",
            "max_step_num": 3,
            "plan_executed_num": 0,
            "max_plan_executed_num": 3,
        }

        with patch(
            "openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats",
            new_callable=AsyncMock,
            return_value=functioncall_response,
        ) as invoke:
            await setup_planner.generate_plan(current_inputs)

        prompt = invoke.await_args.kwargs["messages"]
        assert prompt[1] is prior[0]
        assert prompt[-1]["role"] == "user"
        assert "当前章节研究问题" in prompt[-1]["content"]

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
        expected_error_msg = format_exception_info(
            StatusCode.PLANNER_GENERATE_ERROR,
            "Test Exception",
            prefix="section_idx: 1 | Round 1/3 | retry (1/1). ",
        )

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("Test Exception")
        ):
            result = await setup_planner.generate_plan(test_data)

        assert result.plan_success is False
        assert result.error_msg == expected_error_msg

    @pytest.mark.asyncio
    async def test_generate_plan_exception(self, setup_planner, mock_llm):
        """测试生成计划时发生异常的情况"""
        expected_error_msg = format_exception_info(
            StatusCode.PLANNER_GENERATE_ERROR,
            "Test Exception",
            prefix="section_idx: 1 | Round 1/3 | retry (1/1). ",
        )

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("Test Exception")
        ):
            result = await setup_planner.generate_plan(test_data)

        assert result.plan_success is False
        assert result.error_msg == expected_error_msg

    @pytest.mark.asyncio
    async def test_generate_plan_max_retries(self, setup_planner, mock_llm):
        """测试达到最大重试次数的情况"""
        expected_error_msg = format_exception_info(
            StatusCode.PLANNER_GENERATE_ERROR,
            "Test Exception",
            prefix="section_idx: 1 | Round 1/3 | retry (3/3). ",
        )

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("Test Exception")
        ):
            # 设置最大重试次数为3
            setup_planner.config.max_retry_num = 3
            result = await setup_planner.generate_plan(test_data)

        assert result.plan_success is False
        assert result.error_msg == expected_error_msg

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


@pytest.mark.parametrize("prompt_name", ["planner", "dep_driving_planner"])
def test_planner_prompts_require_step_objects_with_valid_example(prompt_name):
    messages = build_prompt_messages(prompt_name, {**test_data, "language": "en-US", "max_step_num": 3})
    system = messages[0]["content"]
    assert '`"steps": "[...]"` is invalid' in system
    assert '`"steps": ["collect evidence"]`' in system
    example = json.loads(system.split("```json\n")[-1].split("```")[0])
    assert isinstance(example["steps"], list)
    assert all(isinstance(step, dict) for step in example["steps"])
    assert all({"type", "title", "description"} <= step.keys() for step in example["steps"])


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_name", ["planner", "dep_driving_planner"])
@pytest.mark.parametrize("bad_steps, expected_code", [
    ('[{"title":"UNTRUSTED_MODEL_TEXT"}]', "PLAN_STEPS_NOT_ARRAY"),
    (["UNTRUSTED_MODEL_TEXT"], "PLAN_STEP_NOT_OBJECT"),
    ([], "PLAN_STEPS_EMPTY"),
    ([{"type": "info_collecting"}], "PLAN_STEP_REQUIRED_FIELD_MISSING"),
    ([{"type": "info_collecting", "title": [], "description": "Evidence"}], "PLAN_ARGUMENTS_INVALID"),
    ([{"type": "info_collecting", "title": "A", "description": "A"}] * 2, "PLAN_STEPS_TOO_MANY"),
])
async def test_planner_retries_format_errors_with_controlled_feedback(prompt_name, bad_steps, expected_code):
    from copy import deepcopy
    from openjiuwen_deepsearch.algorithm.query_understanding.planner import PlannerConfig

    planner = Planner(PlannerConfig(llm=Mock(), prompt=prompt_name, max_retry_num=2))
    bad = deepcopy(functioncall_response)
    bad["tool_calls"][0]["args"]["steps"] = bad_steps
    valid = deepcopy(functioncall_response)
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats",
        new_callable=AsyncMock, side_effect=[bad, valid],
    ) as invoke:
        result = await planner.generate_plan({**test_data, "max_step_num": 1})
    assert result.plan_success
    first, second = [call.kwargs["messages"] for call in invoke.await_args_list]
    assert all("<retry_feedback>" not in msg["content"] for msg in first)
    assert second[:-1] == first
    feedback = second[-1]
    assert feedback["role"] == "user"
    assert expected_code in feedback["content"]
    assert "<retry_feedback>" in feedback["content"]
    assert "UNTRUSTED_MODEL_TEXT" not in feedback["content"]
    assert "array containing strings" in feedback["content"]


@pytest.mark.asyncio
async def test_planner_transport_retry_preserves_request_without_raw_exception():
    from openjiuwen_deepsearch.algorithm.query_understanding.planner import PlannerConfig

    planner = Planner(PlannerConfig(llm=Mock(), max_retry_num=2))
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats",
        new_callable=AsyncMock, side_effect=[RuntimeError("PRIVATE_PROVIDER_ERROR"), functioncall_response],
    ) as invoke:
        result = await planner.generate_plan({**test_data, "max_step_num": 1})
    assert result.plan_success
    first, second = [call.kwargs["messages"] for call in invoke.await_args_list]
    assert first is second
    assert "PRIVATE_PROVIDER_ERROR" not in str(second)


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_name", ["planner", "dep_driving_planner"])
@pytest.mark.parametrize("second_failure", ["format", "timeout"])
async def test_planner_repeated_failure_feedback_and_request_reuse(prompt_name, second_failure):
    from copy import deepcopy
    from openjiuwen_deepsearch.algorithm.query_understanding.planner import PlannerConfig

    planner = Planner(PlannerConfig(llm=Mock(), prompt=prompt_name, max_retry_num=3))
    invalid_array = deepcopy(functioncall_response)
    invalid_array["tool_calls"][0]["args"]["steps"] = "UNTRUSTED_MODEL_TEXT"
    invalid_element = deepcopy(functioncall_response)
    invalid_element["tool_calls"][0]["args"]["steps"] = ["UNTRUSTED_MODEL_TEXT"]
    middle = invalid_element if second_failure == "format" else TimeoutError("PRIVATE_PROVIDER_ERROR")
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        side_effect=[invalid_array, middle, deepcopy(functioncall_response)],
    ) as invoke:
        result = await planner.generate_plan(test_data)

    assert result.plan_success
    assert invoke.await_count == 3
    first, second, third = [call.kwargs["messages"] for call in invoke.await_args_list]
    assert all("<retry_feedback>" not in msg["content"] for msg in first)
    assert "PLAN_STEPS_NOT_ARRAY" in second[-1]["content"]
    assert second[:-1] == first
    if second_failure == "format":
        assert third[:-1] == first
        assert "PLAN_STEP_NOT_OBJECT" in third[-1]["content"]
        assert "PLAN_STEPS_NOT_ARRAY" not in third[-1]["content"]
        assert len(third) == len(first) + 1
    else:
        assert third is second
        assert "PLAN_STEPS_NOT_ARRAY" in third[-1]["content"]
    assert "UNTRUSTED_MODEL_TEXT" not in str([first, second, third])
    assert "PRIVATE_PROVIDER_ERROR" not in str([first, second, third])
