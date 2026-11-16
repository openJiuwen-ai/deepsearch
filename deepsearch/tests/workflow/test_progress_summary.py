# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""验证已有业务记录汇总、单章生命周期和跨实例交互恢复。"""

import asyncio
import json
import uuid
from dataclasses import FrozenInstanceError, asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.workflow import Workflow, WorkflowCard
from openjiuwen.core.workflow.components.flow.start_comp import Start

from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import InfoRetrievalNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph import graph_builder as collector_graph
from openjiuwen_deepsearch.framework.openjiuwen.agent.editor_team_manager_node import EditorTeamNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import EndNode, StartNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.progress_summary import ProgressSummary
from openjiuwen_deepsearch.framework.openjiuwen.agent.report_progress import ResearchProgress, record_progress_state
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Outline, Plan, RetrievalQuery, Section, Step, StepType, SubReportContent,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_reasoning_team_nodes import (
    DependencyInfoCollectorNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.section_context import SectionReasoningContext
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import report_progress_context
from tests.workflow.test_report_progress import MiniAgent, WorkNode, collect, configured


def records():
    return {
        "report_type_policy": {"report_type": "professional"},
        "current_outline": {"id": "outline-1", "title": "报告", "sections": [
            {"id": "1", "title": "市场", "plans": [{"id": "1-1", "steps": [
                {"id": "1", "retrieval_queries": [{"query": "市场", "doc_infos": [{}, {}]}]},
            ]}]},
            {"id": "2", "title": "技术", "plans": []},
        ]},
        "current_report": {"id": "report-1", "sub_reports": [
            {"section_id": "1", "section_task": "市场", "content": {"sub_report_content_text": "已有正文"}},
            {"section_id": "2", "section_task": "技术", "content": {"sub_report_content_text": ""}},
        ]},
    }


def test_absolute_record_counts_restore_and_repeated_updates_do_not_accumulate():
    state = records()
    summary = ProgressSummary()
    assert summary.snapshot() == (0, None, 0, ())
    for _ in range(3):
        summary.restore_main(state)
    assert summary.snapshot() == (2, 2, 1, ())
    collector = {"section_idx": "2", "plan_idx": "2-1", "step_idx": "1",
                 "history_queries": [{"doc_infos": [{}, {}, {}]}]}
    for _ in range(3):
        summary.record_collector(collector)
    assert summary.snapshot()[0] == 5
    # 同步骤替换为重试后的实际记录，不将两次结果额外相加。
    collector["history_queries"] = [{"doc_infos": [{}]}]
    summary.record_collector(collector)
    assert summary.snapshot()[0] == 3
    collector["history_queries"] = []
    summary.record_collector(collector)
    assert summary.snapshot()[0] == 2


def test_new_outline_discards_old_sections_results_and_completion():
    state = records()
    summary = ProgressSummary()
    summary.restore_main(state)
    summary.start_section("2")
    state["current_outline"] = {"id": "outline-2", "title": "报告", "sections": [
        {"id": "1", "title": "市场", "plans": []},
    ]}
    summary.restore_main(state)
    assert summary.snapshot() == (0, 1, 0, ())
    # 即使标题未变，旧报告也不能成为新大纲已完成章节。
    state["current_report"]["id"] = "report-2"
    summary.restore_main(state)
    assert summary.snapshot() == (0, 1, 1, ())


def test_brief_uses_existing_search_results_and_valid_chapters_not_citations():
    summary = ProgressSummary()
    state = {"report_type_policy": {"report_type": "brief"}, "brief_state": {
        "outline": {"title": "Brief", "sections": [{"id": "b1", "title": "章节一"},
                                                     {"id": "b2", "title": "章节二"}]},
        "collection_context": {"search_results": [{}, {}, {}]},
        "collection": {"citation_registry": [{}] * 10},
        "chapters": [{"section_id": "b1", "raw_markdown": "有效正文"},
                     {"section_id": "b2", "raw_markdown": " "}],
    }}
    for _ in range(2):
        summary.restore_main(state)
    assert summary.snapshot() == (3, 2, 1, ())
    state["brief_state"]["chapters"][1]["raw_markdown"] = None
    summary.restore_main(state)
    assert summary.snapshot() == (3, 2, 1, ())
    state["brief_state"]["collection_context"]["search_results"].append({})
    summary.restore_main(state)
    assert summary.snapshot()[0] == 4


def test_snapshot_is_deeply_immutable_bounded_and_keeps_timestamp_semantics():
    progress = ResearchProgress("c" * 128, "main")
    outline = {"title": "长报告", "sections": [
        {"id": str(i) + "🙂" * 150, "title": "🙂" * 1000} for i in range(20)
    ]}
    progress.update_summary("set_outline", outline)
    original_time = progress.snapshot().updated_at
    for section in outline["sections"]:
        progress.update_summary("start_section", section["id"])
    snapshot = progress.snapshot()
    assert snapshot.total_sections == 20
    assert len(snapshot.processing_sections) == 5
    assert snapshot.updated_at == original_time
    assert len(snapshot.processing_sections[0].title) == 160
    assert len(snapshot.processing_sections[0].section_id) == 128
    assert len(json.dumps(asdict(snapshot), ensure_ascii=False).encode()) < 8192
    with pytest.raises(FrozenInstanceError):
        snapshot.processing_sections[0].title = "changed"
    progress.update_summary("finish_section", outline["sections"][0]["id"], True)
    assert progress.snapshot().completed_sections == 1
    assert snapshot.completed_sections == 0


@pytest.mark.asyncio
async def test_actual_chapter_stream_counts_writing_only_and_clears_failure(monkeypatch):
    progress = ResearchProgress("report", "main")
    state = records()
    state["current_report"] = None
    progress.update_summary("restore_main", state)
    token = report_progress_context.set(progress)
    released, entered = asyncio.Event(), asyncio.Event()

    async def stream(*, workflow, inputs, stream_modes):
        entered.set()
        await released.wait()
        content = "" if workflow == "research" else "已完成正文"
        yield OutputSchema(type="workflow_final", index=0, payload={
            "sub_report_content": SubReportContent(sub_report_content_text=content),
        })

    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.editor_team_manager_node.Runner.run_workflow_streaming",
        stream,
    )
    node = EditorTeamNode()
    main = SimpleNamespace(write_custom_stream=AsyncMock())
    inputs = {"section_idx": "1", "current_outline": state["current_outline"]}
    try:
        task = asyncio.create_task(node._run_section_sub_graph_await(main, "research", inputs, reasoning_only=True))
        await entered.wait()
        assert progress.snapshot().processing_sections[0].section_id == "1"
        released.set()
        await task
        assert progress.snapshot().completed_sections == 0
        assert len(progress.snapshot().processing_sections) == 1
        await node._run_section_sub_graph_await(main, "writing", inputs)
        assert progress.snapshot().completed_sections == 1
        assert not progress.snapshot().processing_sections

        async def fail(**kwargs):
            raise RuntimeError("chapter failed")
            yield

        monkeypatch.setattr(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.editor_team_manager_node.Runner.run_workflow_streaming",
            fail,
        )
        with pytest.raises(RuntimeError, match="chapter failed"):
            await node._run_section_sub_graph_await(main, "writing", {**inputs, "section_idx": "2"})
        assert progress.snapshot().completed_sections == 1
        assert not progress.snapshot().processing_sections
    finally:
        report_progress_context.reset(token)


class SeedRecordsNode(BaseNode):
    async def _do_invoke(self, inputs, session, context):
        session.update_global_state({"search_context": records()})
        return {}


class SummaryAgent(MiniAgent):
    def __init__(self, node, name):
        self.name = name
        super().__init__(node)

    def _get_default_research_name(self):
        return self.name

    def _build_research_workflow(self):
        flow = Workflow(card=WorkflowCard(id=self.research_name, name=self.research_name, version="1"))
        flow.set_start_comp("start", Start(), inputs_schema=self.startnode_input_schema)
        flow.add_workflow_comp("seed", SeedRecordsNode())
        flow.add_workflow_comp("work", self.node)
        flow.set_end_comp("end", EndNode())
        flow.add_connection("start", "seed")
        flow.add_connection("seed", "work")
        flow.add_connection("work", "end")
        return flow


@pytest.mark.asyncio
async def test_new_agent_restores_summary_from_original_hitl_checkpoint(configured):
    cp, config = configured
    entered, release = asyncio.Event(), asyncio.Event()
    name = "summary_resume_" + uuid.uuid4().hex
    first = SummaryAgent(WorkNode(entered, release, interactive=True), name)
    messages = await asyncio.wait_for(collect(first, config, "summary-report"), 5)
    assert any(item["event"] == "waiting_user_input" for item in messages)
    assert first.get_progress("summary-report").collected_info_count == 2
    assert first.get_progress("summary-report").completed_sections == 1
    assert await cp.session_exists("summary-report")

    second = SummaryAgent(WorkNode(entered, release, interactive=True), name)
    task = asyncio.create_task(collect(second, config, "summary-report", interrupt_feedback="accepted"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        snapshot = second.get_progress("summary-report")
        assert snapshot.total_sections == 2
        assert snapshot.completed_sections == 1
        assert snapshot.collected_info_count == 2
        release.set()
        messages = await asyncio.wait_for(task, 5)
        assert messages[-1]["content"] == "ALL END"
        assert second.get_progress("summary-report") is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_summary_observation_errors_never_break_business_execution():
    progress = ResearchProgress("report", "main")
    token = report_progress_context.set(progress)
    try:
        bad = SimpleNamespace(get_workflow_id=lambda: "main", get_global_state=lambda key: 1 / 0)
        record_progress_state(bad)
        assert progress.snapshot().total_sections is None
    finally:
        report_progress_context.reset(token)


@pytest.mark.asyncio
async def test_real_collector_node_updates_summary_from_written_query_records(monkeypatch):
    progress = ResearchProgress("collector-report", "main")
    progress.update_summary("restore_main", records())
    collector = {
        "section_idx": "2", "plan_idx": "2-1", "step_idx": "1", "step_title": "技术资料",
        "search_queries": [RetrievalQuery(query="q1"), RetrievalQuery(query="q2")],
        "history_queries": [], "doc_infos": [], "source_store": {}, "evidence_ledger": {},
        "research_loop_count": 0, "max_research_loops": 2,
    }

    def get_state(key):
        if key == "collector_context":
            return collector
        if key.startswith("collector_context."):
            return collector.get(key.split(".", 1)[1])

    def update_state(values):
        for key, value in values.items():
            collector[key.split(".", 1)[1]] = value

    session = SimpleNamespace(
        get_workflow_id=lambda: "collector", get_component_id=lambda: "collector_info_retrieval",
        get_global_state=get_state, update_global_state=update_state,
    )
    node = InfoRetrievalNode()
    monkeypatch.setattr(node, "_pre_handle", lambda *args: {
        **collector, "max_tool_call_turns_per_query": 2, "api_tools_config": {},
    })

    async def search(state, query):
        return {"doc_infos": [{"title": query.query, "url": "https://example.com/" + query.query,
                                "source_id": query.query}], "source_store": {}, "search_query": query.query}

    monkeypatch.setattr(node, "_run_retrieval_query", search)
    token = report_progress_context.set(progress)
    try:
        await node.invoke({}, session, None)
        assert len(collector["history_queries"]) == 2
        assert progress.snapshot().collected_info_count == 4  # 第一章已有两条，加第二章两条。
        record_progress_state(session)
        assert progress.snapshot().collected_info_count == 4
        assert progress.snapshot().node_id is None  # 子图不覆盖主图位置。
    finally:
        report_progress_context.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_id, step_ids", [("1-1", ["1-1"]), ("1-2", ["1-2-1", "1-2-2"])])
async def test_dependency_collection_writeback_and_hitl_restore_count_once(configured, monkeypatch, plan_id, step_ids):
    """真实依赖并行采集、计划回写和交互恢复使用同一身份，其他计划和章节计数不丢失。"""
    cp, config = configured
    config["info_collector_webpage_enrich_enable"] = False

    async def llm(self, *args):
        if isinstance(self, collector_graph.GenerateQueryNode):
            return collector_graph.SearchQueryList(queries=["q"])
        if isinstance(self, collector_graph.SupervisorNode):
            return collector_graph.Reflection(is_sufficient=True, should_continue=False, knowledge_gap="", next_queries=[])
        return collector_graph.Summary(info_summary="已采集", evaluation="足够")

    for cls in (collector_graph.GenerateQueryNode, collector_graph.SupervisorNode, collector_graph.SummaryNode):
        monkeypatch.setattr(cls, "_invoke_llm_with_retry", llm)
    received_ids = []

    async def retrieval(self, state, query):
        received_ids.append((state["plan_idx"], state["step_idx"]))
        await asyncio.sleep(0)  # 让两个真实采集任务交错运行，检验 ContextVar 隔离。
        return {"search_query": query.query, "doc_infos": [
            {"title": "a", "url": "https://example.com/a", "source_id": "a", "content": "a"},
            {"title": "b", "url": "https://example.com/b", "source_id": "b", "content": "b"},
        ], "source_store": {}}

    monkeypatch.setattr(collector_graph.InfoRetrievalNode, "_run_retrieval_query", retrieval)
    entered, release = asyncio.Event(), asyncio.Event()
    observations = []
    prior = []
    if plan_id == "1-2":
        prior = [Plan(id="1-1", title="已有计划", thought="思路", is_research_completed=False, steps=[Step(
            id="1-1-1", type=StepType.INFO_COLLECTING, title="已有步骤", description="描述",
            retrieval_queries=[RetrievalQuery(query="old", doc_infos=[{}])],
        )])]
    expected = 3 + len(prior) + 2 * len(step_ids)

    class SeedDependencyRecords(BaseNode):
        async def _do_invoke(self, inputs, session, context):
            outline = Outline(id="dependency-outline", title="报告", thought="思路", sections=[
                Section(id="1", title="章节一", description="描述", plans=prior),
                Section(id="2", title="章节二", description="描述", plans=[Plan(
                    id="2-1", title="其他章节计划", thought="思路", is_research_completed=False, steps=[Step(
                        id="1", type=StepType.INFO_COLLECTING, title="步骤", description="描述",
                        retrieval_queries=[RetrievalQuery(query="other", doc_infos=[{}, {}, {}])],
                    )],
                )]),
            ])
            plan = Plan(id=plan_id, title="当前计划", thought="思路", is_research_completed=False, steps=[
                Step(id=sid, type=StepType.INFO_COLLECTING, title=sid, description="描述") for sid in step_ids
            ])
            section = SectionReasoningContext(section_idx="1", language="zh-CN", history_plans=prior).model_dump()
            section["current_plan"] = plan
            session.update_global_state({"search_context.current_outline": outline, "section_context": section})
            return {}

    class CommitDependencyRecords(BaseNode):
        async def _do_invoke(self, inputs, session, context):
            observations.append(agent.get_progress("dependency-count").collected_info_count)
            outline = session.get_global_state("search_context.current_outline")
            outline.sections[0].plans = session.get_global_state("section_context.history_plans")
            session.update_global_state({"search_context.current_outline": outline})
            return {}

    class DependencySummaryAgent(SummaryAgent):
        def _build_research_workflow(self):
            flow = Workflow(card=WorkflowCard(id=self.research_name, name=self.research_name, version="1"))
            flow.set_start_comp("start", StartNode(), inputs_schema=self.startnode_input_schema)
            flow.add_workflow_comp("seed", SeedDependencyRecords())
            flow.add_workflow_comp("collect", DependencyInfoCollectorNode())
            flow.add_workflow_comp("finalize", DependencyInfoCollectorNode())
            flow.add_workflow_comp("commit", CommitDependencyRecords())
            flow.add_workflow_comp("work", self.node)
            flow.set_end_comp("end", EndNode())
            for before, after in zip(
                ["start", "seed", "collect", "finalize", "commit", "work"],
                ["seed", "collect", "finalize", "commit", "work", "end"],
            ):
                flow.add_connection(before, after)
            return flow

    name = "dependency_summary_" + uuid.uuid4().hex
    agent = DependencySummaryAgent(WorkNode(entered, release, interactive=True), name)
    events = await asyncio.wait_for(collect(agent, config, "dependency-count"), 10)
    assert events[-1]["event"] == "waiting_user_input"
    assert observations == [expected]
    assert agent.get_progress("dependency-count").collected_info_count == expected
    assert sorted(received_ids) == sorted((plan_id.split("-")[-1], sid.split("-")[-1]) for sid in step_ids)
    assert await cp.session_exists("dependency-count")
    # 新 Agent 从同一 checkpoint 恢复，重复采集主图记录仍应得到相同值。
    resumed = DependencySummaryAgent(WorkNode(entered, release, interactive=True), name)
    task = asyncio.create_task(collect(resumed, config, "dependency-count", interrupt_feedback="accepted"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert resumed.get_progress("dependency-count").collected_info_count == expected
        release.set()
        events = await asyncio.wait_for(task, 5)
        assert events[-1]["content"] == "ALL END"
        assert not any(row["event"] == "error" for row in events)
        assert not await cp.session_exists("dependency-count")
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
