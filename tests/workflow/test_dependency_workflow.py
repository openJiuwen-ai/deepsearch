# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""测试依赖驱动工作流构建和集成"""

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.resources_manager.resource_manager import ResourceMgr
from openjiuwen.core.workflow import generate_workflow_key
from openjiuwen.core.workflow.workflow import Workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_reasoning_team_nodes import (
    build_dependency_reasoning_workflow,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_writing_team_nodes import (
    build_dependency_writing_workflow,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
    DeepresearchAgent,
    DeepresearchDependencyAgent,
    DeepresearchIntentHybridAgent,
)


def _patch_runner_resource_mgr(monkeypatch: pytest.MonkeyPatch, resource_mgr: ResourceMgr) -> None:
    """Patch Runner.resource_mgr across SDK variants."""
    resource_mgr_attr = getattr(type(Runner), "resource_mgr", None)
    if isinstance(resource_mgr_attr, property) and resource_mgr_attr.fset is None:
        monkeypatch.setattr(Runner, "_resource_manager", resource_mgr, raising=False)
        return
    monkeypatch.setattr(Runner, "resource_mgr", resource_mgr, raising=False)


def _get_workflow_node_type(resource_mgr: ResourceMgr, workflow_key: str, node_id: str) -> str | None:
    """Return the concrete executable type registered for a workflow node."""
    provider = resource_mgr._resource_registry.workflow()._providers.get(workflow_key)
    if provider is None:
        return None
    workflow = provider()
    graph = workflow._internal._graph
    vertex = graph.nodes.get(node_id)
    executable = getattr(vertex, "_executable", None) if vertex else None
    return type(executable).__name__ if executable else None


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
        dep_agent = DeepresearchDependencyAgent()
        general_agent = DeepresearchAgent()

        assert dep_agent.research_name != general_agent.research_name
        assert "dependency" in dep_agent.research_name.lower()


class TestDeepresearchIntentHybridAgent:
    """测试混合大纲 Agent。"""

    def test_hybrid_agent_workflow_creation(self):
        """hybrid Agent 应创建独立 workflow，不影响 parallel 和 dependency_driving。"""
        agent = DeepresearchIntentHybridAgent()

        assert agent is not None
        assert agent.research_name == "research_workflow_hybrid"
        assert agent.version == "1"
        assert agent.agent is not None

    def test_hybrid_agent_name_differs_from_existing_agents(self):
        """hybrid workflow 名称应与已有两种 workflow 区分开。"""
        hybrid_agent = DeepresearchIntentHybridAgent()
        dependency_agent = DeepresearchDependencyAgent()
        parallel_agent = DeepresearchAgent()

        assert hybrid_agent.research_name != dependency_agent.research_name
        assert hybrid_agent.research_name != parallel_agent.research_name
        assert "hybrid" in hybrid_agent.research_name.lower()


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


class TestDependencyWorkflowRegistrationIsolation:
    """Regression coverage for workflow registration contamination."""

    def test_dependency_agent_does_not_register_parallel_workflow_key(self, monkeypatch):
        resource_mgr = ResourceMgr()
        _patch_runner_resource_mgr(monkeypatch, resource_mgr)

        agent = DeepresearchDependencyAgent()

        parallel_key = generate_workflow_key("research_workflow", "1")
        dependency_key = generate_workflow_key(agent.research_name, agent.version)

        assert resource_mgr._resource_registry.workflow()._providers.get(parallel_key) is None
        assert resource_mgr._resource_registry.workflow()._providers.get(dependency_key) is not None

    def test_dependency_then_parallel_agents_keep_distinct_topology(self, monkeypatch):
        resource_mgr = ResourceMgr()
        _patch_runner_resource_mgr(monkeypatch, resource_mgr)

        dependency_agent = DeepresearchDependencyAgent()
        parallel_agent = DeepresearchAgent()

        parallel_key = generate_workflow_key(parallel_agent.research_name, parallel_agent.version)
        dependency_key = generate_workflow_key(dependency_agent.research_name, dependency_agent.version)

        assert _get_workflow_node_type(resource_mgr, parallel_key, "outline") == "OutlineNode"
        assert _get_workflow_node_type(resource_mgr, parallel_key, "outline_interaction") == "OutlineInteractionNode"
        assert _get_workflow_node_type(resource_mgr, parallel_key, "editor_team") == "EditorTeamNode"

        assert _get_workflow_node_type(resource_mgr, dependency_key, "outline") == "DependencyOutlineNode"
        assert _get_workflow_node_type(
            resource_mgr,
            dependency_key,
            "outline_interaction",
        ) == "DependencyOutlineInteractionNode"
        assert _get_workflow_node_type(
            resource_mgr,
            dependency_key,
            "dependency_editor_team",
        ) == "DependencyEditorTeamNode"

    def test_hybrid_agent_registers_mixed_topology(self, monkeypatch):
        """hybrid workflow 应复用普通大纲节点，同时注册普通和依赖两套写作团队。"""
        resource_mgr = ResourceMgr()
        _patch_runner_resource_mgr(monkeypatch, resource_mgr)

        hybrid_agent = DeepresearchIntentHybridAgent()
        hybrid_key = generate_workflow_key(hybrid_agent.research_name, hybrid_agent.version)

        assert resource_mgr._resource_registry.workflow()._providers.get(hybrid_key) is not None
        assert _get_workflow_node_type(resource_mgr, hybrid_key, "intent_recognition") == "IntentRecognitionNode"
        assert _get_workflow_node_type(resource_mgr, hybrid_key, "outline") == "OutlineNode"
        assert _get_workflow_node_type(resource_mgr, hybrid_key, "outline_interaction") == "OutlineInteractionNode"
        assert _get_workflow_node_type(resource_mgr, hybrid_key, "editor_team") == "EditorTeamNode"
        assert _get_workflow_node_type(
            resource_mgr,
            hybrid_key,
            "dependency_editor_team",
        ) == "DependencyEditorTeamNode"

    def test_hybrid_agent_does_not_use_old_hybrid_outline_nodes(self, monkeypatch):
        """hybrid workflow 不应注册旧方案中的 HybridOutlineNode。"""
        resource_mgr = ResourceMgr()
        _patch_runner_resource_mgr(monkeypatch, resource_mgr)

        hybrid_agent = DeepresearchIntentHybridAgent()
        hybrid_key = generate_workflow_key(hybrid_agent.research_name, hybrid_agent.version)

        assert _get_workflow_node_type(resource_mgr, hybrid_key, "outline") != "HybridOutlineNode"
        assert _get_workflow_node_type(resource_mgr, hybrid_key, "outline_interaction") != "HybridOutlineInteractionNode"