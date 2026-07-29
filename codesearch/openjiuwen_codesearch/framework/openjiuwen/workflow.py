# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""code_search 工作流：图组装 + 注册 + 图形态编排器。

图结构（plan §4.4）：
    START → REASONING ⇄ TOOL，两者均可路由 END
工作流对象进程内类级共享（不可变）；每次运行的可变状态经 run_id 注册表注入
（对齐 deepsearch `DeepSearchAgent` 的共享/隔离模式）。
"""

import logging
import threading
from typing import ClassVar, Optional

from openjiuwen.core.application.workflow_agent.workflow_agent import (
    WorkflowAgent as LegacyWorkflowAgent,
)
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.session import workflow_session_vars
from openjiuwen.core.session.constants import WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY
from openjiuwen.core.single_agent.legacy.agent import WorkflowFactory
from openjiuwen.core.single_agent.legacy.config import WorkflowAgentConfig
from openjiuwen.core.workflow import Workflow, WorkflowCard

from openjiuwen_codesearch.domain.result import CodeSearchResult, Termination
from openjiuwen_codesearch.framework.openjiuwen.base_node import init_router
from openjiuwen_codesearch.framework.openjiuwen.nodes import (
    NODE_END,
    NODE_REASONING,
    NODE_START,
    NODE_TOOL,
    CSEndNode,
    CSStartNode,
    ReasoningNode,
    ToolNode,
)
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import (
    CodeSearchRunContext,
    run_session,
)
from openjiuwen_codesearch.framework.openjiuwen.steps import finalize

logger = logging.getLogger(__name__)

WORKFLOW_ID = "code_search"
WORKFLOW_VERSION = "1"
_INPUT_SCHEMA = {"run_id": str, "workflow_name": str}


def build_code_search_workflow() -> Workflow:
    card = WorkflowCard(id=WORKFLOW_ID, version=WORKFLOW_VERSION, name=WORKFLOW_ID)
    flow = Workflow(card=card)
    # 注意：set_start_comp 的 inputs_schema 语义是"默认输入值"（对齐 deepsearch 用法），
    # 不是类型 schema；run_id 等真实输入由 Runner.run_workflow 注入并经
    # commit_user_inputs 进入 session 全局状态。
    flow.set_start_comp(
        start_comp_id=NODE_START,
        component=CSStartNode(),
        inputs_schema={"workflow_name": WORKFLOW_ID},
    )
    flow.add_workflow_comp(NODE_REASONING, ReasoningNode())
    flow.add_workflow_comp(NODE_TOOL, ToolNode())
    flow.set_end_comp(NODE_END, CSEndNode())

    flow.add_connection(NODE_START, NODE_REASONING)
    flow.add_conditional_connection(
        NODE_REASONING, router=init_router(NODE_REASONING, [NODE_TOOL, NODE_END])
    )
    flow.add_conditional_connection(
        NODE_TOOL, router=init_router(NODE_TOOL, [NODE_REASONING, NODE_END])
    )
    return flow


class GraphCodeSearchAgent:
    """图形态编排器：注册共享 workflow，经 Runner 驱动一次检索运行。"""

    _workflow_agent: ClassVar[Optional[LegacyWorkflowAgent]] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def _get_shared_agent(cls) -> LegacyWorkflowAgent:
        if cls._workflow_agent is None:
            with cls._lock:
                if cls._workflow_agent is None:
                    agent = LegacyWorkflowAgent(
                        WorkflowAgentConfig(
                            id="codesearch_workflows",
                            description="CodeSearch agentic retrieval workflow",
                            workflows=[
                                WorkflowCard(
                                    id=WORKFLOW_ID,
                                    version=WORKFLOW_VERSION,
                                    name=WORKFLOW_ID,
                                    description=WORKFLOW_ID,
                                    input_params=_INPUT_SCHEMA,
                                )
                            ],
                        )
                    )
                    agent.add_workflows(
                        [
                            WorkflowFactory(
                                workflow_id=WORKFLOW_ID,
                                workflow_version=WORKFLOW_VERSION,
                                factory=build_code_search_workflow,
                                workflow_name=WORKFLOW_ID,
                                workflow_description=WORKFLOW_ID,
                                input_schema=_INPUT_SCHEMA,
                            )
                        ]
                    )
                    cls._workflow_agent = agent
        return cls._workflow_agent

    async def run(self, ctx: CodeSearchRunContext) -> CodeSearchResult:
        self._get_shared_agent()
        logger.info("Starting multi-turn agentic retrieval loop (graph engine)...")
        # 结构化注册：with 退出自动注销（防长驻服务下的注册表泄漏）
        with run_session(ctx) as run_id:
            # per-run 放宽 workflow 执行超时（openjiuwen 默认仅 60s）：
            # 写 workflow_session_vars 而非 os.environ，重叠运行互不影响（deepsearch 模式）
            session_vars = dict(workflow_session_vars.get() or {})
            session_vars[WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY] = str(
                ctx.config.agent.time_limit_seconds
            )
            session_token = workflow_session_vars.set(session_vars)
            try:
                await Runner.run_workflow(
                    workflow=f"{WORKFLOW_ID}_{WORKFLOW_VERSION}",
                    inputs={"run_id": run_id, "workflow_name": WORKFLOW_ID},
                )
                if ctx.result is None:
                    # 图异常中断且 End 未执行：按已有 pending 终止原因降级收尾
                    logger.warning(
                        "Workflow ended without EndNode result; finalizing from context."
                    )
                    return finalize(ctx, ctx.pending_termination or Termination.LLM_ERROR)
                return ctx.result
            finally:
                workflow_session_vars.reset(session_token)
