# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import copy
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import List, Any

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.flow.end_comp import End
from openjiuwen.core.workflow.components.flow.start_comp import Start

from openjiuwen_deepsearch.algorithm.chart_generation.vlm_chart_generator import VLMChartGenerator
from openjiuwen_deepsearch.algorithm.search_nodes.utils import (
    anonymize_config_for_logging,
)
from openjiuwen_deepsearch.utils.log_utils.log_metrics import metrics_logger, TIME_LOGGER_TAG
from openjiuwen_deepsearch.algorithm.query_understanding.interpreter import query_interpreter
from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    classify_and_recognize_intent,
    normalize_research_query,
    recognize_report_intent,
    resolve_report_type_policy,
    web_search_for_query,
)
from openjiuwen_deepsearch.algorithm.query_understanding.outline_mode_router import (
    route_outline_execution_method,
)
from openjiuwen_deepsearch.algorithm.query_understanding.outliner import Outliner
from openjiuwen_deepsearch.algorithm.research_collector.collector_function import (
    filter_search_results_by_exclude_urls,
)
from openjiuwen_deepsearch.algorithm.report.config import ReportFormat, ReportStyle
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.algorithm.search_nodes.find_action import run_find_action_space
from openjiuwen_deepsearch.algorithm.search_nodes.initialize_state import run_initialize_state
from openjiuwen_deepsearch.algorithm.search_nodes.run_action import (
    RunActionConfig,
    run_action,
)
from openjiuwen_deepsearch.algorithm.search_nodes.tool_node import (
    ExecuteToolConfig,
    execute_tool,
    format_tool_result_for_message,
)
from openjiuwen_deepsearch.algorithm.search_nodes.utils import (
    _save_result,
    format_action_for_log,
    to_dict_safe,
    to_json_safe,
)
from openjiuwen_deepsearch.algorithm.search_nodes.validate_new_state import run_validations
from openjiuwen_deepsearch.algorithm.source_trace.checker import (
    postprocess_by_citation_checker,
    preprocess_info,
)
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer import SourceTracerInfer
from openjiuwen_deepsearch.algorithm.user_feedback_processor.user_feedback_processor import (
    UserFeedbackProcessor,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.history import (
    build_current_outline_update,
    build_rewrite_history_update,
)
from openjiuwen_deepsearch.common.common_constants import (
    CHINESE,
    ENGLISH,
    FINISH_TASK_FEEDBACK,
    MAX_QUERY_LENGTH,
)
from openjiuwen_deepsearch.common.exception import (
    CustomException,
    CustomJiuWenBaseException,
    CustomValueException,
)
from openjiuwen_deepsearch.common.status_code import StatusCode, format_exception_info
from openjiuwen_deepsearch.config.config import (
    Config,
    LocalSearchEngineConfig,
    OUTLINER_SECTION_NUM_MAX,
    WebSearchEngineConfig,
)
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Action,
    build_research_intent_prompt_context,
    Message,
    Outline,
    OutlineInteraction,
    ResearchIntent,
    Result,
    SearchContext,
    State,
    ValidationResult,
)
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import (adapt_llm_model_name, 
                                                                        adapt_vlm_model_name)
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import (
    apply_web_search_domain_constraints,
    apply_web_search_temporal_scope,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import (
    get_effective_workflow_llm_usage,
    save_workflow_llm_usage_to_session,
)
from openjiuwen_deepsearch.utils.common_utils.stream_utils import (
    MessageType,
    StreamEvent,
    custom_stream_output,
    get_current_time,
)
from openjiuwen_deepsearch.utils.common_utils.text_utils import truncate_string
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    model_context,
    session_context,
    tool_context,
)
from openjiuwen_deepsearch.utils.debug_utils.node_debug import (
    NodeDebugData,
    NodeType,
    add_debug_log_wrapper,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.run_telemetry import (
    emit,
    emit_messages_updated,
    emit_state_created,
    runtime_correlation_from,
)

logger = logging.getLogger(__name__)


def _state_id_for_telemetry(state) -> str:
    if state is None:
        return ""
    if hasattr(state, "id"):
        return str(state.id)
    if isinstance(state, dict):
        return str(state.get("id", ""))
    return ""


def _normalize_workflow_llm_config(raw: object) -> dict:
    """Align session ``llm_config`` with AgentConfig shape (nested under ``general``)."""
    if raw is None:
        return {"general": {}}
    if isinstance(raw, dict) and "general" in raw:
        return raw
    if isinstance(raw, dict):
        return {"general": dict(raw)}
    return {"general": {}}


class StartNode(Start):
    """
    起始节点，初始化 Session global_state 中的 search_context 和 config
    """

    async def invoke(self, inputs: Input, session: Session, context: ModelContext):
        """
        入口初始化节点

        Args:
            inputs: 节点入参
            session: 会话上下文
            context: 全局上下文
        """

        # 初始化search_context
        original_query = inputs.get("query", "")
        search_context = SearchContext(
            original_query=original_query,
            session_id=inputs.get("thread_id", ""),
            messages=[Message(role="user", content=original_query)],
            search_mode=inputs.get("search_mode", "research"),
            report_template=inputs.get("report_template", ""),
        )

        session.update_global_state({"search_context": search_context.model_dump()})

        origin_agent_config = inputs.get("agent_config", {})
        agent_config = dict()
        if origin_agent_config:
            agent_config["execute_mode"] = origin_agent_config.get("execute_mode", "commercial")
            agent_config["execution_method"] = origin_agent_config.get(
                "execution_method", ExecutionMethod.PARALLEL.value
            )
            agent_config["workflow_human_in_the_loop"] = origin_agent_config.get("workflow_human_in_the_loop", True)
            agent_config["outline_interaction_enabled"] = origin_agent_config.get("outline_interaction_enabled", True)
            agent_config["outline_interaction_max_rounds"] = origin_agent_config.get(
                "outline_interaction_max_rounds", 3
            )
            agent_config["outliner_max_section_num"] = origin_agent_config.get("outliner_max_section_num", 5)
            agent_config["source_tracer_research_trace_source_switch"] = origin_agent_config.get(
                "source_tracer_research_trace_source_switch", True
            )
            agent_config["source_tracer_generated_citation_switch"] = origin_agent_config.get(
                "source_tracer_generated_citation_switch", True
            )
            agent_config["source_tracer_infer_switch"] = origin_agent_config.get("source_tracer_infer_switch", True)
            agent_config["llm_config"] = origin_agent_config.get("llm_config", {})
            agent_config["info_collector_search_method"] = origin_agent_config.get(
                "info_collector_search_method", "web"
            )
            agent_config["info_collector_webpage_enrich_enable"] = origin_agent_config.get(
                "info_collector_webpage_enrich_enable", False
            )
            agent_config["web_search_engine_config"] = WebSearchEngineConfig(
                search_engine_name=origin_agent_config.get("web_search_engine_config", {}).get("search_engine_name", "")
            )
            agent_config["local_search_engine_config"] = LocalSearchEngineConfig(
                search_engine_name=origin_agent_config.get("local_search_engine_config", {}).get(
                    "search_engine_name", ""
                )
            )
            agent_config["user_feedback_processor_enable"] = origin_agent_config.get(
                "user_feedback_processor_enable", False
            )
            agent_config["user_feedback_processor_max_interactions"] = origin_agent_config.get(
                "user_feedback_processor_max_interactions", 100
            )
            agent_config["stats_info_llm"] = origin_agent_config.get("stats_info_llm", False)
            agent_config["api_tools_config"] = origin_agent_config.get("api_tools_config", {})
            agent_config["vlm_chart_generator_enable"] = origin_agent_config.get("vlm_chart_generator_enable", False)
            agent_config["vlm_chart_generator_max_iterations"] = origin_agent_config.get(
                "vlm_chart_generator_max_iterations", 1
            )
            agent_config["agent_llm_timeouts"] = origin_agent_config.get("agent_llm_timeouts", {})

        service_config = Config().service_config.model_dump()
        service_config["thread_id"] = inputs.get("thread_id", "")
        service_config["interrupt_feedback"] = inputs.get("interrupt_feedback", "")
        # vlm迭代生成图与mermaid图文并茂功能互斥
        if agent_config.get("vlm_chart_generator_enable", False):
            service_config["visualization_enable"] = False
        merge_config = agent_config | service_config
        session.update_global_state({"config": merge_config})


class IntentRecognitionNode(BaseNode):
    """
    报告意图识别节点：从原始 query 中解析报告生成约束，
    所有查询均进入研究报告生成流程（OUTLINE / GENERATE_QUESTIONS）。
    """

    def __init__(self):
        super().__init__()

    async def _resolve_outline_execution_method(self, current_inputs: dict, session: Session) -> str:
        """
        解析并保存本轮大纲实际执行方式。

        Args:
            current_inputs: 意图识别节点预处理后的输入，包含 execution_method、original_query 和 llm_model_name。
            session: 当前工作流会话，用于写入 search_context.outline_execution_method。

        Returns:
            parallel 或 dependency_driving。仅 hybrid 入口会调用 LLM router，固定模式直接映射。
        """
        execution_method = current_inputs.get("execution_method") or ExecutionMethod.PARALLEL.value
        if execution_method == ExecutionMethod.DEPENDENCY_DRIVING.value:
            selected_method = ExecutionMethod.DEPENDENCY_DRIVING.value
        elif execution_method == ExecutionMethod.HYBRID.value:
            selected_method = await route_outline_execution_method(
                current_inputs.get("original_query") or "",
                current_inputs.get("llm_model_name") or "",
            )
        else:
            selected_method = ExecutionMethod.PARALLEL.value

        session.update_global_state({"search_context.outline_execution_method": selected_method})
        logger.info("[IntentRecognitionNode] outline_execution_method=%s", selected_method)
        return selected_method

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info("[IntentRecognitionNode] Start IntentRecognitionNode.")
        return dict(
            original_query=session.get_global_state("search_context.original_query") or "",
            messages=session.get_global_state("search_context.messages") or [],
            llm_model_name=adapt_llm_model_name(session, NodeId.INTENT_RECOGNITION.value),
            execution_method=session.get_global_state("config.execution_method") or ExecutionMethod.PARALLEL.value,
            human_in_the_loop=session.get_global_state("config.workflow_human_in_the_loop"),
            web_search_engine_config=session.get_global_state("config.web_search_engine_config"),
            info_collector_search_method=session.get_global_state("config.info_collector_search_method") or "web",
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        current_inputs = self._pre_handle(inputs, session, context)

        # 执行意图识别
        intent_result = await classify_and_recognize_intent(current_inputs)
        await self._resolve_outline_execution_method(current_inputs, session)

        # 检查搜索模式：仅在 web 或 all 模式下执行网络搜索
        info_collector_search_method = current_inputs.get("info_collector_search_method", "web")
        if info_collector_search_method in ("web", "all"):
            # 预搜索前应用域名约束
            _web_search_engine_config = current_inputs.get("web_search_engine_config")
            _web_search_engine_name = _web_search_engine_config.search_engine_name if _web_search_engine_config else ""
            apply_web_search_domain_constraints(
                search_engine_name=_web_search_engine_name,
                include_domains=intent_result.research_intent.include_domains,
                exclude_domains=intent_result.research_intent.exclude_domains,
            )

            # 使用 research_query 进行入口预搜索
            web_search_engine_name = (
                current_inputs["web_search_engine_config"].search_engine_name
                if current_inputs.get("web_search_engine_config") else "petal"
            )
            research_query = normalize_research_query(intent_result.research_query) or normalize_research_query(
                current_inputs["original_query"]
            )
            web_search_input = {
                "query": research_query,
                "web_search_engine_name": web_search_engine_name,
            }
            web_search_output = await web_search_for_query(web_search_input)

            error_msg = web_search_output.get("error_msg", "")
            if error_msg:
                exception_info = f"[{StatusCode.ENTRY_GENERATE_ERROR.code}] {error_msg}"
                session.update_global_state({
                    "search_context.final_result.exception_info": exception_info,
                })
                add_debug_log_wrapper(session, NodeDebugData(
                    NodeId.INTENT_RECOGNITION.value, 0, NodeType.MAIN.value,
                    output_content=exception_info,
                ))
                logger.error("[IntentRecognitionNode] Web search failed: %s", error_msg)
                return dict(next_node=NodeId.END.value)
            intent_result.entry_search_results = web_search_output.get("search_results", [])
            apply_web_search_temporal_scope(
                search_engine_name=web_search_engine_name,
                temporal_scope=intent_result.research_intent.temporal_scope,
            )
        else:
            # 纯本地模式：跳过网络搜索，使用空结果
            logger.info("[IntentRecognitionNode] Local-only mode, skipping web search.")
            intent_result.entry_search_results = []

        return self._post_handle(inputs, intent_result, session, context)

    def _post_handle(self, inputs: Input, algorithm_output: Any, session: Session, context: ModelContext):
        original_q = algorithm_output.original_query

        lang = (algorithm_output.lang or "zh-CN").lower()
        if "zh" in lang or "chinese" in lang or "中文" in lang:
            lang = CHINESE
        if "en" in lang or "english" in lang or "英文" in lang:
            lang = ENGLISH

        report_type = algorithm_output.research_intent.report_type
        report_policy = resolve_report_type_policy(report_type)
        logger.info(
            "[IntentRecognitionNode] report_type=%s policy=%s",
            report_type,
            report_policy.model_dump(),
        )

        session.update_global_state({
            "search_context.original_query": original_q,
            "search_context.research_intent": algorithm_output.research_intent.model_dump(),
            "search_context.report_type_policy": report_policy.model_dump(),
            "search_context.language": lang,
        })

        if algorithm_output.entry_search_results:
            entry_search_results = filter_search_results_by_exclude_urls(
                algorithm_output.entry_search_results,
                algorithm_output.research_intent.exclude_url,
                algorithm_output.research_intent.exclude_titles,
            )
            session.update_global_state({
                "search_context.entry_search_results": entry_search_results,
            })

        add_debug_log_wrapper(session, NodeDebugData(
            NodeId.INTENT_RECOGNITION.value,
            0,
            NodeType.MAIN.value,
            output_content=algorithm_output.model_dump_json(),
        ))

        human_in_the_loop = session.get_global_state("config.workflow_human_in_the_loop")
        next_node = NodeId.GENERATE_QUESTIONS.value if human_in_the_loop else NodeId.OUTLINE.value

        logger.info("[IntentRecognitionNode] End IntentRecognitionNode, next_node=%s", next_node)
        return dict(language=lang, human_in_the_loop=human_in_the_loop, next_node=next_node)


class FeedbackHandlerNode(BaseNode):

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info(f"[FeedbackHandlerNode] Start FeedbackHandlerNode.")
        feedback_mode = session.get_global_state("config.workflow_feedback_mode")
        return dict(
            feedback_mode=feedback_mode,
            original_query=session.get_global_state("search_context.original_query") or "",
            messages=session.get_global_state("search_context.messages") or [],
            questions=session.get_global_state("search_context.questions") or "",
            llm_model_name=adapt_llm_model_name(session, NodeId.INTENT_RECOGNITION.value),
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        current_inputs = self._pre_handle(inputs, session, context)
        feedback_mode = current_inputs.get("feedback_mode", "cmd")

        user_feedback = await self._get_user_feedback(feedback_mode, session)
        error_detail = ""
        if user_feedback == "Invalid feedback_mode":
            standardized_feedback = user_feedback
            error_detail = feedback_mode
        else:
            standardized_feedback = truncate_string(user_feedback, max_length=MAX_QUERY_LENGTH)
            if not standardized_feedback:
                logger.error("[FeedbackHandlerNode] Invalid feedback, length or type is invalid")
                error_detail = user_feedback or "empty"
                standardized_feedback = "Invalid feedback, length is 0 or type is invalid"

        algorithm_output = dict(user_feedback=standardized_feedback)
        if error_detail:
            algorithm_output["error_detail"] = error_detail
        if standardized_feedback not in {
            "Invalid feedback_mode",
            "Invalid feedback, length is 0 or type is invalid",
            FINISH_TASK_FEEDBACK,
        }:
            intent_inputs = self._build_intent_reparse_inputs(current_inputs, standardized_feedback)
            reparsed_intent = await recognize_report_intent(intent_inputs)
            algorithm_output["reparsed_intent"] = reparsed_intent.model_dump()

        result = self._post_handle(inputs, algorithm_output, session, context)
        return result

    @staticmethod
    def _merge_unique_items(base: list[str], incoming: list[str]) -> list[str]:
        seen: set[str] = set()
        for item in (base or []) + (incoming or []):
            normalized = str(item or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
        return list(seen)

    @staticmethod
    def _build_feedback_messages(
        base_messages: list[dict | Message],
        questions: str,
        user_feedback: str,
    ) -> list[dict]:
        messages: list[dict] = []
        for message in base_messages or []:
            if isinstance(message, dict):
                messages.append(dict(message))
            else:
                messages.append(
                    {
                        "role": getattr(message, "role", "user"),
                        "content": getattr(message, "content", ""),
                        "name": getattr(message, "name", None),
                    }
                )
        if questions:
            messages.append({"role": "assistant", "content": f"Clarification questions:\n{questions}"})
        messages.append({"role": "user", "content": user_feedback})
        return messages

    def _build_intent_reparse_inputs(self, current_inputs: dict, user_feedback: str) -> dict:
        messages = self._build_feedback_messages(
            base_messages=current_inputs.get("messages") or [],
            questions=current_inputs.get("questions") or "",
            user_feedback=user_feedback,
        )

        return {
            "original_query": current_inputs.get("original_query", ""),
            "messages": messages,
            "llm_model_name": current_inputs.get("llm_model_name"),
        }

    def _merge_reparsed_intent(self, session: Session, reparsed_intent: dict) -> dict:
        current_intent = ResearchIntent.model_validate(session.get_global_state("search_context.research_intent") or {})
        incoming_intent = ResearchIntent.model_validate(reparsed_intent.get("research_intent") or {})

        merged_intent = current_intent.model_copy(deep=True)
        if incoming_intent.section_count is not None:
            merged_intent.section_count = incoming_intent.section_count
        if incoming_intent.audience_role:
            merged_intent.audience_role = incoming_intent.audience_role
        if incoming_intent.tone:
            merged_intent.tone = incoming_intent.tone

        merged_intent.include_url = self._merge_unique_items(current_intent.include_url, incoming_intent.include_url)
        merged_intent.exclude_url = self._merge_unique_items(current_intent.exclude_url, incoming_intent.exclude_url)
        merged_intent.exclude_titles = self._merge_unique_items(
            current_intent.exclude_titles, incoming_intent.exclude_titles
        )
        merged_intent.include_domains = self._merge_unique_items(
            current_intent.include_domains, incoming_intent.include_domains
        )
        merged_intent.exclude_domains = self._merge_unique_items(
            current_intent.exclude_domains, incoming_intent.exclude_domains
        )

        if incoming_intent.report_type is not None:
            merged_intent.report_type = incoming_intent.report_type
        if incoming_intent.temporal_scope is not None:
            merged_intent.temporal_scope = incoming_intent.temporal_scope

        return merged_intent.model_dump()

    async def _get_user_feedback(self, feedback_mode: str, session: Session) -> str:
        """按交互模式获取用户反馈内容。

        Args:
            feedback_mode: 反馈交互模式，当前支持 ``cmd`` 和 ``web``。
            session: 当前会话对象。

        Returns:
            str: 规范化后的反馈文本；当交互模式非法时返回 ``Invalid feedback_mode``。
        """
        prompt = "\nEnter your feedback: "

        if feedback_mode == "cmd":
            return input(prompt)
        if feedback_mode == "web":
            if bool(session.get_global_state("config.stats_info_llm")):
                save_workflow_llm_usage_to_session(
                    session=session,
                    session_id=session.get_global_state("config.thread_id"),
                )
            # session.interact本质上是raise Exception的方式，FeedbackHandlerNode内不能使用try except
            user_input = await session.interact(prompt)
            session.update_state({INTERACTIVE_INPUT: None})
            try:
                user_input = json.loads(user_input)
                return user_input.get("feedback", "")
            except json.JSONDecodeError:
                return "Invalid feedback format, expected a JSON string with 'user_feedback' field."
        logger.error(f"[FeedbackHandlerNode] Invalid feedback_mode: {feedback_mode}")
        return "Invalid feedback_mode"

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        user_feedback = algorithm_output.get("user_feedback", "")

        if user_feedback == "Invalid feedback_mode":
            exception_info = format_exception_info(
                StatusCode.FEEDBACK_HANDLER_INVALID_MODE_ERROR,
                algorithm_output.get("error_detail", ""),
            )
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            # 添加FeedbackHandlerNode debug日志
            add_debug_log_wrapper(
                session,
                NodeDebugData(
                    NodeId.FEEDBACK_HANDLER.value,
                    0,
                    NodeType.MAIN.value,
                    output_content=str(exception_info).replace("\\n", "\n"),
                ),
            )
            return dict(next_node=NodeId.END.value)
        if user_feedback == "Invalid feedback, length is 0 or type is invalid":
            exception_info = format_exception_info(
                StatusCode.FEEDBACK_HANDLER_INVALID_FEEDBACK_ERROR,
                algorithm_output.get("error_detail", ""),
            )
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            # 添加FeedbackHandlerNode debug日志
            add_debug_log_wrapper(
                session,
                NodeDebugData(
                    NodeId.FEEDBACK_HANDLER.value,
                    0,
                    NodeType.MAIN.value,
                    output_content=str(exception_info).replace("\\n", "\n"),
                ),
            )
            return dict(next_node=NodeId.END.value)
        if user_feedback == FINISH_TASK_FEEDBACK:
            logger.info(f"[FeedbackHandlerNode] user feedback is FINISH TASK, we will try to finish workflow.")
            # 这里是正常走到结束的，不需要填充exception_info
            return dict(next_node=NodeId.END.value)

        session.update_global_state({"search_context.user_feedback": user_feedback})
        reparsed_intent = algorithm_output.get("reparsed_intent")
        if reparsed_intent:
            merged_intent_dict = self._merge_reparsed_intent(session, reparsed_intent)
            if not merged_intent_dict.get("report_type"):
                merged_intent_dict["report_type"] = "professional"
            merged_policy = resolve_report_type_policy(merged_intent_dict.get("report_type"))
            session.update_global_state(
                {
                    "search_context.research_intent": merged_intent_dict,
                    "search_context.report_type_policy": merged_policy.model_dump(),
                }
            )

            web_search_engine_config = session.get_global_state("config.web_search_engine_config")
            web_search_engine_name = web_search_engine_config.search_engine_name if web_search_engine_config else ""
            apply_web_search_domain_constraints(
                search_engine_name=web_search_engine_name,
                include_domains=merged_intent_dict.get("include_domains", []),
                exclude_domains=merged_intent_dict.get("exclude_domains", []),
            )
            apply_web_search_temporal_scope(
                search_engine_name=web_search_engine_name,
                temporal_scope=merged_intent_dict.get("temporal_scope"),
            )

        add_debug_log_wrapper(
            session, NodeDebugData(NodeId.FEEDBACK_HANDLER.value, 0, NodeType.MAIN.value, output_content=user_feedback)
        )
        logger.info(f"[FeedbackHandlerNode] End FeedbackHandlerNode.")
        return dict(next_node=NodeId.OUTLINE.value)


class ReporterNode(BaseNode):

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info(f"[ReporterNode] Start ReporterNode.")
        current_report = session.get_global_state("search_context.current_report")
        report_task = ""
        all_classified_contents = []
        if current_report:
            report_task = current_report.report_task if hasattr(current_report, "report_task") else ""
            all_classified_contents = (
                current_report.all_classified_contents if hasattr(current_report, "all_classified_contents") else []
            )
        llm_model_name = adapt_llm_model_name(session, NodeId.REPORTER.value)

        visualization_enable = session.get_global_state("config.visualization_enable")
        vlm_chart_generator_enable = bool(
            session.get_global_state("config.vlm_chart_generator_enable")
        )
        rtp = session.get_global_state("search_context.report_type_policy") or {}
        research_intent = session.get_global_state("search_context.research_intent") or {}
        audience_role = (research_intent.get("audience_role", "") or "").strip()
        tone = (research_intent.get("tone", "") or "").strip()
        result = dict(
            thread_id=session.get_global_state("config.thread_id") or "",
            report_style=session.get_global_state("config.report_style") or ReportStyle.SCHOLARLY.value,
            report_format=session.get_global_state("config.report_format") or ReportFormat.MARKDOWN,
            current_outline=session.get_global_state("search_context.current_outline"),
            all_classified_contents=all_classified_contents,
            current_report=current_report,
            language=session.get_global_state("search_context.language") or CHINESE,
            report_task=report_task,
            user_query=session.get_global_state("search_context.original_query"),
            llm_model_name=llm_model_name,
            visualization_enable=visualization_enable,
            vlm_chart_generator_enable=vlm_chart_generator_enable,
            report_type=rtp.get("report_type", "professional"),
            paragraph_style=rtp.get("paragraph_style", "detailed"),
            report_type_policy=rtp,
            research_intent=research_intent,
            audience_role=audience_role,
            tone=tone,
        )
        result.update(build_research_intent_prompt_context(research_intent))
        return result

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext):
        current_inputs = self._pre_handle(inputs, session, context)
        reporter = Reporter(current_inputs.get("llm_model_name"))
        success, report_str = await reporter.generate_report(current_inputs)
        algorithm_output = dict(
            success=success,
            report_str=report_str,
            report=current_inputs.get("report"),
            all_classified_contents=current_inputs.get("all_classified_contents"),
        )

        return self._post_handle(inputs, algorithm_output, session, context)

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        # generate fail
        if not algorithm_output.get("success"):
            current_report = session.get_global_state("search_context.current_report")
            if current_report:
                current_report.report_content = "error: " + algorithm_output.get("report_str")
                session.update_global_state({"search_context.current_report": current_report})
            logger.error("[ReporterNode] ReporterNode ended with fail.")
            report_str = algorithm_output.get("report_str", "")
            if report_str.startswith(f"[{StatusCode.REPORT_GENERATE_ERROR.code}]"):
                exception_info = report_str
            else:
                exception_info = format_exception_info(StatusCode.REPORT_GENERATE_ERROR, report_str)
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            add_debug_log_wrapper(
                session, NodeDebugData(NodeId.REPORTER.value, 0, NodeType.MAIN.value, output_content=exception_info)
            )
            return dict(next_node=NodeId.END.value)

        # generate success
        current_report = session.get_global_state("search_context.current_report")
        if current_report:
            current_report.report_content = algorithm_output.get("report", "")
            current_report.all_classified_contents = algorithm_output.get("all_classified_contents", [])
            session.update_global_state({"search_context.current_report": current_report})

        # 添加报告debug日志
        debug_content = {
            "report_content": current_report.report_content if current_report else "",
            "all_classified_contents": current_report.all_classified_contents if current_report else [],
        }
        add_debug_log_wrapper(
            session,
            NodeDebugData(
                NodeId.REPORTER.value, 0, NodeType.MAIN.value, output_content=str(debug_content).replace("\\n", "\n")
            ),
        )
        return dict(next_node=NodeId.VLM_CHART_GENERATOR.value)


class EndNode(End):
    """
    图结束节点
    """

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """执行结束节点并输出最终结果。

        Args:
            inputs (Input): 节点输入（当前节点不依赖该字段）。
            session (Session): 工作流会话对象。
            context (ModelContext): 模型上下文对象。

        Returns:
            Output: 包含序列化后的 `final_result` 字段。
        """
        logger.info(f"[EndNode] Start EndNode.")
        final_result = session.get_global_state("search_context.final_result") or {}
        response_content = final_result.get("response_content", "") or ""
        if response_content:
            final_result = dict(final_result)
            language = session.get_global_state("search_context.language")
            ai_generated_notice = (
                "> This research report was generated by AI."
                if language == ENGLISH
                else "> 本研究报告由 AI 生成。"
            )
            if not response_content.endswith(ai_generated_notice):
                response_content = f"{response_content}\n\n---\n\n{ai_generated_notice}"
                final_result["response_content"] = response_content
                session.update_global_state({"search_context.final_result.response_content": response_content})
        stats_info_llm = bool(session.get_global_state("config.stats_info_llm"))
        if stats_info_llm:
            session_id = session.get_global_state("config.thread_id")
            workflow_usage = get_effective_workflow_llm_usage(session_id=session_id, session=session)
            final_result = dict(final_result)
            final_result["workflow_llm_token_usage"] = workflow_usage
            session.update_global_state({"search_context.final_result.workflow_llm_token_usage": workflow_usage})
            logger.info(
                f"[EndNode] workflow_llm_token_usage: " f"{json.dumps(workflow_usage, ensure_ascii=False, indent=2)}"
            )
            metrics_logger.info(
                f"{TIME_LOGGER_TAG} session_id: {session_id} ------ "
                f"[LLM CALL STATISTICS]: "
                f"workflow_llm_token_usage{json.dumps(workflow_usage, ensure_ascii=False, indent=2)}"
            )
        logger.info(
            f"[EndNode] Get final result: {'***' if LogManager.is_sensitive() else final_result}",
        )
        final_result_json = json.dumps(final_result, ensure_ascii=False)
        if final_result.get("exception_info", "") == "":
            await session.write_custom_stream(
                {
                    "message_id": str(uuid.uuid4()),
                    "agent": NodeId.END.value,
                    "content": final_result_json,
                    "message_type": MessageType.MESSAGE_CHUNK.value,
                    "event": StreamEvent.SUMMARY_RESPONSE.value,
                    "created_time": get_current_time(),
                }
            )
        else:
            await session.write_custom_stream(
                {
                    "message_id": str(uuid.uuid4()),
                    "agent": NodeId.END.value,
                    "content": final_result_json,
                    "message_type": MessageType.MESSAGE_CHUNK.value,
                    "event": StreamEvent.ERROR.value,
                    "created_time": get_current_time(),
                }
            )
        await session.write_custom_stream(
            {
                "message_id": str(uuid.uuid4()),
                "agent": NodeId.END.value,
                "content": "ALL END",
                "message_type": MessageType.MESSAGE_CHUNK.value,
                "event": StreamEvent.SUMMARY_RESPONSE.value,
                "created_time": get_current_time(),
            }
        )
        # 添加End节点debug日志
        add_debug_log_wrapper(
            session, NodeDebugData(NodeId.END.value, 0, NodeType.MAIN.value, output_content=final_result_json)
        )
        logger.info(f"[EndNode] End EndNode.")

        return dict(final_result=final_result_json)


class GenerateQuestionsNode(BaseNode):

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info(f"[GenerateQuestionsNode] Start GenerateQuestionsNode.")
        language = session.get_global_state("search_context.language")
        query = session.get_global_state("search_context.original_query")
        entry_search_results = session.get_global_state("search_context.entry_search_results") or []
        research_intent = session.get_global_state("search_context.research_intent") or {}
        report_type = research_intent.get("report_type")
        max_gen_question_retry_num = session.get_global_state("config.workflow_max_gen_question_retry_num")
        llm_model_name = adapt_llm_model_name(session, NodeId.GENERATE_QUESTIONS.value)
        return dict(language=language, query=query, entry_search_results=entry_search_results,
                    max_gen_question_retry_num=max_gen_question_retry_num,
                    llm_model_name=llm_model_name,
                    report_type=report_type)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_inputs = self._pre_handle(inputs, session, context)
        current_executed_num = 0
        max_gen_question_retry_num = current_inputs.get("max_gen_question_retry_num", 5)
        algorithm_output = dict()
        while current_executed_num < max_gen_question_retry_num:
            algorithm_output = await query_interpreter(current_inputs)
            current_executed_num += 1
            if algorithm_output.get("result", ""):
                break
            msg = (
                f"[GenerateQuestionsNode] Generate questions failed, retry generating query interpretation "
                f"({current_executed_num}/{max_gen_question_retry_num}) times."
            )
            if current_executed_num < max_gen_question_retry_num:
                logger.warning(msg)
            else:
                logger.error(msg)
        result = self._post_handle(current_inputs, algorithm_output, session, context)
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        if algorithm_output.get("exception_info"):
            exception_info = algorithm_output.get("exception_info")
            logger.error(f"[GenerateQuestionsNode] exception: {'*' if LogManager.is_sensitive() else exception_info}")
            session.update_global_state({"search_context.final_result.exception_info": exception_info})

            add_debug_log_wrapper(
                session,
                NodeDebugData(
                    NodeId.GENERATE_QUESTIONS.value,
                    0,
                    NodeType.MAIN.value,
                    output_content=str(exception_info).replace("\\n", "\n"),
                ),
            )
            return dict(next_node=NodeId.END.value)
        if not algorithm_output.get("result"):
            exception_info = format_exception_info(
                StatusCode.INTERPRETATION_GENERATE_ERROR, "Query Interpreter result is empty."
            )
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            logger.error(f"[GenerateQuestionsNode] {exception_info}")
            add_debug_log_wrapper(
                session,
                NodeDebugData(
                    NodeId.GENERATE_QUESTIONS.value,
                    0,
                    NodeType.MAIN.value,
                    output_content=str(exception_info).replace("\\n", "\n"),
                ),
            )
            return dict(next_node=NodeId.END.value)

        questions_text = algorithm_output.get("result")
        session.update_global_state({"search_context.questions": questions_text})
        add_debug_log_wrapper(
            session,
            NodeDebugData(
                NodeId.GENERATE_QUESTIONS.value, 0, NodeType.MAIN.value, output_content=questions_text
            ),
        )
        logger.info(f"[GenerateQuestionsNode] End GenerateQuestionsNode.")
        return dict(next_node=NodeId.FEEDBACK_HANDLER.value)


class OutlineNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.log_prefix = ""
        self.outline_prompt = "outliner"
        self.with_dep_driving = False

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        self.log_prefix = f"[{self.__class__.__name__}]"
        logger.info(f"{self.log_prefix} Start {self.__class__.__name__}.")
        language = session.get_global_state("search_context.language")
        messages = session.get_global_state("search_context.messages")
        questions = session.get_global_state("search_context.questions")
        user_feedback = session.get_global_state("search_context.user_feedback")
        configured_section_num = session.get_global_state("config.outliner_max_section_num")
        max_outline_retry_num = session.get_global_state("config.outliner_max_generate_outline_retry_num")
        llm_model_name = adapt_llm_model_name(session, NodeId.OUTLINE.value)
        report_template = session.get_global_state("search_context.report_template")
        outline_interactions = session.get_global_state("search_context.outline_interactions") or []
        outline_interaction_mode = ""
        previous_feedback_list = []
        current_interaction_feedback = ""
        outline_interactions = [OutlineInteraction(**i) if isinstance(i, dict) else i for i in outline_interactions]

        if outline_interactions:
            last = outline_interactions[-1]
            outline_interaction_mode = last.interaction_mode
            current_interaction_feedback = last.feedback

            previous_feedback_list = [
                i.feedback for i in outline_interactions if i.interaction_mode == "revise_comment" and i.feedback
            ]

        if previous_feedback_list:
            previous_feedback = "\n".join(
                f"Round {i + 1} feedback: {feedback}" for i, feedback in enumerate(previous_feedback_list)
            )
        else:
            previous_feedback = "No previous feedback."

        # 如果是大纲交互场景，使用交互记录中的 feedback；否则使用 user_feedback
        if outline_interaction_mode:
            user_feedback = current_interaction_feedback

        current_outline = session.get_global_state("search_context.current_outline")
        outline_interaction_enabled = session.get_global_state("config.outline_interaction_enabled")
        api_tools_config = session.get_global_state("config.api_tools_config") or {}
        entry_search_results = session.get_global_state("search_context.entry_search_results") or []
        rtp = session.get_global_state("search_context.report_type_policy") or {}
        research_intent = session.get_global_state("search_context.research_intent") or {}
        requested_section_num = research_intent.get("section_count")
        if requested_section_num:
            section_num = min(int(requested_section_num), OUTLINER_SECTION_NUM_MAX)
        else:
            section_num = configured_section_num
        audience_role = research_intent.get("audience_role") or ""
        tone = research_intent.get("tone") or ""
        vlm_chart_generator_enable = bool(
            session.get_global_state("config.vlm_chart_generator_enable")
        )
        result = dict(
            messages=messages,
            user_feedback=user_feedback,
            questions=questions,
            language=language,
            outline_execution_method=session.get_global_state("search_context.outline_execution_method") or "",
            entry_search_results=entry_search_results,
            section_num=section_num,
            max_section_num=OUTLINER_SECTION_NUM_MAX,
            max_outline_retry_num=max_outline_retry_num,
            llm_model_name=llm_model_name,
            report_template=report_template,
            outline_interaction_mode=outline_interaction_mode,
            current_outline=current_outline,
            outline_interaction_enabled=outline_interaction_enabled,
            previous_feedback=previous_feedback,
            api_tools_config=api_tools_config,
            report_type=rtp.get("report_type", "professional"),
            require_summary_first=rtp.get("require_summary_first", False),
            require_methodology_and_risk=rtp.get("require_methodology_and_risk", False),
            audience_role=audience_role,
            tone=tone,
            vlm_chart_generator_enable=vlm_chart_generator_enable,
        )
        result.update(build_research_intent_prompt_context(research_intent))
        return result

    def _get_with_dep_driving(self, current_inputs: dict) -> bool:
        """
        判断当前大纲生成是否使用依赖驱动工具 schema。

        Args:
            current_inputs: 大纲节点预处理后的输入，优先读取 outline_execution_method。

        Returns:
            True 表示使用 dependency_driving 工具 schema；False 表示使用普通大纲工具 schema。
        """
        selected_method = current_inputs.get("outline_execution_method")
        if selected_method in {ExecutionMethod.PARALLEL.value, ExecutionMethod.DEPENDENCY_DRIVING.value}:
            return selected_method == ExecutionMethod.DEPENDENCY_DRIVING.value
        return self.with_dep_driving

    def _select_prompt_and_dep_driving(self, current_inputs: dict) -> tuple[str, bool, str]:
        """
        同时选择大纲 prompt 与工具 schema。

        Args:
            current_inputs: 大纲节点预处理后的输入，包含交互模式、模板和 outline_execution_method。

        Returns:
            prompt 名称、是否启用 dependency_driving 工具 schema，以及本轮实际执行的大纲模式。
        """
        prompt_name = self._select_prompt_name(current_inputs)
        if prompt_name in {"outliner_template", "outliner_user_revised"}:
            return prompt_name, False, ExecutionMethod.PARALLEL.value
        with_dep_driving = self._get_with_dep_driving(current_inputs)
        selected_method = (
            ExecutionMethod.DEPENDENCY_DRIVING.value
            if with_dep_driving
            else ExecutionMethod.PARALLEL.value
        )
        return prompt_name, with_dep_driving, selected_method

    def _sync_outline_execution_method(
        self,
        current_inputs: dict,
        session: Session,
        selected_method: str,
    ) -> None:
        """
        将本轮实际大纲模式同步回 session，保证 prompt、工具 schema 与后续节点一致。

        Args:
            current_inputs: 大纲节点预处理后的输入。
            session: 当前工作流会话。
            selected_method: 本轮实际执行的大纲模式。
        """
        current_method = current_inputs.get("outline_execution_method")
        if current_method not in {ExecutionMethod.PARALLEL.value, ExecutionMethod.DEPENDENCY_DRIVING.value}:
            return
        if current_method == selected_method:
            return
        current_inputs["outline_execution_method"] = selected_method
        session.update_global_state({"search_context.outline_execution_method": selected_method})
        logger.info(
            "%s Reset outline_execution_method from %s to %s to keep prompt and tool schema consistent.",
            self.log_prefix,
            current_method,
            selected_method,
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_inputs = self._pre_handle(inputs, session, context)
        prompt_name, with_dep_driving, selected_method = self._select_prompt_and_dep_driving(current_inputs)
        self._sync_outline_execution_method(current_inputs, session, selected_method)
        outliner = Outliner(llm_model_name=current_inputs.get("llm_model_name"), prompt_name=prompt_name)
        outliner.with_dep_driving = with_dep_driving
        max_outline_retry_num = current_inputs.get("max_outline_retry_num", 1)

        success_flag = False
        error_msg = ""
        outline_executed_num = 0
        algorithm_output = None
        while not success_flag:
            if outline_executed_num >= max_outline_retry_num:
                last_error = algorithm_output.get("error_msg") if algorithm_output else ""
                detail = last_error or f"Reached max outline retry num: {max_outline_retry_num}"
                error_msg = format_exception_info(
                    StatusCode.OUTLINER_GENERATE_ERROR, detail, prefix=self.log_prefix
                )
                logger.error(error_msg)
                algorithm_output = {
                    "llm_result": "",
                    "current_outline": None,
                    "outline_executed_num": outline_executed_num,
                    "success_flag": False,
                    "error_msg": error_msg,
                }
                break
            if outline_executed_num > 0:
                logger.warning(
                    f"{self.log_prefix} Failed to generate Outline , retry generating outline for the "
                    f"{outline_executed_num}/{max_outline_retry_num} times."
                )
            outline_executed_num += 1
            algorithm_output = await outliner.generate_outline(current_inputs)
            success_flag = algorithm_output.get("success_flag")
            error_msg = algorithm_output.get("error_msg")

        if success_flag:
            outline: Outline = algorithm_output.get("current_outline")
            # 手动流式输出outline
            await custom_stream_output(session, str(uuid.uuid4()), outline.model_dump_json(), NodeId.OUTLINE.value)

        result = self._post_handle(inputs, algorithm_output, session, context)
        return result

    def _select_prompt_name(self, current_inputs: dict) -> str:
        """根据交互模式选择 prompt 名称，补充所需的输入字段"""
        report_template = current_inputs.get("report_template", "")
        outline_interaction_mode = current_inputs.get("outline_interaction_mode", "")
        feedback = current_inputs.get("user_feedback", "")
        if report_template and not outline_interaction_mode:
            return "outliner_template"
        if outline_interaction_mode == "revise_comment":
            if self._get_with_dep_driving(current_inputs):
                return "dep_driving_outliner_interaction"
            return "outliner_interaction"
        if outline_interaction_mode == "revise_outline":
            try:
                current_inputs["user_outline"] = Outline.model_validate_json(feedback)
            except Exception as e:
                logger.error(f"{self.log_prefix} Failed to parse user outline JSON: {e}")
            return "outliner_user_revised"
        if self._get_with_dep_driving(current_inputs):
            return "dep_driving_outliner"
        return self.outline_prompt

    def _get_next_node_after_outline(self, session: Session) -> str:
        """获取大纲生成成功后的下一个节点"""
        selected_method = session.get_global_state("search_context.outline_execution_method")
        if selected_method == ExecutionMethod.DEPENDENCY_DRIVING.value:
            return NodeId.DEPENDENCY_EDITOR_TEAM.value
        return NodeId.EDITOR_TEAM.value

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        """处理大纲生成结果"""
        success_flag = algorithm_output.get("success_flag")

        # 大纲交互兜底
        if not success_flag:
            current_outline = session.get_global_state("search_context.current_outline")
            outline_interactions = session.get_global_state("search_context.outline_interactions") or []
            # 大纲交互场景且已有大纲的情况下才启用兜底
            if current_outline and outline_interactions:
                logger.warning(
                    f"{self.log_prefix} Outline generation failed in interaction mode, " "fallback to previous outline."
                )
                algorithm_output["current_outline"] = current_outline
                algorithm_output["success_flag"] = True
                success_flag = True

        if success_flag:
            outline = algorithm_output.get("current_outline", None)
            session.update_global_state({"search_context.current_outline": outline})

            add_debug_log_wrapper(
                session,
                NodeDebugData(
                    NodeId.OUTLINE.value, 0, NodeType.MAIN.value, output_content=str(outline).replace("\\n", "\n")
                ),
            )

            outline_interaction_enabled = session.get_global_state("config.outline_interaction_enabled")
            if outline_interaction_enabled:
                next_node = NodeId.OUTLINE_INTERACTION.value
                logger.info(f"{self.log_prefix} Outline generated, go to OutlineInteractionNode.")
            else:
                next_node = self._get_next_node_after_outline(session)
                logger.info(f"{self.log_prefix} Successfully generate outline, go to {next_node}.")
        else:
            next_node = NodeId.END.value
            error_msg = algorithm_output.get("error_msg")
            session.update_global_state({"search_context.final_result.exception_info": error_msg})

            add_debug_log_wrapper(
                session, NodeDebugData(NodeId.OUTLINE.value, 0, NodeType.MAIN.value, output_content=error_msg)
            )
            logger.error(f"{self.log_prefix} Failed to generate outline, go to {next_node}.")
        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")

        return dict(next_node=next_node)


class DependencyOutlineNode(OutlineNode):
    def __init__(self):
        super().__init__()
        self.outline_prompt = "dep_driving_outliner"
        self.with_dep_driving = True

    def _get_with_dep_driving(self, current_inputs: dict) -> bool:
        """依赖驱动大纲节点固定使用依赖驱动工具 schema，不受 session 路由状态覆盖。"""
        return True

    def _select_prompt_and_dep_driving(self, current_inputs: dict) -> tuple[str, bool, str]:
        """
        固定依赖驱动 workflow 始终保持 dependency_driving 执行契约。

        Args:
            current_inputs: 大纲节点预处理后的输入。

        Returns:
            prompt 名称、依赖驱动工具 schema 标记，以及 dependency_driving 执行模式。
        """
        return self._select_prompt_name(current_inputs), True, ExecutionMethod.DEPENDENCY_DRIVING.value

    def _get_next_node_after_outline(self, session: Session) -> str:
        """依赖驱动模式下的下一个节点"""
        return NodeId.DEPENDENCY_EDITOR_TEAM.value


class SourceTracerNode(BaseNode):
    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    async def build_citation_checker_result(citation_checker_info, datas, llm_model):
        """
        构建溯源校验结果
        """
        processed_report = citation_checker_info.get("response_content", {})
        citation_checker_result_str = await postprocess_by_citation_checker(processed_report, datas, llm_model)

        result_dict = dict(check_result=True, citation_checker_result_str=citation_checker_result_str)

        return result_dict

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info(f"[SourceTracerNode] Start SourceTracerNode.")
        search_mode = session.get_global_state("search_context.search_mode")
        current_report = session.get_global_state("search_context.current_report")
        # 从 Report 对象中获取内容
        report = getattr(current_report, "report_content", "") if current_report else ""
        merged_trace_source_datas = getattr(current_report, "merged_trace_source_datas", []) if current_report else []
        all_classified_contents = getattr(current_report, "all_classified_contents", []) if current_report else []
        language = session.get_global_state("search_context.language")

        research_trace_source_switch = session.get_global_state("config.source_tracer_research_trace_source_switch")
        llm_model_name = adapt_llm_model_name(session, NodeId.SOURCE_TRACER.value)

        need_exit = False
        if (search_mode == "research") and (research_trace_source_switch is False):
            logger.info(f"[SourceTracerNode] research_trace_source_switch is False, skip trace source.")
            need_exit = True

        # 封装为本节点的Input对象
        return dict(
            need_exit=need_exit,
            search_mode=search_mode,
            report=report,
            merged_trace_source_datas=merged_trace_source_datas,
            all_classified_contents=all_classified_contents,
            research_trace_source_switch=research_trace_source_switch,
            language=language,
            llm_model_name=llm_model_name,
        )

    def _skip_trace_source_handle(
        self, inputs: Input, session: Session, context: ModelContext, current_inputs: dict
    ) -> dict:
        """
        不需要溯源的场景直接跳到后处理
        """
        origin_report = current_inputs.get("report", "")
        search_mode = current_inputs.get("search_mode", "research")
        algorithm_output = dict(need_exit=True, origin_report=origin_report, search_mode=search_mode)
        result = self._post_handle(inputs, algorithm_output, session, context)
        return result

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        try:
            current_inputs = self._pre_handle(inputs, session, context)

            if current_inputs.get("need_exit", False):
                return self._skip_trace_source_handle(inputs, session, context, current_inputs)

            modified_report = current_inputs.get("report", "")
            datas = current_inputs.get("merged_trace_source_datas", [])

            # 预处理数据结构给溯源验证使用
            language = current_inputs.get("language", "zh-CN")
            citation_checker_info = preprocess_info(modified_report, datas, language)
            need_check = citation_checker_info.get("need_check", True)
            if need_check is False:
                return self._skip_trace_source_handle(inputs, session, context, current_inputs)

            # 溯源验证
            check_result_dict = await self.build_citation_checker_result(
                citation_checker_info, datas, current_inputs.get("llm_model_name", "")
            )
        except CustomException as e:
            # 溯源异常的情况下，设置check_result为False，让post_handle记录异常信息返回出去
            if LogManager.is_sensitive():
                logger.error(f"[SourceTracerNode] trace source failed.")
            else:
                logger.error(f"[SourceTracerNode] trace source failed. {str(e)}")
            check_result_dict = {
                "check_result": False,
                "citation_checker_result_str": format_exception_info(StatusCode.SOURCE_TRACER_NODE_ERROR, e),
            }
        except Exception as e:
            if LogManager.is_sensitive():
                logger.error(f"[SourceTracerNode] trace source failed.")
            else:
                logger.error(f"[SourceTracerNode] trace source failed. {str(e)}")
            check_result_dict = {
                "check_result": False,
                "citation_checker_result_str": format_exception_info(StatusCode.SOURCE_TRACER_NODE_ERROR, e),
            }

        algorithm_output = {"check_result_dict": check_result_dict, "origin_report": current_inputs.get("report", "")}
        result = self._post_handle(inputs, algorithm_output, session, context)

        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> dict:
        origin_report = algorithm_output.get("origin_report", "")
        check_result_dict = algorithm_output.get("check_result_dict", {})
        citation_checker_result_str = check_result_dict.get("citation_checker_result_str", "")
        check_result = check_result_dict.get("check_result", False)
        if algorithm_output.get("need_exit", False):
            source_tracer_result = json.dumps(
                {"checked_trace_source_report_content": origin_report, "citation_messages": {}}, ensure_ascii=False
            )
        else:
            if check_result is True:
                source_tracer_result = citation_checker_result_str
            else:
                source_tracer_result = json.dumps(
                    {"checked_trace_source_report_content": origin_report, "citation_messages": {}}, ensure_ascii=False
                )
                session.update_global_state({"search_context.final_result.exception_info": citation_checker_result_str})

        source_tracer_result_dict = json.loads(source_tracer_result)
        checked_trace_source_report_content = source_tracer_result_dict.get("checked_trace_source_report_content", "")
        citation_messages = source_tracer_result_dict.get("citation_messages", {})
        checked_trace_source_datas = citation_messages.get("data", [])

        current_report = session.get_global_state("search_context.current_report")
        if not current_report:
            logger.warning("[SourceTracerNode] current_report is None, skip updating report fields.")
        else:
            current_report.checked_trace_source_report_content = checked_trace_source_report_content
            current_report.checked_trace_source_datas = checked_trace_source_datas
            session.update_global_state({"search_context.current_report": current_report})

        session.update_global_state(
            {"search_context.final_result.response_content": checked_trace_source_report_content}
        )
        session.update_global_state({"search_context.final_result.citation_messages": citation_messages})
        # 添加SourceTracerNode debug日志
        add_debug_log_wrapper(
            session,
            NodeDebugData(
                NodeId.SOURCE_TRACER.value,
                0,
                NodeType.MAIN.value,
                output_content=str(source_tracer_result_dict).replace("\\n", "\n"),
            ),
        )

        logger.info(f"[SourceTracerNode] End SourceTracerNode.")
        logger.info(
            f"[SourceTracerNode] source_tracer_result: " f"{'*' if LogManager.is_sensitive() else source_tracer_result}"
        )

        return dict(next_node=NodeId.SOURCE_TRACER_INFER.value)


class OutlineInteractionNode(BaseNode):
    """大纲交互节点: 接收用户反馈，保存历史，跳转到 OutlineNode 进行优化"""

    def __init__(self):
        super().__init__()
        self.log_prefix = "[OutlineInteractionNode]"

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info(f"{self.log_prefix} Start OutlineInteractionNode.")
        feedback_mode = session.get_global_state("config.workflow_feedback_mode")
        outline_interaction_enabled = session.get_global_state("config.outline_interaction_enabled")
        max_rounds = session.get_global_state("config.outline_interaction_max_rounds")
        outline_interactions = session.get_global_state("search_context.outline_interactions") or []
        current_round = len(outline_interactions)
        return dict(
            feedback_mode=feedback_mode,
            outline_interaction_enabled=outline_interaction_enabled,
            max_rounds=max_rounds,
            current_round=current_round,
        )

    def _get_next_node_after_accept(self, session: Session) -> str:
        """
        根据已选择的大纲执行方式决定用户接受大纲后的写作节点。

        Args:
            session: 当前工作流会话，用于读取 search_context.outline_execution_method。

        Returns:
            editor_team 或 dependency_editor_team。
        """
        selected_method = session.get_global_state("search_context.outline_execution_method")
        if selected_method == ExecutionMethod.DEPENDENCY_DRIVING.value:
            return NodeId.DEPENDENCY_EDITOR_TEAM.value
        return NodeId.EDITOR_TEAM.value

    def _reapply_search_constraints(self, session: Session) -> None:
        """从持久化 session 向当前运行新建的搜索 wrapper 重灌约束。

        Args:
            session: 当前工作流会话。
        """
        web_config = session.get_global_state("config.web_search_engine_config")
        engine_name = web_config.search_engine_name if web_config else ""
        research_intent = session.get_global_state("search_context.research_intent") or {}
        apply_web_search_domain_constraints(
            search_engine_name=engine_name,
            include_domains=research_intent.get("include_domains", []),
            exclude_domains=research_intent.get("exclude_domains", []),
        )
        apply_web_search_temporal_scope(
            search_engine_name=engine_name,
            temporal_scope=research_intent.get("temporal_scope"),
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        current_inputs = self._pre_handle(inputs, session, context)

        if not current_inputs.get("outline_interaction_enabled"):
            logger.info(f"{self.log_prefix} Outline interaction is disabled, skip to editor team.")
            return dict(next_node=self._get_next_node_after_accept(session))

        max_rounds = current_inputs.get("max_rounds", 5)
        current_round = current_inputs.get("current_round", 0)

        if current_round >= max_rounds:
            logger.info(f"{self.log_prefix} Reached max rounds: {max_rounds}")
            await self._notify_user(session, "Maximum interaction rounds reached.", StreamEvent.USER_INPUT_ENDED)
            self._reapply_search_constraints(session)
            return dict(next_node=self._get_next_node_after_accept(session))

        feedback_mode = current_inputs.get("feedback_mode", "cmd")
        user_input = await self._get_user_input(feedback_mode, f"{current_round+1}", session)

        if not user_input:
            logger.warning(f"{self.log_prefix} No user input received")
            return dict(next_node=NodeId.END.value)

        action = user_input.get("interrupt_feedback", "")
        if action == "accepted":
            await self._notify_user(session, "Outline accepted.", StreamEvent.USER_INPUT_ENDED)

        result = self._post_handle(inputs, user_input, session, context)
        return dict(next_node=result)

    def _save_history(self, session: Session, feedback: str, interaction_mode: str):
        """保存交互记录"""
        current_outline = session.get_global_state("search_context.current_outline")
        outline_interactions = session.get_global_state("search_context.outline_interactions") or []
        new_interaction = OutlineInteraction(
            feedback=feedback, interaction_mode=interaction_mode, outline_before=current_outline
        )
        outline_interactions.append(new_interaction)
        session.update_global_state({"search_context.outline_interactions": outline_interactions})

    async def _notify_user(self, session: Session, message: str, event: StreamEvent):
        """通知用户"""
        await session.write_custom_stream(
            {
                "message_id": str(uuid.uuid4()),
                "agent": NodeId.OUTLINE_INTERACTION.value,
                "content": message,
                "message_type": MessageType.MESSAGE_CHUNK.value,
                "event": event.value,
                "created_time": get_current_time(),
            }
        )

    async def _get_user_input(self, feedback_mode: str, message: str, session: Session) -> dict:
        """获取大纲交互阶段的用户输入。

        Args:
            feedback_mode: 反馈交互模式，当前支持 ``cmd`` 和 ``web``。
            message: 当前轮次展示文案中的轮次标识。
            session: 当前会话对象。

        Returns:
            dict: 解析后的输入字典；当输入不是合法 JSON 时返回空字典。
        """
        prompt = f"Round {message}: waiting for user feedback."

        if feedback_mode == "web":
            if bool(session.get_global_state("config.stats_info_llm")):
                save_workflow_llm_usage_to_session(
                    session=session,
                    session_id=session.get_global_state("config.thread_id"),
                )
            user_input = await session.interact(prompt)
            # Clear the consumed resume input so the same feedback is not replayed
            # when outline_interaction is reached again in the current workflow run.
            session.update_state({INTERACTIVE_INPUT: None})
        else:
            user_input = input(prompt)
        try:
            logger.info(f"{self.log_prefix} Received user input: {'***' if LogManager.is_sensitive() else user_input}")
            return json.loads(user_input)
        except json.JSONDecodeError as e:
            exception_info = format_exception_info(StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_JSON, e)
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            # 添加FeedbackHandlerNode debug日志
            add_debug_log_wrapper(
                session,
                NodeDebugData(
                    NodeId.OUTLINE_INTERACTION.value,
                    0,
                    NodeType.MAIN.value,
                    output_content=str(exception_info).replace("\\n", "\n"),
                ),
            )
            return {}

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        action = algorithm_output.get("interrupt_feedback", "")
        feedback = algorithm_output.get("feedback", "")

        if action == "accepted":
            logger.info(f"{self.log_prefix} User accepted the outline")
            self._reapply_search_constraints(session)
            next_node = self._get_next_node_after_accept(session)
        elif action == "revise_comment":
            logger.info(f"{self.log_prefix} User wants to revise with comments")
            self._save_history(session, feedback, "revise_comment")
            next_node = NodeId.OUTLINE.value
        elif action == "revise_outline":
            logger.info(f"{self.log_prefix} User provided revised outline")
            self._save_history(session, feedback, "revise_outline")
            next_node = NodeId.OUTLINE.value
        else:
            logger.warning(f"{self.log_prefix} Invalid user action: {action}.")
            next_node = NodeId.END.value

        add_debug_log_wrapper(
            session,
            NodeDebugData(
                NodeId.OUTLINE_INTERACTION.value,
                0,
                NodeType.MAIN.value,
                output_content=str(algorithm_output).replace("\\n", "\n"),
            ),
        )
        logger.info(f"{self.log_prefix} End OutlineInteractionNode.")
        return next_node


class DependencyOutlineInteractionNode(OutlineInteractionNode):
    def __init__(self):
        super().__init__()
        self.log_prefix = "[DependencyOutlineInteractionNode]"

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        result = await super()._do_invoke(inputs, session, context)
        if result.get("next_node") == NodeId.EDITOR_TEAM.value:
            result["next_node"] = NodeId.DEPENDENCY_EDITOR_TEAM.value
        return result


class SourceTracerInferNode(BaseNode):
    def __init__(self) -> None:
        super().__init__()
        self.log_prefix = "[SourceTracerInferNode]"

    @staticmethod
    async def build_source_tracer_infer_result(infer_infos):
        """调用溯源推理模块生成溯源推理图
        Returns:
            dict = (response, infer_messages, check_infos)
        """
        infer = SourceTracerInfer(infer_infos)
        response, infer_messages, check_infos, error_message = await infer.run()
        if error_message:
            raise Exception(error_message)
        return dict(response=response, infer_messages=infer_messages, check_infos=check_infos)

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info(f"{self.log_prefix} Start SourceTracerInferNode.")
        search_mode = session.get_global_state("search_context.search_mode")
        llm_model_name = adapt_llm_model_name(session, NodeId.SOURCE_TRACER_INFER.value)
        source_tracer_infer_switch = session.get_global_state("config.source_tracer_infer_switch")

        language = session.get_global_state("search_context.language")
        current_report = session.get_global_state("search_context.current_report")
        source_tracer_response = (
            getattr(current_report, "checked_trace_source_report_content", "") if current_report else ""
        )
        all_classified_contents = getattr(current_report, "all_classified_contents", []) if current_report else []

        # 封装本节点的Input对象
        return dict(
            source_tracer_infer_switch=source_tracer_infer_switch,
            search_mode=search_mode,
            llm_model_name=llm_model_name,
            language=language,
            source_tracer_response=source_tracer_response,
            all_classified_contents=all_classified_contents,
        )

    def _post_handle(self, inputs, algorithm_output: dict, session: Session, context: ModelContext):
        infer_success = algorithm_output.get("infer_success", False)
        source_tracer_infer_switch = algorithm_output.get("source_tracer_infer_switch", False)
        if not source_tracer_infer_switch:
            logger.info(f"{self.log_prefix} Skip Infer! Please turn on the source_tracer_infer_switch.")
        else:
            if infer_success:
                logger.info(f"{self.log_prefix} Infer Success!")
            else:
                logger.info(f"{self.log_prefix} Infer Fail!")
        error_msg = algorithm_output.get("error_msg", "")
        response = algorithm_output.get("response", "")
        infer_messages = algorithm_output.get("infer_messages", [])
        scores = algorithm_output.get("scores", [(0, 0)])

        source_tracer_infer_result_dict = dict(response=response, infer_messages=infer_messages, scores=scores)

        session.update_global_state(
            {
                "search_context.final_result.response_content": response,
                "search_context.final_result.infer_messages": infer_messages,
            }
        )

        if error_msg:
            session.update_global_state({"search_context.final_result.exception_info": error_msg})

        # 添加SourceTracerInferNode debug日志
        add_debug_log_wrapper(
            session,
            NodeDebugData(
                NodeId.SOURCE_TRACER.value,
                0,
                NodeType.MAIN.value,
                output_content=str(source_tracer_infer_result_dict).replace("\\n", "\n"),
            ),
        )

        logger.info(f"{self.log_prefix} End SourceTracerInferNode.")
        logger.info(
            f"{self.log_prefix} source_tracer_infer_result:"
            f"{'*' if LogManager.is_sensitive() else source_tracer_infer_result_dict}"
        )

        return dict(next_node=NodeId.END.value)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext):

        scores = [(0, 0)]
        current_inputs = self._pre_handle(inputs, session, context)
        try:
            source_tracer_infer_switch = current_inputs.get("source_tracer_infer_switch", False)
            if not source_tracer_infer_switch:
                algorithm_output = dict(
                    source_tracer_infer_switch=source_tracer_infer_switch,
                    response=current_inputs.get("source_tracer_response", ""),
                )
                return self._post_handle(inputs, algorithm_output, session, context)

            # 溯源推理
            infer_result_dict = await self.build_source_tracer_infer_result(current_inputs)

            # 溯源推理校验
            check_infos = infer_result_dict.get("check_infos", {})
            check_infos["llm_model_name"] = current_inputs.get("llm_model_name", "")
            check_infos["language"] = current_inputs.get("language", "zh")

        except Exception as e:
            error_msg = f"source_tracer_infer failed."
            if LogManager.is_sensitive():
                logger.error(f"{self.log_prefix} {error_msg}")
            else:
                logger.error(f"{self.log_prefix} {error_msg} {e}")
            infer_result_dict = dict(
                infer_success=False,
                response=current_inputs.get("source_tracer_response", ""),
                infer_messages=[],
                scores=[(0, 0)],
                error_msg=format_exception_info(StatusCode.SOURCE_TRACER_INFER_ERROR, e, prefix=self.log_prefix),
                source_tracer_infer_switch=current_inputs.get("source_tracer_infer_switch", False),
            )
        else:
            # 这里添加溯源推理校验模块
            infer_result_dict["scores"] = scores
            infer_result_dict["source_tracer_infer_switch"] = current_inputs.get("source_tracer_infer_switch", False)
            infer_result_dict["infer_success"] = True

        algorithm_output = infer_result_dict
        result = self._post_handle(inputs, algorithm_output, session, context)
        return result


class UserFeedbackProcessorNode(BaseNode):
    """在报告生成完成后，处理用户对局部文本的迭代改写请求。"""

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        """收集用户反馈节点执行所需的会话状态。

        Args:
            inputs: 节点输入。
            session: 当前会话。
            context: 模型上下文。

        Returns:
            包含开关、交互计数、快照标记和最终结果等信息的字典。
        """
        logger.info("[UserFeedbackProcessorNode] Start UserFeedbackProcessorNode.")
        enable = session.get_global_state("config.user_feedback_processor_enable")
        if not enable:
            return dict(disabled=True)

        return dict(
            disabled=False,
            max_interactions=session.get_global_state("config.user_feedback_processor_max_interactions"),
            feedback_mode=session.get_global_state("config.workflow_feedback_mode"),
            interaction_count=session.get_global_state("search_context.feedback_interaction_count") or 0,
            feedback_snapshot_sent=session.get_global_state("search_context.feedback_snapshot_sent") or False,
            language=session.get_global_state("search_context.language"),
            final_result=session.get_global_state("search_context.final_result"),
            current_report=session.get_global_state("search_context.current_report"),
            llm_model_name=adapt_llm_model_name(session, NodeId.USER_FEEDBACK_PROCESSOR.value),
            enable_local_source_trace=(
                session.get_global_state("config.source_tracer_research_trace_source_switch") is not False
            ),
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """执行用户反馈节点主流程并注入上下文变量。

        该方法会先把 ``session`` 与 ``context`` 注入到 contextvar，便于补充搜索
        子链路复用信息采集能力；随后依次执行预处理、动作构建和状态回写。

        Args:
            inputs: 节点输入。
            session: 当前会话。
            context: 模型上下文。

        Returns:
            Output: 节点执行后的路由结果。
        """
        session_context.set(session)
        model_context.set(context)
        current_inputs = self._pre_handle(inputs, session, context)

        # 确定 algorithm_output，包含 next_node 以供 _post_handle 路由
        algorithm_output = await self._build_algorithm_output(current_inputs, session, context)

        return self._post_handle(inputs, algorithm_output, session, context)

    async def _build_algorithm_output(self, current_inputs: dict, session: Session, context: ModelContext) -> dict:
        """解析反馈并构造节点输出。

        Args:
            current_inputs: `_pre_handle` 产出的节点输入快照。
            session: 当前会话。
            context: 模型上下文；供补充搜索等子链路复用。

        Returns:
            包含路由信息、报告更新结果以及交互消耗标记的字典。
        """
        if current_inputs.get("disabled"):
            logger.info("[UserFeedbackProcessorNode] Feature disabled, routing to EndNode.")
            return dict(next_node=NodeId.END.value)

        interaction_count = current_inputs["interaction_count"]
        max_interactions = current_inputs["max_interactions"]
        final_result = current_inputs["final_result"]
        mark_feedback_snapshot_sent = False

        # 首次进入用户反馈阶段时，先把当前完整报告推给前端；后续由 session 标记避免重复推送。
        if not current_inputs["feedback_snapshot_sent"]:
            if final_result:
                final_result_json = json.dumps(final_result, ensure_ascii=False)
                await custom_stream_output(
                    session, str(uuid.uuid4()), final_result_json, NodeId.USER_FEEDBACK_PROCESSOR.value
                )
                # 注意：session.interact 可能触发中断并提前结束当前轮执行，
                # 这里需要立即持久化快照标记，避免下一轮重复发送首帧快照。
                session.update_global_state({"search_context.feedback_snapshot_sent": True})
                mark_feedback_snapshot_sent = True
            else:
                logger.error("[UserFeedbackProcessorNode] Final result not found")
                return dict(next_node=NodeId.END.value)

        report_content = final_result.get("response_content", "") or ""

        raw_feedback = await self._get_user_feedback(current_inputs["feedback_mode"], session)
        consume_interaction = True
        try:
            feedback = UserFeedbackProcessor.parse_feedback(raw_feedback)
            action = feedback.get("action", "")
            consume_interaction = action != "sync"
            processor = UserFeedbackProcessor(current_inputs["llm_model_name"])

            if action == "finish":
                logger.info("[UserFeedbackProcessorNode] User finished feedback, routing to EndNode.")
                await self._notify_user(session, "User feedback finished.", StreamEvent.USER_INPUT_ENDED)
                return dict(
                    next_node=NodeId.END.value,
                    mark_feedback_snapshot_sent=mark_feedback_snapshot_sent,
                )

            if consume_interaction and interaction_count >= max_interactions:
                logger.info(f"[UserFeedbackProcessorNode] Max interactions reached: {max_interactions}")
                await self._notify_user(session, "Maximum interaction rounds reached.", StreamEvent.USER_INPUT_ENDED)
                return dict(
                    next_node=NodeId.END.value,
                    mark_feedback_snapshot_sent=mark_feedback_snapshot_sent,
                )

            UserFeedbackProcessor.validate(feedback, report_content)

            action_result = await processor.execute(
                feedback=feedback,
                final_result=final_result,
                language=current_inputs["language"],
                enable_local_source_trace=current_inputs["enable_local_source_trace"],
                current_report=current_inputs.get("current_report"),
            )
        except CustomException as e:
            if interaction_count >= max_interactions and consume_interaction:
                logger.info(f"[UserFeedbackProcessorNode] Max interactions reached: {max_interactions}")
                await self._notify_user(session, "Maximum interaction rounds reached.", StreamEvent.USER_INPUT_ENDED)
                return dict(
                    next_node=NodeId.END.value,
                    mark_feedback_snapshot_sent=mark_feedback_snapshot_sent,
                )
            logger.error(f"[UserFeedbackProcessorNode] User feedback failed: {e}")
            await UserFeedbackProcessor.send_error(session, e)
            return dict(
                next_node=NodeId.USER_FEEDBACK_PROCESSOR.value,
                interaction_count=interaction_count,
                consume_interaction=consume_interaction,
                mark_feedback_snapshot_sent=mark_feedback_snapshot_sent,
                exception_info=format_exception_info(StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR, e),
            )
        except Exception as e:
            if interaction_count >= max_interactions and consume_interaction:
                logger.info(f"[UserFeedbackProcessorNode] Max interactions reached: {max_interactions}")
                await self._notify_user(session, "Maximum interaction rounds reached.", StreamEvent.USER_INPUT_ENDED)
                return dict(
                    next_node=NodeId.END.value,
                    mark_feedback_snapshot_sent=mark_feedback_snapshot_sent,
                )
            logger.error(f"[UserFeedbackProcessorNode] Action failed: {e}")
            wrapped_error = CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(e=str(e)),
            )
            await UserFeedbackProcessor.send_error(session, wrapped_error)
            return dict(
                next_node=NodeId.USER_FEEDBACK_PROCESSOR.value,
                interaction_count=interaction_count,
                consume_interaction=consume_interaction,
                mark_feedback_snapshot_sent=mark_feedback_snapshot_sent,
                exception_info=format_exception_info(StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR, e),
            )

        stream_result = UserFeedbackProcessor.build_stream_result(feedback, action_result)
        if action_result.get("read_only_result", False):
            updated_final_result = final_result
        else:
            updated_final_result = dict(final_result or {})
            updated_final_result.update(
                {
                    "response_content": action_result["new_report"],
                }
            )
        if "citation_messages" in action_result:
            updated_final_result["citation_messages"] = action_result["citation_messages"]
        if "warning_info" in action_result:
            updated_final_result["warning_info"] = action_result["warning_info"]
        await UserFeedbackProcessor.send_result(
            session=session,
            feedback=feedback,
            result=stream_result,
            final_result=updated_final_result,
            feedback_interaction_count=interaction_count if not consume_interaction else interaction_count + 1,
        )

        return dict(
            next_node=NodeId.USER_FEEDBACK_PROCESSOR.value,
            interaction_count=interaction_count,
            consume_interaction=consume_interaction,
            mark_feedback_snapshot_sent=mark_feedback_snapshot_sent,
            feedback=feedback,
            **action_result,
        )

    async def _get_user_feedback(self, feedback_mode: str, session: Session) -> str:
        """按交互模式获取原始用户反馈。

        Args:
            feedback_mode: 反馈交互模式，当前支持 ``cmd`` 和 ``web``。
            session: 当前会话。

        Returns:
            str: 原始用户输入；当交互模式非法时返回空字符串。
        """
        prompt = "\nProvide your feedback: "
        user_input = ""
        if feedback_mode == "cmd":
            user_input = input(prompt)
        elif feedback_mode == "web":
            if bool(session.get_global_state("config.stats_info_llm")):
                save_workflow_llm_usage_to_session(
                    session=session,
                    session_id=session.get_global_state("config.thread_id"),
                )
            user_input = await session.interact(prompt)
            session.update_state({INTERACTIVE_INPUT: None})
        else:
            logger.error(f"[UserFeedbackProcessorNode] Invalid feedback_mode: {feedback_mode}")
        return user_input

    async def _notify_user(self, session: Session, message: str, event: StreamEvent):
        """向前端发送一条用户反馈节点的提示消息。

        Args:
            session: 当前会话。
            message: 要发送的消息文本。
            event: 对应的流式事件类型。

        Returns:
            None
        """
        await session.write_custom_stream(
            {
                "message_id": str(uuid.uuid4()),
                "agent": NodeId.USER_FEEDBACK_PROCESSOR.value,
                "content": message,
                "message_type": MessageType.MESSAGE_CHUNK.value,
                "event": event.value,
                "created_time": get_current_time(),
            }
        )

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> dict:
        """回写用户反馈节点产生的 session 状态。

        Args:
            inputs: 节点输入。
            algorithm_output: `_build_algorithm_output` 返回结果。
            session: 当前会话。
            context: 模型上下文。

        Returns:
            仅包含下一跳节点的路由结果。
        """
        next_node = algorithm_output["next_node"]
        interaction_count = algorithm_output.get("interaction_count")
        consume_interaction = algorithm_output.get("consume_interaction", True)
        if algorithm_output.get("mark_feedback_snapshot_sent"):
            session.update_global_state({"search_context.feedback_snapshot_sent": True})
        if next_node == NodeId.USER_FEEDBACK_PROCESSOR.value and interaction_count is not None and consume_interaction:
            session.update_global_state({"search_context.feedback_interaction_count": interaction_count + 1})

        exception_info = algorithm_output.get("exception_info")
        if exception_info is not None:
            session.update_global_state({"search_context.final_result.exception_info": exception_info})

        # 非成功更新报告路径（disabled / finish / error）不需要更新报告状态，直接按 next_node 路由。
        if "new_report" not in algorithm_output:
            return dict(next_node=next_node)

        current_final_result = session.get_global_state("search_context.final_result") or {}
        current_report_content = current_final_result.get("response_content", "") or ""
        new_report = algorithm_output["new_report"]
        session.update_global_state({"search_context.final_result.response_content": new_report})
        if "citation_messages" in algorithm_output:
            session.update_global_state(
                {"search_context.final_result.citation_messages": algorithm_output["citation_messages"]}
            )
        if "warning_info" in algorithm_output:
            session.update_global_state({"search_context.final_result.warning_info": algorithm_output["warning_info"]})
        feedback = algorithm_output["feedback"]
        updated_outline = build_current_outline_update(
            current_outline=session.get_global_state("search_context.current_outline"),
            action_result=algorithm_output,
        )
        if updated_outline is not None:
            session.update_global_state({"search_context.current_outline": updated_outline})

        # 记录每次局部改写的关键信息，便于问题排查和后续审计。
        history = session.get_global_state("search_context.rewrite_history") or []
        updated_history = build_rewrite_history_update(
            history=history,
            feedback=feedback,
            action_result=algorithm_output,
            current_report_content=current_report_content,
        )
        if updated_history is None:
            logger.info("[UserFeedbackProcessorNode] Rewrite completed, loop back for next interaction.")
            return dict(next_node=next_node)
        session.update_global_state({"search_context.rewrite_history": updated_history})

        if not algorithm_output.get("sync_only", False):
            add_debug_log_wrapper(session, NodeDebugData(
                NodeId.USER_FEEDBACK_PROCESSOR.value, 0, NodeType.MAIN.value,
                output_content=json.dumps(
                    {
                        "selected_text": feedback.get("selected_text"),
                        "rewritten_text": algorithm_output["rewritten_text"],
                        "rewritten_start_offset": algorithm_output["rewritten_start_offset"],
                        "rewritten_end_offset": algorithm_output["rewritten_end_offset"],
                    },
                    ensure_ascii=False,
                )
            ))

        logger.info("[UserFeedbackProcessorNode] Rewrite completed, loop back for next interaction.")
        return dict(next_node=next_node)


class VLMChartGeneratorNode(BaseNode):
    def __init__(self) -> None:
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info("[VLMChartGeneratorNode] Start VLMChartGeneratorNode.")

        # 获取vlm迭代生成图参数
        # 使用子报告生成模型处理文本类数据
        vlm_chart_generator_enable = session.get_global_state("config.vlm_chart_generator_enable")
        if not vlm_chart_generator_enable:
            return dict(vlm_chart_generator_enable=vlm_chart_generator_enable)

        llm_model_name = adapt_llm_model_name(session, NodeId.SUB_REPORTER.value)
        vlm_chart_generator_max_iterations = session.get_global_state("config.vlm_chart_generator_max_iterations")
        # 使用多模态模型处理图表类数据
        if vlm_chart_generator_max_iterations > 0:
            vlm_model_name = adapt_vlm_model_name(session, NodeId.VLM_CHART_GENERATOR.value)
        else:
            # 不使用迭代优化
            vlm_model_name = llm_model_name

        # 获取vlm输入数据
        current_report = session.get_global_state("search_context.current_report")
        report_content = getattr(current_report, "report_content", "") if current_report else ""
        all_classified_contents = getattr(current_report, "all_classified_contents", []) if current_report else []
        merged_trace_source_datas = getattr(current_report, "merged_trace_source_datas", []) if current_report else []

        visualization_enable = session.get_global_state("config.visualization_enable")
        return dict(
            llm_model_name=llm_model_name,
            vlm_chart_generator_enable=vlm_chart_generator_enable,
            vlm_model_name=vlm_model_name,
            vlm_chart_generator_max_iterations=vlm_chart_generator_max_iterations,
            report_content=report_content,
            all_classified_contents=all_classified_contents,
            trace_source_datas=merged_trace_source_datas,
            visualization_enable=visualization_enable,
        )

    async def _run_vlm_chart_generator_handle(self, inputs: Input) -> dict:
        logger.info("[VLMChartGeneratorNode] Run VLMChartGeneratorNode.")

        report_content = inputs.get("report_content", "")
        all_classified_contents = inputs.get("all_classified_contents", [])
        trace_source_datas = inputs.get("trace_source_datas", [])

        vlm_chart_generator = VLMChartGenerator(
            llm_model_name=inputs.get("llm_model_name", ""),
            vlm_model_name=inputs.get("vlm_model_name", ""),
            vlm_max_iterations=inputs.get("vlm_chart_generator_max_iterations", 1),
        )
        chart_messages, modified_report, new_source_trace_datas = await vlm_chart_generator.run(
            report_content=report_content,
            all_classified_contents=all_classified_contents,
            source_trace_datas=trace_source_datas,
        )

        return dict(
            chart_messages=chart_messages,
            modified_report=modified_report,
            new_source_trace_datas=new_source_trace_datas,
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:

        try:
            current_inputs = self._pre_handle(inputs, session, context)

            if not current_inputs.get("vlm_chart_generator_enable", False):
                vlm_chart_generator_output = {"skip_node": True}
                algorithm_output = {
                    "vlm_chart_generator_output": vlm_chart_generator_output,
                    "current_inputs": current_inputs,
                }
                result = self._post_handle(inputs, algorithm_output, session, context)
                return result

            if current_inputs.get("visualization_enable", True):
                error_msg = "vlm迭代生成图功能与图文并茂功能互斥，使用vlm迭代生成图需要关闭图文并茂功能，\
                    具体做法是将 `visualization_enable` 设为 `False`。"
                # fallback: 添加mermaid内容删除逻辑
                logger.error(f"[VLMChartGeneratorNode] {error_msg}")
                raise ValueError(error_msg)

            vlm_chart_generator_output = await self._run_vlm_chart_generator_handle(current_inputs)

        except CustomException as e:
            if LogManager.is_sensitive():
                logger.error(f"[VLMChartGeneratorNode] vlm_chart_generator failed.")
            else:
                logger.error(f"[VLMChartGeneratorNode] vlm_chart_generator failed: {str(e)}")
            vlm_chart_generator_output = {
                "error_msg": format_exception_info(StatusCode.CHART_GENERATION_ERROR, e),
            }
        except Exception as e:
            if LogManager.is_sensitive():
                logger.error(f"[VLMChartGeneratorNode] vlm_chart_generator failed.")
            else:
                logger.error(f"[VLMChartGeneratorNode] vlm_chart_generator failed: {str(e)}")
            vlm_chart_generator_output = {
                "error_msg": format_exception_info(StatusCode.CHART_GENERATION_ERROR, e),
            }

        algorithm_output = {
            "vlm_chart_generator_output": vlm_chart_generator_output,
            "current_inputs": current_inputs,
        }

        result = self._post_handle(inputs, algorithm_output, session, context)
        logger.info("[VLMChartGeneratorNode] VLMChartGeneratorNode completed.")
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> dict:

        vlm_chart_generator_output = algorithm_output.get("vlm_chart_generator_output", {})
        chart_messages = []
        modified_report = algorithm_output.get("current_inputs", {}).get("report_content", "")
        new_source_trace_datas = algorithm_output.get("current_inputs", {}).get("trace_source_datas", [])

        if vlm_chart_generator_output.get("skip_node", False):
            logger.info("[VLMChartGeneratorNode] vlm_chart_generator_enable is False, skip VLMChartGeneratorNode.")
        elif vlm_chart_generator_output.get("error_msg", ""):
            logger.warning(
                "[VLMChartGeneratorNode] vlm_chart_generator failed: %s",
                vlm_chart_generator_output.get("error_msg", ""),
            )
            session.update_global_state(
                {"search_context.final_result.warning_info": vlm_chart_generator_output.get("error_msg", "")}
            )
        else:
            # 排除开关和错误信息后，更新报告内容
            chart_messages = vlm_chart_generator_output.get("chart_messages", [])
            modified_report = vlm_chart_generator_output.get("modified_report", "")
            new_source_trace_datas = vlm_chart_generator_output.get("new_source_trace_datas", [])

            current_report = session.get_global_state("search_context.current_report")
            current_report.report_content = modified_report
            current_report.merged_trace_source_datas = new_source_trace_datas
            session.update_global_state({"search_context.current_report": current_report})
            session.update_global_state({"search_context.final_result.chart_messages": chart_messages})

        add_debug_log_wrapper(
            session,
            NodeDebugData(
                NodeId.VLM_CHART_GENERATOR.value,
                0,
                NodeType.MAIN.value,
                output_content=json.dumps(
                    {
                        "chart_messages": chart_messages,
                        "modified_report": modified_report,
                        "new_source_trace_datas": new_source_trace_datas,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        output_str = "*" if LogManager.is_sensitive() else vlm_chart_generator_output
        logger.debug("[VLMChartGeneratorNode] vlm_chart_generator_node result: \n%s", output_str)
        return dict(next_node=NodeId.SOURCE_TRACER.value)


class SearchStartNode(Start):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session.update_global_state(inputs or {})

        # The framework filters ``inputs`` against the Start component's
        # ``inputs_schema`` before invoking, so per-run fields like
        # ``agent_config``/``search_config`` (passed via Runner.run_workflow)
        # are not present here. They ARE available in the session global
        # state because the framework calls ``commit_user_inputs(inputs)``
        # before dispatching to the first node.
        origin_agent_config = session.get_global_state("agent_config") or {}
        if not isinstance(origin_agent_config, dict):
            origin_agent_config = {}
        search_workflow_config = session.get_global_state("search_config")
        if not isinstance(search_workflow_config, dict):
            search_workflow_config = Config().service_config.search_workflow.model_dump()
        workflow_name = session.get_global_state("workflow_name") or inputs.get("workflow_name", "")

        # state_creation passes log_dir only inside agent_config; mirror to top-level for tools / LLM logs.
        merged_log_dir = session.get_global_state("log_dir")
        if not merged_log_dir:
            merged_log_dir = origin_agent_config.get("log_dir")
        if merged_log_dir:
            session.update_global_state({"log_dir": merged_log_dir})

        logger.info(
            "[SearchStartNode] resolved workflow_name=%s, agent_config=%s",
            workflow_name,
            "***" if LogManager.is_sensitive() else origin_agent_config,
        )
        llm_config = origin_agent_config.get("llm_config", {}).get("general", {})
        retrieval_settings = origin_agent_config.get("retrieval_settings", {})
        if workflow_name == "init_state_workflow":
            workflow_config = search_workflow_config["init_state_agent"]
            merged_llm_config = {**(llm_config or {})}
            merged_llm_config.setdefault("timeout", 600)
            merged_llm_config.setdefault("max_tries", 10)
            merged_llm_config.setdefault("append_think_tags_to_messages", False)
            workflow_config["llm_config"]["general"] = merged_llm_config
        elif workflow_name == "find_action_workflow":
            workflow_config = search_workflow_config["find_action_agent"]
            merged_llm_config = {**(llm_config or {})}
            merged_llm_config.setdefault("timeout", 600)
            merged_llm_config.setdefault("max_tries", 10)
            merged_llm_config.setdefault("append_think_tags_to_messages", False)
            workflow_config["llm_config"]["general"] = merged_llm_config
        elif workflow_name == "state_creation_workflow":
            workflow_config = search_workflow_config["state_creation_agent"]
            workflow_config["validator_agent"] = copy.deepcopy(
                workflow_config.get("validator_agent", {})
            )
            workflow_config["validator_agent"]["llm_config"]["general"] = llm_config or {}
            merged_llm_config = {**(llm_config or {})}
            merged_llm_config.setdefault("timeout", 1200)
            merged_llm_config.setdefault("max_tries", 20)
            merged_llm_config.setdefault("append_think_tags_to_messages", True)
            workflow_config["llm_config"]["general"] = merged_llm_config
            old_retrieval_settings = workflow_config.get("retrieval_settings", {}) or {}
            workflow_config["retrieval_settings"] = {
                **old_retrieval_settings,
                **(retrieval_settings or {}),
            }
            workflow_config["log_dir"] = origin_agent_config.get("log_dir", "")
            workflow_config["fail_count"] = origin_agent_config.get("fail_count", 0)
        else:
            raise CustomValueException(
                StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.code,
                StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.errmsg.format(config=f"workflow name is {workflow_name}"),
            )
        session.update_global_state({"config": workflow_config})


class SearchEndNode(End):
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        logger.info("[SearchEndNode] Start SearchEndNode.")

        logger.info(f"[SearchEndNode] inputs: {inputs}")

        def _cache_final_result(final_result: dict) -> None:
            inner_session = getattr(session, "_inner", None)
            if inner_session is None:
                return
            state = inner_session.state()
            if hasattr(state, "update_and_commit_workflow_state"):
                state.update_and_commit_workflow_state({"workflow_final_result": final_result})

        workflow_name = session.get_global_state("workflow_name")
        total_input_tokens = session.get_global_state("total_input_tokens") or 0
        total_output_tokens = session.get_global_state("total_output_tokens") or 0
        if "init_state" in workflow_name:
            state = session.get_global_state("init_state")
            payload = {
                "init_state": state,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
            }
            final_result = dict(final_result=payload)
            _cache_final_result(final_result)
            logger.info("[SearchEndNode] End SearchEndNode.")
            return final_result
        if "find_action" in workflow_name:
            actions = session.get_global_state("actions")
            payload = {
                "actions": actions,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
            }
            final_result = dict(final_result=payload)
            _cache_final_result(final_result)
            logger.info("[SearchEndNode] End SearchEndNode.")
            return final_result
        if "state_creation" in workflow_name:
            result = session.get_global_state("result") or {}
            logger.info(f"[SearchEndNode] state creation result: {result}")
            config = session.get_global_state("config") or {}
            payload = {
                "result": result,
                "config": anonymize_config_for_logging(config),
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
            }
            final_result = dict(final_result=payload)
            _cache_final_result(final_result)
            logger.info("[SearchEndNode] End SearchEndNode.")
            return final_result
        logger.info("[SearchEndNode] End SearchEndNode.")
        return dict(final_result={})


class InitializeStateNode(BaseNode):
    """
    初始化状态节点
    """

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info("[InitializeStateNode] Start InitializeStateNode.")
        query = session.get_global_state("query") or {}
        config = session.get_global_state("config") or {}
        raw_llm = config.get("llm_config", None) if config else None
        llm_config = _normalize_workflow_llm_config(raw_llm)
        max_tries = (llm_config.get("general") or {}).get("max_tries", 4)
        total_input_tokens = session.get_global_state("total_input_tokens") or 0
        total_output_tokens = session.get_global_state("total_output_tokens") or 0
        if not query:
            raise CustomJiuWenBaseException(
                StatusCode.PARAM_CHECK_ERROR_STATE_QUERY_REQUIRED.code,
                StatusCode.PARAM_CHECK_ERROR_STATE_QUERY_REQUIRED.errmsg,
            )

        logger.info(
            "[InitializeStateNode] received query: %s",
            "***" if LogManager.is_sensitive() else query,
        )

        return dict(
            query=query,
            config=config,
            llm_config=llm_config,
            max_tries=max_tries,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_inputs = self._pre_handle(inputs, session, context)
        query = current_inputs.get("query")
        llm_config = current_inputs.get("llm_config")
        total_input_tokens = current_inputs.get("total_input_tokens", 0)
        total_output_tokens = current_inputs.get("total_output_tokens", 0)

        try:
            algorithm_output = await run_initialize_state(
                llm_config["general"],
                query,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
            )
        except Exception as e:
            logger.error(
                "[InitializeStateNode] run_initialize_state failed (no fallback, flow must stop): %s",
                "*" if LogManager.is_sensitive() else e,
                exc_info=not LogManager.is_sensitive(),
            )
            raise

        result = self._post_handle(inputs, algorithm_output, session, context)
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        init_state: State = algorithm_output.get("init_state")
        total_input_tokens = algorithm_output.get("total_input_tokens", 0)
        total_output_tokens = algorithm_output.get("total_output_tokens", 0)
        log_dir = session.get_global_state("log_dir")

        with open(
            os.path.join(log_dir, "initial_state.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                to_dict_safe(init_state) | {"messages": algorithm_output.get("messages")},
                f,
                indent=2,
                ensure_ascii=False,
            )

        session.update_global_state(
            {
                "init_state": init_state,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
            }
        )

        emit_state_created(
            source="initialize_state_node",
            origin="initial",
            states=[to_dict_safe(init_state)],
            runtime=session,
            action_id=None,
        )

        logger.info("[InitializeStateNode] End InitializeStateNode.")
        return None


class FindActionSpaceNode(BaseNode):
    """
    查找动作空间节点
    """

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info("[FindActionSpaceNode] Start FindActionSpaceNode.")
        query = session.get_global_state("query") or {}
        state = session.get_global_state("state") or {}
        result = session.get_global_state("result") or None
        config = session.get_global_state("config") or {}
        raw_llm = config.get("llm_config", None) if config else None
        llm_config = _normalize_workflow_llm_config(raw_llm)
        log_dir = session.get_global_state("log_dir")
        total_input_tokens = session.get_global_state("total_input_tokens") or 0
        total_output_tokens = session.get_global_state("total_output_tokens") or 0

        if result:
            result.messages = [result.messages[-1]]

        logger.info(
            "[FindActionSpaceNode] received query: %s",
            "***" if LogManager.is_sensitive() else query,
        )
        logger.info(
            "[FindActionSpaceNode] received state: %s",
            "***" if LogManager.is_sensitive() else state,
        )
        logger.info(
            "[FindActionSpaceNode] received messages: %s",
            "***" if LogManager.is_sensitive() else result,
        )

        return dict(
            query=query,
            state=state,
            result=result,
            config=config,
            llm_config=llm_config,
            log_dir=log_dir,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_inputs = self._pre_handle(inputs, session, context)
        query = current_inputs.get("query")
        state = current_inputs.get("state")
        result = current_inputs.get("result")
        config = current_inputs.get("config")
        llm_config = current_inputs.get("llm_config")
        max_tries = (llm_config.get("general") or {}).get("max_tries", 4) if llm_config else 4
        total_input_tokens = current_inputs.get("total_input_tokens", 0)
        total_output_tokens = current_inputs.get("total_output_tokens", 0)
        try:
            algorithm_output = await run_find_action_space(
                llm_config["general"],
                config,
                query,
                state,
                result,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                max_tries=max_tries,
            )
            if algorithm_output is None:
                logger.error("[FindActionSpaceNode] Error: Failed to find action.")
                algorithm_output = dict(
                    actions=[],
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    success=False,
                    error="Failed to find action after all retries",
                )
        except Exception as e:
            logger.error(
                "[FindActionSpaceNode] exception (fallback to end): %s",
                "*" if LogManager.is_sensitive() else e,
                exc_info=not LogManager.is_sensitive(),
            )
            algorithm_output = dict(
                actions=[],
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                success=False,
                error=f"FindActionSpaceNode exception: {e!s}",
            )
        return self._post_handle(inputs, algorithm_output, session, context)

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        query = session.get_global_state("query")
        state = session.get_global_state("state")
        log_dir = session.get_global_state("log_dir")
        if algorithm_output.get("success"):
            actions: list[Action] = algorithm_output.get("actions", [])
            total_input_tokens = algorithm_output.get("total_input_tokens", 0)
            total_output_tokens = algorithm_output.get("total_output_tokens", 0)

            session.update_global_state(
                {
                    "actions": actions,
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                }
            )

            sid = _state_id_for_telemetry(state)
            sensitive = LogManager.is_sensitive()
            action_rows = []
            for action in actions:
                aid = str(action.state.id) if action.state is not None else sid
                action_rows.append(
                    {
                        "action_id": action.id,
                        "state_id": aid,
                        "proposal_direction": (
                            "***" if sensitive else action.proposal.direction
                        ),
                        "score": action.proposal.score,
                    }
                )
            emit(
                "action_proposals_created",
                {
                    "success": True,
                    "state_id": sid,
                    "num_actions": len(actions),
                    "actions": action_rows,
                    **runtime_correlation_from(session),
                },
                source="find_action_space_node",
                action_id=None,
            )

            id_ = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S%f")[:-3]
            action_file = os.path.join(log_dir, "Action", f"action_{id_}_{uuid.uuid4().hex}.json")
            payload = {
                "question": query,
                "state": to_dict_safe(state),
                "proposals": [action.proposal.direction for action in actions],
                "scores": [action.proposal.score for action in actions],
                "action_ids": [action.id for action in actions],
                "message": actions[0].messages if actions else [],
            }

            safe_payload = to_json_safe(payload)

            with open(action_file, "w", encoding="utf-8") as f:
                json.dump(
                    safe_payload,
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            creation_messages = algorithm_output.get("messages")
            if creation_messages:
                emit_messages_updated(
                    source="find_action_space_node",
                    messages=creation_messages,
                    runtime=session,
                    action_id=None,
                    extra={
                        "phase": "action_creation",
                        "success": True,
                        "num_actions": len(actions),
                    },
                )
            logger.info("[FindActionSpaceNode] End FindActionSpaceNode.")
            return actions
        else:
            error = algorithm_output.get("error", "Unknown error")
            emit(
                "action_proposals_created",
                {
                    "success": False,
                    "state_id": _state_id_for_telemetry(state),
                    "num_actions": 0,
                    "actions": [],
                    "error": "*" if LogManager.is_sensitive() else error,
                    **runtime_correlation_from(session),
                },
                source="find_action_space_node",
                action_id=None,
            )
            id_ = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S%f")[:-3]
            action_file = os.path.join(log_dir, "Action", f"action_{id_}_{uuid.uuid4().hex}.json")
            payload = {
                "question": query,
                "state": to_dict_safe(state),
                "proposals": [f"Error: {error}"],
                "scores": [0],
            }

            safe_payload = to_json_safe(payload)

            with open(action_file, "w", encoding="utf-8") as f:
                json.dump(
                    safe_payload,
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            fail_messages = algorithm_output.get("messages")
            if fail_messages:
                emit_messages_updated(
                    source="find_action_space_node",
                    messages=fail_messages,
                    runtime=session,
                    action_id=None,
                    extra={
                        "phase": "action_creation",
                        "success": False,
                        "error": error,
                    },
                )
            logger.error("[FindActionSpaceNode] End FindActionSpaceNode with error.")
            return None


class RunActionNode(BaseNode):
    """
    执行动作节点

    Outcomes from ``run_run_action`` (single state_creation LLM step):
    - **Valid completion**: Parsed ``<state>`` (including ``new_states: []``) or ``<answer>`` →
      ``success=True``; routed to validation or END. Empty state is intentional signal to the
      orchestrator, not an error; the workflow does not re-run state_creation for it.
    - **Context limit (recoverable)**: ``try_again=True`` → same node re-invoked with trimmed messages
      (see ``context_limit_reached_strategy``).
    - **Tool calls**: The LLM response carries provider-native ``tool_calls`` (ids, names, arguments).
      After successful parse, ``run_action`` passes a normalized list as runtime
      ``pending_tool_calls`` (``{name, arguments, tool_call_id}`` per item) for ``ToolNode`` to
      execute. ``pending_tool_calls`` is the work queue; it is cleared after tools run.
    - **Hard failure**: LLM/parse failures → ``success=False``, END with ``termination`` saved;
      no outer workflow retry of state_creation for that case.
    """

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info("[RunActionNode] Start RunActionNode.")
        action_start_time = session.get_global_state("action_start_time") or time.time()
        config = session.get_global_state("config") or {}
        raw_llm = config.get("llm_config", None) if config else None
        llm_config = _normalize_workflow_llm_config(raw_llm)

        validator_config = config.get("validator_agent", {})
        validate_new_states = validator_config.get("validate_new_states", True)
        validate_answer: bool = validator_config.get("validate_answer", True)

        retrieval_settings = session.get_global_state("retrieval_settings") or config.get("retrieval_settings", {})
        config["retrieval_settings"] = retrieval_settings
        context_limit_reached_strategy = config.get("context_limit_reached_strategy", "fail")
        new_found_evidence_ids = session.get_global_state("new_found_evidence_ids") or []
        retrieval_tool_only = session.get_global_state("retrieval_tool_only") or False
        llm_budget = session.get_global_state("max_llm_calls_per_run")
        if llm_budget is None:
            llm_budget = config.get("max_llm_calls_per_run", 100)
        total_input_tokens = session.get_global_state("total_input_tokens") or 0
        total_output_tokens = session.get_global_state("total_output_tokens") or 0

        raw_messages = session.get_global_state("messages")
        messages = list(raw_messages) if isinstance(raw_messages, list) else []

        action = session.get_global_state("action")
        query = action["question"] if action else ""
        state = action["state"] if action else None

        logger.info(
            "[RunActionNode] received action: %s",
            "***" if LogManager.is_sensitive() else action,
        )

        return dict(
            action_start_time=action_start_time,
            config=config,
            llm_config=llm_config,
            validate_new_states=validate_new_states,
            validate_answer=validate_answer,
            retrieval_settings=retrieval_settings,
            context_limit_reached_strategy=context_limit_reached_strategy,
            new_found_evidence_ids=new_found_evidence_ids,
            retrieval_tool_only=retrieval_tool_only,
            llm_budget=llm_budget,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            messages=messages,
            action=action,
            query=query,
            state=state,
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_inputs = self._pre_handle(inputs, session, context)
        action_start_time = current_inputs.get("action_start_time")
        config = current_inputs.get("config")
        config["log_dir"] = session.get_global_state("log_dir")
        llm_config = current_inputs.get("llm_config")
        validate_new_states = current_inputs.get("validate_new_states")
        validate_answer = current_inputs.get("validate_answer")
        retrieval_settings = current_inputs.get("retrieval_settings")
        context_limit_reached_strategy = current_inputs.get("context_limit_reached_strategy", "fail")
        new_found_evidence_ids = current_inputs.get("new_found_evidence_ids")
        retrieval_tool_only = current_inputs.get("retrieval_tool_only")
        llm_budget = current_inputs.get("llm_budget")
        total_input_tokens = current_inputs.get("total_input_tokens", 0)
        total_output_tokens = current_inputs.get("total_output_tokens", 0)
        messages = current_inputs.get("messages")
        action = current_inputs.get("action")
        query = current_inputs.get("query")
        state = current_inputs.get("state")

        session.update_global_state({"action_start_time": action_start_time})
        session.update_global_state({"config": config})

        if llm_budget <= 0:
            error_message = "Error: Exceeded number of llm calls"
            logger.warning(
                "[RunActionNode] max_llm_calls_per_run exhausted — terminating " "(remaining=%s, configured limit=%s)",
                llm_budget,
                config.get("max_llm_calls_per_run"),
            )
            _save_result(
                config,
                action,
                {
                    "question": query,
                    "messages": messages,
                    "termination": error_message,
                    "time_taken": time.time() - action_start_time,
                },
                time.time() - action_start_time,
            )
            messages.append({"role": "user", "content": error_message})
            algorithm_output = dict(next_node=NodeId.END_NODE.value, messages=messages, error=error_message)
            return self._post_handle(inputs, algorithm_output, session, context)
        elif llm_budget <= 3:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You are running low on remaining LLM calls. "
                        "In your next output you MUST provide either a final <answer> or a <state> patch. "
                        "Do NOT output an <answer> patch unless you have gather enough information to satify all the "
                        "constraints of the variable."
                        "Do NOT make any more tool calls. "
                        "If you decide on outputing a state patch, first describe any promising avenues "
                        "you would still like to explore and why. "
                        "Follow up with the <state> patch, where for each variable, add any relevant findings "
                        "you have made so far to its discovered_clues list."
                    ),
                }
            )
        llm_budget -= 1
        session.update_global_state({"max_llm_calls_per_run": llm_budget})
        session.update_global_state({"tool_call": {}, "pending_tool_calls": []})

        try:
            algorithm_output = await run_action(
                RunActionConfig(
                    llm_config=llm_config["general"],
                    config=config,
                    action=action,
                    state=state,
                    query=query,
                    messages=messages,
                    new_found_evidence_ids=new_found_evidence_ids,
                    validate_new_states=validate_new_states,
                    validate_answer=validate_answer,
                    action_start_time=action_start_time,
                    retrieval_tool_only=retrieval_tool_only,
                    retrieval_settings=retrieval_settings,
                    context_limit_reached_strategy=context_limit_reached_strategy,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                )
            )
            if algorithm_output.get("success"):
                logger.info("[RunActionNode] run_run_action success")
                return self._post_handle(inputs, algorithm_output, session, context)
            if algorithm_output.get("try_again"):
                logger.warning(
                    "[RunActionNode] context limit hit – retrying | %s",
                    format_action_for_log(action),
                )
                # Apply whichever state updates the strategy produced.
                # reduced_retrieval_request: carries updated config + retrieval_settings.
                if "config" in algorithm_output:
                    session.update_global_state(
                        {
                            "config": algorithm_output["config"],
                            "retrieval_settings": algorithm_output["retrieval_settings"],
                            "new_found_evidence_ids": [],
                            "messages": [],
                        }
                    )
                # delete_tool_responses / delete_tool_input_and_responses: carries cleaned messages.
                if "messages" in algorithm_output and "config" not in algorithm_output:
                    session.update_global_state({"messages": algorithm_output["messages"]})
                emit_messages_updated(
                    source="run_action_node_context_retry",
                    messages=session.get_global_state("messages"),
                    runtime=session,
                    extra={"kind": "context_limit"},
                )
                return self._post_handle(
                    inputs,
                    dict(next_node=NodeId.RUN_ACTION.value, success=True),
                    session,
                    context,
                )
            err_msg = algorithm_output.get("error", "")
            messages_for_save = list(algorithm_output.get("messages", []))
            messages_for_save.append({"role": "user", "content": f"Error: {err_msg}"})
            _save_result(
                config,
                action,
                {
                    "question": query,
                    "messages": messages_for_save,
                    "termination": f"Error: {err_msg}",
                    "time_taken": time.time() - action_start_time,
                },
                time.time() - action_start_time,
            )
            return self._post_handle(inputs, algorithm_output, session, context)
        except Exception as e:
            logger.error(
                "[RunActionNode] exception (fallback to end): %s",
                "*" if LogManager.is_sensitive() else e,
                exc_info=not LogManager.is_sensitive(),
            )
            algorithm_output = dict(
                success=False,
                next_node=NodeId.END_NODE.value,
                error=f"RunActionNode exception: {e!s}",
            )
            return self._post_handle(inputs, algorithm_output, session, context)

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        if not algorithm_output.get("success", False):
            next_node = algorithm_output.get("next_node", NodeId.END_NODE.value)
            return dict(next_node=next_node)

        if "next_node" in algorithm_output:
            return dict(next_node=algorithm_output.get("next_node"))

        mode = algorithm_output.get("mode")
        data = algorithm_output.get("data")
        config = algorithm_output.get("config")
        total_input_tokens = algorithm_output.get("total_input_tokens", 0)
        total_output_tokens = algorithm_output.get("total_output_tokens", 0)
        validate_new_states = algorithm_output.get("validate_new_states")
        validate_answer = algorithm_output.get("validate_answer")

        session.update_global_state({"config": config})
        session.update_global_state(
            {
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
            }
        )

        if isinstance(data, dict):
            _log_data = (
                "***"
                if LogManager.is_sensitive()
                else {
                    "tool_calls": data.get("tool_calls", []),
                    "new_states": data.get("new_states", {}),
                    "answer": data.get("answer", {}),
                }
            )
            logger.info(
                "[RunActionNode] parsed LLM result JSON! %s %s",
                mode,
                _log_data,
            )
        else:
            logger.info(
                "[RunActionNode] parsed LLM result JSON! %s %s",
                mode,
                "***" if LogManager.is_sensitive() else data,
            )

        if isinstance(data, Result):
            session.update_global_state({"result": data})
            session.update_global_state({"messages": data.messages})
            if data.new_states:
                emit_state_created(
                    source="run_action_node",
                    origin="action_patch",
                    states=[to_dict_safe(ns) for ns in data.new_states],
                    runtime=session,
                    extra={"run_action_mode": mode},
                )
            emit_messages_updated(
                source="run_action_node",
                messages=data.messages,
                runtime=session,
                extra={"mode": mode, "phase": "result"},
            )
            if "answer" in mode:
                if validate_answer:
                    return dict(next_node=NodeId.VALIDATE_NEW_STATE.value)
                else:
                    return dict(next_node=NodeId.END_NODE.value)
            if "state" in mode:
                if validate_new_states:
                    return dict(next_node=NodeId.VALIDATE_NEW_STATE.value)
                else:
                    return dict(next_node=NodeId.END_NODE.value)

        messages = data.get("messages", [])
        session.update_global_state({"messages": messages})
        emit_messages_updated(
            source="run_action_node",
            messages=messages,
            runtime=session,
            extra={"mode": mode, "phase": "llm_turn"},
        )

        if mode is None:
            return dict(next_node=NodeId.RUN_ACTION.value)
        elif isinstance(data, dict):
            if "tool_calls" in data:
                tcalls = data.get("tool_calls") or []
                session.update_global_state({"pending_tool_calls": tcalls})
                return dict(next_node=NodeId.TOOL.value)
            else:
                return dict(next_node=NodeId.RUN_ACTION.value)
        else:
            return dict(next_node=NodeId.RUN_ACTION.value)


class ToolNode(BaseNode):
    """
    工具调用节点
    """

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info("[ToolNode] Start ToolNode.")
        config = session.get_global_state("config") or {}
        retrieval_settings = session.get_global_state("retrieval_settings") or config.get("retrieval_settings", {})
        # ``tool_map`` is read from the per-run ``tool_context`` ContextVar instead
        # of session global state. Tool clients (e.g. ``MilvusClient``) hold
        # ``_thread.RLock`` objects, and the framework deep-copies session state
        # on every checkpoint (see ``InMemoryStore.save``) and ``asdict``-s log
        # event metadata, both of which would crash on the lock.
        tool_map = tool_context.get() or {}
        new_found_evidence_ids = session.get_global_state("new_found_evidence_ids") or []
        action = session.get_global_state("action") or {}

        pending = session.get_global_state("pending_tool_calls") or []
        tool_calls_queue: list = list(pending) if pending else []
        if not tool_calls_queue:
            legacy = session.get_global_state("tool_call") or {}
            if legacy:
                tool_calls_queue = [legacy]

        messages = session.get_global_state("messages") or []

        logger.info(
            "[ToolNode] executing %d tool call(s): %s",
            len(tool_calls_queue),
            "***" if LogManager.is_sensitive() else tool_calls_queue,
        )

        return dict(
            config=config,
            retrieval_settings=retrieval_settings,
            tool_map=tool_map,
            new_found_evidence_ids=new_found_evidence_ids,
            action=action,
            tool_calls_queue=tool_calls_queue,
            messages=messages,
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_inputs = self._pre_handle(inputs, session, context)
        config = current_inputs.get("config")
        retrieval_settings = current_inputs.get("retrieval_settings")
        tool_map = current_inputs.get("tool_map")
        new_found_evidence_ids = list(current_inputs.get("new_found_evidence_ids") or [])
        action = current_inputs.get("action")
        tool_calls_queue = current_inputs.get("tool_calls_queue") or []
        messages = list(current_inputs.get("messages") or [])

        if not tool_calls_queue:
            logger.warning("[ToolNode] no tool calls in queue; returning to RunAction")
            session.update_global_state(
                {
                    "messages": messages,
                    "tool_call": {},
                    "tool_call_id": "",
                    "pending_tool_calls": [],
                }
            )
            return dict(next_node=NodeId.RUN_ACTION.value)

        for tc in tool_calls_queue:
            tool_name = tc.get("name") or ""
            tool_args = dict(tc.get("arguments") or {})
            tool_call_id = tc.get("tool_call_id") or ""
            try:
                tool_result, new_found_evidence_ids = await execute_tool(
                    ExecuteToolConfig(
                        tool_map=tool_map,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        config=config,
                        retrieval_settings=retrieval_settings,
                        action=action,
                        new_found_evidence_ids=new_found_evidence_ids,
                    )
                )
                if "retrieve" in tool_name.lower():
                    logger.info(
                        "[ToolNode] retrieved evidence ids: %s",
                        "***" if LogManager.is_sensitive() else new_found_evidence_ids,
                    )
                logger.info(
                    "[ToolNode] tool result (%s): %s",
                    tool_name,
                    "***" if LogManager.is_sensitive() else tool_result,
                )
                content = format_tool_result_for_message(tool_result)
            except CustomValueException as e:
                error_msg = str(e)
                if "Available tools:" not in error_msg:
                    available_tools = list(tool_map.keys())
                    friendly_tool_names = []
                    if "web_search" in available_tools:
                        friendly_tool_names.append("search")
                    if "web_fetch" in available_tools:
                        friendly_tool_names.append("fetch")
                    if "retrieve" in available_tools:
                        friendly_tool_names.append("retrieve")
                    if friendly_tool_names:
                        error_msg += f" Available tools: {', '.join(friendly_tool_names)}."
                logger.warning(
                    "[ToolNode] %s",
                    "*" if LogManager.is_sensitive() else error_msg,
                )
                content = error_msg
            except Exception as e:
                error_msg = f"Tool execution error: {str(e)}"
                logger.error(
                    "[ToolNode] %s (fallback: continue to RunAction with error in messages)",
                    "*" if LogManager.is_sensitive() else error_msg,
                    exc_info=not LogManager.is_sensitive(),
                )
                content = error_msg

            messages.append(
                {
                    "role": "tool",
                    "content": content,
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                }
            )

        algorithm_output = dict(
            messages=messages,
            new_found_evidence_ids=new_found_evidence_ids,
        )
        return self._post_handle(inputs, algorithm_output, session, context)

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        messages = algorithm_output.get("messages", [])
        new_found_evidence_ids = algorithm_output.get("new_found_evidence_ids", [])

        session.update_global_state(
            {
                "messages": messages,
                "tool_call": {},
                "tool_call_id": "",
                "pending_tool_calls": [],
            }
        )
        if new_found_evidence_ids:
            session.update_global_state({"new_found_evidence_ids": new_found_evidence_ids})

        emit_messages_updated(
            source="tool_node",
            messages=messages,
            runtime=session,
            extra={"tools_executed": True},
        )

        logger.info("[ToolNode] End ToolNode.")
        return dict(next_node=NodeId.RUN_ACTION.value)


class ValidateNewStateNode(BaseNode):
    """
    验证新状态节点
    """

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info("[ValidateNewStateNode] Start ValidateNewStateNode.")
        config = session.get_global_state("config") or {}
        validator_config = config.get("validator_agent", {})
        total_input_tokens = session.get_global_state("total_input_tokens") or 0
        total_output_tokens = session.get_global_state("total_output_tokens") or 0
        result = session.get_global_state("result") or {}
        new_states = result.new_states if isinstance(result, Result) else result.get("new_states", [])
        action = session.get_global_state("action") or {}
        query = action.get("question", "") if action else ""
        new_found_evidence_ids = session.get_global_state("new_found_evidence_ids") or []
        answer = result.found_answer if isinstance(result, Result) else result.get("found_answer", None)
        messages = session.get_global_state("messages") or []
        mode = "answer" if answer is not None else "state"

        return dict(
            config=config,
            validator_config=validator_config,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            new_states=new_states,
            action=action,
            query=query,
            new_found_evidence_ids=new_found_evidence_ids,
            answer=answer,
            messages=messages,
            mode=mode,
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_inputs = self._pre_handle(inputs, session, context)
        config = current_inputs.get("config", {})
        validator_config = current_inputs.get("validator_config")
        validator_model_name = validator_config.get("llm_config", {}).get("general", {}).get("model_name", None)
        total_input_tokens = current_inputs.get("total_input_tokens", 0)
        total_output_tokens = current_inputs.get("total_output_tokens", 0)
        new_states = current_inputs.get("new_states", [])
        query = current_inputs.get("query")
        new_found_evidence_ids = current_inputs.get("new_found_evidence_ids", [])
        answer = current_inputs.get("answer")
        messages = current_inputs.get("messages", [])
        mode = current_inputs.get("mode")
        action = current_inputs.get("action")

        passed_merged_states: List[State] = []
        all_merged_states: List[State] = []

        try:
            validation_results = await run_validations(new_states, validator_model_name, query)
            for (
                new_state,
                verify_results,
                input_tokens,
                output_tokens,
            ) in validation_results:
                logger.info(
                    "[ValidateNewStateNode] validating new state: %s",
                    "***" if LogManager.is_sensitive() else new_state,
                )
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens

                answer_validation = ValidationResult.passed
                state_verification = ValidationResult.passed

                if verify_results:
                    logger.info(
                        "[ValidateNewStateNode] verify results: %s",
                        "***" if LogManager.is_sensitive() else verify_results,
                    )
                    labels = [(r.candidate_verified_clues.overall or "") for r in verify_results]
                    new_state.verify_result = verify_results
                    if mode == "answer":
                        if ValidationResult.failed in labels:
                            answer_validation = ValidationResult.failed
                        elif ValidationResult.pending in labels:
                            answer_validation = ValidationResult.pending

                        if answer_validation == ValidationResult.passed:
                            result_to_save = Result(
                                messages=messages,
                                new_states=[new_state],
                                found_answer=answer,
                                retrieved_evidence_ids=new_found_evidence_ids
                                + action.get("state", {}).get("retrieved_evidence_ids", []),
                                previous_action_id=action.get("id", ""),
                            )
                            time_taken = time.time() - session.get_global_state("action_start_time")
                            config = _save_result(config, action, result_to_save, time_taken)
                            algorithm_output = dict(
                                next_node=NodeId.END_NODE.value,
                                result_to_save=result_to_save,
                                config=config,
                                total_input_tokens=total_input_tokens,
                                total_output_tokens=total_output_tokens,
                                success=True,
                            )
                            return self._post_handle(inputs, algorithm_output, session, context)
                        else:
                            error_message = (
                                "Error: After verifying the answer, the answer is still not valid. Verified results: "
                                + new_state.str_to_verify_result()
                            )
                            logger.info(f"[ValidateNewStateNode] Answer patch error: {error_message}")
                            messages.append({"role": "user", "content": error_message})
                            algorithm_output = dict(
                                next_node=NodeId.RUN_ACTION.value,
                                messages=messages,
                                total_input_tokens=total_input_tokens,
                                total_output_tokens=total_output_tokens,
                                success=False,
                            )
                            return self._post_handle(inputs, algorithm_output, session, context)

                    else:
                        if ValidationResult.failed in labels:
                            answer_validation = ValidationResult.failed

                        if state_verification == ValidationResult.passed:
                            passed_merged_states.append(new_state)

                        all_merged_states.append(new_state)
            if passed_merged_states or len(all_merged_states) == 0:
                logger.info(
                    f"[ValidateNewStateNode] Passed merged states: %s",
                    "***" if LogManager.is_sensitive() else passed_merged_states,
                )
                result_to_save = Result(
                    messages=messages,
                    new_states=passed_merged_states,
                    found_answer=None,
                    retrieved_evidence_ids=new_found_evidence_ids
                    + action.get("state", {}).get("retrieved_evidence_ids", []),
                    previous_action_id=action.get("id", ""),
                )
                time_taken = time.time() - session.get_global_state("action_start_time")
                updated_config = _save_result(config, action, result_to_save, time_taken)
                algorithm_output = dict(
                    next_node=NodeId.END_NODE.value,
                    result_to_save=result_to_save,
                    config=updated_config,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    success=True,
                )
            else:
                error_message = (
                    "Error: After verifying the states, the states are still not valid. Verified results: "
                    + "\n".join([state.str_to_verify_result() for state in all_merged_states])
                )
                logger.info(
                    "[ValidateNewStateNode] New state patch error: %s",
                    "***" if LogManager.is_sensitive() else error_message,
                )
                messages.append({"role": "user", "content": error_message})
                algorithm_output = dict(
                    next_node=NodeId.RUN_ACTION.value,
                    messages=messages,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    success=False,
                )

            result = self._post_handle(inputs, algorithm_output, session, context)
            return result
        except Exception as e:
            logger.error(
                "[ValidateNewStateNode] exception (fallback to end): %s",
                "*" if LogManager.is_sensitive() else e,
                exc_info=not LogManager.is_sensitive(),
            )
            algorithm_output = dict(
                next_node=NodeId.END_NODE.value,
                success=False,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                error=f"ValidateNewStateNode exception: {e!s}",
            )
            return self._post_handle(inputs, algorithm_output, session, context)

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        next_node = algorithm_output.get("next_node", NodeId.END_NODE.value)
        total_input_tokens = algorithm_output.get("total_input_tokens", 0)
        total_output_tokens = algorithm_output.get("total_output_tokens", 0)

        session.update_global_state(
            {
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
            }
        )

        if algorithm_output.get("success"):
            result_to_save = algorithm_output.get("result_to_save")
            config = algorithm_output.get("config")
            if result_to_save:
                session.update_global_state({"result": result_to_save, "config": config})
                logger.info(
                    "[ValidateNewStateNode] result to save: %s",
                    "***" if LogManager.is_sensitive() else result_to_save,
                )
        else:
            messages = algorithm_output.get("messages", [])
            session.update_global_state({"result": {}, "messages": messages})
            err_content = messages[-1].get("content", "") if messages else ""
            logger.info(
                "[ValidateNewStateNode] error message: %s",
                "***" if LogManager.is_sensitive() else err_content,
            )
        logger.info("[ValidateNewStateNode] End ValidateNewStateNode.")
        return dict(next_node=next_node)
