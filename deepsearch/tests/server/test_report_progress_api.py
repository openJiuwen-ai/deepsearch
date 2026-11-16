# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import create_async_engine

from server.core import report_progress_registry as registry_module
from server.core.report_progress_registry import ReportProgressRegistry
from server.routers import deepsearch_run as router
from server.schemas.deepsearch_run import DeepSearchRequest
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent
from tests.server.progress_test_utils import PREFIX, cancel_router_tasks, configure_progress_router, drain, start
from tests.workflow.test_report_progress import MiniAgent, WorkNode, configured


@pytest_asyncio.fixture
async def runtime(monkeypatch, configured):
    cp, config = configured
    registry = ReportProgressRegistry()
    monkeypatch.setattr(registry_module.settings, "checkpointer_type", "in_memory")
    configure_progress_router(monkeypatch, registry, checkpointer=cp)
    app = FastAPI()
    app.include_router(router.run_router, prefix=PREFIX)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield cp, config, client, registry
    await cancel_router_tasks()
    await registry.shutdown()


@pytest.mark.asyncio
async def test_http_progress_uses_actual_node_and_preserves_completion(runtime):
    cp, config, client, registry = runtime
    entered, release = asyncio.Event(), asyncio.Event()
    agent = MiniAgent(WorkNode(entered, release))
    _, response = start(agent, config)
    reader = asyncio.create_task(drain(response))
    await asyncio.wait_for(entered.wait(), 5)
    progress = await client.get(PREFIX + "/progress", params={"space_id": "s", "conversation_id": "report"})
    assert progress.status_code == 200
    assert set(progress.json()) == {"conversation_id", "node_id", "status", "updated_at",
                                    "collected_info_count", "total_sections", "completed_sections", "processing_sections"}
    assert progress.json()["node_id"] == "work"
    assert agent.get_progress("report").updated_at == progress.json()["updated_at"]
    for params, status in [
        ({"space_id": "wrong", "conversation_id": "report"}, 404),
        ({"space_id": "s", "conversation_id": "unknown"}, 404),
        ({"space_id": "s", "conversation_id": "../escape"}, 422),
        ({"space_id": "", "conversation_id": "report"}, 422),
        ({"conversation_id": "report"}, 422),
    ]:
        assert (await client.get(PREFIX + "/progress", params=params)).status_code == status
    release.set()
    messages = await asyncio.wait_for(reader, 5)
    assert messages[-1]["content"] == "ALL END"
    assert not any(row["event"] in {"error", "cancel", "stopped"} for row in messages)
    assert not await cp.session_exists("report")
    assert await registry.get_progress("s", "report") is None
    assert (await client.get(PREFIX + "/progress", params={"space_id": "s", "conversation_id": "report"})).status_code == 404


@pytest.mark.asyncio
async def test_original_http_cancel_still_interrupts_execution_and_cleans_checkpoint(runtime):
    cp, config, client, registry = runtime
    entered, release = asyncio.Event(), asyncio.Event()
    agent = MiniAgent(WorkNode(entered, release))
    request, response = start(agent, config)
    reader = asyncio.create_task(drain(response))
    await asyncio.wait_for(entered.wait(), 5)
    result = await client.post(PREFIX + "/", json={**request.model_dump(exclude_none=True), "interrupt_feedback": "cancel"})
    assert result.status_code == 200 and result.json()["status"] == "cancelling"
    messages = await asyncio.wait_for(reader, 5)
    assert [row["event"] for row in messages].count("cancel") == 1
    assert not await cp.session_exists("report")
    assert await registry.get_progress("s", "report") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "resume"])
async def test_waiting_report_remains_queryable_after_sdk_and_sse_end(runtime, action):
    cp, config, _, registry = runtime
    entered, release = asyncio.Event(), asyncio.Event()
    agent = MiniAgent(WorkNode(entered, release, interactive=True))
    _, response = start(agent, config)
    waiting = asyncio.Event()

    async def consume():
        try:
            async for row in response.body_iterator:
                if json.loads(row)["event"] == "waiting_user_input":
                    waiting.set()
                    await asyncio.Event().wait()
        finally:
            await response.body_iterator.aclose()

    reader = asyncio.create_task(consume())
    await asyncio.wait_for(waiting.wait(), 5)
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)
    snapshot = await registry.get_progress("s", "report")
    assert snapshot["status"] == "waiting_user_input"
    assert snapshot["node_id"] == "work"
    assert await cp.session_exists("report")
    if action == "cancel":
        await router._handle_remote_cancel("s", "report")
    else:
        _, continued = start(agent, config, feedback="accepted")
        next_reader = asyncio.create_task(drain(continued))
        await asyncio.wait_for(entered.wait(), 5)
        assert (await registry.get_progress("s", "report"))["status"] == "running"
        release.set()
        messages = await asyncio.wait_for(next_reader, 5)
        assert messages[-1]["content"] == "ALL END"
    assert await registry.get_progress("s", "report") is None
    assert not await cp.session_exists("report")


@pytest.mark.asyncio
async def test_query_has_no_dependency_on_agent_creation_or_credentials(runtime, monkeypatch):
    _, _, client, _ = runtime
    monkeypatch.setattr(router.agent_manager, "build_agent_config", lambda *a: pytest.fail("query built agent config"))
    result = await client.get(PREFIX + "/progress", params={"space_id": "s", "conversation_id": "unknown"})
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_redis_query_failure_is_503_without_changing_run_endpoint(runtime, monkeypatch):
    _, _, client, registry = runtime
    monkeypatch.setattr(registry_module.settings, "checkpointer_type", "redis")
    registry._client = AsyncMock()
    registry._client.hget.side_effect = RedisConnectionError("unavailable")
    result = await client.get(PREFIX + "/progress", params={"space_id": "s", "conversation_id": "report"})
    assert result.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["disconnect", "cancel"])
async def test_waiting_cleanup_disconnect_keeps_progress_but_business_cancel_removes_it(runtime, monkeypatch, action):
    cp, config, client, registry = runtime
    close_entered = asyncio.Event()
    close_release = asyncio.Event()
    waiting = asyncio.Event()
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_close(self):
        if not close_entered.is_set():
            close_entered.set()
            await close_release.wait()

    monkeypatch.setattr(DeepresearchAgent, '_aclose_local_search_engines', delayed_close)
    cid = 'review-' + uuid.uuid4().hex
    agent = MiniAgent(WorkNode(entered, release, interactive=True))
    request, response = start(agent, config, cid)
    producer = router._running_tasks['s:' + cid]

    async def consume():
        try:
            async for row in response.body_iterator:
                if json.loads(row).get('event') == 'waiting_user_input':
                    waiting.set()
                    await asyncio.Event().wait()
        finally:
            await response.body_iterator.aclose()

    reader = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(waiting.wait(), 5)
        await asyncio.wait_for(close_entered.wait(), 5)
        assert agent.get_progress(cid).status == 'waiting_user_input'
        if action == "cancel":
            result = await client.post(PREFIX + "/", json={
                **request.model_dump(exclude_none=True), "interrupt_feedback": "cancel",
            })
            assert result.status_code == 200
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        await asyncio.wait_for(asyncio.gather(producer, return_exceptions=True), 5)
        result = await client.get(PREFIX + "/progress", params={"space_id": "s", "conversation_id": cid})
        if action == "cancel":
            assert result.status_code == 404
            assert agent.get_progress(cid) is None
            assert not await cp.session_exists(cid)
        else:
            assert await cp.session_exists(cid)
            assert agent.get_progress(cid).status == "waiting_user_input"
            assert (await registry.get_progress("s", cid))["status"] == "waiting_user_input"
            assert result.status_code == 200
            release.set()
            _, resumed = start(agent, config, cid, feedback="accepted")
            messages = await asyncio.wait_for(drain(resumed), 5)
            assert messages[-1]["content"] == "ALL END"
            assert not await cp.session_exists(cid)
    finally:
        close_release.set()
        reader.cancel()
        producer.cancel()
        await asyncio.gather(reader, producer, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["local_cancel", "remote_cancel", "disconnect"])
async def test_cancel_during_round_preparation_finishes_actual_sse(runtime, monkeypatch, action):
    """轮次准备尚未结束时取消或断连，真实 SSE 响应和发布任务都能结束。"""
    _, config, client, registry = runtime
    monkeypatch.setattr(registry_module.settings, "checkpointer_type", "redis")
    registry._client = AsyncMock()
    allocation_entered = asyncio.Event()

    async def delayed_allocate(script, *args):
        if script == registry_module._ALLOCATE:
            allocation_entered.set()
            await asyncio.Event().wait()
        return 1

    registry._client.eval.side_effect = delayed_allocate
    entered, release, disconnect = (asyncio.Event() for _ in range(3))
    cid = "prepare-" + action
    request, response = start(MiniAgent(WorkNode(entered, release)), config, cid)
    producer = router._running_tasks["s:" + cid]
    registration = registry._entries["s:" + cid]
    sent = []

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    stream = asyncio.create_task(response({"type": "http", "asgi": {"version": "3.0"}}, receive, send))
    try:
        await asyncio.wait_for(allocation_entered.wait(), 5)
        if action == "local_cancel":
            result = await client.post(PREFIX + "/", json={
                **request.model_dump(exclude_none=True), "interrupt_feedback": "cancel",
            })
            assert result.status_code == 200
        elif action == "remote_cancel":
            await router._handle_remote_cancel("s", cid)
        else:
            disconnect.set()
        await asyncio.wait_for(stream, 5)
        await asyncio.wait_for(asyncio.gather(producer, return_exceptions=True), 5)
        assert not entered.is_set()
        assert registration.closed and registration.progress.snapshot() is None
        assert registration.task.done()
        assert "s:" + cid not in registry._entries
        if action != "disconnect":
            body = b"".join(message.get("body", b"") for message in sent)
            assert body.count(b'"event": "cancel"') == 1
            assert sent[-1]["type"] == "http.response.body"
            assert not sent[-1].get("more_body", False)
    finally:
        disconnect.set()
        producer.cancel()
        stream.cancel()
        await asyncio.gather(producer, stream, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unavailable", "timeout"])
async def test_round_preparation_failure_still_runs_original_report(runtime, monkeypatch, failure):
    """分配失败或超过原有两秒预算后，原报告继续执行并正常结束。"""
    cp, config, _, registry = runtime
    monkeypatch.setattr(registry_module.settings, "checkpointer_type", "redis")
    registry._client = AsyncMock()

    async def failed_allocate(script, *args):
        if script == registry_module._ALLOCATE:
            if failure == "timeout":
                await asyncio.Event().wait()
            raise RedisConnectionError("isolated allocation failure")
        return 1

    registry._client.eval.side_effect = failed_allocate
    entered, release = asyncio.Event(), asyncio.Event()
    agent = MiniAgent(WorkNode(entered, release))
    _, response = start(agent, config)
    reader = asyncio.create_task(drain(response))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert agent.get_progress("report").node_id == "work"
        release.set()
        messages = await asyncio.wait_for(reader, 5)
        assert messages[-1]["content"] == "ALL END"
        assert not any(row["event"] in {"cancel", "error"} for row in messages)
        assert not await cp.session_exists("report")
        assert not registry._entries
    finally:
        release.set()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["in_memory", "persistence"])
async def test_local_cancel_after_waiting_disconnect_removes_progress(runtime, monkeypatch, tmp_path, backend):
    """等待断连后通过真实本地取消路径释放 checkpoint、移除进度，并保留取消消费契约。"""
    cp, config, client, registry = runtime
    engine = None
    if backend == "persistence":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'checkpoint.db'}")
        cp = await CheckpointerFactory.create(CheckpointerConfig(
            type="persistence", conf={"db_type": "sqlite", "db_client": engine},
        ))
        monkeypatch.setattr(CheckpointerFactory, "_default_checkpointer", cp)
    monkeypatch.setattr(registry_module.settings, "checkpointer_type", backend)
    manager = router.agent_manager
    monkeypatch.setattr(manager, "cleanup_session_cache", type(manager).cleanup_session_cache.__get__(manager))
    entered, release, waiting = (asyncio.Event() for _ in range(3))
    sdk_cleanup = asyncio.Event()

    async def delayed_sdk_cleanup(self):
        sdk_cleanup.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(DeepresearchAgent, "_aclose_local_search_engines", delayed_sdk_cleanup)
    agent = MiniAgent(WorkNode(entered, release, interactive=True))
    request, response = start(agent, config)
    producer = router._running_tasks["s:report"]

    async def consume():
        try:
            async for row in response.body_iterator:
                if json.loads(row).get("event") == "waiting_user_input":
                    waiting.set()
                    await asyncio.Event().wait()
        finally:
            await response.body_iterator.aclose()

    reader = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(waiting.wait(), 5)
        # SQLite 写入 checkpoint 是异步的，先让本轮 SDK 完成保存再模拟等待期间断连。
        await asyncio.wait_for(sdk_cleanup.wait(), 5)
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        await asyncio.wait_for(asyncio.gather(producer, return_exceptions=True), 5)
        assert await cp.session_exists("report")
        assert (await registry.get_progress("s", "report"))["status"] == "waiting_user_input"
        for _ in range(2):
            result = await client.post(PREFIX + "/", json={
                **request.model_dump(exclude_none=True), "interrupt_feedback": "cancel",
            })
            assert result.status_code == 200 and result.json()["status"] == "cancelling"
            assert not await cp.session_exists("report")
            assert (await client.get(PREFIX + "/progress", params={
                "space_id": "s", "conversation_id": "report",
            })).status_code == 404
            assert agent.get_progress("report") is None
            assert router._cancel_events["s:report"].is_set()
        _, followup = start(agent, config, feedback="accepted")
        assert [row["event"] for row in await drain(followup)] == ["cancel"]
        assert "s:report" not in router._cancel_events
        assert await registry.get_progress("s", "report") is None
    finally:
        reader.cancel()
        producer.cancel()
        await asyncio.gather(reader, producer, return_exceptions=True)
        if engine is not None:
            await engine.dispose()


@pytest.mark.asyncio
async def test_waiting_cancel_cleanup_does_not_remove_new_progress_round(runtime, monkeypatch):
    """取消的异步清理只能关闭原轮次，不能移除期间登记的新轮次。"""
    _, _, _, registry = runtime
    original = registry.register("s", "report")
    router._cancel_events["s:report"] = asyncio.Event()
    cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event()

    async def delayed_cleanup(*args):
        cleanup_entered.set()
        await cleanup_release.wait()

    monkeypatch.setattr(router.agent_manager, "cleanup_session_cache", delayed_cleanup)
    request = DeepSearchRequest(space_id="s", conversation_id="report", message="", interrupt_feedback="cancel")
    cancelling = asyncio.create_task(router._handle_cancel_request(request))
    try:
        await asyncio.wait_for(cleanup_entered.wait(), 5)
        current = registry.register("s", "report")
        current.progress.enter_node("outline")
        cleanup_release.set()
        await asyncio.wait_for(cancelling, 5)
        assert original.progress.snapshot() is None
        assert (await registry.get_progress("s", "report"))["node_id"] == "outline"
        assert not current.closed
    finally:
        cleanup_release.set()
        await asyncio.gather(cancelling, return_exceptions=True)
