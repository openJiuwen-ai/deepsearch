# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import inspect
import json
import logging
import time
import uuid
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.workflow import Workflow, WorkflowCard
from openjiuwen.core.workflow.components.flow.start_comp import Start

from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import EndNode
from openjiuwen_deepsearch.framework.openjiuwen.agent import report_progress as progress_module
from openjiuwen_deepsearch.framework.openjiuwen.agent.report_progress import (
    ReportProgressMixin, ResearchProgress, observe_report_progress, record_main_node,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    local_search_context, report_progress_context, web_search_context,
)
from tests.utils.mock_config import get_default_agent_config


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="需要操作系统支持切换进程本地时区")
@pytest.mark.parametrize("zone, offset_hours", [("Asia/Shanghai", 8), ("UTC", 0)])
def test_progress_update_times_match_business_log_timezone(monkeypatch, zone, offset_hours):
    """初始化、节点和状态时间与日志本地时区一致，汇总和重复节点不更新时间。"""
    instants = [datetime(2026, 10, 23, 2, 0, second, tzinfo=timezone.utc) for second in range(4)]
    clock = SimpleNamespace(now=Mock(side_effect=instants))
    try:
        with monkeypatch.context() as scoped:
            scoped.setenv("TZ", zone)
            time.tzset()
            scoped.setattr(progress_module, "datetime", clock)
            progress = ResearchProgress("clock")
            snapshots = [progress.snapshot()]
            progress.enter_node("work")
            snapshots.append(progress.snapshot())
            progress.enter_node("work")
            progress.update_summary("set_outline", {"title": "报告", "sections": []})
            assert progress.snapshot().updated_at == snapshots[-1].updated_at
            assert clock.now.call_count == 2
            progress.observe('{"event":"waiting_user_input"}')
            snapshots.append(progress.snapshot())
            progress.mark_cancelling()
            snapshots.append(progress.snapshot())
            formatter = logging.Formatter()
            for snapshot, instant in zip(snapshots, instants):
                actual = datetime.fromisoformat(snapshot.updated_at)
                assert actual.utcoffset() == timedelta(hours=offset_hours)
                assert actual.timestamp() == instant.timestamp()
                record = logging.LogRecord("clock", logging.INFO, __file__, 0, "", (), None)
                record.created, record.msecs = instant.timestamp(), 0
                assert actual.strftime("%Y-%m-%d %H:%M:%S") == formatter.formatTime(record).split(",")[0]
            assert clock.now.call_count == 4
    finally:
        # monkeypatch 恢复 TZ 环境变量后，还需刷新 C 库的时区状态，避免影响其他测试。
        time.tzset()


def test_snapshots_are_immutable_and_only_main_nodes_are_recorded():
    progress = ResearchProgress("a", "main")
    token = report_progress_context.set(progress)
    try:
        assert progress.snapshot().node_id is None
        for workflow_id, node_id in [("main", "editor_team"), ("chapter", "collector")]:
            record_main_node(SimpleNamespace(
                get_workflow_id=lambda: workflow_id, get_component_id=lambda: node_id,
            ))
        snapshot = progress.snapshot()
        assert snapshot.node_id == "editor_team"
        progress.enter_node("editor_team")
        assert progress.snapshot().updated_at == snapshot.updated_at
        with pytest.raises(FrozenInstanceError):
            snapshot.status = "waiting_user_input"
        record_main_node(object())  # 观察失败不阻断节点执行。
        progress.mark_cancelling()
        progress.observe('{"event":[]}')
        progress.observe('[]')
        progress.observe('not JSON')
        progress.observe('{"event":"waiting_user_input"}')
        assert progress.snapshot().status == "cancelling"
    finally:
        report_progress_context.reset(token)
    progress.finish()
    assert progress.snapshot() is None


class StubAgent(ReportProgressMixin):
    def __init__(self):
        self._init_report_progress()
        self.research_name = "main"

    @observe_report_progress
    async def run(self, conversation_id, entered, release, payload="message"):
        entered.set()
        await release.wait()
        yield payload


@pytest.mark.asyncio
async def test_sdk_observation_preserves_signature_and_forwards_exact_output():
    agent = StubAgent()
    entered, release = asyncio.Event(), asyncio.Event()
    stream = agent.run("a", entered, release)
    assert agent.get_progress("a") is None
    async def consume():
        return [chunk async for chunk in stream]

    task = asyncio.create_task(consume())
    await entered.wait()
    assert agent.get_progress("a").status == "running"
    assert inspect.signature(StubAgent.run) == inspect.signature(StubAgent.run.__wrapped__)
    release.set()
    assert await task == ["message"]
    assert agent.get_progress("a") is None


@pytest.mark.asyncio
async def test_old_iteration_cleanup_does_not_remove_new_iteration_or_other_report():
    agent = StubAgent()
    old_entered, new_entered, other_entered = (asyncio.Event() for _ in range(3))
    old_release, new_release, other_release = (asyncio.Event() for _ in range(3))

    async def consume(cid, entered, release):
        return [chunk async for chunk in agent.run(cid, entered, release)]

    old = asyncio.create_task(consume("a", old_entered, old_release))
    await old_entered.wait()
    new = asyncio.create_task(consume("a", new_entered, new_release))
    other = asyncio.create_task(consume("b", other_entered, other_release))
    await new_entered.wait()
    await other_entered.wait()
    old.cancel()
    with pytest.raises(asyncio.CancelledError):
        await old
    assert agent.get_progress("a") is not None
    assert agent.get_progress("b") is not None
    new_release.set()
    other_release.set()
    await asyncio.gather(new, other)
    assert agent.get_progress("a") is None
    assert agent.get_progress("b") is None


@pytest.fixture
def configured(monkeypatch):
    cp = InMemoryCheckpointer()
    monkeypatch.setattr(CheckpointerFactory, "_default_checkpointer", cp)
    monkeypatch.setattr(CheckpointerFactory, "_type_checkpointers", {})
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.create_llm_obj", lambda *a, **k: object(),
    )
    monkeypatch.setattr(
        DeepresearchAgent, "_initialize_tools",
        lambda self, config: (web_search_context.set({}), local_search_context.set({})),
    )
    monkeypatch.setattr(DeepresearchAgent, "_aopen_local_search_engines", AsyncMock())
    monkeypatch.setattr(DeepresearchAgent, "_aclose_local_search_engines", AsyncMock())
    return cp, get_default_agent_config()


class WorkNode(BaseNode):
    def __init__(self, entered, release, *, interactive=False, fail=False):
        super().__init__()
        self.entered, self.release = entered, release
        self.interactive, self.fail = interactive, fail

    async def _do_invoke(self, inputs, session, context):
        if self.interactive:
            await session.interact("Confirm outline")
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("research failed")
        return {}


class MiniAgent(DeepresearchAgent):
    def __init__(self, node):
        self.node = node
        super().__init__()

    def _get_default_research_name(self):
        return "progress_test_" + uuid.uuid4().hex

    def _build_research_workflow(self):
        flow = Workflow(card=WorkflowCard(id=self.research_name, name=self.research_name, version="1"))
        flow.set_start_comp("start", Start(), inputs_schema=self.startnode_input_schema)
        flow.add_workflow_comp("work", self.node)
        flow.set_end_comp("end", EndNode())
        flow.add_connection("start", "work")
        flow.add_connection("work", "end")
        return flow


async def collect(agent, config, cid, **kwargs):
    return [json.loads(chunk) async for chunk in agent.run(
        message="hello", conversation_id=cid, agent_config=config, **kwargs,
    )]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_real_graph_progress_and_original_completion_cleanup(configured, fail):
    cp, config = configured
    entered, release = asyncio.Event(), asyncio.Event()
    agent = MiniAgent(WorkNode(entered, release, fail=fail))
    task = asyncio.create_task(collect(agent, config, "report"))
    await asyncio.wait_for(entered.wait(), 5)
    assert agent.get_progress("report").node_id == "work"
    assert agent.get_progress("unknown") is None
    release.set()
    messages = await asyncio.wait_for(task, 5)
    assert any(item["event"] == "error" for item in messages) == fail
    assert messages[-1]["content"] == "ALL END"
    assert agent.get_progress("report") is None
    assert not await cp.session_exists("report")
    assert report_progress_context.get() is None


@pytest.mark.asyncio
async def test_real_graph_waiting_and_resume_keep_original_checkpoint_contract(configured):
    cp, config = configured
    entered, release = asyncio.Event(), asyncio.Event()
    agent = MiniAgent(WorkNode(entered, release, interactive=True))
    first = await asyncio.wait_for(collect(agent, config, "interactive"), 5)
    assert any(item["event"] == "waiting_user_input" for item in first)
    assert agent.get_progress("interactive").status == "waiting_user_input"
    assert await cp.session_exists("interactive")
    task = asyncio.create_task(collect(agent, config, "interactive", interrupt_feedback="accepted"))
    await asyncio.wait_for(entered.wait(), 5)
    assert agent.get_progress("interactive").status == "running"
    release.set()
    await asyncio.wait_for(task, 5)
    assert agent.get_progress("interactive") is None
    assert not await cp.session_exists("interactive")
