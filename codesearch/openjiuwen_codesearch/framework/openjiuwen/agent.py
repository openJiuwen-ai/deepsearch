# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CodeSearchAgent（react 形态）：纯代码循环编排器。

与图形态（workflow.py 的 GraphCodeSearchAgent）共享 steps.py 的同一份阶段逻辑，
仅驱动方式不同：这里是 while 循环，图形态由 openjiuwen Runner 按路由驱动。
无实例运行态（全部在 RunContext），同一实例可安全并发 run。
"""

import logging

from openjiuwen_codesearch.domain.result import CodeSearchResult
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import CodeSearchRunContext
from openjiuwen_codesearch.framework.openjiuwen.steps import finalize, reasoning_step, tool_step

logger = logging.getLogger(__name__)


class CodeSearchAgent:
    async def run(self, ctx: CodeSearchRunContext) -> CodeSearchResult:
        logger.info("Starting multi-turn agentic retrieval loop (react engine)...")
        while True:
            termination = await reasoning_step(ctx)
            if termination is not None:
                return finalize(ctx, termination)
            termination = await tool_step(ctx)
            if termination is not None:
                return finalize(ctx, termination)
