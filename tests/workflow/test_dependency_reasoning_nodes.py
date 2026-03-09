# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""测试依赖驱动规划子图节点"""

import pytest
from unittest.mock import Mock, MagicMock

from openjiuwen.core.session.node import Session
from openjiuwen.core.context_engine.base import ModelContext

from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_reasoning_team_nodes import (
    SectionReasoningStartNode,
    SectionReasoningEndNode,
    DependencyPlanReasoningNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Plan,
    Step,
    StepType,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


class TestSectionReasoningStartNode:
    """测试 SectionReasoningStartNode"""

    @pytest.mark.asyncio
    async def test_section_reasoning_start_node_init(self):
        """测试初始化 section_context"""
        node = SectionReasoningStartNode()
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)

        inputs = {
            "language": "zh-CN",
            "messages": [{"role": "user", "content": "test"}],
            "section_idx": "1",
            "config": {"test": "config"},
        }

        result = await node.invoke(inputs, session, context)

        assert result == inputs
        session.update_global_state.assert_called_once()
        call_args = session.update_global_state.call_args[0][0]

        assert "section_context" in call_args
        section_context = call_args["section_context"]
        assert section_context["language"] == "zh-CN"
        assert section_context["section_idx"] == "1"

    @pytest.mark.asyncio
    async def test_section_reasoning_start_node_background_knowledge(self):
        """测试从 parent_section_steps 提取背景知识"""
        node = SectionReasoningStartNode()
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)

        parent_steps = [
            Step(
                id="1-1-1",
                title="Parent Step 1",
                description="Description 1",
                type=StepType.INFO_COLLECTING,
                step_result="Result 1",
                evaluation="Good",
            )
        ]

        inputs = {
            "language": "zh-CN",
            "messages": [],
            "section_idx": "2",
            "parent_section_steps": parent_steps,
            "config": {},
        }

        result = await node.invoke(inputs, session, context)

        session.update_global_state.assert_called_once()
        call_args = session.update_global_state.call_args[0][0]

        section_context = call_args["section_context"]
        assert "plan_background_knowledge" in section_context
        assert "step_background_knowledge" in section_context

        plan_bg = section_context["plan_background_knowledge"]
        assert "1-1-1" in plan_bg
        assert "Parent Step 1" in plan_bg["1-1-1"]


class TestSectionReasoningEndNode:
    """测试 SectionReasoningEndNode"""

    @pytest.mark.asyncio
    async def test_section_reasoning_end_node_return(self):
        """测试返回 history_plans"""
        node = SectionReasoningEndNode()
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)

        mock_plans = [
            Plan(
                id="1",
                language="zh-CN",
                title="Test Plan",
                thought="Test thought",
                is_research_completed=False,
                steps=[],
            )
        ]
        session.get_global_state.return_value = mock_plans

        inputs = {"test": "input"}
        result = await node.invoke(inputs, session, context)

        assert isinstance(result, dict)
        assert "plans" in result
        assert result["plans"] == mock_plans

        session.get_global_state.assert_called_with("section_context.history_plans")


class TestDependencyPlanReasoningNode:
    """测试 DependencyPlanReasoningNode"""

    @pytest.fixture
    def mock_session(self):
        """创建 mock session"""
        session = Mock(spec=Session)
        session.get_global_state.side_effect = lambda key: {
            "section_context.section_idx": "1",
            "section_context.plan_executed_num": 0,
            "section_context.plan_background_knowledge": {},
            "section_context.messages": [],
        }.get(key, None)
        return session

    @pytest.fixture
    def mock_context(self):
        """创建 mock context"""
        return Mock(spec=ModelContext)

    def test_dependency_plan_reasoning_success_not_completed(
        self, mock_session, mock_context
    ):
        """测试规划成功但信息不足时路由到信息收集"""
        node = DependencyPlanReasoningNode()

        mock_plan = Plan(
            id="1-1",
            language="zh-CN",
            title="Test Plan",
            thought="Test thought",
            is_research_completed=False,
            steps=[
                Step(
                    id="1-1-1",
                    title="Step 1",
                    description="Description 1",
                    type=StepType.INFO_COLLECTING,
                )
            ],
        )

        algorithm_output = {"plan": mock_plan, "success": True, "response_messages": []}

        inputs = {}
        result = node._post_handle(inputs, algorithm_output, mock_session, mock_context)

        assert result["next_node"] == NodeId.INFO_COLLECTOR.value

    def test_dependency_plan_reasoning_success_completed(
        self, mock_session, mock_context
    ):
        """测试规划成功且信息充足时路由到 END"""
        node = DependencyPlanReasoningNode()

        mock_plan = Plan(
            id="1-1",
            language="zh-CN",
            title="Test Plan",
            thought="Test thought",
            is_research_completed=True,
            steps=[],
        )

        algorithm_output = {"plan": mock_plan, "success": True, "response_messages": []}

        inputs = {}
        result = node._post_handle(inputs, algorithm_output, mock_session, mock_context)

        assert result["next_node"] == NodeId.END.value

    def test_dependency_plan_reasoning_failure(self, mock_session, mock_context):
        """测试规划失败时路由到 END"""
        node = DependencyPlanReasoningNode()

        algorithm_output = {"plan": None, "success": False, "error_msg": "Test error", "response_messages": []}

        inputs = {}
        result = node._post_handle(inputs, algorithm_output, mock_session, mock_context)

        assert result["next_node"] == NodeId.END.value

    def test_dependency_plan_id_format(self, mock_session, mock_context):
        """验证 plan.id 格式为 {section_idx}-{plan_executed_num}"""
        node = DependencyPlanReasoningNode()

        mock_plan = Plan(
            id="temp",
            language="zh-CN",
            title="Test Plan",
            thought="Test thought",
            is_research_completed=False,
            steps=[],
        )

        algorithm_output = {"plan": mock_plan, "success": True, "response_messages": []}

        inputs = {}
        node._post_handle(inputs, algorithm_output, mock_session, mock_context)

        assert mock_plan.id == "1-1"

    def test_dependency_plan_background_knowledge_injection(
        self, mock_session, mock_context
    ):
        """验证背景知识注入到 plan"""
        node = DependencyPlanReasoningNode()

        mock_plan = Plan(
            id="1-1",
            language="zh-CN",
            title="Test Plan",
            thought="Test thought",
            is_research_completed=False,
            steps=[],
        )

        mock_bg_knowledge = {"step-1": "Background info"}
        mock_session.get_global_state.side_effect = lambda key: {
            "section_context.section_idx": "1",
            "section_context.plan_executed_num": 0,
            "section_context.plan_background_knowledge": mock_bg_knowledge,
            "section_context.messages": [],
        }.get(key, None)

        algorithm_output = {"plan": mock_plan, "success": True, "response_messages": []}

        inputs = {}
        node._post_handle(inputs, algorithm_output, mock_session, mock_context)

        assert mock_plan.background_knowledge == mock_bg_knowledge
