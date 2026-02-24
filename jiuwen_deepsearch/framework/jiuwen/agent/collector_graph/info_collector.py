# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import logging
from typing import Any

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session

from jiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from jiuwen_deepsearch.algorithm.research_collector.collector_function import process_tool_call, remove_duplicate_items
from jiuwen_deepsearch.algorithm.research_collector.doc_evaluation import run_doc_evaluation
from jiuwen_deepsearch.config.config import Config
from jiuwen_deepsearch.framework.jiuwen.agent.base_node import BaseNode
from jiuwen_deepsearch.framework.jiuwen.tools import create_web_search_tool, create_local_search_tool
from jiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, record_llm_retry_log
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from jiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from jiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context
from jiuwen_deepsearch.utils.constants_utils.search_engine_constants import LocalSearch, SearchEngine
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager

max_retries = Config().service_config.info_collector_max_retry_num
logger = logging.getLogger(__name__)


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
        llm_model_name = session.get_global_state("config.llm_config.model_name")
        self.llm = llm_context.get().get(llm_model_name)

        state = dict(
            search_queries=session.get_global_state("collector_context.search_queries"),
            max_tool_steps=session.get_global_state("collector_context.max_tool_steps"),
            section_idx=section_idx,
            step_title=step_title,
            search_method=session.get_global_state("config.info_collector_search_method"),
            web_search_engine_name=web_search_engine_name,
            local_search_engine_name=local_search_engine_name
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
                "step_title": state.get("step_title", ""),
                "max_tool_steps": state.get("max_tool_steps", 3),
                "search_method": state.get("search_method", "web"),
                "web_search_engine_name": state.get("web_search_engine_name", None),
                "local_search_engine_name": state.get("local_search_engine_name", None),
            }
            sub_task = self._collector_main(sub_state)
            tasks.append(sub_task)
        tasks_results = await asyncio.gather(*tasks)

        node_output = self._post_handle(inputs, tasks_results, session, context)
        return node_output

    def _post_handle(self, inputs: Input, algorithm_output: list, session: Session, context: ModelContext):
        section_idx = session.get_global_state("collector_context.section_idx")
        step_title = session.get_global_state("collector_context.step_title")
        doc_infos: list = session.get_global_state("collector_context.doc_infos")
        search_queries = session.get_global_state("collector_context.search_queries")
        history_queries = session.get_global_state("collector_context.history_queries")

        new_doc_infos = []
        for retrieval_query, result in zip(search_queries, algorithm_output):
            core_doc_info = [(doc.get("title", ""), doc.get("url", "")) for doc in doc_infos if isinstance(doc, dict)]
            task_doc_infos = result.get("doc_infos", [])
            if LogManager.is_sensitive():
                logger.info(f"section_idx: {section_idx} | gathered item count before duplicate: {len(task_doc_infos)}")
            else:
                logger.info(f"section_idx: {section_idx} | step title: {step_title} | "
                            f"[InfoRetrievalNode] Query: {result.get('search_query', '')} | "
                            f"gathered item count before duplicate: {len(task_doc_infos)}")
            for task_doc in task_doc_infos:
                if (task_doc.get("title", ""), task_doc.get("url", "")) not in core_doc_info:
                    # 记录本地新收集信息
                    new_doc_infos.append(task_doc)

            retrieval_query.doc_infos = task_doc_infos
            history_queries.append(retrieval_query)
            doc_infos.extend(task_doc_infos)

        doc_infos = remove_duplicate_items(doc_infos)

        session.update_global_state({"collector_context.history_queries": history_queries})
        session.update_global_state({"collector_context.new_doc_infos_current_loop": new_doc_infos})
        session.update_global_state({"collector_context.doc_infos": doc_infos})
        if LogManager.is_sensitive():
            logger.info("section_idx: %s | [InfoRetrievalNode] End InfoRetrievalNode.", section_idx)
        else:
            logger.info("section_idx: %s | step title: %s | [InfoRetrievalNode] End InfoRetrievalNode."
                        "Get %s doc_infos item.", section_idx, step_title, len(doc_infos))

        return dict()

    async def _collector_main(self, state: dict):
        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")
        if LogManager.is_sensitive():
            logger.info(f"section_idx: {section_idx} | [InfoRetrievalNode] Start InfoRetrieval main function."
                        f"Collecting info for query, Max tool steps: {state['max_tool_steps']}")
        else:
            logger.info(f"section_idx: {section_idx} | [InfoRetrievalNode] Start InfoRetrieval main function. | "
                        f"step title: {step_title} Collecting info for query: {state['search_query']} | "
                        f"Max tool steps: {state['max_tool_steps']}")

        query = state.get("search_query", step_title)
        agent_input = {
            "messages": [UserMessage(content=f"Now deal with the Query:\n[Query]: {query}\n\n"), ],
            "remaining_steps": None,
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": [],
        }

        tool_list, tool_dict = self._prepare_collector_tool(state)

        state, agent_input = await self._collector_llm(state, agent_input, tool_list, tool_dict)

        web_record, local_record = [], []
        if len(agent_input["web_page_search_record"]) > 0:
            web_record = remove_duplicate_items(agent_input["web_page_search_record"])
        if len(agent_input["local_text_search_record"]) > 0:
            local_record = remove_duplicate_items(agent_input["local_text_search_record"])

        doc_infos, scored_result = await self._structure_result(web_record, local_record, query)

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
            "web_record": web_record,
            "local_record": local_record,
            "search_query": query,
        }

    async def _invoke_llm_with_retry(self, tool_prompt: list, tool_list: list, state: dict):
        section_idx = state.get("section_idx", 0)
        step_title = state.get("step_title", "")
        query = state.get("search_query", step_title)

        response = None
        for retry_idx in range(max_retries):
            try:
                response = await ainvoke_llm_with_stats(
                    self.llm, tool_prompt, llm_type="basic", agent_name=NodeId.COLLECTOR_INFO.value, tools=tool_list)
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
        max_tool_steps = state.get("max_tool_steps", 3)

        for i in range(max_tool_steps):
            if LogManager.is_sensitive():
                logger.info(f"section_idx: {section_idx} |"
                            f"[InfoRetrievalNode] Current step index: {i + 1}")
            else:
                logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                            f"Collecting info for query: {query} | "
                            f"[InfoRetrievalNode] Current step index: {i + 1}")
            agent_input["remaining_steps"] = max_tool_steps - i
            tool_prompt = apply_system_prompt("collector", agent_input)

            response = await self._invoke_llm_with_retry(tool_prompt, tool_list, state)
            agent_input = await self._process_llm_response(response, agent_input, tool_dict, state)
            if response is None or not response.get("tool_calls", []):
                break
            if i + 1 == max_tool_steps:
                if LogManager.is_sensitive():
                    logger.info(f"section_idx: {section_idx} | "
                                f"[InfoRetrievalNode] | [COLLECTOR TOOL CALL] reach max tool steps {max_tool_steps}")
                else:
                    logger.info(f"section_idx: {section_idx} | step title {step_title} | "
                                f"Collecting info for query: {query} | [InfoRetrievalNode] | "
                                f"[COLLECTOR TOOL CALL] reach max tool steps limit {max_tool_steps}")

        return state, agent_input

    async def _structure_result(self, web_record: list, local_record: list, query: str):
        gathered_info = [
            {
                "url": record.get("url", ""),
                "title": record.get("title", "Untitled"),
                "content": record.get("content", "")
            }
            for record in web_record + local_record
        ]

        contents = []
        doc_infos = []

        for info in gathered_info:
            doc_info = {
                "doc_time": "未提供时间信息",
                "source_authority": "未提供权威性得分",
                "task_relevance": "未提供相关性得分",
                "original_content": info.get("content", ""),
                "information_richness": "未提供可答性得分",
                "data_density": "未提供数据密度得分",
                "url": info.get("url", ""),
                "title": info.get("title", "Untitled"),
                "query": query,
            }
            contents.append(info.get("content", ""))
            doc_infos.append(doc_info)

        if len(doc_infos) != 0:
            scored_result = await run_doc_evaluation(
                query=query,
                contents=contents,
                llm=self.llm
            )
        else:
            scored_result = []

        return doc_infos, scored_result

    def _process_post_process_result(self, scored_result: list[dict], doc_infos: list, section_idx: int):
        for idx, scored in enumerate(scored_result[:len(doc_infos)]):
            try:
                index = int(scored.get("content"))
            except (KeyError, ValueError):
                logger.warning(f"section_idx: {section_idx} | [InfoRetrievalNode] "
                               f"Failed to get content form score result, using index:{idx} as fallback")
                index = idx

            try:
                scores: dict = scored.get("scores")
                authority = str(scores.get("authority")) if scores.get("authority") else "未提供权威性得分"
                relevance = str(scores.get("relevance")) if scores.get("relevance") else "未提供相关性得分"
                answerability = str(scores.get("answerability")) if scores.get("answerability") else "未提供可答性得分"
                data_density = str(scores.get("data_density")) if scores.get("data_density") else "未提供数据密度得分"
            except Exception:
                authority = "未提供权威性得分"
                relevance = "未提供相关性得分"
                answerability = "未提供可答性得分"
                data_density = "未提供数据密度得分"
            try:
                doc_time = scored.get("doc_time") if scored.get("doc_time") else "未提供时间信息"
            except Exception:
                doc_time = "未提供时间信息"

            doc_infos[index]["source_authority"] = f"该篇文章的信息来源权威性和可信度得分：{authority}"
            doc_infos[index]["task_relevance"] = f"该篇文章的内容与当前任务的相关性得分：{relevance}"
            doc_infos[index]["information_richness"] = f"该篇文章的信息丰富程度与可答性得分：{answerability}"
            doc_infos[index]["data_density"] = f"该篇文章的数据丰富和密集程度得分：{data_density}"
            doc_infos[index]["doc_time"] = doc_time

        return doc_infos

    def _prepare_collector_tool(self, state: dict):
        """准备信息收集器工具."""
        search_method = state.get("search_method", "web")
        web_search_tool = create_web_search_tool()
        local_search_tool = create_local_search_tool()

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

        return tool_list, tool_dict
