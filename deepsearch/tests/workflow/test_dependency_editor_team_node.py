# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""依赖编辑团队运行时边界场景测试。"""

import asyncio
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph import (
    dependency_reasoning_team_nodes as dependency_nodes,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.editor_team_manager_node import (
    DependencyEditorTeamNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_reasoning_team_nodes import (
    DependencyInfoCollectorNode,
    SectionReasoningStartNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.collector_execution_service import (
    CollectorExecutionResult,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import (
    InfoCollectorNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Outline,
    RetrievalQuery,
    Plan,
    Section,
    Step,
    StepType,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


class _FakeCompiledGraph:
    def __init__(self, seen_session_ids, failing_steps=None, delay: float = 0.01):
        self.seen_session_ids = seen_session_ids
        self.failing_steps = set(failing_steps or [])
        self.delay = delay

    async def invoke(self, inputs, workflow_session):
        collector_inputs = inputs["inputs"]
        step_title = collector_inputs["step_title"]
        self.seen_session_ids.append(workflow_session.session_id())
        checkpointer = workflow_session.checkpointer()
        await checkpointer.pre_workflow_execute(workflow_session, collector_inputs)
        try:
            await asyncio.sleep(self.delay)
            if step_title in self.failing_steps:
                raise RuntimeError(f"collector failure for {step_title}")
            workflow_state = workflow_session.state()
            workflow_state.update_global(
                {
                    "collector_context": {
                        "history_queries": [RetrievalQuery(query=f"query-{step_title}")],
                        "doc_infos": [{"title": step_title}],
                        "info_summary": f"summary-{step_title}",
                        "evaluation": f"evaluation-{step_title}",
                        "messages": collector_inputs.get("messages", []),
                    }
                }
            )
            workflow_state.commit()
        except Exception as exc:
            await checkpointer.post_workflow_execute(workflow_session, {}, exc)
        else:
            await checkpointer.post_workflow_execute(workflow_session, {}, None)


class _FakeCollectorInternal:
    def __init__(self, seen_session_ids, failing_steps=None):
        self.seen_session_ids = seen_session_ids
        self.failing_steps = failing_steps or []

    def compile(self, workflow_session, context=None):
        return _FakeCompiledGraph(self.seen_session_ids, failing_steps=self.failing_steps)

    async def reset(self):
        return None


class _FakeCollectorGraph:
    def __init__(self, seen_session_ids, failing_steps=None):
        self.card = Mock()
        self.card.id = "fake-collector-workflow"
        self._internal = _FakeCollectorInternal(seen_session_ids, failing_steps=failing_steps)


def _build_dependency_inner_session():
    inner_session = Mock()
    inner_session.callback_manager.return_value = Mock()
    inner_session.stream_writer_manager.return_value = None
    inner_session.config.return_value = Mock()
    inner_session.tracer.return_value = None
    inner_session.checkpointer.return_value = CheckpointerFactory.get_checkpointer()
    return inner_session


class TestSectionReasoningContextDefaults:
    """校验依赖推理状态的默认值初始化。"""

    @pytest.mark.asyncio
    async def test_reasoning_start_node_initializes_runtime_defaults(self):
        node = SectionReasoningStartNode()
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)

        result = await node.invoke(
            {
                "language": "zh-CN",
                "messages": [],
                "section_idx": "1",
                "config": {},
            },
            session,
            context,
        )

        assert result["section_idx"] == "1"
        state = session.update_global_state.call_args[0][0]["section_context"]
        assert state["collected_doc_num"] == 0
        assert state["warning_infos"] == []
        assert state["exception_infos"] == []


class TestDependencyInfoCollectorNode:
    """校验依赖信息收集节点的状态流转。"""

    def test_pre_handle_reads_collected_doc_num_for_follow_up_rounds(self):
        node = DependencyInfoCollectorNode()
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)
        current_plan = Plan(
            id="1-1",
            language="zh-CN",
            title="Test Plan",
            thought="thought",
            is_research_completed=False,
            steps=[],
        )
        session.get_global_state.side_effect = lambda key: {
            "section_context.section_idx": "1",
            "section_context.current_plan": current_plan,
            "section_context.added_completed_steps": [],
            "section_context.current_plan_is_completed": False,
            "section_context.language": "zh-CN",
            "section_context.messages": [],
            "section_context.history_plans": [],
            "section_context.collected_doc_num": 4,
            "section_context.warning_infos": [],
            "section_context.plan_background_knowledge": {},
            "section_context.step_background_knowledge": {},
            "config.info_collector_max_search_query_count": 2,
            "config.info_collector_max_research_loops": 2,
            "config.info_collector_max_tool_call_turns_per_query": 3,
        }.get(key)

        result = node._pre_handle({}, session, context)

        assert result["collected_doc_num"] == 4

    def test_update_section_state_marks_blocked_plan_and_avoids_none_math(self):
        node = DependencyInfoCollectorNode()
        node.log_prefix = "section_idx: 1 | plan_id: 1-1 | [DependencyInfoCollectorNode]"
        current_plan = Plan(
            id="1-1",
            language="zh-CN",
            title="Blocked plan",
            thought="Need prior step",
            is_research_completed=False,
            steps=[
                Step(
                    id="1-1-2",
                    title="Blocked Step",
                    description="Depends on missing step",
                    type=StepType.INFO_COLLECTING,
                    parent_ids=["1-1-1"],
                )
            ],
        )

        updated = node._update_section_state(
            {
                "plan_background_knowledge": {},
                "added_completed_steps": [],
                "current_plan": current_plan,
                "messages": [],
                "warning_infos": [],
                "history_plans": [],
                "collected_doc_num": None,
                "step_background_knowledge": {},
            },
            [],
            [],
        )

        assert updated["current_plan_is_completed"] is True
        assert updated["history_plans"] == [current_plan]
        assert updated["warning_infos"]
        assert "blocked tasks with unsatisfied parent steps" in updated["warning_infos"][0]

    def test_post_handle_flushes_warning_infos_and_collected_doc_num(self):
        node = DependencyInfoCollectorNode()
        node.log_prefix = "section_idx: 1 | plan_id: 1-1 | [DependencyInfoCollectorNode]"
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)

        result = node._post_handle(
            {},
            {
                "current_plan_is_completed": True,
                "plan_background_knowledge": {"1-1-1": "bg"},
                "history_plans": [],
                "warning_infos": ["warning"],
                "collected_doc_num": 3,
            },
            session,
            context,
        )

        assert result["next_node"] == NodeId.PLAN_REASONING.value
        update_calls = [call.args[0] for call in session.update_global_state.call_args_list]
        assert {"section_context.collected_doc_num": 3} in update_calls
        assert {"section_context.warning_infos": ["warning"]} in update_calls


class TestDependencyEditorTeamNode:
    """校验依赖图编排过程中的保护逻辑。"""

    def test_get_task_execute_sequence_returns_empty_for_cycle(self):
        node = DependencyEditorTeamNode()
        outline = Outline(
            id="outline-1",
            language="zh-CN",
            thought="cycle",
            title="Cyclic Outline",
            sections=[
                Section(
                    id="1",
                    title="Section 1",
                    description="Depends on 2",
                    parent_ids=["2"],
                    relationships=["dependency"],
                ),
                Section(
                    id="2",
                    title="Section 2",
                    description="Depends on 1",
                    parent_ids=["1"],
                    relationships=["dependency"],
                ),
            ],
        )

        assert node.get_task_execute_sequence(outline) == []

    @pytest.mark.asyncio
    async def test_do_invoke_ends_when_execution_sequence_is_empty(self):
        node = DependencyEditorTeamNode()
        session = Mock(spec=Session)
        context = Mock(spec=ModelContext)
        cyclic_outline = Outline(
            id="outline-1",
            language="zh-CN",
            thought="cycle",
            title="Cyclic Outline",
            sections=[
                Section(
                    id="1",
                    title="Section 1",
                    description="Depends on 2",
                    parent_ids=["2"],
                    relationships=["dependency"],
                ),
                Section(
                    id="2",
                    title="Section 2",
                    description="Depends on 1",
                    parent_ids=["1"],
                    relationships=["dependency"],
                ),
            ],
        )

        node._pre_handle = Mock(
            return_value={
                "language": "zh-CN",
                "messages": [],
                "outline": cyclic_outline,
                "history_outlines": [],
                "report_template": "",
                "history_reports": [],
                "session_id": "session-1",
                "config": {},
            }
        )
        node._handle_warning_exception_info = Mock()

        result = await node._do_invoke({}, session, context)

        assert result["next_node"] == NodeId.END.value
        node._handle_warning_exception_info.assert_called_once()


@pytest.mark.asyncio
async def test_dependency_info_collector_isolates_parallel_step_inputs_and_results():
    node = DependencyInfoCollectorNode()
    session = Mock(spec=Session)
    context = Mock(spec=ModelContext)
    current_plan = Plan(
        id="1-1",
        language="zh-CN",
        title="Parallel Plan",
        thought="Collect in parallel",
        is_research_completed=False,
        steps=[
            Step(id="1-1-1", title="Step A", description="Desc A", type=StepType.INFO_COLLECTING),
            Step(id="1-1-2", title="Step B", description="Desc B", type=StepType.INFO_COLLECTING),
            Step(id="1-1-3", title="Step C", description="Desc C", type=StepType.INFO_COLLECTING),
        ],
    )
    state_map = {
        "section_context.section_idx": "1",
        "section_context.current_plan": current_plan,
        "section_context.added_completed_steps": [],
        "section_context.current_plan_is_completed": False,
        "section_context.language": "zh-CN",
        "section_context.messages": [],
        "section_context.history_plans": [],
        "section_context.collected_doc_num": 0,
        "section_context.warning_infos": [],
        "section_context.plan_background_knowledge": {},
        "section_context.step_background_knowledge": {},
        "config.info_collector_max_search_query_count": 2,
        "config.info_collector_max_research_loops": 2,
        "config.info_collector_max_tool_call_turns_per_query": 3,
    }
    session.get_global_state.side_effect = lambda key: state_map.get(key)
    session.update_global_state = Mock()

    received_inputs = []

    async def fake_run_collector(collector_inputs, *_args):
        received_inputs.append(collector_inputs)
        await asyncio.sleep(0)
        step_title = collector_inputs["step_title"]
        return {
            "history_queries": [RetrievalQuery(query=f"query-{step_title}")],
            "doc_infos": [{"title": step_title}],
            "info_summary": f"summary-{step_title}",
            "evaluation": f"evaluation-{step_title}",
            "messages": collector_inputs["messages"],
        }

    node._run_dependency_collector_graph = fake_run_collector

    result = await node._do_invoke({}, session, context)

    assert result["next_node"] == NodeId.INFO_COLLECTOR.value
    assert [item["step_title"] for item in received_inputs] == ["Step A", "Step B", "Step C"]
    assert len({id(item) for item in received_inputs}) == 3
    assert current_plan.steps[0].retrieval_queries[0].query == "query-Step A"
    assert current_plan.steps[1].retrieval_queries[0].query == "query-Step B"
    assert current_plan.steps[2].retrieval_queries[0].query == "query-Step C"
    assert current_plan.steps[0].step_result == "summary-Step A"
    assert current_plan.steps[1].step_result == "summary-Step B"
    assert current_plan.steps[2].step_result == "summary-Step C"


class ExposedInfoCollectorNode(InfoCollectorNode):
    """Expose the normal collector node for a lightweight regression test."""

    async def do_invoke(self, *args, **kwargs):
        return await self._do_invoke(*args, **kwargs)


@pytest.mark.asyncio
async def test_normal_info_collector_stays_sequential():
    node = ExposedInfoCollectorNode()
    session = Mock(spec=Session)
    context = Mock(spec=ModelContext)
    current_plan = Plan(
        id="1",
        language="zh-CN",
        title="Sequential Plan",
        thought="thought",
        is_research_completed=False,
        steps=[
            Step(id="unused-1", title="Normal Step 1", description="Desc 1", type=StepType.INFO_COLLECTING),
            Step(id="unused-2", title="Normal Step 2", description="Desc 2", type=StepType.INFO_COLLECTING),
        ],
    )
    state_map = {
        "section_context.section_idx": "1",
        "section_context.current_plan": current_plan,
        "section_context.language": "zh-CN",
        "section_context.messages": [],
        "section_context.history_plans": [],
        "section_context.collected_doc_num": 0,
        "section_context.warning_infos": [],
        "config.info_collector_max_search_query_count": 2,
        "config.info_collector_max_research_loops": 2,
        "config.info_collector_max_tool_call_turns_per_query": 3,
    }
    session.get_global_state.side_effect = lambda key: state_map.get(key)
    session.update_global_state = Mock()

    seen_step_titles = []

    async def fake_run_collector(inputs, *_args, **_kwargs):
        step_title = inputs["step_title"]
        seen_step_titles.append(step_title)
        return {
            "history_queries": [RetrievalQuery(query=f"query-{step_title}")],
            "doc_infos": [{"title": step_title}],
            "info_summary": f"summary-{step_title}",
            "evaluation": f"evaluation-{step_title}",
        }

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.collector_execution_service."
        "run_info_collector_sub_graph",
        fake_run_collector,
    ):
        result = await node.do_invoke({}, session, context)

    assert result["next_node"] == NodeId.PLAN_REASONING.value
    assert seen_step_titles == ["Normal Step 1", "Normal Step 2"]
    assert current_plan.steps[0].step_result == "summary-Normal Step 1"
    assert current_plan.steps[1].step_result == "summary-Normal Step 2"


@pytest.mark.asyncio
async def test_normal_info_collector_keeps_previous_collected_doc_num_when_current_round_empty():
    node = ExposedInfoCollectorNode()
    session = Mock(spec=Session)
    context = Mock(spec=ModelContext)
    current_plan = Plan(
        id="1",
        language="zh-CN",
        title="Sequential Plan",
        thought="thought",
        is_research_completed=False,
        steps=[
            Step(id="1", title="Normal Step 1", description="Desc 1", type=StepType.INFO_COLLECTING),
        ],
    )
    state_map = {
        "section_context.section_idx": "1",
        "section_context.current_plan": current_plan,
        "section_context.language": "zh-CN",
        "section_context.messages": [],
        "section_context.history_plans": [],
        "section_context.collected_doc_num": 5,
        "section_context.warning_infos": [],
        "config.info_collector_max_search_query_count": 2,
        "config.info_collector_max_research_loops": 2,
        "config.info_collector_max_tool_call_turns_per_query": 3,
    }
    session.get_global_state.side_effect = lambda key: state_map.get(key)
    session.update_global_state = Mock()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes."
        "CollectorExecutionService.run_plan",
        new=AsyncMock(
            return_value=CollectorExecutionResult(
                collect_steps=current_plan.steps,
                collected_doc_num=0,
                messages=[],
            )
        ),
    ):
        result = await node.do_invoke({}, session, context)

    assert result["next_node"] == NodeId.PLAN_REASONING.value
    update_calls = [call.args[0] for call in session.update_global_state.call_args_list]
    assert {"section_context.collected_doc_num": 5} in update_calls


@pytest.mark.asyncio
async def test_dependency_isolated_collectors_use_unique_session_ids_without_warning(monkeypatch, caplog):
    node = DependencyInfoCollectorNode()
    session = Mock(spec=Session)
    session._inner = _build_dependency_inner_session()
    session.get_global_state.side_effect = lambda key: None
    context = Mock(spec=ModelContext)
    seen_session_ids = []
    checkpointer = session._inner.checkpointer.return_value

    monkeypatch.setattr(
        dependency_nodes,
        "build_info_collector_sub_graph",
        lambda: _FakeCollectorGraph(seen_session_ids),
    )

    with caplog.at_level(logging.WARNING):
        result_a, result_b = await asyncio.gather(
            node._run_dependency_collector_graph(
                {"step_title": "Step A", "messages": []},
                session,
                context,
            ),
            node._run_dependency_collector_graph(
                {"step_title": "Step B", "messages": []},
                session,
                context,
            ),
        )

    assert result_a["info_summary"] == "summary-Step A"
    assert result_b["info_summary"] == "summary-Step B"
    assert len(seen_session_ids) == 2
    assert len(set(seen_session_ids)) == 2
    assert not any("workflow_store of workflow" in record.getMessage() for record in caplog.records)

    for session_id in set(seen_session_ids):
        await checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_dependency_isolated_collectors_keep_original_exception_without_warning(monkeypatch, caplog):
    node = DependencyInfoCollectorNode()
    session = Mock(spec=Session)
    session._inner = _build_dependency_inner_session()
    session.get_global_state.side_effect = lambda key: None
    context = Mock(spec=ModelContext)
    seen_session_ids = []
    checkpointer = session._inner.checkpointer.return_value

    monkeypatch.setattr(
        dependency_nodes,
        "build_info_collector_sub_graph",
        lambda: _FakeCollectorGraph(seen_session_ids, failing_steps=["Step B"]),
    )

    with caplog.at_level(logging.WARNING):
        results = await asyncio.gather(
            node._run_dependency_collector_graph(
                {"step_title": "Step A", "messages": []},
                session,
                context,
            ),
            node._run_dependency_collector_graph(
                {"step_title": "Step B", "messages": []},
                session,
                context,
            ),
            return_exceptions=True,
        )

    assert results[0]["info_summary"] == "summary-Step A"
    assert isinstance(results[1], RuntimeError)
    assert str(results[1]) == "collector failure for Step B"
    assert len(seen_session_ids) == 2
    assert len(set(seen_session_ids)) == 2
    assert not any("workflow_store of workflow" in record.getMessage() for record in caplog.records)

    for session_id in set(seen_session_ids):
        await checkpointer.release(session_id)
