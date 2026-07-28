# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""BaseNode 三段式模板 + 路由构造。复制自 deepsearch 惯例（复制不共享，
见 refactor-notes §2.2 第 3 条），按 codesearch 需要精简。
"""

import logging
import time

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter

logger = logging.getLogger(__name__)


class BaseNode(WorkflowComponent):
    """节点封装：子类实现 `_do_invoke`（调 steps 阶段函数）；
    `invoke` 由基类统一注入计时/日志，子类不覆写。
    """

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        node_name = type(self).__name__
        start = time.monotonic()
        try:
            return await self._do_invoke(inputs, session, context)
        finally:
            logger.info("[%s] finished in %.3fs", node_name, time.monotonic() - start)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        raise NotImplementedError(f"{type(self).__name__} must implement _do_invoke")


def init_router(current_node: str, next_nodes: list[str]) -> BranchRouter:
    """按节点输出的 `next_node` 字段分支（与 deepsearch 同款条件表达式）。"""
    router = BranchRouter()
    for next_node in next_nodes:
        router.add_branch(f"${{{current_node}.next_node}} == {next_node!r}", next_node)
    return router
