# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""测试依赖驱动工作流构建和集成"""

import pytest

from openjiuwen.core.workflow.workflow import Workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_reasoning_team_nodes import (
    build_dependency_reasoning_workflow,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_writing_team_nodes import (
    build_dependency_writing_workflow,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
    DeepresearchDependencyAgent,
)


class TestDependencyReasoningWorkflow:
    """测试依赖驱动任务规划子图工作流"""

    def test_build_dependency_reasoning_workflow(self):
        """测试构建依赖驱动规划子图工作流"""
        workflow = build_dependency_reasoning_workflow()

        assert workflow is not None
        assert isinstance(workflow, Workflow)


class TestDependencyWritingWorkflow:
    """测试依赖驱动写作子图工作流"""

    def test_build_dependency_writing_workflow(self):
        """测试构建依赖驱动写作子图工作流"""
        workflow = build_dependency_writing_workflow()

        assert workflow is not None
        assert isinstance(workflow, Workflow)


class TestDeepresearchDependencyAgent:
    """测试 DeepresearchDependencyAgent"""

    def test_dependency_agent_workflow_creation(self):
        """测试依赖驱动 Agent 工作流创建"""
        agent = DeepresearchDependencyAgent()

        assert agent is not None
        assert agent.research_name == "research_workflow_dependency_driving"
        assert agent.version == "1"
        assert agent.agent is not None

    def test_dependency_agent_name_differs_from_general(self):
        """验证依赖驱动 Agent 名称与通用 Agent 不同"""
        from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
            DeepresearchAgent,
        )

        dep_agent = DeepresearchDependencyAgent()
        general_agent = DeepresearchAgent()

        assert dep_agent.research_name != general_agent.research_name
        assert "dependency" in dep_agent.research_name.lower()


class TestDependencyReasoningIntegration:
    """测试依赖驱动规划子图集成"""

    def test_dependency_reasoning_workflow_creation(self):
        """测试依赖驱动规划子图可以成功创建"""
        workflow = build_dependency_reasoning_workflow()

        assert workflow is not None


class TestDependencyWritingIntegration:
    """测试依赖驱动写作子图集成"""

    def test_dependency_writing_workflow_creation(self):
        """测试依赖驱动写作子图可以成功创建"""
        workflow = build_dependency_writing_workflow()

        assert workflow is not None


class TestDependencyAgentE2E:
    """测试 DeepresearchDependencyAgent 端到端流程"""

    def test_dependency_agent_creation(self):
        """测试依赖驱动 Agent 可以成功创建"""
        agent = DeepresearchDependencyAgent()

        assert agent is not None
        assert agent.research_name == "research_workflow_dependency_driving"
