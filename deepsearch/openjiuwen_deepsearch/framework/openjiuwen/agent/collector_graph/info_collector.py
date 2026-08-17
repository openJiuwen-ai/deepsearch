# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.research_collector.collector_function import process_tool_call, \
    process_tool_result, remove_duplicate_items
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    CollectorSourceStore,
    build_evaluation_documents,
    build_evidence_atom,
    normalize_doc_info_scores_and_time,
    normalize_scores,
)
from openjiuwen_deepsearch.algorithm.research_collector.doc_evaluation import run_doc_evaluation
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import (
    append_attempted_queries,
    ensure_ledger,
)
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import adapt_llm_model_name
from openjiuwen_deepsearch.framework.openjiuwen.tools import create_web_search_tool, create_local_search_tool, \
    build_runtime_api_tools
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, record_llm_retry_log
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId
from openjiuwen_deepsearch.utils.constants_utils.search_engine_constants import LocalSearch, SearchEngine
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

max_retries = Config().service_config.info_collector_max_retry_num
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectSearchRequest:
    tool: Any
    tool_name: str
    query: str
    search_engine_name: str
    fallback_to_default: bool = True
    retry_on_error: bool = True


@dataclass(frozen=True)
class VerticalSearchFallbackLog:
    section_idx: int
    step_title: str
    search_engine_name: str
    default_search_engine_name: str
    query: str
    reason: str


@dataclass(frozen=True)
class VerticalSearchFailureDetailLog:
    section_idx: int
    step_title: str
    operation: str
    error: Any
    extra_info: str
    level: int = logging.INFO


def _merge_source_store(
    source_store: dict,
    task_source_store: dict,
    section_idx: int | str,
    search_query: str,
) -> None:
    """合并单个 query 的 source store，遇到冲突时保留首个正文。

    Args:
        source_store: collector session 中已聚合的 source_id 到正文映射。
        task_source_store: 当前 query 生成的 source_id 到正文映射。
        section_idx: 当前章节索引，用于日志。
        search_query: 当前检索 query，用于定位冲突来源。
    """
    for source_id, content in task_source_store.items():
        if source_id in source_store:
            if source_store[source_id] != content:
                logger.warning(
                    "section_idx: %s | [InfoRetrievalNode] source_store source_id conflict. "
                    "source_id=%s | query=%s | keeping first content.",
                    section_idx,
                    source_id,
                    search_query,
                )
            continue
        source_store[source_id] = content


class InfoRetrievalNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.llm: Any = None

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state("collector_context.section_idx")
        step_title = session.get_global_state("collector_context.step_title")
        if LogManager.is_sensitive():
            logger.info("section_idx: %s | [InfoRetrievalNode] Start InfoRetrievalNode.", section_idx)
        else:
            logger.info("section_idx: %s | step title: %s | [InfoRetrievalNode] Start InfoRetrievalNode.",
                        section_idx, step_title)
        web_search_engine_config = session.get_global_state("config.web_search_engine_config")
        web_search_engine_name = web_search_engine_config.search_engine_name if \
            web_search_engine_config else SearchEngine.PETAL.value
        local_search_engine_config = session.get_global_state("config.local_search_engine_config")
        local_search_engine_name = local_search_engine_config.search_engine_name if \
            local_search_engine_config else LocalSearch.OPENAPI.value
        llm_model_name = adapt_llm_model_name(session, NodeId.INFO_COLLECTOR.value)
        self.llm = llm_context.get().get(llm_model_name)

        state = dict(
            search_queries=session.get_global_state("collector_context.search_queries"),
            max_tool_call_turns_per_query=session.get_global_state(
                "collector_context.max_tool_call_turns_per_query"
            ),
            section_idx=section_idx,
            plan_idx=session.get_global_state("collector_context.plan_idx"),
            step_idx=session.get_global_state("collector_context.step_idx"),
            step_title=step_title,
            research_loop_count=session.get_global_state("collector_context.research_loop_count"),
            max_research_loops=session.get_global_state("collector_context.max_research_loops"),
            search_method=session.get_global_state("config.info_collector_search_method"),
            web_search_engine_name=web_search_engine_name,
            local_search_engine_name=local_search_engine_name,
            api_tools_config=session.get_global_state("config.api_tools_config") or {},
            research_intent=session.get_global_state("collector_context.research_intent") or {},
        )
        return state

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        state = self._pre_handle(inputs, session, context)
        session_context.set(session)

        tasks = []
        for retrieval_query in state.get("search_queries", []):
            sub_state = {
                "search_query": retrieval_query.query,
                "section_idx": state.get("section_idx", 0),
                "plan_idx": state.get("plan_idx", 0),
                "step_idx": state.get("step_idx", 0),
                "step_title": state.get("step_title", ""),
                "max_tool_call_turns_per_query": state.get("max_tool_call_turns_per_query", 2),
                "search_method": state.get("search_method", "web"),
                "web_search_engine_name": state.get("web_search_engine_name", None),
                "secondary_web_search_engine_name": getattr(retrieval_query, "search_engine_name", ""),
                "local_search_engine_name": state.get("local_search_engine_name", None),
                "api_tools_config": state.get("api_tools_config", {}),
                "research_intent": state.get("research_intent", {}),
            }
            sub_task = self._collector_main(sub_state)
            tasks.append(sub_task)
        tasks_results = await asyncio.gather(*tasks)

        node_output = self._post_handle(inputs, tasks_results, session, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: list, session: Session, context: ModelContext):
        """合并子查询采集结果并更新 collector session 状态。

        Args:
            inputs: 当前节点输入。
            algorithm_output: 各 retrieval query 的采集结果。
            session: 当前运行 session。
            context: 图执行上下文。

        Returns:
            空输出字典。
        """
        section_idx = session.get_global_state("collector_context.section_idx")
        plan_idx = session.get_global_state("collector_context.plan_idx")
        step_idx = session.get_global_state("collector_context.step_idx")
        step_title = session.get_global_state("collector_context.step_title")
        research_loop_count = session.get_global_state("collector_context.research_loop_count") or 0
        max_research_loops = session.get_global_state("collector_context.max_research_loops")
        doc_infos: list = session.get_global_state("collector_context.doc_infos") or []
        search_queries = session.get_global_state("collector_context.search_queries")
        history_queries = session.get_global_state("collector_context.history_queries")
        current_ledger = ensure_ledger(session.get_global_state("collector_context.evidence_ledger"))
        source_store = session.get_global_state("collector_context.source_store") or {}

        new_doc_infos = []
        attempted_queries = []
        for retrieval_query, result in zip(search_queries, algorithm_output):
            known_source_ids = {
                doc.get("source_id") for doc in doc_infos
                if isinstance(doc, dict) and doc.get("source_id")
            }
            core_doc_info = [(doc.get("title", ""), doc.get("url", "")) for doc in doc_infos if isinstance(doc, dict)]
            task_doc_infos = result.get("doc_infos", [])
            attempted_queries.append(retrieval_query.query)
            task_source_store = result.get("source_store", {})
            if isinstance(task_source_store, dict):
                _merge_source_store(
                    source_store=source_store,
                    task_source_store=task_source_store,
                    section_idx=section_idx,
                    search_query=result.get("search_query", ""),
                )
            if LogManager.is_sensitive():
                logger.info(f"section_idx: {section_idx} | gathered item count before duplicate: {len(task_doc_infos)}")
            else:
                logger.info(f"section_idx: {section_idx} | step title: {step_title} | "
                            f"[InfoRetrievalNode] Query: {result.get('search_query', '')} | "
                            f"gathered item count before duplicate: {len(task_doc_infos)}")
            for task_doc in task_doc_infos:
                source_id = task_doc.get("source_id")
                is_new_source = source_id and source_id not in known_source_ids
                legacy_doc_key = (task_doc.get("title", ""), task_doc.get("url", ""))
                is_new_legacy_doc = not source_id and legacy_doc_key not in core_doc_info
                if is_new_source or is_new_legacy_doc:
                    # 记录本地新收集信息
                    new_doc_infos.append(task_doc)
                    if source_id:
                        known_source_ids.add(source_id)
                    else:
                        core_doc_info.append(legacy_doc_key)

            retrieval_query.doc_infos = task_doc_infos
            history_queries.append(retrieval_query)
            doc_infos.extend(task_doc_infos)

        doc_infos = remove_duplicate_items(doc_infos)
        updated_ledger = append_attempted_queries(current_ledger, attempted_queries)

        session.update_global_state({"collector_context.history_queries": history_queries})
        session.update_global_state({"collector_context.new_doc_infos_current_loop": new_doc_infos})
        session.update_global_state({"collector_context.doc_infos": doc_infos})
        session.update_global_state({"collector_context.evidence_ledger": updated_ledger.model_dump()})
        session.update_global_state({"collector_context.source_store": source_store})
        if LogManager.is_sensitive():
            logger.info("section_idx: %s | [InfoRetrievalNode] End InfoRetrievalNode.", section_idx)
            logger.info(
                "section_idx: %s | plan_idx: %s | step_idx: %s | [InfoRetrievalNode] loop_summary "
                "research_loop_index=%s max_research_loops=%s query_count=%s new_doc_count=%s "
                "total_doc_count=%s attempted_query_count=%s",
                section_idx, plan_idx, step_idx, research_loop_count + 1, max_research_loops,
                len(search_queries or []), len(new_doc_infos), len(doc_infos), len(updated_ledger.attempted_queries),
            )
        else:
            logger.info("section_idx: %s | step title: %s | [InfoRetrievalNode] End InfoRetrievalNode."
                        "Get %s doc_infos item. attempted query count: %s",
                        section_idx, step_title, len(doc_infos), len(updated_ledger.attempted_queries))
            logger.info(
                "section_idx: %s | plan_idx: %s | step_idx: %s | step_title: %s | "
                "[InfoRetrievalNode] loop_summary research_loop_index=%s max_research_loops=%s "
                "query_count=%s new_doc_count=%s total_doc_count=%s attempted_query_count=%s",
                section_idx, plan_idx, step_idx, step_title, research_loop_count + 1, max_research_loops,
                len(search_queries or []), len(new_doc_infos), len(doc_infos), len(updated_ledger.attempted_queries),
            )

        return dict()

    async def _collector_main(self, state: dict):
        """执行单个检索 query 的信息收集和后处理。

        Args:
            state: 当前 query 的 collector 子状态。

        Returns:
            包含 doc_infos、source_store、原始工具记录和 query 的结果字典。
        """
        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")
        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | [InfoRetrievalNode] Start InfoRetrieval main function."
                        f"Collecting info for query, Max tool call turns per query: "
                        f"{state['max_tool_call_turns_per_query']}")
        else:
            logger.info(f"section_idx: {section_idx} | [InfoRetrievalNode] Start InfoRetrieval main function. | "
                        f"step title: {step_title} Collecting info for query: {state['search_query']} | "
                        f"Max tool call turns per query: {state['max_tool_call_turns_per_query']}")

        query = state.get("search_query", step_title)
        agent_input = {
            "messages": [UserMessage(content=f"Now deal with the Query:\n[Query]: {query}\n\n"), ],
            "remaining_steps": None,
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": [],
            "research_intent": state.get("research_intent", {}),
        }

        tool_list, tool_dict = self._prepare_collector_tool(state)

        # 当 collector_tools 为空且 search_method 为 web/local 时，直接调用对应 search tool
        collector_tools = state.get("api_tools_config", {}).get("collector_tools", [])
        search_method = state.get("search_method", "web")
        if not collector_tools and search_method in ("web", "local"):
            # 直接调用对应 search tool
            tool_name = f"{search_method}_search_tool"
            if tool_name not in tool_dict:
                if LogManager.is_sensitive():
                    logger.error(f"section_idx: {section_idx} | "
                                 f"[InfoRetrievalNode] Direct call: tool '{tool_name}' not found in tool_dict")
                else:
                    logger.error(f"section_idx: {section_idx} | step title: {step_title} | "
                                 f"[InfoRetrievalNode] Direct call: tool '{tool_name}' not found in tool_dict")
                processed_results = []
            else:
                processed_results = []
                if search_method == "web":
                    engine_names = self._web_search_engines_for_query(state)
                    default_search_engine_name = str(
                        state.get("web_search_engine_name") or SearchEngine.PETAL.value
                    ).strip()
                    fallback_to_default = default_search_engine_name not in engine_names
                    raw_results = await asyncio.gather(*[
                        self._direct_search_with_retry(
                            DirectSearchRequest(
                                tool=tool_dict[tool_name],
                                tool_name=tool_name,
                                query=query,
                                search_engine_name=engine_name,
                                fallback_to_default=fallback_to_default,
                                retry_on_error=True,
                            ),
                            state,
                        )
                        for engine_name in engine_names
                    ])
                    for tool_result_raw in raw_results:
                        if not tool_result_raw:
                            continue
                        tool_result_json = json.dumps(tool_result_raw, ensure_ascii=False, indent=4)
                        processed_results.extend(process_tool_result(tool_name, tool_result_json, agent_input))
                    if LogManager.is_sensitive():
                        logger.info(f"section_idx: {section_idx} | "
                                    f"[InfoRetrievalNode] Direct web calls completed, engines: {len(engine_names)}")
                    else:
                        logger.info(f"section_idx: {section_idx} | step title: {step_title} | "
                                    f"[InfoRetrievalNode] Direct web calls for {engine_names}, results: "
                                    f"{len(processed_results)}")
                else:
                    for retry_idx in range(max_retries):
                        try:
                            search_engine_name = state.get(f"{search_method}_search_engine_name")
                            tool_call_args = {"query": query, "search_engine_name": search_engine_name}
                            tool_result_raw = await tool_dict[tool_name].invoke(tool_call_args)

                            if isinstance(tool_result_raw, dict) and tool_result_raw.get("error"):
                                error_msg = tool_result_raw.get("error", "")
                                current_try = retry_idx + 1
                                record_llm_retry_log(
                                    current_try=current_try, max_retries=max_retries,
                                    section_idx=section_idx, step_title=step_title,
                                    operation=f"direct call search tool '{tool_name}' returned error",
                                    error=error_msg, extra_info=query,
                                )
                                if current_try < max_retries:
                                    continue
                                processed_results = []
                                break

                            tool_result_json = json.dumps(tool_result_raw, ensure_ascii=False, indent=4)
                            processed_results = process_tool_result(tool_name, tool_result_json, agent_input)
                            break

                        except Exception as e:
                            current_try = retry_idx + 1
                            record_llm_retry_log(
                                current_try=current_try, max_retries=max_retries,
                                section_idx=section_idx, step_title=step_title,
                                operation=f"direct call search tool '{tool_name}'",
                                error=e, extra_info=query,
                            )
                            if current_try < max_retries:
                                continue
                            processed_results = []
        else:
            # MCP/custom tools 或多工具复杂路由走 LLM tool-calling
            state, agent_input = await self._collector_llm(state, agent_input, tool_list, tool_dict)
            agent_input = await self._run_secondary_web_search_if_needed(state, agent_input, tool_dict)

        web_record, local_record = [], []
        if len(agent_input["web_page_search_record"]) > 0:
            web_record = remove_duplicate_items(agent_input["web_page_search_record"])
        if len(agent_input["local_text_search_record"]) > 0:
            local_record = remove_duplicate_items(agent_input["local_text_search_record"])

        doc_infos, scored_result, source_store = await self._structure_result(web_record, local_record, query)

        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | "
                        f"[InfoRetrievalNode] Gathered {len(doc_infos)} items of information. | "
                        f"Starting to Update doc_infos after post process.")
        else:
            logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                        f"[InfoRetrievalNode] Collecting info for query: {query} | "
                        f"Gathered {len(doc_infos)} items of information. | "
                        f"Starting to Updating doc_infos after post process.")
        doc_infos = self._process_post_process_result(scored_result, doc_infos, section_idx)

        return {
            "messages": agent_input["messages"],
            "doc_infos": doc_infos,
            "source_store": source_store,
            "web_record": web_record,
            "local_record": local_record,
            "search_query": query,
        }

    @staticmethod
    def _web_search_engines_for_query(state: dict) -> list[str]:
        engines = [
            state.get("web_search_engine_name") or SearchEngine.PETAL.value,
            state.get("secondary_web_search_engine_name") or "",
        ]
        result = []
        for engine in engines:
            engine = str(engine or "").strip()
            if engine and engine not in result:
                result.append(engine)
        return result

    @staticmethod
    def _agent_called_tool(agent_input: dict, tool_name: str) -> bool:
        for message in agent_input.get("messages", []):
            if isinstance(message, dict):
                message_name = message.get("name")
                tool_calls = message.get("tool_calls", [])
            else:
                message_name = getattr(message, "name", None)
                tool_calls = getattr(message, "tool_calls", [])
            if message_name == tool_name:
                return True
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if isinstance(tool_call, dict) and tool_call.get("name") == tool_name:
                    return True
        return False

    async def _direct_search_with_retry(
            self,
            request: DirectSearchRequest,
            state: dict,
    ):
        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")
        default_search_engine_name = str(
            state.get("web_search_engine_name") or SearchEngine.PETAL.value
        ).strip()
        retry_direct_search = object()

        async def handle_search_failure(
                error: Any,
                reason: str,
                current_try: int,
                retryable: bool = True,
        ):
            should_fallback_to_default = self._should_fallback_to_default_search(
                request.search_engine_name,
                default_search_engine_name,
            )
            operation = f"direct call search tool '{request.tool_name}'"
            if reason == "returned_error":
                operation = f"{operation} returned error"

            if request.fallback_to_default and should_fallback_to_default:
                self._log_vertical_search_fallback(
                    VerticalSearchFallbackLog(
                        section_idx=section_idx,
                        step_title=step_title,
                        search_engine_name=request.search_engine_name,
                        default_search_engine_name=default_search_engine_name,
                        query=request.query,
                        reason=reason,
                    )
                )
                fallback_operation = (
                    f"{operation}, fallback to default search engine "
                    f"'{default_search_engine_name}'"
                )
                if reason == "exception":
                    fallback_operation = (
                        f"{operation} failed, fallback to default search engine "
                        f"'{default_search_engine_name}'"
                    )
                self._log_vertical_search_failure_detail(
                    VerticalSearchFailureDetailLog(
                        section_idx=section_idx,
                        step_title=step_title,
                        operation=fallback_operation,
                        error=error,
                        extra_info=f"{request.search_engine_name}: {request.query}",
                        level=logging.WARNING,
                    )
                )
                return await self._direct_search_with_retry(
                    DirectSearchRequest(
                        tool=request.tool,
                        tool_name=request.tool_name,
                        query=request.query,
                        search_engine_name=default_search_engine_name,
                    ),
                    state,
                )

            if not request.retry_on_error and should_fallback_to_default:
                self._log_vertical_search_failed_fast(
                    section_idx,
                    step_title,
                    request.search_engine_name,
                    request.query,
                    reason,
                )
                return None

            record_llm_retry_log(
                current_try=current_try,
                max_retries=max_retries if request.retry_on_error and retryable else current_try,
                section_idx=section_idx,
                step_title=step_title,
                operation=operation,
                error=error,
                extra_info=f"{request.search_engine_name}: {request.query}",
            )
            if request.retry_on_error and retryable and current_try < max_retries:
                return retry_direct_search
            return None

        for retry_idx in range(max_retries):
            current_try = retry_idx + 1
            try:
                tool_result_raw = await request.tool.invoke({
                    "query": request.query,
                    "search_engine_name": request.search_engine_name,
                })
                if isinstance(tool_result_raw, dict) and tool_result_raw.get("error"):
                    error_msg = tool_result_raw.get("error", "")
                    failure_result = await handle_search_failure(
                        error_msg,
                        "returned_error",
                        current_try,
                        retryable=tool_result_raw.get("retryable", True) is not False,
                    )
                    if failure_result is retry_direct_search:
                        continue
                    return failure_result
                return tool_result_raw
            except Exception as e:
                failure_result = await handle_search_failure(e, "exception", current_try)
                if failure_result is retry_direct_search:
                    continue
                return failure_result

    @staticmethod
    def _should_fallback_to_default_search(search_engine_name: str, default_search_engine_name: str) -> bool:
        search_engine_name = str(search_engine_name or "").strip()
        default_search_engine_name = str(default_search_engine_name or "").strip()
        return bool(
            search_engine_name
            and default_search_engine_name
            and search_engine_name != default_search_engine_name
        )

    @staticmethod
    def _log_vertical_search_failed_fast(
            section_idx: int,
            step_title: str,
            search_engine_name: str,
            query: str,
            reason: str,
    ) -> None:
        if LogManager.is_sensitive():
            logger.info(
                "section_idx: %s | [InfoRetrievalNode] Vertical search failed fast. "
                "engine=%s reason=%s",
                section_idx,
                search_engine_name,
                reason,
            )
        else:
            logger.info(
                "section_idx: %s | step title: %s | [InfoRetrievalNode] Vertical search failed fast. "
                "engine=%s query=%s reason=%s",
                section_idx,
                step_title,
                search_engine_name,
                query,
                reason,
            )

    @staticmethod
    def _log_vertical_search_failure_detail(
            failure_log: VerticalSearchFailureDetailLog,
    ) -> None:
        log_method = logger.log
        if LogManager.is_sensitive():
            log_method(
                failure_log.level,
                "section_idx: %s | [InfoRetrievalNode] %s",
                failure_log.section_idx,
                failure_log.operation,
            )
        else:
            log_method(
                failure_log.level,
                "section_idx: %s | step title: %s | [InfoRetrievalNode] %s: %s | Extra Info: %s",
                failure_log.section_idx,
                failure_log.step_title,
                failure_log.operation,
                failure_log.error,
                failure_log.extra_info,
                exc_info=isinstance(failure_log.error, BaseException),
            )

    @staticmethod
    def _log_vertical_search_fallback(
            fallback_log: VerticalSearchFallbackLog,
    ) -> None:
        if LogManager.is_sensitive():
            logger.info(
                "section_idx: %s | [InfoRetrievalNode] Vertical search fallback to default. "
                "engine=%s default_engine=%s reason=%s",
                fallback_log.section_idx,
                fallback_log.search_engine_name,
                fallback_log.default_search_engine_name,
                fallback_log.reason,
            )
        else:
            logger.info(
                "section_idx: %s | step title: %s | [InfoRetrievalNode] Vertical search fallback to default. "
                "engine=%s default_engine=%s query=%s reason=%s",
                fallback_log.section_idx,
                fallback_log.step_title,
                fallback_log.search_engine_name,
                fallback_log.default_search_engine_name,
                fallback_log.query,
                fallback_log.reason,
            )

    async def _run_secondary_web_search_if_needed(
            self,
            state: dict,
            agent_input: dict,
            tool_dict: dict,
    ) -> dict:
        secondary_engine = str(state.get("secondary_web_search_engine_name") or "").strip()
        primary_engine = str(state.get("web_search_engine_name") or SearchEngine.PETAL.value).strip()
        if not secondary_engine or secondary_engine == primary_engine:
            return agent_input

        tool_name = "web_search_tool"
        if tool_name not in tool_dict:
            return agent_input

        query = state.get("search_query", state.get("step_title", ""))
        fallback_to_default = not self._agent_called_tool(agent_input, tool_name)
        tool_result_raw = await self._direct_search_with_retry(
            DirectSearchRequest(
                tool=tool_dict[tool_name],
                tool_name=tool_name,
                query=query,
                search_engine_name=secondary_engine,
                fallback_to_default=fallback_to_default,
                retry_on_error=True,
            ),
            state,
        )
        if not tool_result_raw:
            return agent_input

        tool_result_json = json.dumps(tool_result_raw, ensure_ascii=False, indent=4)
        process_tool_result(tool_name, tool_result_json, agent_input)
        if LogManager.is_sensitive():
            logger.info(
                "section_idx: %s | [InfoRetrievalNode] Secondary web search completed.",
                state.get("section_idx", 0),
            )
        else:
            logger.info(
                "section_idx: %s | step title: %s | [InfoRetrievalNode] Secondary web search completed. "
                "engine=%s",
                state.get("section_idx", 0),
                state.get("step_title", ""),
                secondary_engine,
            )
        return agent_input

    async def _invoke_llm_with_retry(self, tool_prompt: list, tool_list: list, state: dict):
        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")
        query = state.get("search_query", step_title)

        response = None
        for retry_idx in range(max_retries):
            try:
                response = await ainvoke_llm_with_stats(
                    self.llm, tool_prompt, llm_type="basic",
                    agent_name=AgentLlmName.COLLECTOR_INFO_RETRIEVAL.value, tools=tool_list)
                break
            except Exception as e:
                current_try = retry_idx + 1
                task_description = "get info for query"
                record_llm_retry_log(current_try, max_retries, section_idx, step_title,
                                     error=e, operation=task_description, extra_info=query)

        return response

    async def _process_llm_response(self, response: any, agent_input: dict, tool_dict: dict, state: dict):
        step_info = dict(
            section_idx=state.get("section_idx", 0),
            step_title=state.get("step_title", ""),
            query=state.get("search_query", ""),
            web_search_engine_name=state.get("web_search_engine_name"),
            local_search_engine_name=state.get("local_search_engine_name"),
        )
        if response and response.get("tool_calls", []):
            agent_input = await process_tool_call(response, agent_input, tool_dict, step_info)

        return agent_input

    async def _collector_llm(self, state: dict, agent_input: dict, tool_list: list, tool_dict: dict):
        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")
        query = state.get("search_query", step_title)
        max_tool_call_turns_per_query = state.get("max_tool_call_turns_per_query", 2)

        for i in range(max_tool_call_turns_per_query):
            if LogManager.is_sensitive():
                logger.info(f"section_idx: {section_idx} |"
                            f"[InfoRetrievalNode] Current step index: {i + 1}")
            else:
                logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                            f"Collecting info for query: {query} | "
                            f"[InfoRetrievalNode] Current step index: {i + 1}")
            agent_input["remaining_steps"] = max_tool_call_turns_per_query - i
            tool_prompt = apply_system_prompt("collector", agent_input)

            response = await self._invoke_llm_with_retry(tool_prompt, tool_list, state)
            agent_input = await self._process_llm_response(response, agent_input, tool_dict, state)
            if response is None or not response.get("tool_calls", []):
                break
            if i + 1 == max_tool_call_turns_per_query:
                if LogManager.is_sensitive():
                    logger.info(f"section_idx: {section_idx} | "
                                f"[InfoRetrievalNode] | [COLLECTOR TOOL CALL] reach max tool call turns "
                                f"{max_tool_call_turns_per_query}")
                else:
                    logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                                f"Collecting info for query: {query} | [InfoRetrievalNode] | "
                                f"[COLLECTOR TOOL CALL] reach max tool call turns limit "
                                f"{max_tool_call_turns_per_query}")

        return state, agent_input

    async def _structure_result(self, web_record: list, local_record: list, query: str):
        """把工具记录转换为 doc_infos、评分结果和 source store。

        Args:
            web_record: Web 搜索记录列表。
            local_record: 本地搜索记录列表。
            query: 当前检索 query。

        Returns:
            `(doc_infos, scored_result, source_store)` 三元组。
        """
        source_store = CollectorSourceStore()
        doc_infos = []

        for record in web_record + local_record:
            _, doc_info = build_evidence_atom(record=record, query=query, source_store=source_store)
            doc_infos.append(doc_info)

        if len(doc_infos) != 0:
            scored_result = await run_doc_evaluation(
                query=query,
                documents=build_evaluation_documents(doc_infos),
                llm=self.llm
            )
        else:
            scored_result = []

        return doc_infos, scored_result, source_store.to_dict()

    def _process_post_process_result(self, scored_result: list[dict], doc_infos: list, section_idx: int):
        """把 evaluator 结果合并回 doc_infos。

        Args:
            scored_result: evaluator 输出的评分结果。
            doc_infos: 当前 query 生成的兼容 doc_infos。
            section_idx: 当前章节索引，用于日志。

        Returns:
            已补齐结构化 scores 和兼容期字段的 doc_infos。
        """
        seen_indexes = set()
        for idx, scored in enumerate(scored_result):
            if not isinstance(scored, dict):
                logger.warning(f"section_idx: {section_idx} | [InfoRetrievalNode] "
                               f"Score result is not a dict (type={type(scored).__name__}), skipping index:{idx}")
                continue
            if "content" in scored:
                logger.warning(f"section_idx: {section_idx} | [InfoRetrievalNode] "
                               f"Score result contains deprecated content index, skipping index:{idx}")
                continue
            if "document_index" not in scored:
                logger.warning(f"section_idx: {section_idx} | [InfoRetrievalNode] "
                               f"Score result missing document_index, skipping index:{idx}")
                continue
            try:
                index = int(scored.get("document_index"))
            except (TypeError, ValueError):
                logger.warning(f"section_idx: {section_idx} | [InfoRetrievalNode] "
                               f"Invalid score result document_index, skipping index:{idx}")
                continue
            if index < 0 or index >= len(doc_infos):
                logger.warning(f"section_idx: {section_idx} | [InfoRetrievalNode] "
                               f"Score result document_index:{index} is out of range, skipping")
                continue
            if index in seen_indexes:
                logger.warning(f"section_idx: {section_idx} | [InfoRetrievalNode] "
                               f"Duplicate score result document_index:{index}, skipping")
                continue

            scores = normalize_scores(scored.get("scores"))
            publish_time = scored.get("publish_time") or scored.get("doc_time") or "未提供时间信息"
            doc_infos[index]["scores"] = scores
            doc_infos[index]["publish_time"] = publish_time
            normalize_doc_info_scores_and_time(doc_infos[index])
            seen_indexes.add(index)

        return doc_infos

    def _prepare_collector_tool(self, state: dict):
        """准备信息收集器工具."""
        search_method = state.get("search_method", "web")
        web_search_tool = create_web_search_tool()
        local_search_tool = create_local_search_tool()
        api_tools = build_runtime_api_tools(
            state.get("api_tools_config", {}).get("collector_tools", []),
        )

        tool_dict = {}
        tool_list = []
        if search_method == "web":
            tool_list.append(web_search_tool.card.tool_info())
            tool_dict.update({
                "web_search_tool": web_search_tool,
            })
        elif search_method == "local":
            tool_list.append(local_search_tool.card.tool_info())
            tool_dict.update({"local_search_tool": local_search_tool})
        else:
            tool_list.append(web_search_tool.card.tool_info())
            tool_list.append(local_search_tool.card.tool_info())
            tool_dict.update({
                "web_search_tool": web_search_tool,
                "local_search_tool": local_search_tool
            })

        for api_tool in api_tools:
            tool_list.append(api_tool.card.tool_info())
            tool_dict[api_tool.card.name] = api_tool

        return tool_list, tool_dict
