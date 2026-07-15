# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

from openjiuwen.core.graph.base import CONFIG_KEY, INPUTS_KEY
from openjiuwen.core.session import WorkflowSession
from openjiuwen.core.session.state.workflow_state import InMemoryState
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder import (
    build_info_collector_sub_graph,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Message, Plan, Step, StepType
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


async def run_info_collector_sub_graph(
    agent_input: dict,
    session: Any,
    context: Any,
) -> dict:
    """构建并执行信息采集中小子图，返回 ``session`` 中的 ``collector_context``（缺省为 ``{}``）。

    Args:
        agent_input: 传入子图的输入字典。
        session: 当前会话对象。
        context: 当前模型上下文对象。

    Returns:
        dict: 信息采集子图执行后的上下文字典。
    """
    collector_graph = build_info_collector_sub_graph()
    inner_session = getattr(session, "_inner", None)

    if inner_session is None:
        await collector_graph.invoke(
            agent_input,
            session,
            context,
            is_sub=True,
        )
        collector_context = session.get_global_state("collector_context")
        return collector_context or {}

    collector_internal = getattr(collector_graph, "_internal")
    workflow_session = WorkflowSession(
        workflow_id=collector_graph.card.id,
        parent=inner_session,
        session_id=uuid.uuid4().hex,
        state=InMemoryState(),
    )

    if inner_session.stream_writer_manager():
        workflow_session.set_stream_writer_manager(inner_session.stream_writer_manager())

    if hasattr(collector_internal, "auto_complete_abilities"):
        collector_internal.auto_complete_abilities()
    workflow_config_getter = getattr(collector_internal, "config", None)
    if callable(workflow_config_getter):
        workflow_session.config().add_workflow_config(
            workflow_id=collector_graph.card.id,
            workflow_config=workflow_config_getter(),
        )

    config = session.get_global_state("config")
    if config is not None:
        workflow_state = cast(InMemoryState, workflow_session.state())
        workflow_state.update_global({"config": config})
        workflow_state.commit()

    try:
        compiled_graph = collector_internal.compile(workflow_session, context=context)
        await compiled_graph.invoke({INPUTS_KEY: agent_input, CONFIG_KEY: None}, workflow_session)
        return workflow_session.state().get_global("collector_context") or {}
    finally:
        await workflow_session.close()
        await collector_internal.reset()


def _collector_execution_log_prefix(section_idx: int | str, plan_id: str) -> str:
    """构造信息采集执行链路日志前缀（小节索引 + 计划 id）。"""
    return f"[CollectorExecutionService] section_idx: {section_idx} | plan_idx: {plan_id} |"


def _merge_source_store(
    source_store: dict,
    step_source_store: dict,
    log_prefix: str,
    step_id: str,
) -> None:
    """合并单个采集步骤的 source store，遇到冲突时保留首个正文。

    Args:
        source_store: 当前 run_plan 已聚合的 source_id 到正文映射。
        step_source_store: 当前采集步骤返回的 source_id 到正文映射。
        log_prefix: 当前 collector 执行链路日志前缀。
        step_id: 当前采集步骤 ID，用于定位冲突来源。
    """
    for source_id, content in step_source_store.items():
        if source_id in source_store:
            if source_store[source_id] != content:
                logger.warning(
                    "%s source_store source_id conflict. source_id=%s | step_id=%s | keeping first content.",
                    log_prefix,
                    source_id,
                    step_id,
                )
            continue
        source_store[source_id] = content


@dataclass
class CollectorExecutionResult:
    """单次 ``run_plan`` 聚合的步骤结果、摘要、消息、引用文档和正文存储。

    Attributes:
        collect_steps: 本次执行完成的信息采集步骤。
        collected_doc_num: 本次执行累计收集的文档数量。
        info_summary: 最后一个采集步骤生成的信息摘要。
        evaluation: 最后一个采集步骤生成的信息充分性评估。
        messages: 需要追加到 section 上下文的消息列表。
        doc_infos: 本次执行累计收集的兼容 doc_infos。
        source_store: 本次执行累计收集的 source_id 到正文映射。
    """

    collect_steps: list[Step]
    collected_doc_num: int = 0
    info_summary: str | None = None
    evaluation: str | None = None
    messages: list[Message] = field(default_factory=list)
    doc_infos: list = field(default_factory=list)
    source_store: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CollectorInputBuildConfig:
    """封装构造 collector 子图输入时使用的相关执行参数。

    Attributes:
        max_search_query_count: 单轮最大检索 query 数量。
        max_research_loops: research loop 最大轮次。
        max_tool_call_turns_per_query: 单个检索 query 最大工具调用轮次。
    """

    max_search_query_count: int
    max_research_loops: int
    max_tool_call_turns_per_query: int


@dataclass(frozen=True)
class CollectorInputBuildParams:
    """封装 ``_input_build`` 所需的计划、步骤与运行上下文。"""

    plan: Plan
    step: Step
    language: str
    section_idx: int | str
    build_config: CollectorInputBuildConfig
    report_type: str | None = None
    research_intent: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CollectorRunPlanConfig:
    """封装执行 collector plan 时使用的上下文与相关配置。

    Attributes:
        language: 输出语言。
        section_idx: 当前章节索引。
        max_search_query_count: 单轮最大检索 query 数量。
        max_research_loops: research loop 最大轮次。
        max_tool_call_turns_per_query: 单个检索 query 最大工具调用轮次。
    """

    language: str
    section_idx: int | str
    max_search_query_count: int
    max_research_loops: int
    max_tool_call_turns_per_query: int

    def to_input_build_config(self) -> CollectorInputBuildConfig:
        """提取 ``_input_build`` 需要的相关配置。"""
        return CollectorInputBuildConfig(
            max_search_query_count=self.max_search_query_count,
            max_research_loops=self.max_research_loops,
            max_tool_call_turns_per_query=self.max_tool_call_turns_per_query,
        )


class CollectorExecutionService:
    """封装「计划 → 信息采集子图 → 汇总上下文」的执行流程，供主流程与补充搜索等复用。"""

    async def run_plan(
        self,
        plan: Plan,
        run_config: CollectorRunPlanConfig,
        session,
        context,
    ) -> CollectorExecutionResult:
        """顺序执行计划中未完成的信息收集步骤，回写 ``step`` 字段并累积 ``CollectorExecutionResult``。

        Args:
            plan: 待执行的计划对象。
            run_config: collector 执行时所需的语言、小节索引和查询配置。
            session: 当前会话对象。
            context: 当前模型上下文对象。

        Returns:
            CollectorExecutionResult: 聚合的执行结果，包含步骤、摘要、消息、文档信息和 source store。
        """
        collect_steps: list[Step] = []
        current_doc_num: int = 0
        messages: list[Message] = []
        info_summary: str | None = None
        evaluation: str | None = None
        all_doc_infos: list = []
        all_source_store: dict = {}
        log_prefix = _collector_execution_log_prefix(
            section_idx=run_config.section_idx, plan_id=plan.id or ""
        )
        build_config = run_config.to_input_build_config()
        rtp = session.get_global_state("section_context.report_type_policy") or {}
        report_type = rtp.get("report_type", "professional") if isinstance(rtp, dict) else "professional"
        research_intent = session.get_global_state("section_context.research_intent") or {}

        for idx, step in enumerate(plan.steps):
            step.id = f"{idx + 1}"
            if step.type != StepType.INFO_COLLECTING or step.step_result:
                continue

            sub_inputs = self._input_build(
                CollectorInputBuildParams(
                    plan=plan,
                    step=step,
                    language=run_config.language,
                    section_idx=run_config.section_idx,
                    build_config=build_config,
                    report_type=report_type,
                    research_intent=research_intent,
                )
            )
            logger.info(
                f"{log_prefix} Start step {step.id}: The input is"
                f"{'*' if LogManager.is_sensitive() else sub_inputs}",
            )

            collector_context = await run_info_collector_sub_graph(
                sub_inputs,
                session,
                context
            )

            info_summary = collector_context.get("info_summary")
            evaluation = collector_context.get("evaluation")
            history_queries = collector_context.get("history_queries")
            doc_infos = collector_context.get("doc_infos", [])
            step_source_store = collector_context.get("source_store", {})

            step.step_result = info_summary
            step.evaluation = evaluation
            step.retrieval_queries = history_queries
            current_doc_num += len(doc_infos)
            all_doc_infos.extend(doc_infos)
            if isinstance(step_source_store, dict):
                _merge_source_store(
                    source_store=all_source_store,
                    step_source_store=step_source_store,
                    log_prefix=log_prefix,
                    step_id=step.id or "",
                )
            collect_steps.append(step)

            messages.append(
                Message(role="assistant", content=step.step_result if step.step_result is not None else ""),
            )
            

            logger.info(
                f"{log_prefix} End step {step.id}: The result is: "
                f"{'*' if LogManager.is_sensitive() else step.model_dump()}",
            )

        return CollectorExecutionResult(
            collect_steps=collect_steps,
            collected_doc_num=current_doc_num,
            info_summary=info_summary,
            evaluation=evaluation,
            messages=messages,
            doc_infos=all_doc_infos,
            source_store=all_source_store,
        )

    @staticmethod
    def _input_build(params: CollectorInputBuildParams) -> dict:
        """拼装传入 ``build_info_collector_sub_graph`` 的标准 ``agent_input`` 字典。

        Args:
            params: 计划、步骤与子图构造所需的相关配置。

        Returns:
            dict: 标准化的子图输入字典。
        """
        plan = params.plan
        step = params.step
        build_config = params.build_config
        message = "Now deal with the task: \n"
        message += f"You should focus on [Topic]: {plan.title}\n"
        message += f"pay attention to [Condition]: {plan.thought}"
        message += f":\n[Task Title]: {step.title}\n[Problem]: {step.description}"
        message += "\nPlease analyze this task and start your ReAct process:\n"
        message += "1. Reason about what information you need to gather\n"
        message += "2. Use appropriate tools to get that information\n"
        message += "3. Continue reasoning and acting until you have sufficient information\n"
        message += "4. Call info_seeker_task_done when ready to provide your complete findings\n\n"
        message += "Begin with your initial reasoning about the task."

        return {
            "language": params.language,
            "messages": [Message(role="user", content=message)],
            "section_idx": params.section_idx,
            "plan_idx": plan.id,
            "plan_title": plan.title,
            "plan_thought": plan.thought,
            "step_idx": step.id,
            "step_title": step.title,
            "step_description": step.description,
            "max_search_query_count": build_config.max_search_query_count,
            "max_research_loops": build_config.max_research_loops,
            "max_tool_call_turns_per_query": build_config.max_tool_call_turns_per_query,
            "report_type": params.report_type or "professional",
            "research_intent": params.research_intent or {},
        }
