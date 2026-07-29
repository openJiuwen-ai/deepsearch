# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""测试依赖驱动工作流中的背景知识管理函数"""

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_reasoning_team_nodes import (
    _extract_plan_background_knowledge,
    _extract_step_background_knowledge,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Step,
    StepType,
)


class TestExtractPlanBackgroundKnowledge:
    """测试 _extract_plan_background_knowledge 函数"""

    def test_extract_plan_background_knowledge_normal(self):
        """正常步骤列表提取"""
        steps = [
            Step(
                id="1-1-1",
                title="Step 1 Title",
                description="Step 1 Description",
                type=StepType.INFO_COLLECTING,
                evaluation="Good evaluation",
            ),
            Step(
                id="1-1-2",
                title="Step 2 Title",
                description="Step 2 Description",
                type=StepType.INFO_COLLECTING,
                evaluation="Excellent evaluation",
            ),
        ]

        result = _extract_plan_background_knowledge(steps)

        assert isinstance(result, dict)
        assert len(result) == 2
        assert "1-1-1" in result
        assert "1-1-2" in result
        assert "[Step id] : 1-1-1;" in result["1-1-1"]
        assert "[Step title] : Step 1 Title;" in result["1-1-1"]
        assert "[Step description] : Step 1 Description;" in result["1-1-1"]
        assert "[Step evaluation] : Good evaluation;" in result["1-1-1"]

    def test_extract_plan_background_knowledge_empty(self):
        """空列表返回空字典"""
        steps = []
        result = _extract_plan_background_knowledge(steps)
        assert isinstance(result, dict)
        assert len(result) == 0

    def test_extract_plan_background_knowledge_no_evaluation(self):
        """步骤无 evaluation 字段"""
        steps = [
            Step(
                id="1-2-1",
                title="Test Step",
                description="Test Description",
                type=StepType.INFO_COLLECTING,
            )
        ]
        result = _extract_plan_background_knowledge(steps)
        assert "1-2-1" in result
        assert "[Step evaluation] : ;" in result["1-2-1"]

    def test_extract_plan_background_knowledge_single_step(self):
        """单个步骤提取"""
        steps = [
            Step(
                id="2-1-1",
                title="Single Step",
                description="Single Description",
                type=StepType.INFO_COLLECTING,
                evaluation="Single evaluation",
            )
        ]
        result = _extract_plan_background_knowledge(steps)
        assert len(result) == 1
        assert "2-1-1" in result


class TestExtractStepBackgroundKnowledge:
    """测试 _extract_step_background_knowledge 函数"""

    def test_extract_step_background_knowledge_with_result(self):
        """有 step_result 的提取"""
        steps = [
            Step(
                id="1-1-1",
                title="Step Title",
                description="Step Description",
                type=StepType.INFO_COLLECTING,
                step_result="This is the step result content",
            )
        ]

        result = _extract_step_background_knowledge(steps)

        assert isinstance(result, dict)
        assert len(result) == 1
        assert "1-1-1" in result
        assert "[title] : Step Title;" in result["1-1-1"]
        assert "[description] : Step Description;" in result["1-1-1"]
        assert "[content] : This is the step result content;" in result["1-1-1"]

    def test_extract_step_background_knowledge_no_result(self):
        """无 step_result 的处理"""
        steps = [
            Step(
                id="1-2-1",
                title="No Result Step",
                description="No Result Description",
                type=StepType.INFO_COLLECTING,
            )
        ]
        result = _extract_step_background_knowledge(steps)
        assert "1-2-1" in result
        assert "[content] : None;" in result["1-2-1"]

    def test_extract_step_background_knowledge_empty(self):
        """空步骤列表"""
        steps = []
        result = _extract_step_background_knowledge(steps)
        assert isinstance(result, dict)
        assert len(result) == 0

    def test_extract_step_background_knowledge_multiple_steps(self):
        """多个步骤提取"""
        steps = [
            Step(
                id="1-1-1",
                title="Step 1",
                description="Description 1",
                type=StepType.INFO_COLLECTING,
                step_result="Result 1",
            ),
            Step(
                id="1-1-2",
                title="Step 2",
                description="Description 2",
                type=StepType.INFO_COLLECTING,
                step_result="Result 2",
            ),
            Step(
                id="1-1-3",
                title="Step 3",
                description="Description 3",
                type=StepType.INFO_COLLECTING,
                step_result="Result 3",
            ),
        ]
        result = _extract_step_background_knowledge(steps)
        assert len(result) == 3
        assert "1-1-1" in result
        assert "1-1-2" in result
        assert "1-1-3" in result
        assert "[content] : Result 1;" in result["1-1-1"]
        assert "[content] : Result 2;" in result["1-1-2"]
        assert "[content] : Result 3;" in result["1-1-3"]
