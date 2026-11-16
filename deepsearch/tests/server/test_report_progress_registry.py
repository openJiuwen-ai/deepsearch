# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""使用独立 Redis 实例验证共享进度，不连接现有业务 Redis。"""

import asyncio
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from redis import Redis as SyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from server.core import cancel_bus, report_progress_registry as module
from server.core.report_progress_registry import ProgressStorageUnavailable, ReportProgressRegistry
from server.routers import deepsearch_run as router
from tests.server.progress_test_utils import PREFIX, cancel_router_tasks, configure_progress_router, drain, start
from tests.workflow.test_report_progress import MiniAgent, WorkNode, configured
from tests.workflow.test_progress_summary import records


@pytest.fixture(scope="module")
def isolated_redis(tmp_path_factory):
    executable = os.environ.get("DEEPSEARCH_TEST_REDIS_SERVER") or shutil.which("redis-server")
    if not executable:
        pytest.skip("需要 redis-server 或 DEEPSEARCH_TEST_REDIS_SERVER，测试不会连接现有 Redis")
    directory = tmp_path_factory.mktemp("progress-redis")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (directory / "redis.log").open("w") as log:
        process = subprocess.Popen(
            [executable, "--bind", "127.0.0.1", "--port", str(port), "--save", "",
             "--appendonly", "no", "--dir", str(directory)], stdout=log, stderr=subprocess.STDOUT,
        )
        client = SyncRedis(host="127.0.0.1", port=port, socket_timeout=1)
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    client.ping()
                    break
                except RedisConnectionError:
                    assert process.poll() is None and time.monotonic() < deadline
                    time.sleep(0.02)
            yield f"redis://127.0.0.1:{port}/0"
        finally:
            client.close()
            process.terminate()
            process.wait(timeout=5)


@pytest_asyncio.fixture
async def registries(monkeypatch, isolated_redis):
    monkeypatch.setattr(module.settings, "checkpointer_type", "redis")
    monkeypatch.setattr(module.settings, "redis_url", isolated_redis)
    monkeypatch.setattr(module.settings, "redis_cluster_mode", False)
    first = ReportProgressRegistry(ttl_seconds=2, heartbeat_seconds=0.02)
    second = ReportProgressRegistry(ttl_seconds=2, heartbeat_seconds=0.02)
    try:
        yield first, second
    finally:
        await first.shutdown()
        await second.shutdown()


async def wait_snapshot(registry, cid, *, node=None):
    async with asyncio.timeout(5):
        while True:
            snapshot = await registry.get_progress("s", cid)
            if snapshot is not None and (node is None or snapshot["node_id"] == node):
                return snapshot
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_other_worker_reads_scoped_progress_without_checkpoint_or_control_fields(registries):
    first, second = registries
    cid = uuid.uuid4().hex
    a, b = first.register("s", cid), first.register("s", cid + "b")
    a.progress.enter_node("editor_team")
    a.progress.update_summary("restore_main", records())
    a.progress.update_summary("start_section", "2")
    b.progress.enter_node("brief_reporter")
    snapshot = await wait_snapshot(second, cid, node="editor_team")
    assert set(snapshot) == {"conversation_id", "status", "node_id", "updated_at",
                             "collected_info_count", "total_sections", "completed_sections", "processing_sections"}
    assert snapshot["collected_info_count"] == 2
    assert snapshot["total_sections"] == 2
    assert snapshot["completed_sections"] == 1
    assert snapshot["processing_sections"] == [{"section_id": "2", "title": "技术"}]
    assert await second.get_progress("other", cid) is None
    assert set(await first._redis().hkeys(a.redis_key)) == {"owner", "snapshot"}
    assert (await wait_snapshot(second, cid + "b"))["node_id"] == "brief_reporter"
    a.progress.observe('{"event":"waiting_user_input"}')
    async with asyncio.timeout(5):
        while (await second.get_progress("s", cid))["status"] != "waiting_user_input":
            await asyncio.sleep(0.01)
    await first.close(a)
    assert await second.get_progress("s", cid) is None
    assert await second.get_progress("s", cid + "b") is not None


@pytest.mark.asyncio
async def test_unchanged_snapshots_only_renew_and_changed_counts_publish(registries):
    first, second = registries
    client = first._redis()
    original_eval = client.eval
    calls = []

    async def observe(script, *args):
        calls.append((script, args))
        return await original_eval(script, *args)

    client.eval = observe
    cid = uuid.uuid4().hex
    registration = first.register("s", cid)
    await wait_snapshot(second, cid)
    async with asyncio.timeout(5):
        while not any(script == module._RENEW for script, _ in calls):
            await asyncio.sleep(0.02)
    assert sum(script == module._REGISTER for script, _ in calls) == 1
    assert not any(script == module._REFRESH for script, _ in calls)
    renew_args = next(args for script, args in calls if script == module._RENEW)
    assert len(renew_args) == 9  # 四个同槽 key 加 owner、epoch、version、TTL；没有快照。
    registration.progress.update_summary("restore_main", records())
    async with asyncio.timeout(5):
        while (await second.get_progress("s", cid))["collected_info_count"] != 2:
            await asyncio.sleep(0.02)
    assert any(script == module._REFRESH for script, _ in calls)


@pytest.mark.asyncio
async def test_old_four_field_record_remains_readable(registries):
    first, second = registries
    cid = uuid.uuid4().hex
    snapshot = {"conversation_id": cid, "status": "running", "node_id": "outline", "updated_at": "now"}
    key = first._key("s", cid)
    await first._redis().hset(key, mapping={"owner": "old", "snapshot": json.dumps(snapshot)})
    await first._redis().expire(key, 2)
    result = await second.get_progress("s", cid)
    assert result["node_id"] == "outline"
    assert result["total_sections"] is None
    assert result["collected_info_count"] == result["completed_sections"] == 0
    assert result["processing_sections"] == []


@pytest.mark.asyncio
async def test_previous_iteration_cannot_overwrite_or_delete_current_projection(registries):
    first, second = registries
    cid = uuid.uuid4().hex
    old = first.register("s", cid)
    old.progress.enter_node("outline_interaction")
    await wait_snapshot(second, cid)
    current = second.register("s", cid)
    current.progress.enter_node("editor_team")
    await wait_snapshot(first, cid, node="editor_team")
    async with asyncio.timeout(5):
        while not old.closed:
            await asyncio.sleep(0.01)
    # 停止旧发布不取消执行，也不结束 SDK 本地观察。
    assert old.progress.snapshot() is not None
    await first.close(old)
    assert (await first.get_progress("s", cid))["node_id"] == "editor_team"
    assert await second._redis().hget(current.redis_key, "owner") == current.token


@pytest.mark.asyncio
async def test_abnormal_publisher_exit_expires_and_queries_do_not_renew(registries):
    first, second = registries
    cid = uuid.uuid4().hex
    registration = first.register("s", cid)
    await wait_snapshot(second, cid)
    registration.task.cancel()  # 模拟执行进程不再续期，保留 Redis 记录。
    await asyncio.gather(registration.task, return_exceptions=True)
    before = await second._redis().pttl(registration.redis_key)
    await asyncio.sleep(0.08)
    for _ in range(4):
        assert await second.get_progress("s", cid) is not None
    assert await second._redis().pttl(registration.redis_key) < before
    async with asyncio.timeout(5):
        while await second.get_progress("s", cid) is not None:
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_expired_projection_is_not_recreated_by_old_publisher(registries):
    first, second = registries
    cid = uuid.uuid4().hex
    registration = first.register("s", cid)
    await wait_snapshot(second, cid)
    await second._redis().delete(registration.redis_key)
    async with asyncio.timeout(5):
        while not registration.closed:
            await asyncio.sleep(0.01)
    assert await second.get_progress("s", cid) is None
    assert registration.progress.snapshot() is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["invalid JSON", '{"status":"stopping"}', "[]"])
async def test_invalid_shared_snapshots_are_storage_errors(registries, value):
    first, second = registries
    cid = uuid.uuid4().hex
    key = first._key("s", cid)
    await first._redis().hset(key, mapping={"owner": "test", "snapshot": value})
    await first._redis().expire(key, 2)
    with pytest.raises(ProgressStorageUnavailable):
        await second.get_progress("s", cid)


@pytest.mark.asyncio
async def test_http_worker_b_queries_real_graph_executed_by_worker_a(registries, configured, monkeypatch):
    first, second = registries
    cp, config = configured
    configure_progress_router(monkeypatch, first)
    # 隔离 B 的路由模块和本地字典；保留进程中的原取消总线回调。
    monkeypatch.setattr(cancel_bus, "register_cancel_handler", lambda callback: None)
    name = "server.routers._progress_worker_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, router.__file__)
    worker_b = importlib.util.module_from_spec(spec)
    sys.modules[name] = worker_b
    try:
        spec.loader.exec_module(worker_b)
        worker_b.report_progress_registry = second
        app = FastAPI()
        app.include_router(worker_b.run_router, prefix=PREFIX)
        cid = uuid.uuid4().hex
        entered, release = asyncio.Event(), asyncio.Event()
        agent = MiniAgent(WorkNode(entered, release))
        _, response = start(agent, config, cid)
        reader = asyncio.create_task(drain(response))
        await asyncio.wait_for(entered.wait(), 5)
        await wait_snapshot(second, cid, node="work")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker-b") as client:
            result = await client.get(PREFIX + "/progress", params={"space_id": "s", "conversation_id": cid})
            assert result.status_code == 200 and result.json()["node_id"] == "work"
            assert not worker_b._running_tasks and not second._entries
            release.set()
            output = await asyncio.wait_for(reader, 5)
            assert output[-1]["content"] == "ALL END"
            assert not await cp.session_exists(cid)
            assert (await client.get(PREFIX + "/progress", params={"space_id": "s", "conversation_id": cid})).status_code == 404
    finally:
        await cancel_router_tasks()
        sys.modules.pop(name, None)


@pytest.mark.asyncio
async def test_publication_failure_does_not_block_original_report(configured, monkeypatch):
    _, config = configured
    registry = ReportProgressRegistry(heartbeat_seconds=0.01)
    monkeypatch.setattr(module.settings, "checkpointer_type", "redis")
    registry._client = AsyncMock()
    registry._client.eval.side_effect = RedisConnectionError("unavailable")
    configure_progress_router(monkeypatch, registry)
    entered, release = asyncio.Event(), asyncio.Event()
    agent = MiniAgent(WorkNode(entered, release))
    _, response = start(agent, config, uuid.uuid4().hex)
    reader = asyncio.create_task(drain(response))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        output = await asyncio.wait_for(reader, 5)
        assert output[-1]["content"] == "ALL END"
        assert not any(row["event"] in {"error", "cancel", "stopped"} for row in output)
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        await registry.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_reply", [False, True])
@pytest.mark.parametrize("new_round_state", [
    "active", "closed", "expired", "new_epoch", "missing_watermark", "reallocated_old_token",
])
async def test_delayed_old_initial_publish_cannot_replace_or_resurrect_new_round(
    registries, monkeypatch, lost_reply, new_round_state,
):
    """真实 Redis 验证旧首次发布及响应丢失重试不能覆盖新轮次或复活。"""
    first, second = registries
    cid = uuid.uuid4().hex
    client = first._redis()
    original_eval = client.eval
    blocked, release = asyncio.Event(), asyncio.Event()
    register_calls = 0

    async def delayed_eval(script, *args):
        nonlocal register_calls
        if script == module._REGISTER:
            register_calls += 1
            if lost_reply and register_calls == 1:
                await original_eval(script, *args)
                raise RedisConnectionError("lost reply after applying REGISTER")
            blocked.set()
            await release.wait()
        return await original_eval(script, *args)

    monkeypatch.setattr(client, "eval", delayed_eval)
    old = first.register("s", cid)
    old.progress.enter_node("outline_interaction")
    try:
        await asyncio.wait_for(blocked.wait(), 5)
        current = second.register("s", cid)
        current.progress.enter_node("editor_team")
        await wait_snapshot(second, cid, node="editor_team")
        assert int(current.version) > int(old.version)
        assert current.epoch == old.epoch
        if new_round_state == "closed":
            await second.close(current)
        elif new_round_state == "missing_watermark":
            await second._redis().delete(current.redis_keys[1])
        elif new_round_state in {"expired", "new_epoch", "reallocated_old_token"}:
            current.task.cancel()
            await asyncio.gather(current.task, return_exceptions=True)
            async with asyncio.timeout(5):
                while await second._redis().exists(*current.redis_keys, old.redis_keys[2]):
                    await asyncio.sleep(0.02)
            if new_round_state == "new_epoch":
                successor = second.register("s", cid)
                successor.progress.enter_node("reporter")
                await wait_snapshot(second, cid, node="reporter")
                assert successor.epoch != old.epoch
                assert successor.version == "1"
            elif new_round_state == "reallocated_old_token":
                # 模拟已超时分配命令的迟到副本，不能复用过期前的 epoch 和凭据。
                lease = await second._redis().eval(
                    module._ALLOCATE, 2, old.redis_keys[3], old.redis_keys[2], old.token, first.ttl_seconds,
                )
                assert lease.split(":", 1)[0] != old.epoch
        release.set()
        async with asyncio.timeout(5):
            while not old.closed:
                await asyncio.sleep(0.02)
        snapshot = await second.get_progress("s", cid)
        if new_round_state == "active":
            assert snapshot["node_id"] == "editor_team"
            assert not current.closed
            current.progress.enter_node("reporter")
            await wait_snapshot(first, cid, node="reporter")
        elif new_round_state == "new_epoch":
            assert snapshot["node_id"] == "reporter"
            assert not successor.closed
        elif new_round_state == "missing_watermark":
            assert snapshot is None or snapshot["node_id"] == "editor_team"
        else:
            assert snapshot is None
        # 旧发布器退出只清理自己的观察凭据，不结束本地业务观察。
        assert old.progress.snapshot() is not None
    finally:
        release.set()


@pytest.mark.asyncio
async def test_round_allocation_response_loss_does_not_retry_or_block_execution(registries, monkeypatch):
    first, second = registries
    cid = uuid.uuid4().hex
    client = first._redis()
    original_eval = client.eval
    allocations = 0

    async def lost_reply(script, *args):
        nonlocal allocations
        result = await original_eval(script, *args)
        if script == module._ALLOCATE:
            allocations += 1
            raise RedisConnectionError("lost allocation reply")
        return result

    monkeypatch.setattr(client, "eval", lost_reply)
    old = first.register("s", cid)
    await asyncio.wait_for(first.prepare(old), 5)
    assert old.closed and old.progress.snapshot() is not None
    assert allocations == 1
    current = second.register("s", cid)
    current.progress.enter_node("editor_team")
    await wait_snapshot(second, cid, node="editor_team")
    assert await first._redis().get(old.redis_keys[2]) is not None
    await first.close(old)
    assert (await second.get_progress("s", cid))["node_id"] == "editor_team"


@pytest.mark.asyncio
@pytest.mark.parametrize("applied", [False, True])
async def test_initial_publish_retry_reuses_the_preallocated_round(registries, monkeypatch, applied):
    first, second = registries
    cid = uuid.uuid4().hex
    client = first._redis()
    original_eval = client.eval
    allocations, publications = 0, 0

    async def fail_first_publish(script, *args):
        nonlocal allocations, publications
        if script == module._ALLOCATE:
            allocations += 1
        if script == module._REGISTER:
            publications += 1
            if publications == 1:
                if applied:
                    await original_eval(script, *args)
                raise RedisConnectionError("initial publish unavailable")
        return await original_eval(script, *args)

    monkeypatch.setattr(client, "eval", fail_first_publish)
    registration = first.register("s", cid)
    registration.progress.enter_node("editor_team")
    async with asyncio.timeout(5):
        while publications < 2:
            await asyncio.sleep(.02)
    await wait_snapshot(second, cid, node="editor_team")
    assert allocations == 1 and registration.version == "1"
    assert not registration.closed
    assert await second._redis().hget(registration.redis_key, "owner") == registration.token


@pytest.mark.asyncio
async def test_round_preparation_timeout_releases_waiter_without_retry(registries, monkeypatch):
    first, _ = registries
    entered = asyncio.Event()
    allocations = 0

    async def blocked_allocation(script, *args):
        nonlocal allocations
        assert script == module._ALLOCATE
        allocations += 1
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(first._redis(), "eval", blocked_allocation)
    registration = first.register("s", uuid.uuid4().hex)
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(first.prepare(registration), 3)
    assert registration.closed
    assert registration.progress.snapshot() is not None
    assert allocations == 1
