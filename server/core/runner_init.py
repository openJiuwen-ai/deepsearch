# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Runner 初始化模块

在 FastAPI 启动时调用，配置 openJiuwen Runner 和 Checkpointer。
支持三种模式：in_memory（开发）、persistence（单机生产）、redis（分布式生产）。
"""

import logging
import types

from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

from server.core.config import settings

logger = logging.getLogger(__name__)
SUPPORTED_CHECKPOINTER_TYPES = {"in_memory", "persistence", "redis"}
# 记录已打过兼容补丁的 checkpointer 实例 ID，避免重复 monkey patch。
PATCHED_CHECKPOINTER_IDS = set()


def _patch_checkpointer_interactive_recovery():
    """
    Make checkpointer accept dict inputs and auto-convert recovery messages.

    This keeps business-layer inputs as normal dict while satisfying
    checkpointer's interactive recovery path for persistence/redis backends.
    """
    checkpointer = CheckpointerFactory.get_checkpointer()
    checkpointer_id = id(checkpointer) if checkpointer else None
    # 幂等保护：同一个 checkpointer 实例只 patch 一次。
    if not checkpointer or checkpointer_id in PATCHED_CHECKPOINTER_IDS:
        return

    original = checkpointer.pre_workflow_execute

    async def _patched_pre_workflow_execute(self, session, inputs):
        # DeepSearch 业务层使用 dict 入参；当命中交互恢复场景时，
        # 转换为 InteractiveInput 以兼容 checkpointer 的恢复逻辑。
        effective_inputs = inputs
        if isinstance(inputs, dict):
            query = inputs.get("query")
            if isinstance(query, InteractiveInput):
                effective_inputs = query
            else:
                should_recover = False
                workflow_storage = getattr(self, "_workflow_storage", None)
                if workflow_storage and hasattr(workflow_storage, "exists"):
                    try:
                        should_recover = await workflow_storage.exists(session)
                    except Exception as e:
                        logger.debug("Failed to auto-detect workflow recovery state: %s", e)

                if query is not None and should_recover:
                    effective_inputs = InteractiveInput(query)
        return await original(session, effective_inputs)

    # monkey patch checkpointer 的执行前钩子，注入输入兼容处理。
    checkpointer.pre_workflow_execute = types.MethodType(_patched_pre_workflow_execute, checkpointer)
    # 打标记，防止重复构建 pre_workflow_execute。
    PATCHED_CHECKPOINTER_IDS.add(checkpointer_id)
    logger.info("Applied checkpointer interactive recovery compatibility patch.")


def _build_checkpointer_config() -> CheckpointerConfig:
    """
    根据环境配置构建 CheckpointerConfig

    Returns:
        CheckpointerConfig: 配置好的 Checkpointer 配置对象
    """
    cp_type = (settings.checkpointer_type or "").strip().lower()
    if cp_type not in SUPPORTED_CHECKPOINTER_TYPES:
        raise ValueError(
            "Invalid CHECKPOINTER_TYPE: "
            f"{settings.checkpointer_type}. Supported values: "
            f"{', '.join(sorted(SUPPORTED_CHECKPOINTER_TYPES))}."
        )

    if cp_type == "redis":
        conf = {
            "connection": {
                "url": settings.redis_url,
                "cluster_mode": settings.redis_cluster_mode,
            },
            "ttl": {
                "default_ttl": settings.redis_ttl,
                "refresh_on_read": settings.redis_refresh_on_read,
            }
        }
    elif cp_type == "persistence":
        conf = {
            "db_type": settings.checkpointer_db_type,
            "db_path": settings.checkpointer_db_path,
        }
    else:  # in_memory
        conf = {}

    return CheckpointerConfig(type=cp_type, conf=conf)


async def init_runner():
    """
    初始化 Runner，配置 Checkpointer。

    应在 FastAPI lifespan startup 中调用。
    """
    cp_type = (settings.checkpointer_type or "").strip().lower()
    if cp_type not in SUPPORTED_CHECKPOINTER_TYPES:
        raise ValueError(
            "Invalid CHECKPOINTER_TYPE: "
            f"{settings.checkpointer_type}. Supported values: "
            f"{', '.join(sorted(SUPPORTED_CHECKPOINTER_TYPES))}."
        )

    # 根据 checkpointer 类型导入对应的 provider 以完成注册
    if cp_type == "redis":
        from openjiuwen.extensions.checkpointer.redis import checkpointer  # noqa: F401
        logger.info("Redis checkpointer provider registered.")
    elif cp_type == "persistence":
        import openjiuwen.core.session.checkpointer.persistence  # noqa: F401
        logger.info("Persistence checkpointer provider registered.")

    runner_config = RunnerConfig()
    runner_config.distributed_mode = False
    runner_config.checkpointer_config = _build_checkpointer_config()

    Runner.set_config(runner_config)
    await Runner.start()
    _patch_checkpointer_interactive_recovery()

    logger.info(
        "Runner initialized with checkpointer type: %s",
        cp_type,
    )


async def shutdown_runner():
    """
    关闭 Runner 释放资源。

    应在 FastAPI lifespan shutdown 中调用。
    """
    try:
        if hasattr(Runner, "stop"):
            await Runner.stop()
        logger.info("Runner shut down.")
    except Exception as e:
        logger.warning("Error shutting down runner: %s", e)
