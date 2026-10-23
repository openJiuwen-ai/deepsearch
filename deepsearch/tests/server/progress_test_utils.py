# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""报告进度 HTTP 测试的公共环境与流消费工具。"""

import asyncio
import json
from unittest.mock import AsyncMock

from server.core import report_progress_registry as registry_module
from server.routers import deepsearch_run as router
from server.schemas.deepsearch_run import DeepSearchRequest

PREFIX = "/api/v1/agent/deepsearch/run"


def configure_progress_router(monkeypatch, registry, *, checkpointer=None):
    """隔离路由全局状态；按需将会话清理关联到测试 checkpointer。"""
    monkeypatch.setattr(registry_module, "report_progress_registry", registry)
    monkeypatch.setattr(router, "report_progress_registry", registry)
    for name in ("_running_tasks", "_cancel_events", "_cancel_event_timestamps",
                 "_running_agents", "_resume_requested_events"):
        monkeypatch.setattr(router, name, {})
    monkeypatch.setattr(router.LogManager, "new_run", lambda: None)

    async def cleanup(space_id, conversation_id):
        await checkpointer.release(conversation_id)

    monkeypatch.setattr(
        router.agent_manager, "cleanup_session_cache",
        AsyncMock(side_effect=cleanup if checkpointer is not None else None),
    )


async def cancel_router_tasks():
    """回收当前测试遗留的 producer，避免影响后续用例。"""
    tasks = list(router._running_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def start(agent, config, cid="report", *, feedback="", mode="research"):
    """通过原 HTTP producer/consumer 入口执行测试 Agent。"""
    request = DeepSearchRequest(
        space_id="s", conversation_id=cid, message="hello", interrupt_feedback=feedback, search_mode=mode,
    )
    response = router._create_streaming_response(request, agent, {
        "message": "hello", "conversation_id": cid, "agent_config": config, "interrupt_feedback": feedback,
    })
    return request, response


async def drain(response):
    """将原 SSE 响应消费为业务事件列表。"""
    return [json.loads(chunk) async for chunk in response.body_iterator]
