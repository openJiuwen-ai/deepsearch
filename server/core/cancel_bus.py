# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
跨进程取消总线（基于 Redis 的简单实现）

职责：
- 在 redis checkpointer 模式下，为 DeepSearch 提供跨 worker / 实例的取消分发能力；
- 不直接依赖具体业务路由，由业务方注册取消处理回调。

启用条件：
- 仅在 checkpointer_type == "redis" 时启用（由 server.core.config.settings.checkpointer_type 控制）；
- 需要配置有效的 redis_url（server.core.config.settings.redis_url）；
- 非 redis 模式下，相关函数会静默返回 None/False，不影响业务逻辑。

使用方式：
1. 业务侧（如 deepsearch_run）在模块加载时调用：
   register_cancel_handler(async def handler(space_id, conversation_id): ...)
2. 应用启动 / 关闭时分别调用：
   await start_cancel_listener()
   await stop_cancel_listener()
3. 业务侧在本进程未命中任务时调用：
   await publish_remote_cancel(space_id, conversation_id)

注意：
- in_memory / persistence 模式下不支持跨进程取消，只能取消本进程内的任务。
"""

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

from redis.asyncio import Redis

from server.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Redis | None = None
_cancel_listener_task: asyncio.Task | None = None
_CANCEL_CHANNEL = "deepsearch:cancel"

_cancel_handler: Optional[Callable[[str, str], Awaitable[None]]] = None


def register_cancel_handler(handler: Callable[[str, str], Awaitable[None]]):
    """
    注册跨进程取消回调，由业务侧提供。

    handler 接收两个参数：
        space_id: str
        conversation_id: str
    """
    global _cancel_handler
    _cancel_handler = handler
    logger.info("Registered cancel handler for deepsearch cancel bus.")


async def _get_redis_client() -> Redis | None:
    """
    初始化或返回 Redis 客户端，仅在 redis checkpointer 模式下启用。
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    cp_type = (settings.checkpointer_type or "").strip().lower()
    if cp_type != "redis":
        # 非 redis 模式下不启用跨进程取消总线
        return None

    try:
        _redis_client = Redis.from_url(settings.redis_url)
        # 简单探活，失败则回退为 None
        await _redis_client.ping()
        logger.info("Initialized Redis client for deepsearch cancel bus.")
    except Exception as e:
        logger.warning("Failed to initialize Redis client for cancel bus: %s", e)
        _redis_client = None
    return _redis_client


async def publish_remote_cancel(space_id: str, conversation_id: str) -> bool:
    """
    将取消请求发布到 Redis 频道，由真正持有该会话任务的进程处理。
    """
    client = await _get_redis_client()
    if client is None:
        return False

    payload = {
        "space_id": space_id,
        "conversation_id": conversation_id,
    }
    try:
        await client.publish(_CANCEL_CHANNEL, json.dumps(payload, ensure_ascii=False))
        logger.info("Published remote cancel to Redis for %s:%s", space_id, conversation_id)
        return True
    except Exception as e:
        logger.warning("Failed to publish remote cancel for %s:%s, error: %s", space_id, conversation_id, e)
        return False


async def _cancel_listener_loop():
    """
    后台协程：订阅 Redis 取消频道，接收跨进程取消指令。
    收到消息后，如果已注册业务回调，则调用。
    """
    client = await _get_redis_client()
    if client is None:
        # 非 redis 模式或初始化失败，直接返回
        return

    try:
        pubsub = client.pubsub()
        await pubsub.subscribe(_CANCEL_CHANNEL)
        logger.info("Subscribed to Redis cancel channel: %s", _CANCEL_CHANNEL)

        async for message in pubsub.listen():
            if message is None:
                continue
            if message.get("type") != "message":
                continue
            data = message.get("data")
            try:
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                payload = json.loads(data)
                space_id = str(payload.get("space_id", ""))
                conversation_id = str(payload.get("conversation_id", ""))
                if space_id and conversation_id and _cancel_handler is not None:
                    await _cancel_handler(space_id, conversation_id)
            except Exception as e:
                logger.warning("Failed to process cancel bus message: %s", e)
    except asyncio.CancelledError:
        logger.info("Cancel listener loop cancelled, shutting down.")
        raise
    except Exception as e:
        logger.warning("Cancel listener loop stopped due to error: %s", e)


async def start_cancel_listener():
    """
    在应用启动时调用，开启 Redis 取消监听协程（仅 redis 模式有效）。
    """
    global _cancel_listener_task
    if _cancel_listener_task is not None and not _cancel_listener_task.done():
        return

    # 仅在 redis checkpointer 模式下启动监听
    cp_type = (settings.checkpointer_type or "").strip().lower()
    if cp_type != "redis":
        return

    _cancel_listener_task = asyncio.create_task(_cancel_listener_loop(), name="deepsearch_cancel_listener")
    logger.info("Started deepsearch cancel listener task.")


async def stop_cancel_listener():
    """
    在应用关闭时调用，停止 Redis 取消监听协程。
    """
    global _cancel_listener_task
    if _cancel_listener_task is None:
        return

    if not _cancel_listener_task.done():
        _cancel_listener_task.cancel()
        try:
            await _cancel_listener_task
        except asyncio.CancelledError:
            # CancelledError 在取消任务时是预期行为，记录但不影响关闭流程
            logger.debug("Cancel listener task was cancelled as expected during shutdown")
    _cancel_listener_task = None
    logger.info("Stopped deepsearch cancel listener task.")

