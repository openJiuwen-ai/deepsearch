from unittest.mock import Mock, AsyncMock, patch

import pytest

from jiuwen_deepsearch.algorithm.query_understanding.planner import Planner, PlannerResult, create_plan_tool
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import Plan, StepType

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
tool_name = create_plan_tool(1).card.name
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
        with patch('jiuwen_deepsearch.algorithm.query_understanding.planner.llm_context', return_value=mock_llm):
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
                'jiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
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
            error_msg='section_idx: 1 | Round 1/3 | Error when Planner generating a plan. retry (1/1).error: **'
        )

        with patch(
                'jiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
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
            error_msg='section_idx: 1 | Round 1/3 | Error when Planner generating a plan. retry (1/1).error: **'
        )

        with patch(
                'jiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
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
            error_msg='section_idx: 1 | Round 1/3 | Error when Planner generating a plan. retry (3/3).error: **'
        )

        with patch(
                'jiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("Test Exception")
        ):
            # 设置最大重试次数为3
            setup_planner.config.max_retry_num = 3
            result = await setup_planner.generate_plan(test_data)

        assert result == mock_llm_response
