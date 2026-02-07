# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import uuid
import json
from typing import List, Any

from pydantic import BaseModel, Field

from openjiuwen.core.component.end_comp import End
from openjiuwen.core.component.start_comp import Start
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.core.utils.llm.messages import AIMessage

from jiuwen_deepsearch.framework.jiuwen.agent.base_node import BaseNode, init_router
from jiuwen_deepsearch.framework.jiuwen.agent.collector_graph.collector_state import CollectorState
from jiuwen_deepsearch.framework.jiuwen.agent.collector_graph.info_collector import InfoRetrievalNode
from jiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from jiuwen_deepsearch.config.config import ServiceConfig
from jiuwen_deepsearch.utils.constants_utils.runtime_contextvars import llm_context
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from jiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, record_llm_retry_log
from jiuwen_deepsearch.utils.common_utils.stream_utils import MessageType, StreamEvent, get_current_time

from jiuwen_deepsearch.utils.constants_utils.runtime_contextvars import runtime_context

logger = logging.getLogger(__name__)

max_retries = ServiceConfig().info_collector_max_retry_num


class SearchQueryList(BaseModel):
    query: List[str] = Field(
        description="A list of search queries to be used for web research."
    )
    description: str = Field(
        description="A brief explanation of why these queries are relevant to the research topic."
    )


class Reflection(BaseModel):
    is_sufficient: bool = Field(
        description="Whether the provided summaries are sufficient to answer the user's question."
    )
    knowledge_gap: str = Field(
        description="A description of what information is missing or needs clarification."
    )
    next_queries: List[str] = Field(
        description="A list of follow-up queries to address the knowledge gap."
    )


class Summary(BaseModel):
    need_programmer: bool = Field(
        description="Indicates whether a programmer is needed for further assistance."
    )
    programmer_task: str = Field(
        description="A detailed description of the task to be assigned to the programmer."
    )
    info_summary: str = Field(
        description="A concise summary of the collected information relevant to the research topic."
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


class StartNode(Start):
    """
    起始节点，初始化 Runtime global_state 中的 search_context 和 config
    """

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        """Invoke method of StartNode."""
        # 校验input
        self._validate_inputs(inputs)
        inputs = self._fill_default_values(inputs)

        # 初始化search_context
        collector_context = CollectorState(
            language=inputs.get("language", "zh-CN"),
            messages=inputs.get("messages", []),
            section_idx=inputs.get("section_idx", 0),
            plan_idx=inputs.get("plan_idx", 0),
            step_idx=inputs.get("step_idx", 0),
            step_title=inputs.get("step_title", ""),
            step_description=inputs.get("step_description", []),
            initial_search_query_count=inputs.get("initial_search_query_count", 1),
            max_research_loops=inputs.get("max_research_loops", 1),
            max_react_recursion_limit=inputs.get("max_react_recursion_limit", 5),
            research_loop_count=0,
        )
        runtime.update_global_state({"collector_context": collector_context.model_dump()})

        return inputs


class GenerateQueryNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.llm: Any = None

    def _pre_handle(self, inputs: Input, runtime: Runtime, context: Context):
        section_idx = runtime.get_global_state("collector_context.section_idx")
        logger.info(f"section_idx: {section_idx} | [GenerateQueryNode] Start GenerateQueryNode.")
        step_title = runtime.get_global_state("collector_context.step_title")
        messages = runtime.get_global_state("collector_context.messages")
        number_queries = runtime.get_global_state("collector_context.initial_search_query_count")
        language = runtime.get_global_state("collector_context.language")
        max_research_loops = runtime.get_global_state("collector_context.max_research_loops")
        max_react_recursion_limit = runtime.get_global_state("collector_context.max_react_recursion_limit")

        step_num = (max_react_recursion_limit - 2) // max_research_loops - 1
        max_tool_steps = max(int(step_num), 1)
        runtime.update_global_state({"collector_context.max_tool_steps": max_tool_steps})
        llm_model_name = runtime.get_global_state("config.llm_config.model_name")
        self.llm = llm_context.get().get(llm_model_name)

        return dict(section_idx=section_idx, step_title=step_title,
                    messages=messages, number_queries=number_queries,
                    language=language)

    async def _do_invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        state = self._pre_handle(inputs, runtime, context)
        runtime_context.set(runtime)

        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")
        messages = state.get("messages", [])
        number_queries = state.get("number_queries", 1)
        language = state.get("language", "zh-CN")

        agent_input = {
            "research_record": get_research_record(messages),
            "number_queries": number_queries,
            "language": language
        }
        formatted_prompt = apply_system_prompt("collector_gen_query", agent_input)

        result: SearchQueryList = await self._invoke_llm_with_retry(formatted_prompt, section_idx, step_title)

        if len(result.query) > number_queries:
            result.query = result.query[:number_queries]

        if not LogManager.is_sensitive():
            logger.debug("section_idx: %s | step title %s | [GenerateQueryNode] Generated search queries: %s",
                         section_idx, step_title, result.query)
            logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                        f"[GenerateQueryNode] Initial queries count: {len(result.query)}")
        else:
            logger.info(f"section_idx: {section_idx} |"
                        f"[GenerateQueryNode] Initial queries count: {len(result.query)}")

        node_output = self._post_handle(inputs, result, runtime, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: SearchQueryList, runtime: Runtime, context: Context):
        runtime.update_global_state({"collector_context.search_query": algorithm_output.query})
        section_idx = runtime.get_global_state("collector_context.section_idx")
        logger.info(f"section_idx: {section_idx} | [GenerateQueryNode] End GenerateQueryNode.")

        return dict()

    async def _invoke_llm_with_retry(self, formatted_prompt: list, section_idx: int, step_title: str):
        result = None
        for retry_idx in range(max_retries):
            try:
                result = await ainvoke_llm_with_stats(
                    self.llm, formatted_prompt, agent_name=NodeId.COLLECTOR_QUERY_GEN.value, schema=SearchQueryList)
                break
            except Exception as e:
                current_try = retry_idx + 1
                task_description = "generate search query"
                record_llm_retry_log(current_try, max_retries, section_idx, step_title,
                                     error=e, operation=task_description)
        if result is None:
            result = SearchQueryList(
                query=[step_title],
                description="Error when generate search query, use step title as query."
            )

        return result


class SupervisorNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.llm: Any = None

    def _pre_handle(self, inputs: Input, runtime: Runtime, context: Context):
        section_idx = runtime.get_global_state("collector_context.section_idx")
        plan_idx = runtime.get_global_state("collector_context.plan_idx")
        step_idx = runtime.get_global_state("collector_context.step_idx")
        logger.info(f"section_idx: {section_idx} | [SupervisorNode] Start SupervisorNode.")
        step_title = runtime.get_global_state("collector_context.step_title")
        step_description = runtime.get_global_state("collector_context.step_description")
        number_queries = runtime.get_global_state("collector_context.initial_search_query_count")
        language = runtime.get_global_state("collector_context.language")
        doc_infos = runtime.get_global_state("collector_context.doc_infos")
        new_doc_infos_current_loop = runtime.get_global_state("collector_context.new_doc_infos_current_loop")
        research_loop_count = runtime.get_global_state("collector_context.research_loop_count")
        llm_model_name = runtime.get_global_state("config.llm_config.model_name")
        self.llm = llm_context.get().get(llm_model_name)

        return dict(section_idx=section_idx, plan_idx=plan_idx, step_idx=step_idx, step_title=step_title,
                    step_description=step_description,
                    number_queries=number_queries, language=language, doc_infos=doc_infos,
                    new_doc_infos_current_loop=new_doc_infos_current_loop, research_loop_count=research_loop_count)

    async def _do_invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        state = self._pre_handle(inputs, runtime, context)
        runtime_context.set(runtime)

        section_idx = state.get("section_idx", 0)
        plan_idx = state.get("plan_idx", 0)
        step_idx = state.get("step_idx", 0)
        step_title = state.get("step_title", "")
        research_loop_count = state.get("research_loop_count", 1)
        number_queries = state.get("number_queries", 1)
        doc_infos = state.get("doc_infos", [])

        for item in state.get("new_doc_infos_current_loop", []):
            result = {
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "query": item.get("query", ""),
            }
            await runtime.write_custom_stream({
                "message_id": str(uuid.uuid4()),
                "plan_idx": str(plan_idx),
                "step_idx": str(step_idx),
                "agent": NodeId.COLLECTOR_INFO.value,
                "content": json.dumps(result, ensure_ascii=False),
                "message_type": MessageType.MESSAGE_CHUNK.value,
                "event": StreamEvent.SUMMARY_RESPONSE.value,
                "created_time": get_current_time()
            })

        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | "
                        f"[SupervisorNode] Reflecting on collected information.")
        else:
            logger.info(f"section_idx: {section_idx} | step title {step_title} | Current doc_infos item count "
                        f"{len(doc_infos)} | [SupervisorNode] Reflecting on collected information doc_infos.")

        agent_input = {
            "research_record": f"[Task Title]: {step_title}\n[Task Description]: {state.get('step_description', '')}",
            "number_queries": number_queries,
            "language": state.get("language", "zh-CN"),
            "doc_infos": doc_infos,
        }
        formatted_prompt = apply_system_prompt("collector_supervisor", agent_input)

        result: Reflection = await self._invoke_llm_with_retry(formatted_prompt, section_idx, step_title)

        if len(result.next_queries) > number_queries:
            result.next_queries = result.next_queries[:number_queries]

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
        node_output = self._post_handle(inputs, state, runtime, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: dict, runtime: Runtime, context: Context):
        max_research_loops = runtime.get_global_state("collector_context.max_research_loops")
        research_loop_count = algorithm_output["research_loop_count"]
        reflection: Reflection = algorithm_output["reflection"]

        runtime.update_global_state({"collector_context.research_loop_count": research_loop_count})
        runtime.update_global_state({"collector_context.is_sufficient": reflection.is_sufficient})
        runtime.update_global_state({"collector_context.knowledge_gap": reflection.knowledge_gap})
        runtime.update_global_state({"collector_context.search_query": reflection.next_queries})

        section_idx = runtime.get_global_state("collector_context.section_idx")
        step_title = algorithm_output.get("step_title", "")
        if reflection.is_sufficient:
            logger.info("section_idx: %s | step_title: %s | [SupervisorNode] End SupervisorNode. "
                        "cause: is_sufficient=True.", section_idx, step_title)
            return dict(next_node=NodeId.COLLECTOR_SUMMARY.value)
        if research_loop_count >= max_research_loops:
            logger.info("section_idx: %s | step_title: %s | [SupervisorNode] End SupervisorNode. "
                        "cause: research_loop_count reach max loops limit %s",
                        section_idx, step_title, max_research_loops)
            return dict(next_node=NodeId.COLLECTOR_SUMMARY.value)
        logger.info("section_idx: %s | step_title: %s | [SupervisorNode] End SupervisorNode.",
                    section_idx, step_title)

        return dict(next_node=NodeId.COLLECTOR_INFO.value)

    async def _invoke_llm_with_retry(self, formatted_prompt: list, section_idx: int, step_title: int):
        result = None
        for retry_idx in range(max_retries):
            try:
                result = await ainvoke_llm_with_stats(
                    self.llm, formatted_prompt, agent_name=NodeId.COLLECTOR_SUPERVISOR.value, schema=Reflection)
                break
            except Exception as e:
                current_try = retry_idx + 1
                task_description = "generate reflection"
                record_llm_retry_log(current_try, max_retries, section_idx, step_title,
                                     error=e, operation=task_description)

        if result is None:
            result = Reflection(
                is_sufficient=True,
                knowledge_gap="",
                next_queries=[]
            )

        return result


class SummaryNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.llm: Any = None

    def _pre_handle(self, inputs: Input, runtime: Runtime, context: Context):
        section_idx = runtime.get_global_state("collector_context.section_idx")
        step_title = runtime.get_global_state("collector_context.step_title")
        logger.info("section_idx: %s | step_title: %s | [SummaryNode] Start SummaryNode.", section_idx, step_title)
        step_description = runtime.get_global_state("collector_context.step_description")
        language = runtime.get_global_state("collector_context.language")
        doc_infos = runtime.get_global_state("collector_context.doc_infos")
        llm_model_name = runtime.get_global_state("config.llm_config.model_name")
        self.llm = llm_context.get().get(llm_model_name)

        return dict(section_idx=section_idx, step_title=step_title, step_description=step_description,
                    language=language, doc_infos=doc_infos)

    async def _do_invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        state = self._pre_handle(inputs, runtime, context)
        runtime_context.set(runtime)

        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")
        step_description = state.get("step_description", "")
        doc_infos = state.get("doc_infos", [])

        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | "
                        f"[SummaryNode] Gathered {len(doc_infos)} unique items of information. | "
                        f"[SummaryNode] Starting to Generate summary based on collected information.")
        else:
            logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                        f"[SummaryNode] Gathered {len(doc_infos)} unique items of information. | "
                        f"[SummaryNode] Generating summary based on collected information.")

        agent_input = {
            "research_record": f"[Task Title]: {step_title}\n[Task Description]: {step_description}",
            "doc_infos": doc_infos,
            "language": state.get("language", "zh-CN"),
        }
        formatted_prompt = apply_system_prompt("collector_final", agent_input)

        result: Summary = await self._invoke_llm_with_retry(formatted_prompt, state, doc_infos)

        node_output = self._post_handle(inputs, result, runtime, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: Summary, runtime: Runtime, context: Context):
        section_idx = runtime.get_global_state("collector_context.section_idx")
        step_title = runtime.get_global_state("collector_context.step_title")
        runtime.update_global_state({"collector_context.need_programmer": algorithm_output.need_programmer})
        runtime.update_global_state({"collector_context.programmer_task": algorithm_output.programmer_task})
        runtime.update_global_state({"collector_context.info_summary": algorithm_output.info_summary})
        allow_programmer = runtime.get_global_state("config.info_collector_allow_programmer")
        if algorithm_output.need_programmer and allow_programmer:
            next_node = NodeId.COLLECTOR_PROGRAMMER.value
        else:
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
                    self.llm, formatted_prompt, agent_name=NodeId.COLLECTOR_SUMMARY.value, schema=Summary)
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
                need_programmer=False,
                programmer_task="",
                info_summary=""
            )

        return result


class ProgrammerNode(BaseNode):

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, runtime: Runtime, context: Context):
        logger.info(f"[ProgrammerNode] Start ProgrammerNode.")
        return dict()

    async def _do_invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        logger.info(f"[ProgrammerNode] ProgrammerNode is current not available, go to graph end.")
        algorithm_output = {}
        result = self._post_handle(inputs, algorithm_output, runtime, context)
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, runtime: Runtime, context: Context):
        logger.info(f"[ProgrammerNode] End ProgrammerNode.")
        return dict()


class GraphEndNode(BaseNode):

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, runtime: Runtime, context: Context):
        section_idx = runtime.get_global_state("collector_context.section_idx")
        plan_idx = runtime.get_global_state("collector_context.plan_idx")
        step_idx = runtime.get_global_state("collector_context.step_idx")
        logger.info(f"section_idx: {section_idx} | [GraphEndNode] Start GraphEndNode.")
        step_title = runtime.get_global_state("collector_context.step_title")
        info_summary = runtime.get_global_state("collector_context.info_summary")
        doc_infos = runtime.get_global_state("collector_context.doc_infos")
        gathered_info = runtime.get_global_state("collector_context.gathered_info")

        return dict(section_idx=section_idx, plan_idx=plan_idx, step_idx=step_idx, step_title=step_title,
                    info_summary=info_summary,
                    doc_infos=doc_infos, gathered_info=gathered_info)

    async def _do_invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        state = self._pre_handle(inputs, runtime, context)

        section_idx = state.get("section_idx", 0)
        plan_idx = state.get("plan_idx", 0)
        step_idx = state.get("step_idx", 0)
        step_title = state.get("step_title", "")
        info_summary = state.get("info_summary", "")

        if info_summary:
            await runtime.write_custom_stream({
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

        node_output = self._post_handle(inputs, state, runtime, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: dict, runtime: Runtime, context: Context):
        info_summary = algorithm_output.get("info_summary", "")

        messages: list = runtime.get_global_state("collector_context.messages")
        messages.append(AIMessage(content=info_summary))

        runtime.update_global_state({"collector_context.messages": messages})
        section_idx = runtime.get_global_state("collector_context.section_idx")
        logger.info(f"section_idx: {section_idx} | [GraphEndNode] End GraphEndNode.")

        return dict()


def build_info_collector_sub_graph() -> Workflow:
    """创建InfoCollector子图."""
    sub_workflow = Workflow()
    sub_workflow.set_start_comp(
        NodeId.START.value,
        StartNode(
            {"inputs": [
                {"id": "language", "type": "String", "required": "true", "sourceType": "ref"},
                {"id": "messages", "type": "list", "required": "true", "sourceType": "ref"},
                {"id": "section_idx", "type": "Integer", "required": "true", "sourceType": "ref"},
                {"id": "plan_idx", "type": "Integer", "required": "true", "sourceType": "ref"},
                {"id": "step_idx", "type": "Integer", "required": "true", "sourceType": "ref"},
                {"id": "step_title", "type": "String", "required": "true", "sourceType": "ref"},
                {"id": "step_description", "type": "String", "required": "true", "sourceType": "ref"},
                {"id": "initial_search_query_count", "type": "Integer", "required": "true", "sourceType": "ref"},
                {"id": "max_research_loops", "type": "Integer", "required": "true", "sourceType": "ref"},
                {"id": "max_react_recursion_limit", "type": "Integer", "required": "true", "sourceType": "ref"}
            ]}
        ),
        inputs_schema={
            "language": "${language}", "messages": "${messages}",
            "section_idx": "${section_idx}", "plan_idx": "${plan_idx}",
            "step_idx": "${step_idx}", "step_title": "${step_title}",
            "step_description": "${step_description}",
            "initial_search_query_count": "${initial_search_query_count}",
            "max_research_loops": "${max_research_loops}",
            "max_react_recursion_limit": "${max_react_recursion_limit}"
        }
    )
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_QUERY_GEN.value, GenerateQueryNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_INFO.value, InfoRetrievalNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_SUPERVISOR.value, SupervisorNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_SUMMARY.value, SummaryNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_PROGRAMMER.value, ProgrammerNode())
    sub_workflow.add_workflow_comp(NodeId.COLLECTOR_END.value, GraphEndNode())
    sub_workflow.set_end_comp(NodeId.END.value, End())

    # 添加边 add_connection
    sub_workflow.add_connection(NodeId.START.value, NodeId.COLLECTOR_QUERY_GEN.value)
    sub_workflow.add_connection(NodeId.COLLECTOR_QUERY_GEN.value, NodeId.COLLECTOR_INFO.value)
    sub_workflow.add_connection(NodeId.COLLECTOR_INFO.value, NodeId.COLLECTOR_SUPERVISOR.value)
    supervisor_router = init_router(NodeId.COLLECTOR_SUPERVISOR.value,
                                    [NodeId.COLLECTOR_SUMMARY.value, NodeId.COLLECTOR_INFO.value])
    sub_workflow.add_conditional_connection(NodeId.COLLECTOR_SUPERVISOR.value, router=supervisor_router)
    summary_router = init_router(NodeId.COLLECTOR_SUMMARY.value,
                                 [NodeId.COLLECTOR_PROGRAMMER.value, NodeId.COLLECTOR_END.value])
    sub_workflow.add_conditional_connection(NodeId.COLLECTOR_SUMMARY.value, router=summary_router)
    sub_workflow.add_connection(NodeId.COLLECTOR_PROGRAMMER.value, NodeId.COLLECTOR_END.value)
    sub_workflow.add_connection(NodeId.COLLECTOR_END.value, NodeId.END.value)

    return sub_workflow
