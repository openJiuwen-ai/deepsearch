# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""测试依赖驱动 Planner 工具"""

from unittest.mock import AsyncMock, Mock

import pytest
from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.algorithm.query_understanding.planner import (
    create_plan_tool,
    Planner,
    PlannerConfig,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.editor_team_manager_node import EditorTeamNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_reasoning_team_nodes import (
    DependencyPlanReasoningNode,
    SectionReasoningStartNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline, Section


@pytest.mark.asyncio
async def test_section_description_reaches_dependency_planner_user_message(monkeypatch):
    description = "仅比较华东地区、2024年的企业采购成本，不讨论个人消费价格"
    section = Section(id="1", title="采购成本比较", description=description)
    outline = Outline(title="企业采购研究报告", thought="", sections=[section])
    section_state = EditorTeamNode()._create_section_state_from_state(
        {
            "original_query": "研究企业采购成本",
            "messages": [{"role": "user", "content": "研究企业采购成本"}],
            "config": {
                "planner_max_step_num": 3,
                "planner_max_retry_num": 1,
                "workflow_max_plan_executed_num": 3,
                "llm_config": {"general": {"model_name": "basic"}},
            },
        },
        outline,
        section,
    )
    runtime_state = {}

    def get_global_state(path):
        value = runtime_state
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        return value

    session = Mock(spec=Session)
    session.update_global_state.side_effect = runtime_state.update
    session.get_global_state.side_effect = get_global_state
    await SectionReasoningStartNode().invoke(section_state, session, None)
    node = DependencyPlanReasoningNode()
    current_inputs = node._pre_handle({}, session, None)
    llm_invoke = AsyncMock(return_value={
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "plan-1",
            "name": "generate_plan",
            "args": {
                "language": "zh-CN", "title": "采购成本计划", "thought": "",
                "is_research_completed": True, "steps": [],
            },
        }],
    })
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.query_understanding.planner.ainvoke_llm_with_stats",
        llm_invoke,
    )
    planner = Planner(PlannerConfig(llm=Mock(), prompt=node.prompt, max_retry_num=1))
    await planner.generate_plan(current_inputs)

    messages = llm_invoke.await_args.kwargs["messages"]
    assert messages[-1]["role"] == "user"
    assert "采购成本比较" in messages[-1]["content"]
    assert description in messages[-1]["content"]
    assert description not in messages[0]["content"]


class TestDependencyPlannerTool:
    """测试依赖驱动 Planner 工具"""

    def test_create_plan_tool_dependency_mode(self):
        """验证使用 dependency_planner prompt 时创建工具成功"""
        state = {"max_step_num": 5}
        tool = create_plan_tool(state, "dependency_planner")

        assert tool is not None
        assert hasattr(tool, "card")
        assert tool.card.name == "generate_plan"

    def test_create_plan_tool_general_mode(self):
        """验证使用 planner prompt 时创建工具成功"""
        state = {"max_step_num": 5}
        tool = create_plan_tool(state, "planner")

        assert tool is not None
        assert tool.card.name == "generate_plan"

    def test_dependency_plan_tool_params(self):
        """验证工具参数包含 id, parent_ids, relationships"""
        state = {"max_step_num": 5, "section_idx": "1", "plan_executed_num": 0}
        tool = create_plan_tool(state, "dependency_planner")

        params = tool.card.input_params
        assert params is not None

        properties = params.get("properties", {})
        assert "steps" in properties

        steps_param = properties.get("steps", {})
        items = steps_param.get("items", {})
        item_properties = items.get("properties", {})

        assert "id" in item_properties
        assert "parent_ids" in item_properties
        assert "relationships" in item_properties

    def test_dependency_plan_tool_step_format_description(self):
        """验证步骤 ID 格式描述正确"""
        state = {"max_step_num": 5, "section_idx": "2", "plan_executed_num": 1}
        tool = create_plan_tool(state, "dependency_planner")

        params = tool.card.input_params
        steps_param = params.get("properties", {}).get("steps", {})
        items = steps_param.get("items", {})
        id_param = items.get("properties", {}).get("id", {})

        description = id_param.get("description", "")
        assert "2-2-" in description or "section" in description.lower()

    def test_dependency_plan_reasoning_node_prompt(self):
        """验证 DependencyPlanReasoningNode 使用正确的 prompt"""
        node = DependencyPlanReasoningNode()
        assert node.prompt == "dep_driving_planner"

    def test_dependency_plan_tool_with_state(self):
        """验证工具从 state 中正确获取参数"""
        state = {"max_step_num": 10, "section_idx": "3", "plan_executed_num": 2}
        tool = create_plan_tool(state, "dependency_planner")

        steps_param = tool.card.input_params.get("properties", {}).get("steps", {})
        steps_description = steps_param.get("description", "")
        assert "10" in steps_description or "Maximum" in steps_description
