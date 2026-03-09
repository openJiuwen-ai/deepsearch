# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""测试依赖驱动写作子图节点"""

import pytest
from unittest.mock import Mock

from openjiuwen.core.session.node import Session
from openjiuwen.core.context_engine.base import ModelContext

from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_writing_team_nodes import (
    SectionWritingStartNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Plan,
    Step,
    StepType,
)


class TestSectionWritingStartNode:
    """测试 SectionWritingStartNode"""

    @pytest.mark.asyncio
    async def test_section_writing_start_node_init(self):
        """测试初始化 section_context"""
        node = SectionWritingStartNode()
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)

        inputs = {
            "language": "zh-CN",
            "messages": [{"role": "user", "content": "test"}],
            "section_idx": "1",
            "report_task": "Test Report",
            "section_task": "Section 1",
            "section_description": "Test section",
            "section_iscore": True,
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
        assert section_context["report_task"] == "Test Report"
        assert section_context["section_task"] == "Section 1"

    @pytest.mark.asyncio
    async def test_section_writing_background_knowledge(self):
        """测试 sub_report_background_knowledge 传递"""
        node = SectionWritingStartNode()
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)

        mock_bg_knowledge = [
            {"step_id": "1-1-1", "content": "Background info 1"},
            {"step_id": "1-1-2", "content": "Background info 2"},
        ]

        inputs = {
            "language": "zh-CN",
            "messages": [],
            "section_idx": "1",
            "report_task": "Test Report",
            "section_task": "Section 1",
            "sub_report_background_knowledge": mock_bg_knowledge,
            "config": {},
        }

        result = await node.invoke(inputs, session, context)

        session.update_global_state.assert_called_once()
        call_args = session.update_global_state.call_args[0][0]

        section_context = call_args["section_context"]
        assert "sub_report_background_knowledge" in section_context
        assert section_context["sub_report_background_knowledge"] == mock_bg_knowledge

    @pytest.mark.asyncio
    async def test_section_writing_history_plans(self):
        """测试 history_plans 传递"""
        node = SectionWritingStartNode()
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)

        mock_history_plans = [
            Plan(
                id="1-1",
                language="zh-CN",
                title="Plan 1",
                thought="Thought 1",
                is_research_completed=False,
                steps=[
                    Step(
                        id="1-1-1",
                        title="Step 1",
                        description="Description 1",
                        type=StepType.INFO_COLLECTING,
                        step_result="Result 1",
                    )
                ],
            )
        ]

        inputs = {
            "language": "zh-CN",
            "messages": [],
            "section_idx": "1",
            "report_task": "Test Report",
            "section_task": "Section 1",
            "history_plans": mock_history_plans,
            "config": {},
        }

        result = await node.invoke(inputs, session, context)

        session.update_global_state.assert_called_once()
        call_args = session.update_global_state.call_args[0][0]

        section_context = call_args["section_context"]
        assert "history_plans" in section_context
        assert len(section_context["history_plans"]) == 1
        assert section_context["history_plans"][0]["id"] == "1-1"
