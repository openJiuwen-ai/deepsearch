# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import re
import uuid
from typing import List, Any, Literal, Sequence

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.flow.end_comp import End
from openjiuwen.core.workflow.components.flow.start_comp import Start
from openjiuwen.core.workflow.workflow import Workflow
from pydantic import BaseModel, Field

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    build_summary_evidence_pack,
    build_supervisor_evidence_table,
)
from openjiuwen_deepsearch.config.config import ServiceConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode, init_router
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.collector_context import CollectorContext
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import InfoRetrievalNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment import WebPageEnrichmentNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import (
    EvidenceLedger,
    build_ledger_brief,
    ensure_ledger,
    merge_ledger_update,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    RetrievalQuery,
    build_temporal_scope_prompt_context,
)
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import adapt_llm_model_name
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, record_llm_retry_log
from openjiuwen_deepsearch.utils.common_utils.stream_utils import MessageType, StreamEvent, get_current_time
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

max_retries = ServiceConfig().info_collector_max_retry_num


class SearchQueryItem(BaseModel):
    query: str = Field(description="Search query text.")
    search_engine_name: Literal["", "pubmed", "arxiv"] = Field(
        default="",
        description="Secondary vertical search engine for this query.",
    )


class SearchQueryList(BaseModel):
    missing_evidence: List[str] = Field(
        default_factory=list,
        description="Verifiable evidence requirements for the current collector step."
    )
    queries: List[str | SearchQueryItem] = Field(
        description="A list of search queries or query objects to be used for web research."
    )


class Reflection(BaseModel):
    """Supervisor 对当前收集轮次的结构化反思结果。

    Attributes:
        is_sufficient: 当前证据是否足以产出可靠的 step-level summary。
        should_continue: 当证据仍不足时，继续下一轮检索是否仍有价值。
        knowledge_gap: 缺失信息或可披露限制的简要描述。
        next_queries: 用于补齐剩余关键缺口的后续检索 query。
        known_facts: 本轮新确认的事实。
        missing_evidence: 仍阻碍当前 step 可靠总结的可验证证据需求。
    """

    is_sufficient: bool = Field(
        description="Whether the provided summaries are sufficient to answer the user's question."
    )
    should_continue: bool = Field(
        default=True,
        description="Whether another retrieval loop is likely to add useful evidence when not sufficient."
    )
    knowledge_gap: str = Field(
        description="A description of what information is missing or needs clarification."
    )
    next_queries: List[str] = Field(
        description="A list of follow-up queries to address the knowledge gap."
    )
    known_facts: List[str] = Field(
        default_factory=list,
        description="Key facts supported by the current ledger or gathered information."
    )
    missing_evidence: List[str] = Field(
        default_factory=list,
        description="Remaining verifiable evidence requirements."
    )


class Summary(BaseModel):
    info_summary: str = Field(
        description="A concise summary of the collected information relevant to the research topic."
    )
    evaluation: str = Field(
        description="A detailed description of the evaluating the gathered information for task."
    )


def get_research_record(messages: List[dict]) -> str:
    """
    Get the research record from the messages.
    """
    if len(messages) == 1:
        research_record = messages[-1].get('content')
    else:
        research_record = ""
        for message in messages:
            if message.get('role') == "user":
                research_record += f"User: {message.get('content')}\n"
    return research_record


def _validate_query_count(
        queries: Sequence[str | SearchQueryItem],
        max_search_query_count: int,
        field_name: str,
) -> None:
    """校验 LLM 生成的 query 数量没有超过单轮硬上限。

    Args:
        queries: LLM 生成的 query 项列表。
        max_search_query_count: 单轮允许的最大 query 数量。
        field_name: 被校验字段名，用于错误日志。

    Raises:
        ValueError: query 数量超过硬上限时抛出。
    """
    if len(queries) > max_search_query_count:
        raise ValueError(
            f"{field_name} count {len(queries)} exceeds max_search_query_count {max_search_query_count}"
        )


def route_secondary_search_engine_for_query(query: str) -> str:
    """Choose a query-level vertical search engine only for clear academic domains."""
    text = (query or "").lower()
    if not text:
        return ""

    def has_keyword(keywords: tuple[str, ...]) -> bool:
        for keyword in keywords:
            if re.search(r"[a-z0-9]", keyword):
                if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text):
                    return True
            elif keyword in text:
                return True
        return False

    pubmed_keywords = (
        "medicine", "medical", "clinical", "clinic", "biomedical", "biology", "biological",
        "drug", "disease", "epidemiology", "gene", "genetic", "protein", "patient",
        "therapy", "treatment", "trial", "pubmed", "cancer", "医学", "临床", "生物",
        "药物", "疾病", "流行病", "基因", "蛋白", "患者", "治疗",
    )
    pubmed_keywords += (
        "drugs", "diseases", "genes", "proteins", "patients",
        "therapies", "treatments", "trials",
    )
    arxiv_source_keywords = (
        "arxiv", "preprint", "预印本",
    )
    arxiv_domain_keywords = (
        "artificial intelligence", "computer science", "algorithm", "machine learning",
        "deep learning", "large language model", "retrieval augmented", "neural network",
        "transformer", "mathematics", "physics", "计算机", "算法", "机器学习", "大模型",
        "数学", "物理",
    )
    arxiv_short_tokens = (
        "ai", "cs", "llm", "rag", "ml",
    )
    arxiv_domain_keywords += (
        "algorithms", "neural networks", "transformers",
    )
    if has_keyword(pubmed_keywords):
        return "pubmed"
    if has_keyword(arxiv_source_keywords) or has_keyword(arxiv_domain_keywords) or has_keyword(arxiv_short_tokens):
        return "arxiv"
    return ""


def normalize_search_query_item(item: str | SearchQueryItem) -> SearchQueryItem:
    if isinstance(item, SearchQueryItem):
        query = item.query
        if "search_engine_name" in item.model_fields_set:
            secondary_engine = item.search_engine_name
        else:
            secondary_engine = route_secondary_search_engine_for_query(query)
        return SearchQueryItem(query=query, search_engine_name=secondary_engine)
    query = str(item)
    return SearchQueryItem(
        query=query,
        search_engine_name=route_secondary_search_engine_for_query(query),
    )


def _fallback_search_query_list(
    result: SearchQueryList | None,
    step_title: str,
    max_search_query_count: int,
) -> SearchQueryList:
    """构造初始 query 生成失败后的保守 fallback 结果。

    Args:
        result: 最近一次可用的 LLM 结构化结果。
        step_title: 当前采集步骤标题。
        max_search_query_count: 单轮允许的最大 query 数量。

    Returns:
        SearchQueryList: 使用 missing evidence 或 step title 构造的 fallback query 结果。
    """
    if max_search_query_count <= 0:
        return SearchQueryList(queries=[], missing_evidence=result.missing_evidence if result else [])
    if result and result.missing_evidence:
        return SearchQueryList(
            queries=result.missing_evidence[:max_search_query_count],
            missing_evidence=result.missing_evidence,
        )
    return SearchQueryList(queries=[step_title] if step_title else [], missing_evidence=[])


class StartNode(Start):
    """
    起始节点，初始化 Session global_state 中的 collector_context
    """

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """Invoke method of StartNode."""
        # 初始化 collector_context
        collector_context = CollectorContext(
            language=inputs.get("language", "zh-CN"),
            messages=inputs.get("messages", []),
            section_idx=inputs.get("section_idx", 0),
            plan_idx=inputs.get("plan_idx", 0),
            plan_title=inputs.get("plan_title", ""),
            plan_thought=inputs.get("plan_thought", ""),
            step_idx=inputs.get("step_idx", 0),
            step_title=inputs.get("step_title", ""),
            step_description=inputs.get("step_description", ""),
            step_background_knowledge=inputs.get("step_background_knowledge") or [],
            max_search_query_count=inputs.get("max_search_query_count", 5),
            max_research_loops=inputs.get("max_research_loops", 2),
            max_tool_call_turns_per_query=inputs.get("max_tool_call_turns_per_query", 2),
            evidence_ledger={},
            report_type=inputs.get("report_type", "professional"),
            research_intent=inputs.get("research_intent") or {},
        )
        session.update_global_state({"collector_context": collector_context.model_dump()})

        return inputs


class GenerateQueryNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.llm: Any = None

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state("collector_context.section_idx")
        logger.info(f"section_idx: {section_idx} | [GenerateQueryNode] Start GenerateQueryNode.")
        plan_title = session.get_global_state("collector_context.plan_title")
        plan_thought = session.get_global_state("collector_context.plan_thought")
        step_title = session.get_global_state("collector_context.step_title")
        step_description = session.get_global_state("collector_context.step_description")
        max_search_query_count = session.get_global_state("collector_context.max_search_query_count")
        language = session.get_global_state("collector_context.language")
        evidence_ledger = session.get_global_state("collector_context.evidence_ledger")
        research_intent = session.get_global_state("collector_context.research_intent") or {}
        llm_model_name = adapt_llm_model_name(session, NodeId.INFO_COLLECTOR.value)
        self.llm = llm_context.get().get(llm_model_name)

        return dict(section_idx=section_idx, plan_title=plan_title, plan_thought=plan_thought, step_title=step_title,
                    step_description=step_description,
                    max_search_query_count=max_search_query_count,
                    language=language, evidence_ledger=evidence_ledger,
                    research_intent=research_intent)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """根据研究记录生成当前采集轮次的检索 query。

        Args:
            inputs: 当前节点输入。
            session: 当前运行 session。
            context: 图执行上下文。

        Returns:
            包含下一节点 ID 的输出字典。
        """
        state = self._pre_handle(inputs, session, context)
        session_context.set(session)

        section_idx = state.get("section_idx", 0)
        plan_title = state.get("plan_title", "")
        plan_thought = state.get("plan_thought", "")
        step_title = state.get("step_title", "")
        step_description = state.get("step_description", "")
        max_search_query_count = state.get("max_search_query_count", 5)
        language = state.get("language", "zh-CN")

        report_type = session.get_global_state("collector_context.report_type") or "professional"

        agent_input = {
            "plan_title": plan_title,
            "plan_thought": plan_thought,
            "step_title": step_title,
            "step_description": step_description,
            "max_search_query_count": max_search_query_count,
            "language": language,
            "report_type": report_type,
        }
        agent_input.update(build_temporal_scope_prompt_context(state.get("research_intent")))
        formatted_prompt = apply_system_prompt("collector_gen_query", agent_input)

        result: SearchQueryList = await self._invoke_llm_with_retry(
            formatted_prompt,
            section_idx,
            step_title,
            max_search_query_count,
        )

        if not LogManager.is_sensitive():
            logger.debug("section_idx: %s | step title %s | [GenerateQueryNode] Generated search queries: %s",
                         section_idx, step_title, result.queries)
            logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                        f"[GenerateQueryNode] Initial queries count: {len(result.queries)} | "
                        f"missing_evidence count: {len(result.missing_evidence)}")
        else:
            logger.info(f"section_idx: {section_idx} |"
                        f"[GenerateQueryNode] Initial queries count: {len(result.queries)}")

        node_output = self._post_handle(inputs, result, session, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: SearchQueryList, session: Session, context: ModelContext):
        query_items = [normalize_search_query_item(query) for query in algorithm_output.queries]
        search_queries = [RetrievalQuery(
            query=item.query,
            search_engine_name=item.search_engine_name,
        ) for item in query_items]
        current_ledger = ensure_ledger(session.get_global_state("collector_context.evidence_ledger"))
        ledger_update = EvidenceLedger(missing_evidence=algorithm_output.missing_evidence)
        updated_ledger = merge_ledger_update(current_ledger, ledger_update)

        session.update_global_state({"collector_context.evidence_ledger": updated_ledger.model_dump()})
        session.update_global_state({"collector_context.search_queries": search_queries})
        section_idx = session.get_global_state("collector_context.section_idx")
        logger.info(f"section_idx: {section_idx} | [GenerateQueryNode] End GenerateQueryNode.")

        return dict()

    async def _invoke_llm_with_retry(
        self,
        formatted_prompt: list,
        section_idx: int,
        step_title: str,
        max_search_query_count: int,
    ):
        result = None
        for retry_idx in range(max_retries):
            try:
                result = await ainvoke_llm_with_stats(
                    self.llm, formatted_prompt, agent_name=AgentLlmName.COLLECTOR_QUERY_GENERATION.value,
                    schema=SearchQueryList)
                _validate_query_count(result.queries, max_search_query_count, "queries")
                break
            except Exception as e:
                current_try = retry_idx + 1
                task_description = "generate search query"
                record_llm_retry_log(current_try, max_retries, section_idx, step_title,
                                     error=e, operation=task_description)
        if result is None or len(result.queries) > max_search_query_count:
            result = _fallback_search_query_list(result, step_title, max_search_query_count)

        return result


class SupervisorNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.llm: Any = None

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state("collector_context.section_idx")
        plan_idx = session.get_global_state("collector_context.plan_idx")
        step_idx = session.get_global_state("collector_context.step_idx")
        logger.info(f"section_idx: {section_idx} | [SupervisorNode] Start SupervisorNode.")
        plan_title = session.get_global_state("collector_context.plan_title")
        plan_thought = session.get_global_state("collector_context.plan_thought")
        step_title = session.get_global_state("collector_context.step_title")
        step_description = session.get_global_state("collector_context.step_description")
        max_search_query_count = session.get_global_state("collector_context.max_search_query_count")
        language = session.get_global_state("collector_context.language")
        new_doc_infos_current_loop = session.get_global_state("collector_context.new_doc_infos_current_loop")
        evidence_ledger = session.get_global_state("collector_context.evidence_ledger")
        research_intent = session.get_global_state("collector_context.research_intent") or {}
        research_loop_count = session.get_global_state("collector_context.research_loop_count")
        llm_model_name = adapt_llm_model_name(session, NodeId.INFO_COLLECTOR.value)
        self.llm = llm_context.get().get(llm_model_name)

        return dict(section_idx=section_idx, plan_idx=plan_idx, step_idx=step_idx,
                    plan_title=plan_title, plan_thought=plan_thought,
                    step_title=step_title, step_description=step_description,
                    max_search_query_count=max_search_query_count, language=language,
                    new_doc_infos_current_loop=new_doc_infos_current_loop,
                    evidence_ledger=evidence_ledger,
                    research_intent=research_intent,
                    research_loop_count=research_loop_count)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """根据 compact evidence table 判断信息是否充分并生成后续 query。

        Args:
            inputs: 当前节点输入。
            session: 当前运行 session。
            context: 图执行上下文。

        Returns:
            包含下一节点 ID 的输出字典。
        """
        state = self._pre_handle(inputs, session, context)
        session_context.set(session)

        section_idx = state.get("section_idx", 0)
        plan_idx = state.get("plan_idx", 0)
        step_idx = state.get("step_idx", 0)
        plan_title = state.get("plan_title", "")
        plan_thought = state.get("plan_thought", "")
        step_title = state.get("step_title", "")
        step_description = state.get("step_description", "")
        research_loop_count = state.get("research_loop_count", 1)
        max_search_query_count = state.get("max_search_query_count", 5)
        new_doc_infos = state.get("new_doc_infos_current_loop", []) or []
        current_ledger = ensure_ledger(state.get("evidence_ledger"))
        ledger_brief = build_ledger_brief(current_ledger)

        for item in new_doc_infos:
            doc_info = {
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "query": item.get("query", ""),
            }
            await session.write_custom_stream({
                "message_id": str(uuid.uuid4()),
                "plan_idx": str(plan_idx),
                "step_idx": str(step_idx),
                "agent": NodeId.COLLECTOR_INFO.value,
                "content": json.dumps(doc_info, ensure_ascii=False),
                "message_type": MessageType.MESSAGE_CHUNK.value,
                "event": StreamEvent.SUMMARY_RESPONSE.value,
                "created_time": get_current_time()
            })

        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | "
                        f"[SupervisorNode] Reflecting on collected information.")
        else:
            logger.info(f"section_idx: {section_idx} | step title {step_title} | Current evidence_table item count "
                        f"{len(new_doc_infos)} | [SupervisorNode] Reflecting on collected information. "
                        f"ledger known/missing/attempted: "
                        f"{len(current_ledger.known_facts)}/{len(current_ledger.missing_evidence)}/"
                        f"{len(current_ledger.attempted_queries)} | ledger brief: {ledger_brief}")

        evidence_table = build_supervisor_evidence_table(new_doc_infos)
        report_type = session.get_global_state("collector_context.report_type") or "professional"

        agent_input = {
            "plan_title": plan_title,
            "plan_thought": plan_thought,
            "step_title": step_title,
            "step_description": step_description,
            "ledger_brief": ledger_brief,
            "evidence_table": evidence_table,
            "max_search_query_count": max_search_query_count,
            "language": state.get("language", "zh-CN"),
            "report_type": report_type,
        }
        agent_input.update(build_temporal_scope_prompt_context(state.get("research_intent")))
        formatted_prompt = apply_system_prompt("collector_supervisor", agent_input)

        result: Reflection = await self._invoke_llm_with_retry(
            formatted_prompt,
            section_idx,
            step_title,
            max_search_query_count,
        )

        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | "
                        f"[SupervisorNode] Reflection result.is_sufficient: {result.is_sufficient} | "
                        f"[SupervisorNode] Follow-up queries: {len(result.next_queries)}")
        else:
            logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                        f"[SupervisorNode] Reflection result: {result} | "
                        f"[SupervisorNode] Follow-up queries: {len(result.next_queries)}")

        state["reflection"] = result
        state["research_loop_count"] = research_loop_count + 1
        node_output = self._post_handle(inputs, state, session, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        """根据 supervisor 反思结果更新 collector 状态并决定下一节点。

        Args:
            inputs: 当前节点输入。
            algorithm_output: 包含 reflection 和递增后 research_loop_count 的节点输出。
            session: 当前运行 session。
            context: 图执行上下文。

        Returns:
            包含下一节点 ID 的输出字典。
        """
        max_research_loops = session.get_global_state("collector_context.max_research_loops")
        research_loop_count = algorithm_output["research_loop_count"]
        reflection: Reflection = algorithm_output["reflection"]

        session.update_global_state({"collector_context.research_loop_count": research_loop_count})
        session.update_global_state({"collector_context.is_sufficient": reflection.is_sufficient})
        session.update_global_state({"collector_context.should_continue": reflection.should_continue})
        session.update_global_state({"collector_context.knowledge_gap": reflection.knowledge_gap})

        missing_evidence = reflection.missing_evidence
        if not missing_evidence and reflection.knowledge_gap:
            missing_evidence = [reflection.knowledge_gap]
        ledger_update = EvidenceLedger(
            known_facts=reflection.known_facts,
            missing_evidence=missing_evidence,
        )
        current_ledger = ensure_ledger(session.get_global_state("collector_context.evidence_ledger"))
        updated_ledger = merge_ledger_update(
            current_ledger,
            ledger_update,
            clear_missing_evidence=reflection.is_sufficient,
        )
        session.update_global_state({"collector_context.evidence_ledger": updated_ledger.model_dump()})

        section_idx = session.get_global_state("collector_context.section_idx")
        plan_idx = algorithm_output.get("plan_idx", "")
        step_idx = algorithm_output.get("step_idx", "")
        step_title = algorithm_output.get("step_title", "")
        next_queries = reflection.next_queries
        if reflection.is_sufficient or not reflection.should_continue:
            next_queries = []
        if not reflection.is_sufficient and reflection.should_continue and not next_queries:
            logger.warning(
                "section_idx: %s | step_title: %s | [SupervisorNode] should_continue=True but "
                "next_queries is empty; falling back to one missing evidence query.",
                section_idx,
                step_title,
            )
            if updated_ledger.missing_evidence:
                next_queries = [updated_ledger.missing_evidence[0]]
            elif reflection.knowledge_gap:
                next_queries = [reflection.knowledge_gap]
        search_queries = [RetrievalQuery(
            query=query,
            search_engine_name=route_secondary_search_engine_for_query(query),
        ) for query in next_queries]
        session.update_global_state({"collector_context.search_queries": search_queries})

        stop_reason = "continue"
        if reflection.is_sufficient:
            stop_reason = "is_sufficient"
        elif research_loop_count >= max_research_loops:
            stop_reason = "max_research_loops"
        elif not reflection.should_continue:
            stop_reason = "should_continue_false"
        elif not search_queries:
            stop_reason = "no_follow_up_query"

        if LogManager.is_sensitive():
            logger.info(
                "section_idx: %s | plan_idx: %s | step_idx: %s | [SupervisorNode] loop_decision "
                "research_loop_count=%s max_research_loops=%s is_sufficient=%s should_continue=%s "
                "stop_reason=%s next_query_count=%s",
                section_idx, plan_idx, step_idx, research_loop_count, max_research_loops,
                reflection.is_sufficient, reflection.should_continue, stop_reason, len(search_queries),
            )
        else:
            logger.info(
                "section_idx: %s | plan_idx: %s | step_idx: %s | step_title: %s | "
                "[SupervisorNode] loop_decision research_loop_count=%s max_research_loops=%s "
                "is_sufficient=%s should_continue=%s stop_reason=%s next_query_count=%s",
                section_idx, plan_idx, step_idx, step_title, research_loop_count, max_research_loops,
                reflection.is_sufficient, reflection.should_continue, stop_reason, len(search_queries),
            )
        if reflection.is_sufficient:
            logger.info("section_idx: %s | step_title: %s | [SupervisorNode] End SupervisorNode. "
                        "cause: is_sufficient=True.", section_idx, step_title)
            return dict(next_node=NodeId.COLLECTOR_SUMMARY.value)
        if research_loop_count >= max_research_loops:
            logger.info("section_idx: %s | step_title: %s | [SupervisorNode] End SupervisorNode. "
                        "cause: research_loop_count reach max loops limit %s",
                        section_idx, step_title, max_research_loops)
            return dict(next_node=NodeId.COLLECTOR_SUMMARY.value)
        if not reflection.should_continue:
            logger.info("section_idx: %s | step_title: %s | [SupervisorNode] End SupervisorNode. "
                        "cause: should_continue=False.", section_idx, step_title)
            return dict(next_node=NodeId.COLLECTOR_SUMMARY.value)
        if not search_queries:
            logger.info("section_idx: %s | step_title: %s | [SupervisorNode] End SupervisorNode. "
                        "cause: no follow-up query.", section_idx, step_title)
            return dict(next_node=NodeId.COLLECTOR_SUMMARY.value)
        logger.info("section_idx: %s | step_title: %s | [SupervisorNode] End SupervisorNode.",
                    section_idx, step_title)

        return dict(next_node=NodeId.COLLECTOR_INFO.value)

    async def _invoke_llm_with_retry(
        self,
        formatted_prompt: list,
        section_idx: int,
        step_title: str,
        max_search_query_count: int,
    ):
        result = None
        for retry_idx in range(max_retries):
            try:
                result = await ainvoke_llm_with_stats(
                    self.llm, formatted_prompt, agent_name=AgentLlmName.COLLECTOR_SUPERVISOR.value, schema=Reflection)
                _validate_query_count(result.next_queries, max_search_query_count, "next_queries")
                break
            except Exception as e:
                current_try = retry_idx + 1
                task_description = "generate reflection"
                record_llm_retry_log(current_try, max_retries, section_idx, step_title,
                                     error=e, operation=task_description)

        if result is None or len(result.next_queries) > max_search_query_count:
            result = Reflection(
                is_sufficient=True,
                should_continue=False,
                knowledge_gap="",
                next_queries=[],
                known_facts=[],
                missing_evidence=[],
            )

        return result


class SummaryNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.llm: Any = None

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state("collector_context.section_idx")
        step_title = session.get_global_state("collector_context.step_title")
        logger.info("section_idx: %s | step_title: %s | [SummaryNode] Start SummaryNode.", section_idx, step_title)
        plan_title = session.get_global_state("collector_context.plan_title")
        plan_thought = session.get_global_state("collector_context.plan_thought")
        step_description = session.get_global_state("collector_context.step_description")
        step_background_knowledge = session.get_global_state("collector_context.step_background_knowledge")
        language = session.get_global_state("collector_context.language")
        doc_infos = session.get_global_state("collector_context.doc_infos")
        evidence_ledger = session.get_global_state("collector_context.evidence_ledger")
        llm_model_name = adapt_llm_model_name(session, NodeId.INFO_COLLECTOR.value)
        self.llm = llm_context.get().get(llm_model_name)

        return dict(section_idx=section_idx, plan_title=plan_title, plan_thought=plan_thought,
                    step_title=step_title, step_description=step_description,
                    language=language, doc_infos=doc_infos, step_background_knowledge=step_background_knowledge,
                    evidence_ledger=evidence_ledger)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        state = self._pre_handle(inputs, session, context)
        session_context.set(session)

        section_idx = state.get("section_idx", 0)
        plan_title = state.get("plan_title", "")
        plan_thought = state.get("plan_thought", "")
        step_title = state.get("step_title", "")
        step_description = state.get("step_description", "")
        doc_infos = state.get("doc_infos", [])
        step_background_knowledge = state.get("step_background_knowledge", [])
        current_ledger = ensure_ledger(state.get("evidence_ledger"))
        ledger_brief = build_ledger_brief(current_ledger)
        evidence_brief = ledger_brief

        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | "
                        f"[SummaryNode] Gathered {len(doc_infos)} unique items of information. | "
                        f"[SummaryNode] Starting to Generate summary based on collected information.")
        else:
            logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                        f"[SummaryNode] Gathered {len(doc_infos)} unique items of information. | "
                        f"[SummaryNode] Generating summary based on collected information.")
        logger.debug("section_idx: %s | step title %s | [SummaryNode] evidence_brief before summary:\n%s",
                     section_idx, step_title, evidence_brief)

        evidence_pack = build_summary_evidence_pack(doc_infos)

        agent_input = {
            "plan_title": plan_title,
            "plan_thought": plan_thought,
            "step_title": step_title,
            "step_description": step_description,
            "evidence_pack": evidence_pack,
            "language": state.get("language", "zh-CN"),
            "step_background_knowledge": step_background_knowledge,
            "ledger_brief": evidence_brief,
            "missing_evidence": current_ledger.missing_evidence,
        }
        formatted_prompt = apply_system_prompt("collector_final", agent_input)

        result: Summary = await self._invoke_llm_with_retry(formatted_prompt, state, doc_infos)

        node_output = self._post_handle(inputs, result, session, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: Summary, session: Session, context: ModelContext):
        section_idx = session.get_global_state("collector_context.section_idx")
        step_title = session.get_global_state("collector_context.step_title")
        session.update_global_state({"collector_context.info_summary": algorithm_output.info_summary})
        session.update_global_state({"collector_context.evaluation": algorithm_output.evaluation})
        next_node = NodeId.COLLECTOR_END.value
        logger.info(f"section_idx: %s | step_title %s | [SummaryNode] End SummaryNode.", section_idx, step_title)

        return dict(next_node=next_node)

    async def _invoke_llm_with_retry(self, formatted_prompt: list, state: dict, doc_infos: list):
        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")

        result = None
        for retry_idx in range(max_retries):
            try:
                result = await ainvoke_llm_with_stats(
                    self.llm, formatted_prompt, agent_name=AgentLlmName.COLLECTOR_SUMMARY.value, schema=Summary)
                break
            except Exception as e:
                current_try = retry_idx + 1
                task_description = "generate collector summary"
                record_llm_retry_log(current_try, max_retries, section_idx, step_title,
                                     error=e, operation=task_description)

        if result is None:
            logger.error(f"section_idx: {section_idx} | step_title {step_title} | [SummaryNode] "
                         f"Gathered {len(doc_infos)} items of information. Error when generate collector summary.")
            result = Summary(
                info_summary="",
                evaluation="",
            )

        return result


class GraphEndNode(BaseNode):

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state("collector_context.section_idx")
        plan_idx = session.get_global_state("collector_context.plan_idx")
        step_idx = session.get_global_state("collector_context.step_idx")
        logger.info(f"section_idx: {section_idx} | [GraphEndNode] Start GraphEndNode.")
        step_title = session.get_global_state("collector_context.step_title")
        info_summary = session.get_global_state("collector_context.info_summary")

        return dict(section_idx=section_idx, plan_idx=plan_idx, step_idx=step_idx,
                    step_title=step_title, info_summary=info_summary)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        state = self._pre_handle(inputs, session, context)

        section_idx = state.get("section_idx", 0)
        plan_idx = state.get("plan_idx", 0)
        step_idx = state.get("step_idx", 0)
        step_title = state.get("step_title", "")
        info_summary = state.get("info_summary", "")

        if info_summary:
            await session.write_custom_stream({
                "message_id": str(uuid.uuid4()),
                "plan_idx": str(plan_idx),
                "step_idx": str(step_idx),
                "agent": NodeId.COLLECTOR_SUMMARY.value,
                "content": info_summary,
                "message_type": MessageType.MESSAGE_CHUNK.value,
                "event": StreamEvent.SUMMARY_RESPONSE.value,
                "created_time": get_current_time()
            })

        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | "
                        f"[GraphEndNode] Finalizing the info collection graph.")
        else:
            logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                        f"[GraphEndNode] Finalizing the info collection graph.")

        node_output = self._post_handle(inputs, state, session, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        info_summary = algorithm_output.get("info_summary", "")

        messages: list = session.get_global_state("collector_context.messages")
        messages.append(AssistantMessage(content=info_summary))

        session.update_global_state({"collector_context.messages": messages})
        section_idx = session.get_global_state("collector_context.section_idx")
        logger.info(f"section_idx: {section_idx} | [GraphEndNode] End GraphEndNode.")

        return dict()


def build_info_collector_sub_graph() -> Workflow:
    """创建InfoCollector子图."""
    sub_workflow = Workflow()
    sub_workflow.set_start_comp(
        NodeId.START.value,
        StartNode(),
        inputs_schema={
            "language": "${language}", "messages": "${messages}",
            "section_idx": "${section_idx}", "plan_idx": "${plan_idx}",
            "plan_title": "${plan_title}", "plan_thought": "${plan_thought}",
            "step_idx": "${step_idx}", "step_title": "${step_title}",
            "step_description": "${step_description}",
            "step_background_knowledge": "${step_background_knowledge}",
            "max_search_query_count": "${max_search_query_count}",
            "max_research_loops": "${max_research_loops}",
            "max_tool_call_turns_per_query": "${max_tool_call_turns_per_query}",
            "report_type": "${report_type}",
            "research_intent": "${research_intent}",
        }
    )
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_QUERY_GEN.value, GenerateQueryNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_INFO.value, InfoRetrievalNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_WEBPAGE_ENRICHMENT.value, WebPageEnrichmentNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_SUPERVISOR.value, SupervisorNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_SUMMARY.value, SummaryNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_END.value, GraphEndNode())
    sub_workflow.set_end_comp(NodeId.END.value, End())

    # 添加边 add_connection
    sub_workflow.add_connection(NodeId.START.value, NodeId.COLLECTOR_QUERY_GEN.value)
    sub_workflow.add_connection(NodeId.COLLECTOR_QUERY_GEN.value, NodeId.COLLECTOR_INFO.value)
    sub_workflow.add_connection(NodeId.COLLECTOR_INFO.value, NodeId.COLLECTOR_WEBPAGE_ENRICHMENT.value)
    sub_workflow.add_connection(
        NodeId.COLLECTOR_WEBPAGE_ENRICHMENT.value,
        NodeId.COLLECTOR_SUPERVISOR.value,
    )
    supervisor_router = init_router(NodeId.COLLECTOR_SUPERVISOR.value,
                                    [NodeId.COLLECTOR_SUMMARY.value, NodeId.COLLECTOR_INFO.value])
    sub_workflow.add_conditional_connection(NodeId.COLLECTOR_SUPERVISOR.value, router=supervisor_router)
    sub_workflow.add_connection(NodeId.COLLECTOR_SUMMARY.value, NodeId.COLLECTOR_END.value)
    sub_workflow.add_connection(NodeId.COLLECTOR_END.value, NodeId.END.value)

    return sub_workflow
