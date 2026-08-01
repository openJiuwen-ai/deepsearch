# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""workflow 图节点：CodeSearchAgent 阶段方法的薄包装（"机械包装"，plan §4.4）。

会话纪律：workflow session 只携带 `run_id`（可序列化字符串）；
记忆/检索器/LLM 等活对象全部经 runtime_context 的运行注册表取回，
不进会被复制的 workflow state。
"""

import logging

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.flow.end_comp import End
from openjiuwen.core.workflow.components.flow.start_comp import Start

from openjiuwen_codesearch.domain.result import Termination
from openjiuwen_codesearch.framework.openjiuwen.agent import CodeSearchAgent
from openjiuwen_codesearch.framework.openjiuwen.base_node import BaseNode
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import get_run_context

logger = logging.getLogger(__name__)

NODE_START = "start"
NODE_REASONING = "reasoning"
NODE_TOOL = "tool"
NODE_END = "end"

# Stateless agent; shared instance is safe for concurrent runs.
_CS_AGENT = CodeSearchAgent()


def _ctx_from_session(session: Session):
    run_id = session.get_global_state("run_id")
    return get_run_context(run_id)


class CSStartNode(Start):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session.update_global_state(inputs or {})
        run_id = session.get_global_state("run_id")
        logger.info("[CSStartNode] starting code_search workflow, run_id=%s", run_id)
        get_run_context(run_id)  # 提前校验注册表命中，坏 run_id 直接失败


class ReasoningNode(BaseNode):
    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        ctx = _ctx_from_session(session)
        termination = await _CS_AGENT.reasoning_step(ctx)
        if termination is not None:
            ctx.pending_termination = termination
            return dict(next_node=NODE_END)
        return dict(next_node=NODE_TOOL)


class ToolNode(BaseNode):
    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        ctx = _ctx_from_session(session)
        termination = await _CS_AGENT.tool_step(ctx)
        if termination is not None:
            ctx.pending_termination = termination
            return dict(next_node=NODE_END)
        return dict(next_node=NODE_REASONING)


class CSEndNode(End):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        ctx = _ctx_from_session(session)
        termination = ctx.pending_termination or Termination.MAX_TURNS
        result = _CS_AGENT.finalize(ctx, termination)
        logger.info("[CSEndNode] workflow finished: %s", result.termination.value)
        # 大结果留在 RunContext；workflow 出口只回传轻量元数据（明确输出契约，
        # 避免 deepsearch 式的多层防御解包）
        return dict(
            final_result={
                "termination": result.termination.value,
                "turns": result.turns,
                "num_hits": len(result.hits),
            }
        )
