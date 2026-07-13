# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import base64
import copy
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any, AsyncGenerator, ClassVar, Optional

from openjiuwen.core.application.workflow_agent.workflow_agent import (
    WorkflowAgent as LegacyWorkflowAgent,
)
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.session import workflow_session_vars
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.constants import WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY
from openjiuwen.core.session.stream.base import CustomSchema, OutputSchema
from openjiuwen.core.single_agent.legacy.agent import WorkflowFactory
from openjiuwen.core.single_agent.legacy.config import WorkflowAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.workflow import Workflow, WorkflowCard, WorkflowOutput
from pydantic import ValidationError

from openjiuwen_deepsearch.algorithm.prompts.template import get_prompt_section
from openjiuwen_deepsearch.algorithm.report_template.template_generator import TemplateGenerator
from openjiuwen_deepsearch.algorithm.search_agent.action_pool import ActionPool
from openjiuwen_deepsearch.algorithm.search_agent.deepsearch_agent import (
    parse_and_validate_find_action_result,
    parse_and_validate_init_state_result,
    parse_and_validate_state_creation_result,
)
from openjiuwen_deepsearch.algorithm.search_nodes.utils import anonymize_config_for_logging
from openjiuwen_deepsearch.algorithm.search_nodes.llm_utils import _run_llm_via_ainvoke
from openjiuwen_deepsearch.algorithm.search_nodes.run_action import (
    _parse_one_native_tool_call,
    get_tool_definitions,
)
from openjiuwen_deepsearch.algorithm.search_nodes.tool_node import (
    ExecuteToolConfig,
    execute_tool,
    format_tool_result_for_message,
)
from openjiuwen_deepsearch.algorithm.search_nodes.utils import (
    SaveSearchFinalResultConfig,
    Termination,
    _save_and_return_search_final_result,
    to_dict_safe,
    to_json_safe,
)
from openjiuwen_deepsearch.algorithm.search_tools.retriever_tool import RetrieveTool
from openjiuwen_deepsearch.algorithm.search_tools.web_fetch_tool import WebFetch
from openjiuwen_deepsearch.algorithm.search_tools.web_search_tool import WebSearch
from openjiuwen_deepsearch.algorithm.user_feedback_processor.action_definitions import (
    _is_report_feedback_payload,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode, format_exception_info
from openjiuwen_deepsearch.config.config import (
    AgentConfig,
    Config,
    CustomLocalSearchConfig,
    CustomWebSearchConfig,
    LocalSearchEngineConfig,
    MilvusConfig,
    PerQuestionParams,
    SearchWorkflowConfig,
    WebSearchEngineConfig,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import init_router
from openjiuwen_deepsearch.framework.openjiuwen.agent.editor_team_manager_node import (
    DependencyEditorTeamNode,
    EditorTeamNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import (
    DependencyOutlineInteractionNode,
    DependencyOutlineNode,
    EndNode,
    IntentRecognitionNode,
    FeedbackHandlerNode,
    FindActionSpaceNode,
    GenerateQuestionsNode,
    InitializeStateNode,
    OutlineInteractionNode,
    OutlineNode,
    ReporterNode,
    RunActionNode,
    SearchEndNode,
    SearchStartNode,
    SourceTracerInferNode,
    SourceTracerNode,
    StartNode,
    ToolNode,
    UserFeedbackProcessorNode,
    ValidateNewStateNode,
    VLMChartGeneratorNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Action,
    Result,
    SearchFinalResult,
    State,
)
from openjiuwen_deepsearch.framework.openjiuwen.core.workflow_agent import WorkflowControllerConfig
from openjiuwen_deepsearch.framework.openjiuwen.core.workflow_agent.workflow_agent import WorkflowAgent
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import LlmConfigCategory
from openjiuwen_deepsearch.framework.openjiuwen.tools import (
    update_local_search_mapping,
    update_web_search_mapping,
)
from openjiuwen_deepsearch.llm.llm_request_adapter import resolve_llm_thinking_enabled
from openjiuwen_deepsearch.llm.llm_wrapper import create_llm_obj
from openjiuwen_deepsearch.utils.common_utils.llm_utils import (
    get_effective_workflow_llm_usage,
    is_workflow_llm_usage_empty,
    pop_workflow_llm_usage,
)
from openjiuwen_deepsearch.utils.common_utils.security_utils import ensure_safe_directory, zero_secret
from openjiuwen_deepsearch.utils.common_utils.stream_utils import (
    MessageType,
    StreamEvent,
    get_current_time,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId
from openjiuwen_deepsearch.utils.constants_utils.search_engine_constants import SearchEngine
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    llm_context,
    local_search_context,
    session_context,
    tool_context,
    web_search_context,
)
from openjiuwen_deepsearch.utils.log_utils.log_common import session_id_ctx
from openjiuwen_deepsearch.utils.log_utils.log_interface import record_interface_log
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.log_utils.log_metrics import TIME_LOGGER_TAG, metrics_logger
from openjiuwen_deepsearch.utils.rate_limiter_utils.qps_limiter import qps_rate_limiter
from openjiuwen_deepsearch.utils.run_telemetry import emit, emit_messages_updated
from openjiuwen_deepsearch.utils.validation_utils.field_validation import (
    validate_agent_required_field,
)
from openjiuwen_deepsearch.utils.validation_utils.param_validation import (
    validate_generate_template_params,
    validate_run_agent_params,
)


def _build_retrieve_tool(milvus_cfg: MilvusConfig) -> RetrieveTool:
    kwargs = {}
    if milvus_cfg.retriever_class is not None:
        kwargs["retriever_class"] = milvus_cfg.retriever_class
    return RetrieveTool(
        {
            "milvus_host": milvus_cfg.milvus_host,
            "milvus_port": milvus_cfg.milvus_port,
            "database_name": milvus_cfg.database_name,
            "collection_name": milvus_cfg.collection_name,
            "embedder_model_name": milvus_cfg.embedder_model_name,
            "embedder_api_key": milvus_cfg.embedder_api_key,
            "embedder_base_url": milvus_cfg.embedder_base_url,
            "embedder_timeout": milvus_cfg.embedder_timeout,
        },
        **kwargs,
    )


logger = logging.getLogger(__name__)


def _redact_agent_config_for_workflow_inputs(agent_config: Any) -> dict:
    """Build a redacted copy of agent_config for workflow logging boundaries."""
    return anonymize_config_for_logging(copy.deepcopy(to_dict_safe(agent_config)))


@dataclass
class DeepSearchRunContext:
    agent_config: AgentConfig
    search_config: SearchWorkflowConfig
    per_question_params: PerQuestionParams
    query: str
    log_dir: str
    time_limit: int
    gold_answer: str | None
    tool_map: dict[str, Any]
    action_pool: ActionPool
    final_answer: str | None = None
    fail_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


class BaseAgent:
    """
    base agent: agent基类
    """

    async def run(
        self,
        message: str,
        conversation_id: str,
        agent_config: dict,
        report_template: str = "",
        interrupt_feedback: str = "",
    ):
        """
        运行agent的抽象方法

        Args:
            message (str): 入参query
            conversation_id (str): 会话ID
            agent_config (dict): agent的配置
            report_template (str): 报告模板
            interrupt_feedback (str): HITL的用户反馈信息

        Returns:
            Async generator that yields StreamEvent objects.

        """
        raise CustomValueException(StatusCode.AGENT_RUN_NOT_SUPPORT.code, StatusCode.AGENT_RUN_NOT_SUPPORT.errmsg)

    async def generate_template(self, file_name: str, file_stream: str, is_template: bool, agent_config: dict):
        """
        生成报告模板的抽象方法

        Args:
            file_name (str): 文件名，包括后缀
            file_stream (str): base64编码的文件内容
            is_template (bool): 是否为模板文件(True:模板文件，False:从报告生成)
            agent_config (dict): agent的配置

        Returns:
            dict: {"status" str, "template_content" str, "error_message" str}

        """
        start_time = time.time()
        success = False
        response_info = {}
        try:
            validate_generate_template_params(file_name, file_stream, is_template)
            validate_agent_required_field(agent_config)
            result = await TemplateGenerator.generate_template(
                file_name=file_name, file_stream=file_stream, is_template=is_template, agent_config=agent_config
            )
            success = result.get("status", "").lower() == "success"
            response_info = (
                {}
                if success
                else {
                    "exception_info": format_exception_info(
                        StatusCode.TEMPLATE_GENERATE_ERROR, result.get("error_message", "")
                    )
                }
            )
            return result
        except Exception as e:
            if LogManager.is_sensitive():
                logger.error(f"[extract_template]")
            else:
                logger.error(f"[extract_template] {e}")
            if LogManager.is_sensitive():
                error_msg = format_exception_info(
                    StatusCode.TEMPLATE_GENERATE_ERROR, "Error when generating template."
                )
            else:
                error_msg = format_exception_info(StatusCode.TEMPLATE_GENERATE_ERROR, e)
            response_info = {"exception_info": error_msg}
            return {"status": "fail", "template_content": "", "error_message": error_msg}
        finally:
            duration_min = (time.time() - start_time) / 60
            record_interface_log(
                role="SVR",
                session_id="-",
                api_name="generate_template",
                duration_min=duration_min,
                success=success,
                response_info=response_info,
            )


class DeepresearchAgent(BaseAgent):
    """
    Deepresearch agent: 生成报告 Agent，通用模型，并行执行任务，不带模板
    """

    def __init__(self):
        self.research_name = self._get_default_research_name()
        self.version = "1"
        self.agent = None
        self.workflow_input_schema = {
            "query": {
                "type": "string",
            },
            "thread_id": {
                "type": "string",
            },
            "conversation_id": {
                "type": "string",
            },
            "report_template": {
                "type": "string",
            },
            "interrupt_feedback": {
                "type": "string",
            },
            "agent_config": {
                "type": "object",
            },
        }
        self.startnode_input_schema = {
            "query": "${query}",
            "thread_id": "${thread_id}",
            "conversation_id": "${conversation_id}",
            "report_template": "${report_template}",
            "interrupt_feedback": "${interrupt_feedback}",
            "agent_config": "${agent_config}",
        }

        self.research_workflow = None
        self._create_research_workflow_agent()

    def _get_default_research_name(self) -> str:
        return "research_workflow"

    @staticmethod
    def _build_workflow_provider(builder, workflow_card: WorkflowCard):
        """构造可重复调用的 workflow provider。

        Args:
            builder: 用于创建 workflow 实例的可调用对象。
            workflow_card: 当前 workflow 的卡片元信息。

        Returns:
            callable: 每次调用都返回新 workflow 实例、并附带 workflow card 元属性的 provider。
        """

        def _provider():
            return builder()

        _provider.id = workflow_card.id
        _provider.version = workflow_card.version
        _provider.name = workflow_card.name
        _provider.description = workflow_card.description
        _provider.input_params = workflow_card.input_params
        return _provider

    @staticmethod
    def _build_interrupt_message(thread_id: str, chunk: OutputSchema):
        payload_id = getattr(getattr(chunk, "payload", None), "id", "")
        interrupt_message = {
            "conversation_id": thread_id,
            "agent": payload_id,
            "section_idx": getattr(chunk, "section_idx", "0"),
            "plan_idx": getattr(chunk, "plan_idx", "0"),
            "step_idx": getattr(chunk, "step_idx", "0"),
            "message_id": str(uuid.uuid4()),
            "role": "assistant",
            "content": chunk.payload.value,
            "message_type": MessageType.INTERRUPT.value,
            "event": StreamEvent.WAITING_USER_INPUT.value,
            "created_time": getattr(chunk, "created_time", ""),
        }
        if not LogManager.is_sensitive():
            logger.debug("[OUTPUT] Interrupt event: %s", json.dumps(interrupt_message, ensure_ascii=False))
        return json.dumps(interrupt_message, ensure_ascii=False)

    @staticmethod
    def _build_output_message(thread_id: str, chunk: CustomSchema):
        output_message = {
            "conversation_id": thread_id,
            "section_idx": getattr(chunk, "section_idx", "0"),
            "plan_idx": getattr(chunk, "plan_idx", "0"),
            "step_idx": getattr(chunk, "step_idx", "0"),
            "message_id": getattr(chunk, "message_id", ""),
            "agent": getattr(chunk, "agent", "Default"),
            "role": "assistant",
            "content": getattr(chunk, "content", ""),
            "message_type": getattr(chunk, "message_type", ""),
            "event": getattr(chunk, "event", ""),
            "created_time": getattr(chunk, "created_time", ""),
        }
        if hasattr(chunk, "finish_reason"):
            output_message["finish_reason"] = getattr(chunk, "finish_reason")
        if not LogManager.is_sensitive():
            logger.debug("[OUTPUT] Message event: %s", json.dumps(output_message, ensure_ascii=False))
        return json.dumps(output_message, ensure_ascii=False)

    @staticmethod
    async def _release_checkpointer_session(conversation_id: str):
        """显式释放 checkpointer 会话状态，防止分布式场景残留。"""
        try:
            checkpointer = CheckpointerFactory.get_checkpointer()
            if not checkpointer:
                return
            release_result = checkpointer.release(conversation_id)
            if hasattr(release_result, "__await__"):
                await release_result
        except Exception as e:
            if not LogManager.is_sensitive():
                logger.warning(f"[DeepResearchAgent.run] Failed to release checkpointer session: {e}")
            else:
                logger.warning("[DeepResearchAgent.run] Failed to release checkpointer session.")

    @staticmethod
    def _register_web_search_tool(custom_web: CustomWebSearchConfig, search_config: WebSearchEngineConfig):
        """注册网络搜索工具"""
        search_engine_mapping = update_web_search_mapping(
            custom_web.custom_web_search_file, custom_web.custom_web_search_func
        )
        web_engine_name = search_config.search_engine_name
        if web_engine_name not in search_engine_mapping:
            error_msg = f"Failed to register web engine: {web_engine_name}, engine is not found in the registry."
            logger.error(f"[Tool Init] {error_msg}")
            raise CustomValueException(StatusCode.WEB_SEARCH_INSTANCE_OBTAIN_ERROR.code, message=error_msg)
        return web_engine_name, search_engine_mapping

    @staticmethod
    def _register_local_search_tool(custom_local: CustomLocalSearchConfig, search_config: LocalSearchEngineConfig):
        """注册本地搜索工具"""
        local_engine_mapping = update_local_search_mapping(
            custom_local.custom_local_search_file,
            custom_local.custom_local_search_func,
        )
        engine_name = search_config.search_engine_name
        # native 参数校验
        if engine_name == "native":
            if not search_config.knowledge_base_configs:
                error_msg = "native local search requires knowledge_base_configs"
                logger.error(f"[Tool Init] {error_msg}")
                raise CustomValueException(
                    StatusCode.LOCAL_SEARCH_INSTANCE_OBTAIN_ERROR.code,
                    message=error_msg,
                )

        if engine_name not in local_engine_mapping:
            error_msg = f"Failed to register local engine: {engine_name}, " f"engine is not found in the registry."
            logger.error(f"[Tool Init] {error_msg}")
            raise CustomValueException(
                StatusCode.LOCAL_SEARCH_INSTANCE_OBTAIN_ERROR.code,
                message=error_msg,
            )
        return engine_name, local_engine_mapping

    @staticmethod
    async def _aopen_local_search_engines():
        for name, engine in (local_search_context.get() or {}).items():
            if hasattr(engine, "aopen"):
                try:
                    await engine.aopen()
                    logger.debug("LocalSearch engine [%s] opened.", name)
                except Exception as e:
                    logger.warning("Failed to open local search engine [%s]: %s", name, e)

    @staticmethod
    async def _aclose_local_search_engines():
        for name, engine in (local_search_context.get() or {}).items():
            if hasattr(engine, "aclose"):
                try:
                    await engine.aclose()
                    logger.debug("LocalSearch engine [%s] async closed.", name)
                except Exception as e:
                    logger.warning("Failed to async close local search engine [%s]: %s", name, e)

    @staticmethod
    def _reset_context_tokens(llm_token, web_search_token, local_search_token):
        if llm_token is not None:
            llm_context.reset(llm_token)
        if web_search_token is not None:
            web_search_context.reset(web_search_token)
        if local_search_token is not None:
            local_search_context.reset(local_search_token)

    @staticmethod
    def _build_stream_error_payload(conversation_id: str, final_result_info: dict):
        return {
            "conversation_id": conversation_id,
            "message_id": str(uuid.uuid4()),
            "agent": NodeId.FRAMEWORK.value,
            "role": "assistant",
            "content": json.dumps(final_result_info, ensure_ascii=False),
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": StreamEvent.ERROR.value,
            "created_time": get_current_time(),
        }

    @staticmethod
    def _build_stream_end_payload(conversation_id: str):
        return {
            "conversation_id": conversation_id,
            "message_id": str(uuid.uuid4()),
            "agent": NodeId.FRAMEWORK.value,
            "role": "assistant",
            "content": "ALL END",
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": StreamEvent.SUMMARY_RESPONSE.value,
            "created_time": get_current_time(),
        }

    @staticmethod
    async def _emit_error_and_end_stream(conversation_id: str, final_result_info: dict):
        try:
            yield json.dumps(
                DeepresearchAgent._build_stream_error_payload(conversation_id, final_result_info), ensure_ascii=False
            )
            yield json.dumps(DeepresearchAgent._build_stream_end_payload(conversation_id), ensure_ascii=False)
        except Exception as stream_err:
            logger.warning("[DeepResearchAgent.run] Failed to emit error stream event: %s", stream_err)

    @staticmethod
    def _prepare_stream_query(message: str, interrupt_feedback: str):
        is_report_feedback = _is_report_feedback_payload(message)
        if interrupt_feedback and not is_report_feedback:
            return json.dumps({"interrupt_feedback": interrupt_feedback, "feedback": message}), is_report_feedback
        return message, is_report_feedback

    async def _consume_stream_chunks(
        self,
        conversation_id: str,
        message: str,
        decoded_template: str,
        interrupt_feedback: str,
        session_agent_config: dict,
    ):
        is_all_end = False
        final_result_info = {}
        filter_dup_flag = False
        stream_query, is_report_feedback = self._prepare_stream_query(message, interrupt_feedback)
        workflow_agent_config = _redact_agent_config_for_workflow_inputs(session_agent_config)

        async for chunk in Runner.run_agent_streaming(
            agent=self.agent,
            inputs={
                "query": stream_query,
                "thread_id": conversation_id,
                "conversation_id": conversation_id,
                "report_template": decoded_template,
                "interrupt_feedback": interrupt_feedback,
                "resume_interaction": is_report_feedback,
                "agent_config": workflow_agent_config,
            },
        ):
            if getattr(chunk, "type", "") == "__interaction__":
                filter_dup_flag = False
                yield self._build_interrupt_message(conversation_id, chunk), is_all_end, final_result_info
                continue
            if filter_dup_flag:
                continue
            if isinstance(chunk, CustomSchema):
                agent = getattr(chunk, "agent", "")
                event = getattr(chunk, "event", "")
                if agent == NodeId.GENERATE_QUESTIONS.value and event == StreamEvent.DONE.value:
                    filter_dup_flag = True
                endnode_info = parse_endnode_content(chunk)
                if endnode_info:
                    final_result_info = endnode_info
                if getattr(chunk, "content", "") == "ALL END":
                    is_all_end = True
                yield self._build_output_message(conversation_id, chunk), is_all_end, final_result_info

    async def run(
        self,
        message: Optional[str] = None,
        conversation_id: Optional[str] = None,
        agent_config: Optional[dict] = None,
        report_template: str = "",
        interrupt_feedback: str = "",
    ):
        """执行一次 workflow 并以流式方式返回消息。

        Args:
            message: 用户输入消息或反馈内容。
            conversation_id: 会话 ID，同时作为 workflow thread_id。
            agent_config: 本次运行的 Agent 配置字典。
            report_template: 报告模板（支持 base64 或明文）。
            interrupt_feedback: 交互中断反馈标识。

        Yields:
            str: JSON 序列化后的流式事件消息。

        Raises:
            CustomValueException: 参数校验失败或配置不合法时抛出。
        """
        validate_run_agent_params(message, conversation_id, report_template, interrupt_feedback)
        validate_agent_required_field(agent_config)

        start_time = time.time()
        llm_token = None
        web_search_token = None
        local_search_token = None

        try:
            session_agent_config = AgentConfig.model_validate(agent_config)
            llm_configs = session_agent_config.llm_config
            if LlmConfigCategory.GENERAL.value not in llm_configs:
                raise CustomValueException(
                    error_code=StatusCode.LLM_CONFIG_NONE.code, message=StatusCode.LLM_CONFIG_NONE.errmsg
                )

            # LLM 思考开关属于 SDK 内部运行配置，默认关闭并统一作用于所有模型槽位。
            thinking_enabled = resolve_llm_thinking_enabled()
            all_llms = {}
            for _, llm_config in llm_configs.items():
                llm_obj = create_llm_obj(llm_config, thinking_enabled=thinking_enabled)
                all_llms[llm_config.model_name] = llm_obj
            llm_token = llm_context.set(all_llms)

            web_search_token, local_search_token = self._initialize_tools(session_agent_config)
            await self._aopen_local_search_engines()
        except CustomValueException:
            self._reset_context_tokens(llm_token, web_search_token, local_search_token)
            raise
        except ValidationError as e:
            self._reset_context_tokens(llm_token, web_search_token, local_search_token)
            if LogManager.is_sensitive():
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR_NO_PRINT.code,
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR_NO_PRINT.errmsg,
                ) from e
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(e=str(e)),
            ) from e

        token = session_id_ctx.set(conversation_id)
        stats_info_llm_enabled = bool(session_agent_config.stats_info_llm)
        decoded_template = report_template
        if report_template:
            decoded_template = self._handle_report_template(report_template)

        is_all_end = False
        final_result_info = {}
        try:
            session_agent_config = session_agent_config.model_dump()
            async for payload, stream_end, stream_info in self._consume_stream_chunks(
                conversation_id=conversation_id,
                message=message,
                decoded_template=decoded_template,
                interrupt_feedback=interrupt_feedback,
                session_agent_config=session_agent_config,
            ):
                is_all_end = stream_end
                final_result_info = stream_info
                yield payload
        except Exception as e:
            if not LogManager.is_sensitive() or isinstance(e, CustomValueException):
                logger.error(f"[DeepResearchAgent.run] Session closed with error: {e}")
                final_result_info = {
                    "exception_info": format_exception_info(StatusCode.WORKFLOW_RUN_ERROR, e)
                }
            else:
                logger.error(f"[DeepResearchAgent.run] Session closed with error.")
                final_result_info = {
                    "exception_info": format_exception_info(
                        StatusCode.WORKFLOW_RUN_ERROR, "Session closed with error."
                    )
                }
            if stats_info_llm_enabled:
                try:
                    current_session = session_context.get()
                except Exception:
                    current_session = None
                workflow_usage = get_effective_workflow_llm_usage(
                    session_id=conversation_id,
                    session=current_session,
                )
                if not is_workflow_llm_usage_empty(workflow_usage):
                    final_result_info["workflow_llm_token_usage"] = workflow_usage

            async for payload in self._emit_error_and_end_stream(conversation_id, final_result_info):
                yield payload

            if stats_info_llm_enabled:
                pop_workflow_llm_usage(conversation_id)

            await self.agent.release_session(conversation_id)
            await self._release_checkpointer_session(conversation_id)
            session_id_ctx.reset(token)
        finally:
            metrics_logger.info(
                f"{TIME_LOGGER_TAG} thread_id: {conversation_id} ------ [DeepResearchAgent[0].run]"
                f" executed time: {(time.time() - start_time) :.2f} s"
            )

            record_interface_log(
                role="SVR",
                session_id=conversation_id,
                api_name="run",
                duration_min=(time.time() - start_time) / 60,
                success=not bool(final_result_info.get("exception_info")),
                response_info=final_result_info if bool(final_result_info.get("exception_info")) else {},
            )
            try:
                await self._aclose_local_search_engines()
            except Exception as e:
                if not LogManager.is_sensitive():
                    logger.warning(f"Failed to close local search engines: {e}")
                else:
                    logger.warning(f"Failed to close local search engines.")
            finally:
                self._reset_context_tokens(llm_token, web_search_token, local_search_token)

            if is_all_end:
                zero_secret(
                    session_agent_config.get("web_search_engine_config", {}).get(
                        "search_api_key", bytearray("", encoding="utf-8")
                    )
                )
                zero_secret(
                    session_agent_config.get("local_search_engine_config", {}).get(
                        "search_api_key", bytearray("", encoding="utf-8")
                    )
                )
                await self.agent.release_session(conversation_id)
                await self._release_checkpointer_session(conversation_id)
                session_id_ctx.reset(token)
            if stats_info_llm_enabled and is_all_end:
                pop_workflow_llm_usage(conversation_id)

    def _build_research_workflow(self):
        _id = self.research_name
        name = self.research_name
        version = self.version
        card = WorkflowCard(
            id=_id,
            version=version,
            name=name,
        )

        flow = Workflow(card=card)
        flow.set_start_comp(
            start_comp_id=NodeId.START.value, component=StartNode(), inputs_schema=self.startnode_input_schema
        )
        # 主图节点
        flow.add_workflow_comp(NodeId.INTENT_RECOGNITION.value, IntentRecognitionNode())
        flow.add_workflow_comp(NodeId.GENERATE_QUESTIONS.value, GenerateQuestionsNode())
        flow.add_workflow_comp(NodeId.FEEDBACK_HANDLER.value, FeedbackHandlerNode())
        flow.add_workflow_comp(NodeId.OUTLINE.value, OutlineNode())
        flow.add_workflow_comp(NodeId.OUTLINE_INTERACTION.value, OutlineInteractionNode())
        # 子图节点
        flow.add_workflow_comp(NodeId.EDITOR_TEAM.value, EditorTeamNode())
        flow.add_workflow_comp(NodeId.REPORTER.value, ReporterNode())
        flow.add_workflow_comp(NodeId.VLM_CHART_GENERATOR.value, VLMChartGeneratorNode())
        flow.add_workflow_comp(NodeId.SOURCE_TRACER.value, SourceTracerNode())
        flow.add_workflow_comp(NodeId.SOURCE_TRACER_INFER.value, SourceTracerInferNode())
        flow.add_workflow_comp(NodeId.USER_FEEDBACK_PROCESSOR.value, UserFeedbackProcessorNode())
        flow.set_end_comp(NodeId.END.value, EndNode())

        # 添加边
        flow.add_connection(NodeId.START.value, NodeId.INTENT_RECOGNITION.value)

        # 添加条件边
        intent_recognition_router = init_router(
            NodeId.INTENT_RECOGNITION.value,
            [NodeId.OUTLINE.value, NodeId.GENERATE_QUESTIONS.value, NodeId.END.value],
        )
        generate_questions_router = init_router(
            NodeId.GENERATE_QUESTIONS.value, [NodeId.FEEDBACK_HANDLER.value, NodeId.END.value]
        )
        outline_router = init_router(
            NodeId.OUTLINE.value, [NodeId.OUTLINE_INTERACTION.value, NodeId.EDITOR_TEAM.value, NodeId.END.value]
        )
        outline_interaction_router = init_router(
            NodeId.OUTLINE_INTERACTION.value, [NodeId.OUTLINE.value, NodeId.EDITOR_TEAM.value, NodeId.END.value]
        )
        reporter_router = init_router(NodeId.REPORTER.value, [NodeId.END.value, NodeId.VLM_CHART_GENERATOR.value])
        feedback_handler_router = init_router(NodeId.FEEDBACK_HANDLER.value, [NodeId.OUTLINE.value, NodeId.END.value])
        editor_team_router = init_router(NodeId.EDITOR_TEAM.value, [NodeId.REPORTER.value, NodeId.END.value])
        user_feedback_processor_router = init_router(
            NodeId.USER_FEEDBACK_PROCESSOR.value, [NodeId.USER_FEEDBACK_PROCESSOR.value, NodeId.END.value]
        )
        flow.add_conditional_connection(NodeId.INTENT_RECOGNITION.value, router=intent_recognition_router)
        flow.add_conditional_connection(NodeId.GENERATE_QUESTIONS.value, router=generate_questions_router)
        flow.add_conditional_connection(NodeId.OUTLINE.value, router=outline_router)
        flow.add_conditional_connection(NodeId.FEEDBACK_HANDLER.value, router=feedback_handler_router)
        flow.add_conditional_connection(NodeId.REPORTER.value, router=reporter_router)
        flow.add_conditional_connection(NodeId.EDITOR_TEAM.value, router=editor_team_router)
        flow.add_conditional_connection(NodeId.OUTLINE_INTERACTION.value, router=outline_interaction_router)
        flow.add_connection(NodeId.VLM_CHART_GENERATOR.value, NodeId.SOURCE_TRACER.value)
        flow.add_connection(NodeId.SOURCE_TRACER.value, NodeId.SOURCE_TRACER_INFER.value)
        flow.add_connection(NodeId.SOURCE_TRACER_INFER.value, NodeId.USER_FEEDBACK_PROCESSOR.value)
        flow.add_conditional_connection(NodeId.USER_FEEDBACK_PROCESSOR.value, router=user_feedback_processor_router)

        return flow

    def _create_research_workflow_agent(self):
        """创建Deepresearch工作流Agent实例"""
        workflow_card = WorkflowCard(
            id=self.research_name,
            version=self.version,
            name=self.research_name,
            description=self.research_name,
            input_params=self.workflow_input_schema,
        )

        card = AgentCard(
            id=self.research_name,
            name=self.research_name,
            description=self.research_name,
        )
        config = WorkflowControllerConfig(
            id=self.research_name,
            version=self.version,
            description=self.research_name,
            workflows=[workflow_card],
        )
        self.agent = WorkflowAgent(card=card, config=config)
        self.agent.add_workflows([self._build_workflow_provider(self._build_research_workflow, workflow_card)])

    def _handle_report_template(self, report_template):
        decoded_template = None
        try:
            decoded_template = base64.b64decode(report_template).decode("utf-8")
            logging.debug("[DeepresearchAgent.run] Successfully decoded base64 report_template")
        except Exception as e:
            if not LogManager.is_sensitive():
                logging.warning(f"[DeepresearchAgent.run] Failed to decode base64 report template: {e}")
            else:
                logging.warning(f"[DeepresearchAgent.run] Failed to decode base64 report template.")
            decoded_template = report_template
        return decoded_template

    def _initialize_tools(self, agent_config: AgentConfig):
        """初始化搜索工具"""
        custom_web = agent_config.custom_web_search_config
        custom_local = agent_config.custom_local_search_config
        web_search_config = agent_config.web_search_engine_config
        local_search_config = agent_config.local_search_engine_config

        web_engine_name, web_mapping = self._register_web_search_tool(custom_web, web_search_config)
        local_engine_name, local_mapping = self._register_local_search_tool(custom_local, local_search_config)
        web_engine_configs = {
            web_engine_name: web_search_config.model_dump(),
        }
        for engine_name in (SearchEngine.PUBMED.value, SearchEngine.ARXIV.value):
            if engine_name in web_mapping and engine_name not in web_engine_configs:
                vertical_config = web_search_config.model_dump()
                vertical_config["search_engine_name"] = engine_name
                vertical_config["search_url"] = ""
                vertical_config["search_api_key"] = bytearray("", encoding="utf-8")
                web_engine_configs[engine_name] = vertical_config
        web_search_token = web_search_context.set(
            {
                name: web_mapping[name](**engine_config)
                for name, engine_config in web_engine_configs.items()
            }
        )
        local_search_token = local_search_context.set(
            {local_engine_name: local_mapping[local_engine_name](**local_search_config.model_dump())}
        )

        # 注册QPS限流器
        qps_limiter = qps_rate_limiter
        qps_limiter.set_max_qps(agent_config.web_search_max_qps)

        return web_search_token, local_search_token


class DeepresearchDependencyAgent(DeepresearchAgent):
    """
    Deepresearch agent: 生成报告 Agent，通用模型，依赖驱动执行任务，不带模板
    """

    def _get_default_research_name(self) -> str:
        return "research_workflow_dependency_driving"

    def _create_research_workflow_agent(self):
        workflow_card = WorkflowCard(
            id=self.research_name,
            version=self.version,
            name=self.research_name,
            description=self.research_name,
            input_params=self.workflow_input_schema,
        )

        card = AgentCard(
            id=self.research_name,
            name=self.research_name,
            description=self.research_name,
        )
        config = WorkflowControllerConfig(
            id=self.research_name,
            version=self.version,
            description=self.research_name,
            workflows=[workflow_card],
        )
        self.agent = WorkflowAgent(card=card, config=config)
        self.agent.add_workflows(
            [self._build_workflow_provider(self._build_research_dependency_workflow, workflow_card)]
        )

    def _build_research_dependency_workflow(self):
        _id = self.research_name
        name = self.research_name
        version = self.version
        # workflow配置
        card = WorkflowCard(
            id=_id,
            version=version,
            name=name,
        )
        # workflow
        flow = Workflow(card=card)
        # 添加起始node
        flow.set_start_comp(
            start_comp_id=NodeId.START.value, component=StartNode(), inputs_schema=self.startnode_input_schema
        )
        # 添加node
        flow.add_workflow_comp(NodeId.INTENT_RECOGNITION.value, IntentRecognitionNode())
        flow.add_workflow_comp(NodeId.GENERATE_QUESTIONS.value, GenerateQuestionsNode())
        flow.add_workflow_comp(NodeId.FEEDBACK_HANDLER.value, FeedbackHandlerNode())
        flow.add_workflow_comp(NodeId.OUTLINE.value, DependencyOutlineNode())
        flow.add_workflow_comp(NodeId.OUTLINE_INTERACTION.value, DependencyOutlineInteractionNode())
        # 依赖驱动编辑团队节点（推理+写作按层流水线并行）
        flow.add_workflow_comp(NodeId.DEPENDENCY_EDITOR_TEAM.value, DependencyEditorTeamNode())
        flow.add_workflow_comp(NodeId.REPORTER.value, ReporterNode())
        flow.add_workflow_comp(NodeId.VLM_CHART_GENERATOR.value, VLMChartGeneratorNode())
        flow.add_workflow_comp(NodeId.SOURCE_TRACER.value, SourceTracerNode())
        flow.add_workflow_comp(NodeId.SOURCE_TRACER_INFER.value, SourceTracerInferNode())
        flow.add_workflow_comp(NodeId.USER_FEEDBACK_PROCESSOR.value, UserFeedbackProcessorNode())
        flow.set_end_comp(NodeId.END.value, EndNode())

        # 添加边 add_connection
        flow.add_connection(NodeId.START.value, NodeId.INTENT_RECOGNITION.value)

        # 添加条件边
        intent_recognition_router = init_router(
            NodeId.INTENT_RECOGNITION.value,
            [NodeId.OUTLINE.value, NodeId.GENERATE_QUESTIONS.value, NodeId.END.value],
        )
        generate_questions_router = init_router(
            NodeId.GENERATE_QUESTIONS.value, [NodeId.FEEDBACK_HANDLER.value, NodeId.END.value]
        )
        outline_router = init_router(
            NodeId.OUTLINE.value,
            [NodeId.OUTLINE_INTERACTION.value, NodeId.DEPENDENCY_EDITOR_TEAM.value, NodeId.END.value],
        )
        outline_interaction_router = init_router(
            NodeId.OUTLINE_INTERACTION.value,
            [NodeId.OUTLINE.value, NodeId.DEPENDENCY_EDITOR_TEAM.value, NodeId.END.value],
        )
        reporter_router = init_router(NodeId.REPORTER.value, [NodeId.END.value, NodeId.VLM_CHART_GENERATOR.value])
        feedback_handler_router = init_router(NodeId.FEEDBACK_HANDLER.value, [NodeId.OUTLINE.value, NodeId.END.value])
        dependency_editor_router = init_router(
            NodeId.DEPENDENCY_EDITOR_TEAM.value, [NodeId.REPORTER.value, NodeId.END.value]
        )
        user_feedback_processor_router = init_router(
            NodeId.USER_FEEDBACK_PROCESSOR.value, [NodeId.USER_FEEDBACK_PROCESSOR.value, NodeId.END.value]
        )
        flow.add_conditional_connection(NodeId.INTENT_RECOGNITION.value, router=intent_recognition_router)
        flow.add_conditional_connection(NodeId.GENERATE_QUESTIONS.value, router=generate_questions_router)
        flow.add_conditional_connection(NodeId.OUTLINE.value, router=outline_router)
        flow.add_conditional_connection(NodeId.FEEDBACK_HANDLER.value, router=feedback_handler_router)
        flow.add_conditional_connection(NodeId.OUTLINE_INTERACTION.value, router=outline_interaction_router)
        flow.add_conditional_connection(NodeId.REPORTER.value, router=reporter_router)
        flow.add_conditional_connection(NodeId.DEPENDENCY_EDITOR_TEAM.value, router=dependency_editor_router)
        flow.add_connection(NodeId.VLM_CHART_GENERATOR.value, NodeId.SOURCE_TRACER.value)
        flow.add_connection(NodeId.SOURCE_TRACER.value, NodeId.SOURCE_TRACER_INFER.value)
        flow.add_connection(NodeId.SOURCE_TRACER_INFER.value, NodeId.USER_FEEDBACK_PROCESSOR.value)
        flow.add_conditional_connection(NodeId.USER_FEEDBACK_PROCESSOR.value, router=user_feedback_processor_router)

        return flow


class DeepSearchAgent(BaseAgent):
    _workflow_agent: ClassVar[LegacyWorkflowAgent | None] = None
    _workflow_agent_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        self.version: str = "1"
        self.agent: LegacyWorkflowAgent | None = None

    def setup_log_directory(self, save_as: str, action_pool: ActionPool) -> str:
        """Create a timestamp-based log directory with Action and Result subfolders"""
        base_log_dir: str | None = LogManager.get_log_dir()

        log_dir = os.path.join(base_log_dir, save_as)

        os.makedirs(os.path.join(log_dir, "Action"), exist_ok=True)
        os.makedirs(os.path.join(log_dir, "Result"), exist_ok=True)

        action_pool.log_dir = log_dir
        return log_dir

    def _create_run_context(
        self,
        *,
        agent_config: AgentConfig,
        search_config: SearchWorkflowConfig,
        query: str,
        log_dir: str,
        tool_map: dict[str, Any],
        gold_answer: str | None = None,
        per_question_params: PerQuestionParams | None = None,
        time_limit: int | None = None,
        action_pool: ActionPool | None = None,
    ) -> DeepSearchRunContext:
        resolved_params = per_question_params or agent_config.search_workflow_per_question_params
        resolved_action_pool = action_pool or ActionPool()
        resolved_action_pool.log_dir = log_dir
        resolved_time_limit = int(time_limit if time_limit is not None else resolved_params.time_limit)
        return DeepSearchRunContext(
            agent_config=agent_config,
            search_config=search_config,
            per_question_params=resolved_params,
            query=query,
            log_dir=log_dir,
            time_limit=resolved_time_limit,
            gold_answer=gold_answer,
            tool_map=tool_map,
            action_pool=resolved_action_pool,
        )

    @staticmethod
    def _subworkflow_context_inputs(run_context: DeepSearchRunContext, workflow_name: str) -> dict[str, Any]:
        """Build per-run config inputs for DeepSearch sub-workflows.

        Sub-workflows are globally registered with fixed IDs, so runtime inputs must
        carry the active request config to avoid stale model settings across runs.
        """
        agent_config = to_dict_safe(run_context.agent_config)
        search_config = to_dict_safe(run_context.search_config)
        if not isinstance(agent_config, dict):
            agent_config = {}
        if not isinstance(search_config, dict):
            search_config = Config().service_config.search_workflow.model_dump()
        return {
            "workflow_name": workflow_name,
            "agent_config": agent_config,
            "search_config": search_config,
        }

    @staticmethod
    def _build_init_state_workflow() -> Workflow:
        card = WorkflowCard(id="init_state", version="1", name="init_state")
        wf = Workflow(card=card)

        wf.set_start_comp(
            start_comp_id=NodeId.START_NODE.value,
            component=SearchStartNode(),
            inputs_schema={
                "workflow_name": "init_state_workflow",
            },
        )

        wf.add_workflow_comp(NodeId.INITIAL_STATE.value, InitializeStateNode())
        wf.set_end_comp(NodeId.END_NODE.value, SearchEndNode())

        wf.add_connection(NodeId.START_NODE.value, NodeId.INITIAL_STATE.value)
        wf.add_connection(NodeId.INITIAL_STATE.value, NodeId.END_NODE.value)
        return wf

    @staticmethod
    def _build_find_action_workflow() -> Workflow:
        card = WorkflowCard(id="find_action", version="1", name="find_action")
        wf = Workflow(card=card)

        wf.set_start_comp(
            start_comp_id=NodeId.START_NODE.value,
            component=SearchStartNode(),
            inputs_schema={
                "workflow_name": "find_action_workflow",
            },
        )

        wf.add_workflow_comp(NodeId.FIND_ACTION_SPACE.value, FindActionSpaceNode())
        wf.set_end_comp(NodeId.END_NODE.value, SearchEndNode())

        wf.add_connection(NodeId.START_NODE.value, NodeId.FIND_ACTION_SPACE.value)
        wf.add_connection(NodeId.FIND_ACTION_SPACE.value, NodeId.END_NODE.value)
        return wf

    @staticmethod
    def _build_state_creation_workflow() -> Workflow:
        card = WorkflowCard(id="state_creation", version="1", name="state_creation")
        wf = Workflow(card=card)

        wf.set_start_comp(
            start_comp_id=NodeId.START_NODE.value,
            component=SearchStartNode(),
            inputs_schema={"workflow_name": "state_creation_workflow"},
        )

        wf.add_workflow_comp(NodeId.TOOL.value, ToolNode())
        wf.add_workflow_comp(NodeId.RUN_ACTION.value, RunActionNode())
        wf.add_workflow_comp(NodeId.VALIDATE_NEW_STATE.value, ValidateNewStateNode())
        wf.set_end_comp(NodeId.END_NODE.value, SearchEndNode())

        wf.add_connection(NodeId.START_NODE.value, NodeId.RUN_ACTION.value)
        wf.add_connection(NodeId.TOOL.value, NodeId.RUN_ACTION.value)

        run_iter = init_router(
            NodeId.RUN_ACTION.value,
            [
                NodeId.TOOL.value,
                NodeId.RUN_ACTION.value,
                NodeId.VALIDATE_NEW_STATE.value,
                NodeId.END_NODE.value,
            ],
        )
        validator_router = init_router(
            NodeId.VALIDATE_NEW_STATE.value,
            [NodeId.RUN_ACTION.value, NodeId.END_NODE.value],
        )
        wf.add_conditional_connection(NodeId.RUN_ACTION.value, router=run_iter)
        wf.add_conditional_connection(NodeId.VALIDATE_NEW_STATE.value, router=validator_router)

        return wf

    @staticmethod
    def _build_subworkflow_schemas() -> list[WorkflowCard]:
        return [
            WorkflowCard(
                id="init_state",
                version="1",
                name="init_state",
                description="init_state",
                input_params={"query": str, "total_input_tokens": int, "total_output_tokens": int, "log_dir": str},
            ),
            WorkflowCard(
                id="find_action",
                version="1",
                name="find_action",
                description="find_action",
                input_params={
                    "state": State,
                    "result": Result,
                    "query": str,
                    "log_dir": str,
                    "total_input_tokens": int,
                    "total_output_tokens": int,
                },
            ),
            WorkflowCard(
                id="state_creation",
                version="1",
                name="state_creation",
                description="state_creation",
                input_params={
                    "action": Action,
                    "log_dir": str,
                    "fail_count": int,
                    "retrieval_tool_only": bool,
                    "total_input_tokens": int,
                    "total_output_tokens": int,
                },
            ),
        ]

    @classmethod
    def _create_subworkflow_agent(cls) -> LegacyWorkflowAgent:
        agent = LegacyWorkflowAgent(
            WorkflowAgentConfig(
                id="deepsearch_sub_workflows",
                description="DeepSearch init/find/state_creation subgraphs",
                workflows=cls._build_subworkflow_schemas(),
            )
        )

        init_state_factory = WorkflowFactory(
            workflow_id="init_state",
            workflow_version="1",
            factory=cls._build_init_state_workflow,
            workflow_name="init_state",
            workflow_description="init_state",
            input_schema={"query": str, "total_input_tokens": int, "total_output_tokens": int, "log_dir": str},
        )
        find_action_factory = WorkflowFactory(
            workflow_id="find_action",
            workflow_version="1",
            factory=cls._build_find_action_workflow,
            workflow_name="find_action",
            workflow_description="find_action",
            input_schema={
                "state": State,
                "result": Result,
                "query": str,
                "log_dir": str,
                "total_input_tokens": int,
                "total_output_tokens": int,
            },
        )
        state_creation_factory = WorkflowFactory(
            workflow_id="state_creation",
            workflow_version="1",
            factory=cls._build_state_creation_workflow,
            workflow_name="state_creation",
            workflow_description="state_creation",
            input_schema={
                "action": Action,
                "log_dir": str,
                "fail_count": int,
                "retrieval_tool_only": bool,
                "total_input_tokens": int,
                "total_output_tokens": int,
            },
        )

        agent.add_workflows(
            [
                init_state_factory,
                find_action_factory,
                state_creation_factory,
            ]
        )
        return agent

    @classmethod
    def _get_shared_agent(cls):
        if cls._workflow_agent is None:
            with cls._workflow_agent_lock:
                if cls._workflow_agent is None:
                    cls._workflow_agent = cls._create_subworkflow_agent()
        return cls._workflow_agent

    def _build_agent(self):
        self.agent = self._get_shared_agent()
        return self.agent

    async def _cancel_running_tasks(
        self,
        run_context: DeepSearchRunContext,
        running_tasks: set[asyncio.Task],
        task_to_action: dict[asyncio.Task, Action],
    ) -> None:
        """Cancel all in-flight state_creation tasks and mark them as completed."""
        if not running_tasks:
            return
        count = len(running_tasks)
        for task in running_tasks:
            task.cancel()
        await asyncio.gather(*running_tasks, return_exceptions=True)
        for task in list(running_tasks):
            action = task_to_action.pop(task, None)
            if action is not None:
                run_context.action_pool.record_completed(action, None)
        running_tasks.clear()
        logger.info("[DeepSearchAgent] cancelled %d remaining tasks", count)

    async def run_state_creation_workflow(
        self,
        run_context: DeepSearchRunContext,
        action: Any,
        semaphore: asyncio.Semaphore,
    ) -> Any:
        async with semaphore:
            return await Runner.run_workflow(
                workflow="state_creation_1",
                inputs={
                    **self._subworkflow_context_inputs(run_context, "state_creation_workflow"),
                    "action": to_dict_safe(action),
                    "retrieval_tool_only": "retrieve" in run_context.tool_map,
                    "total_input_tokens": run_context.total_input_tokens,
                    "total_output_tokens": run_context.total_output_tokens,
                    "log_dir": run_context.log_dir,
                    "fail_count": run_context.fail_count,
                },
            )

    async def _run_internal(self, run_context: DeepSearchRunContext) -> SearchFinalResult:
        start_time: float = time.time()

        max_workers: int = run_context.per_question_params.max_workers
        sem = asyncio.Semaphore(max_workers)

        max_tries: int = run_context.search_config.init_state_agent.max_tries
        init_state_result: dict[str, Any] | None = None
        last_exception: Exception | None = None

        try:
            init_result: WorkflowOutput = await Runner.run_workflow(
                workflow="init_state_1",
                inputs={
                    **self._subworkflow_context_inputs(run_context, "init_state_workflow"),
                    "query": run_context.query,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "log_dir": run_context.log_dir,
                },
            )
            init_state_result = parse_and_validate_init_state_result(init_result)
        except Exception as e:
            last_exception = e
            logger.error(f"[DeepSearchAgent] init_state error, failed all {max_tries} attempts: {e}")

        if init_state_result is None:
            raise CustomValueException(
                StatusCode.AGENT_INIT_STATE_ERROR.code,
                StatusCode.AGENT_INIT_STATE_ERROR.errmsg,
            ) from last_exception

        run_context.total_input_tokens += init_state_result.get("total_input_tokens", 0)
        run_context.total_output_tokens += init_state_result.get("total_output_tokens", 0)

        init_state: State = init_state_result.get("init_state")
        logger.info(f"[DeepSearchAgent] initial state: %s", "***" if LogManager.is_sensitive() else init_state)

        actions_result: WorkflowOutput = await Runner.run_workflow(
            workflow="find_action_1",
            inputs={
                **self._subworkflow_context_inputs(run_context, "find_action_workflow"),
                "state": init_state,
                "query": run_context.query,
                "result": None,
                "log_dir": run_context.log_dir,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
            },
        )
        actions_dict: dict[str, Any] = parse_and_validate_find_action_result(actions_result.result)

        logger.info(
            f"[DeepSearchAgent] initial actions: %s",
            "***" if LogManager.is_sensitive() else [a.proposal.direction for a in actions_dict.get("actions", [])],
        )

        run_context.total_input_tokens += actions_dict.get("total_input_tokens", 0)
        run_context.total_output_tokens += actions_dict.get("total_output_tokens", 0)

        run_context.action_pool.add(actions_dict.get("actions", []))

        actions_explored: int = 0
        actions_explored_limit: int = run_context.per_question_params.actions_explored_limit
        fail_limit: int = run_context.per_question_params.fail_limit
        answer_mode_top_k: int = run_context.per_question_params.answer_mode_top_k
        provide_best_guess: bool = run_context.per_question_params.provide_best_guess

        running_tasks: set[asyncio.Task[WorkflowOutput]] = set()
        task_to_action: dict[asyncio.Task[WorkflowOutput], Action] = {}
        config: dict = {}

        termination_reason: Termination = Termination.ACTION_POOL_DEPLETED

        find_action_pool_depleted_retries_left: int = run_context.per_question_params.retry_count_on_empty_action_space

        while not run_context.final_answer:
            if (time.time() - start_time) > run_context.time_limit:
                termination_reason = Termination.TIME_LIMIT
                logger.info("[DeepSearchAgent] %s", termination_reason.log_message)
                break
            if actions_explored_limit > 0 and actions_explored >= actions_explored_limit:
                termination_reason = Termination.ACTIONS_EXPLORED_LIMIT
                logger.info("[DeepSearchAgent] %s (%d)", termination_reason.log_message, actions_explored_limit)
                break
            if fail_limit > 0 and run_context.fail_count >= fail_limit:
                termination_reason = Termination.FAIL_LIMIT
                logger.info("[DeepSearchAgent] %s (%d)", termination_reason.log_message, fail_limit)
                break
            available_slots: int = max_workers - len(running_tasks)

            # If there is idle workers, assign them a task by sampling from the action pool
            if available_slots > 0 and run_context.action_pool.size() > 0:
                sampled: list[Action] = run_context.action_pool.sample(available_slots)
                logger.info(
                    f"[DeepSearchAgent] sampled actions (dynamic): %s",
                    "***" if LogManager.is_sensitive() else [a.proposal.direction for a in sampled],
                )

                for action in sampled:
                    task = asyncio.create_task(
                        self.run_state_creation_workflow(
                            run_context=run_context,
                            action=action,
                            semaphore=sem,
                        )
                    )
                    running_tasks.add(task)
                    task_to_action[task] = action

            # If there is no running tasks (meaning that the action_pool has no actions to sample and give to workers)
            if not running_tasks:
                if find_action_pool_depleted_retries_left <= 0:
                    logger.info(
                        "[DeepSearchAgent] action pool empty and no running tasks; "
                        "find_action retries exhausted (retry_count_on_empty_action_space=%d)",
                        run_context.per_question_params.retry_count_on_empty_action_space,
                    )
                    break

                find_action_pool_depleted_retries_left -= 1
                logger.info(
                    "[DeepSearchAgent] action pool empty; re-running find_action "
                    "(retries remaining after this call: %d)",
                    find_action_pool_depleted_retries_left,
                )
                retry_strategy = run_context.search_config.find_action_agent.action_pool_depleted_strategy

                retry_result = None
                if retry_strategy == "dependent_retry" and run_context.action_pool.completed_actions:
                    failed_summaries: list[str] = []
                    for i, (action, action_result) in enumerate(run_context.action_pool.completed_actions, 1):
                        entry = f"{i}. Direction: {action.proposal.direction}"
                        if isinstance(action_result, Result) and action_result.messages:
                            entry += action_result.get_summary()
                        failed_summaries.append(entry)

                    prompt_content: str = get_prompt_section(
                        "deepsearch_dependent_retry_find_action",
                        {
                            "failed_count": len(failed_summaries),
                            "failed_summaries": "\n\n".join(failed_summaries),
                        },
                    )
                    retry_result = Result(
                        messages=[{"role": "user", "content": prompt_content}],
                        new_states=[],
                        found_answer=None,
                        previous_action_id=run_context.action_pool.completed_actions[-1][0].id,
                    )

                actions_result = await Runner.run_workflow(
                    workflow="find_action_1",
                    inputs={
                        **self._subworkflow_context_inputs(run_context, "find_action_workflow"),
                        "state": init_state,
                        "query": run_context.query,
                        "result": retry_result,
                        "log_dir": run_context.log_dir,
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                    },
                )
                actions_dict = parse_and_validate_find_action_result(actions_result.result)
                run_context.action_pool.add(actions_dict.get("actions", []))
                continue

            done: set[asyncio.Task[WorkflowOutput]]
            done, _ = await asyncio.wait(
                running_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # if a worker signals that their task is complete
            for task in done:
                running_tasks.remove(task)
                completed_action: Action = task_to_action.pop(task)
                actions_explored += 1

                states: WorkflowOutput = await task
                logger.info(f"[DeepSearchAgent] action result: %s", "***" if LogManager.is_sensitive() else states)

                state_result: dict[str, Any] = parse_and_validate_state_creation_result(states.result)
                run_context.total_input_tokens += state_result.get("total_input_tokens", 0)
                run_context.total_output_tokens += state_result.get("total_output_tokens", 0)

                result: Result | None = state_result.get("result")
                config = anonymize_config_for_logging(state_result.get("config", {}))
                run_context.fail_count += config.get("fail_count", 0)

                run_context.action_pool.record_completed(completed_action, result)

                if not isinstance(result, Result):
                    continue

                if result.found_answer:
                    if answer_mode_top_k <= 1:
                        run_context.final_answer = result.found_answer
                        logger.info(
                            f"[DeepSearchAgent] found final answer! %s",
                            "***" if LogManager.is_sensitive() else run_context.final_answer,
                        )
                        await self._cancel_running_tasks(run_context, running_tasks, task_to_action)
                        return _save_and_return_search_final_result(
                            SaveSearchFinalResultConfig(
                                question=run_context.query,
                                messages=result.messages,
                                prediction=result.found_answer,
                                gold_answer=run_context.gold_answer,
                                termination=Termination.ANSWER,
                                retrieved_evidence_ids=result.retrieved_evidence_ids,
                                params={
                                    "total_input_tokens": run_context.total_input_tokens,
                                    "total_output_tokens": run_context.total_output_tokens,
                                    "start_time": start_time,
                                    "log_dir": run_context.log_dir,
                                },
                                config=config,
                            )
                        )
                    else:
                        run_context.action_pool.record_successful_answer(completed_action, result)
                        collected = run_context.action_pool.successful_answer_count()
                        logger.info(
                            "[DeepSearchAgent] top-k mode: collected %d/%d answers%s",
                            collected,
                            answer_mode_top_k,
                            " ***" if LogManager.is_sensitive() else f" (latest: {result.found_answer})",
                        )
                        if collected >= answer_mode_top_k:
                            best_action, best_result = run_context.action_pool.get_best_answer()
                            run_context.final_answer = best_result.found_answer
                            logger.info(
                                "[DeepSearchAgent] top-k mode: returning best answer%s",
                                " ***" if LogManager.is_sensitive() else f": {run_context.final_answer}",
                            )
                            await self._cancel_running_tasks(run_context, running_tasks, task_to_action)
                            return _save_and_return_search_final_result(
                                SaveSearchFinalResultConfig(
                                    question=run_context.query,
                                    messages=best_result.messages,
                                    prediction=best_result.found_answer,
                                    gold_answer=run_context.gold_answer,
                                    termination=Termination.ANSWER,
                                    retrieved_evidence_ids=best_result.retrieved_evidence_ids,
                                    params={
                                        "total_input_tokens": run_context.total_input_tokens,
                                        "total_output_tokens": run_context.total_output_tokens,
                                        "start_time": start_time,
                                        "log_dir": run_context.log_dir,
                                    },
                                    config=config,
                                )
                            )
                        continue

                if result.new_states:
                    all_new_actions: list[Action] = []
                    for new_state in result.new_states:
                        new_actions_result: WorkflowOutput = await Runner.run_workflow(
                            workflow="find_action_1",
                            inputs={
                                **self._subworkflow_context_inputs(run_context, "find_action_workflow"),
                                "state": new_state,
                                "query": run_context.query,
                                "result": result,
                                "log_dir": run_context.log_dir,
                                "total_input_tokens": 0,
                                "total_output_tokens": 0,
                            },
                        )
                        new_actions_dict: dict[str, Any] = parse_and_validate_find_action_result(
                            new_actions_result.result
                        )
                        new_actions: list[Action] = new_actions_dict.get("actions", [])
                        all_new_actions.extend(new_actions)

                        run_context.total_input_tokens += new_actions_dict.get("total_input_tokens", 0)
                        run_context.total_output_tokens += new_actions_dict.get("total_output_tokens", 0)

                        logger.info(
                            f"[DeepSearchAgent] new actions: %s",
                            "***" if LogManager.is_sensitive() else [a.proposal.direction for a in new_actions],
                        )

                    run_context.action_pool.add(all_new_actions)

        await self._cancel_running_tasks(run_context, running_tasks, task_to_action)

        best_pair = run_context.action_pool.get_best_answer()
        if best_pair is not None:
            best_action, best_result = best_pair
            run_context.final_answer = best_result.found_answer
            effective_termination = (
                Termination.TIMEOUT_ANSWER if termination_reason == Termination.TIME_LIMIT else termination_reason
            )
            logger.info(
                "[DeepSearchAgent] %s: returning best collected answer%s",
                effective_termination.name,
                " ***" if LogManager.is_sensitive() else f": {run_context.final_answer}",
            )
            return _save_and_return_search_final_result(
                SaveSearchFinalResultConfig(
                    question=run_context.query,
                    messages=best_result.messages,
                    prediction=best_result.found_answer,
                    gold_answer=run_context.gold_answer,
                    termination=effective_termination,
                    retrieved_evidence_ids=best_result.retrieved_evidence_ids,
                    params={
                        "total_input_tokens": run_context.total_input_tokens,
                        "total_output_tokens": run_context.total_output_tokens,
                        "start_time": start_time,
                        "log_dir": run_context.log_dir,
                    },
                    config=config,
                )
            )

        if termination_reason == Termination.TIME_LIMIT and provide_best_guess:
            guess_triple = run_context.action_pool.get_best_guess()
            if guess_triple is not None:
                guess_action, guess_result, guess_candidate = guess_triple
                run_context.final_answer = guess_candidate
                logger.info(
                    "[DeepSearchAgent] TIMEOUT_GUESS: returning best-guess candidate%s",
                    " ***" if LogManager.is_sensitive() else f": {run_context.final_answer}",
                )
                return _save_and_return_search_final_result(
                    SaveSearchFinalResultConfig(
                        question=run_context.query,
                        messages=guess_result.messages if guess_result else [],
                        prediction=guess_candidate,
                        gold_answer=run_context.gold_answer,
                        termination=Termination.TIMEOUT_GUESS,
                        retrieved_evidence_ids=(guess_result.retrieved_evidence_ids if guess_result else []),
                        params={
                            "total_input_tokens": run_context.total_input_tokens,
                            "total_output_tokens": run_context.total_output_tokens,
                            "start_time": start_time,
                            "log_dir": run_context.log_dir,
                        },
                        config=config,
                    )
                )

        return _save_and_return_search_final_result(
            SaveSearchFinalResultConfig(
                question=run_context.query,
                messages=[],
                prediction=run_context.final_answer,
                gold_answer=run_context.gold_answer,
                termination=termination_reason,
                retrieved_evidence_ids=[],
                params={
                    "total_input_tokens": run_context.total_input_tokens,
                    "total_output_tokens": run_context.total_output_tokens,
                    "start_time": start_time,
                    "log_dir": run_context.log_dir,
                },
                config=config,
            )
        )

    async def run(
        self,
        message: str,
        conversation_id: str,
        agent_config: dict,
        report_template: str = "",
        interrupt_feedback: str = "",
    ) -> AsyncGenerator[str, None]:
        """Run DeepSearch with the same surface as ``BaseAgent.run``.

        Optional per-run fields may be supplied inside ``agent_config`` and are
        removed before ``AgentConfig`` validation: ``service_config`` (dict, e.g.
        ``{"search_workflow": ...}``) and ``gold_answer`` (str | None).
        """
        validate_run_agent_params(message, conversation_id, report_template, interrupt_feedback)

        if not isinstance(agent_config, dict):
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                    e="agent_config must be a dict",
                ),
            )
        # Shallow copy of the top-level mapping only; avoid ``copy.deepcopy`` on the raw dict
        # so nested values that are not copyable (e.g. locks) cannot break the run entry path.
        agent_config_for_model = dict(agent_config)
        service_config: Optional[dict] = agent_config_for_model.pop("service_config", None)
        gold_answer: str | None = agent_config_for_model.pop("gold_answer", None)

        validate_agent_required_field(agent_config_for_model)

        llm_token = None
        tool_token = None
        workflow_session_token = None
        run_context: DeepSearchRunContext | None = None
        try:
            session_agent_config = AgentConfig.model_validate(agent_config_for_model).model_copy(deep=True)
            logger.info(f"[DeepSearchAgent] agent_config: {session_agent_config}")

            try:
                search_config = SearchWorkflowConfig.model_validate(
                    (service_config or {}).get("search_workflow", {})
                )
            except Exception as e:
                logger.warning(
                    "[DeepSearchAgent] Invalid or missing search_workflow in service_config; "
                    "using default SearchWorkflowConfig. Error: %s",
                    "*" if LogManager.is_sensitive() else e,
                    exc_info=not LogManager.is_sensitive(),
                )
                search_config = SearchWorkflowConfig()

            per_question_params = session_agent_config.search_workflow_per_question_params
            logger.info(f"[DeepSearchAgent] per_question_params: {per_question_params}")

            llm_configs = session_agent_config.llm_config
            if LlmConfigCategory.GENERAL.value not in llm_configs:
                raise CustomValueException(
                    error_code=StatusCode.LLM_CONFIG_NONE.code, message=StatusCode.LLM_CONFIG_NONE.errmsg
                )

            all_llms = {}
            for _, llm_config in llm_configs.items():
                llm_obj = create_llm_obj(llm_config)
                all_llms[llm_config.model_name] = llm_obj
            general_cfg = llm_configs[LlmConfigCategory.GENERAL.value]
            init_llm_map = search_config.init_state_agent.llm_config
            init_llm = init_llm_map.get("general") if init_llm_map else None
            if init_llm is not None and init_llm.model_name and init_llm.model_name != general_cfg.model_name:
                base_llm = all_llms.get(general_cfg.model_name)
                if base_llm is not None:
                    all_llms[init_llm.model_name] = {
                        "model": base_llm["model"],
                        "model_name": init_llm.model_name,
                    }
                    logger.info(
                        "[DeepSearchAgent] registered extra LLM for init_state_agent: %s",
                        init_llm.model_name,
                    )

            llm_token = llm_context.set(all_llms)

            tool_class: list[Any] = []
            if per_question_params.tool_map == "search_fetch":
                tool_class.append(WebFetch({"jina_api_key": session_agent_config.jina_api_key}))
                tool_class.append(WebSearch({"serper_api_key": session_agent_config.serper_api_key}))
                zero_secret(session_agent_config.jina_api_key)
                zero_secret(session_agent_config.serper_api_key)
            elif per_question_params.tool_map == "retrieve":
                milvus_cfg = session_agent_config.search_workflow_milvus_config
                tool_class.append(_build_retrieve_tool(milvus_cfg))
                zero_secret(milvus_cfg.embedder_api_key)
            else:
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                        e=f"Invalid tool map: {per_question_params.tool_map}"
                    ),
                )

            tool_map = {tool.name: tool for tool in tool_class}
            tool_token = tool_context.set(tool_map)

            action_pool = ActionPool()
            log_dir = self.setup_log_directory(f"result_{conversation_id}", action_pool)
            run_context = self._create_run_context(
                agent_config=session_agent_config.model_copy(deep=True),
                search_config=search_config,
                per_question_params=per_question_params,
                query=message,
                log_dir=log_dir,
                time_limit=int(per_question_params.time_limit),
                gold_answer=gold_answer,
                tool_map=tool_map,
                action_pool=action_pool,
            )

            current_workflow_session_vars = dict(workflow_session_vars.get() or {})
            current_workflow_session_vars[WORKFLOW_EXECUTE_TIMEOUT_ENV_KEY] = str(run_context.time_limit)
            workflow_session_token = workflow_session_vars.set(current_workflow_session_vars)
            self._build_agent()

            result: SearchFinalResult = await self._run_internal(run_context)
            try:
                await run_context.action_pool.flush_snapshot()
            except Exception as e:
                logger.warning(
                    "[DeepSearchAgent] Failed to flush action pool snapshot before final result: %s",
                    e,
                    exc_info=not LogManager.is_sensitive(),
                )
            if hasattr(result, "model_dump"):
                yield json.dumps(to_json_safe(result.model_dump()), ensure_ascii=False)
            elif isinstance(result, dict):
                yield json.dumps(to_json_safe(result), ensure_ascii=False)
            else:
                yield json.dumps({"result": str(result)}, ensure_ascii=False)
        finally:
            if run_context is not None:
                try:
                    await run_context.action_pool.flush_snapshot()
                except Exception as e:
                    logger.warning(
                        "[DeepSearchAgent] Failed to flush action pool snapshot during cleanup: %s",
                        e,
                        exc_info=not LogManager.is_sensitive(),
                    )
            if workflow_session_token is not None:
                workflow_session_vars.reset(workflow_session_token)
            if llm_token is not None:
                llm_context.reset(llm_token)
            if tool_token is not None:
                tool_context.reset(tool_token)


class SimpleReactSearchAgent(BaseAgent):
    _cached_system_prompt: str | None = None

    @classmethod
    def _load_system_prompt(cls) -> str:
        if cls._cached_system_prompt is None:
            path = (
                Path(__file__).resolve().parents[3] / "algorithm" / "prompts" / "simple_react_search.md"
            )
            cls._cached_system_prompt = path.read_text(encoding="utf-8").strip()
        return cls._cached_system_prompt

    async def run(
        self,
        message: str,
        conversation_id: str,
        agent_config: dict,
        *,
        report_template: str = "",
        interrupt_feedback: str = "",
        service_config: Optional[dict] = None,
        gold_answer: str | None = None,
    ) -> AsyncGenerator[str, None]:
        validate_run_agent_params(
            message, conversation_id, report_template, interrupt_feedback
        )
        validate_agent_required_field(agent_config)
        _parsed_agent_cfg = AgentConfig.model_validate(agent_config)
        session_agent_config = _parsed_agent_cfg.model_copy(deep=True)
        try:
            search_config = SearchWorkflowConfig.model_validate(
                (service_config or {}).get("search_workflow", {})
            )
        except Exception:
            search_config = SearchWorkflowConfig()

        general = session_agent_config.llm_config.get("general")
        if general is None:
            raise CustomValueException(
                error_code=StatusCode.LLM_CONFIG_NONE.code,
                message=StatusCode.LLM_CONFIG_NONE.errmsg,
            )
        llm_registry = {general.model_name: create_llm_obj(general.model_copy(deep=True))}

        llm_token = llm_context.set(llm_registry)
        try:
            per_question_params: PerQuestionParams = (
                session_agent_config.search_workflow_per_question_params
            )
            if per_question_params.tool_map == "search_fetch":
                tool_class = [
                    WebFetch({"jina_api_key": session_agent_config.jina_api_key}),
                    WebSearch({"serper_api_key": session_agent_config.serper_api_key}),
                ]
                zero_secret(session_agent_config.jina_api_key)
                zero_secret(session_agent_config.serper_api_key)
            elif per_question_params.tool_map == "retrieve":
                milvus_cfg = session_agent_config.search_workflow_milvus_config
                tool_class = [_build_retrieve_tool(milvus_cfg)]
                zero_secret(milvus_cfg.embedder_api_key)
            else:
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                        e=f"Invalid tool map: {per_question_params.tool_map}"
                    ),
                )
            tool_map = {tool.name: tool for tool in tool_class}

            retrieval_only = per_question_params.tool_map == "retrieve"
            tools_list = get_tool_definitions(retrieval_tool_only=retrieval_only)

            sc_dict = to_dict_safe(search_config.state_creation_agent) or {}
            retrieval_settings = to_dict_safe(sc_dict.get("retrieval_settings") or {})
            tool_exec_config_base = dict(sc_dict)

            base_log_dir = LogManager.get_log_dir() or "./output/logs"
            log_dir = ensure_safe_directory(
                os.path.join(base_log_dir, f"result_{conversation_id}"),
                base_log_dir,
            )

            emit(
                "react_run_started",
                {
                    "conversation_id": conversation_id,
                    "tool_map": {k: v.__class__.__name__ for k, v in tool_map.items()},
                    "model_name": general.model_name,
                    "log_dir": "***" if LogManager.is_sensitive() else log_dir,
                },
                source="workflow.SimpleReactSearchAgent",
                action_id=None,
            )

            max_steps = 1000
            # Only pass fields read by _run_llm_via_ainvoke — never the full agent_config (API keys, URLs).
            llm_invoke_cfg = {
                "model_name": general.model_name,
                "max_tries": getattr(general, "max_tries", 4),
                "append_think_tags_to_messages": getattr(
                    general, "append_think_tags_to_messages", False
                ),
            }

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": self._load_system_prompt()},
                {"role": "user", "content": message},
            ]

            start_time = time.time()
            total_in_tok = 0
            total_out_tok = 0
            prediction: str | None = None
            new_found_evidence_ids: list[Any] = []

            for _step in range(max_steps):
                try:
                    raw, _reasoning, in_tok, out_tok = await _run_llm_via_ainvoke(
                        messages=messages,
                        config=llm_invoke_cfg,
                        agent_name=AgentLlmName.SIMPLE_REACT_SEARCH.value,
                        tools=tools_list,
                    )
                except Exception as e:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": f"Error: {e}",
                        }
                    )
                    break

                total_in_tok += int(in_tok or 0)
                total_out_tok += int(out_tok or 0)

                if isinstance(raw, dict):
                    resp_content = raw.get("content") or ""
                    tool_calls = raw.get("tool_calls") or []
                else:
                    resp_content = raw if isinstance(raw, str) else str(raw)
                    tool_calls = []

                n_tools = len(tool_calls) if isinstance(tool_calls, list) else 0
                tool_names: list[str] = []
                if isinstance(tool_calls, list) and not LogManager.is_sensitive():
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        nm = tc.get("name") or (
                            (tc.get("function") or {}).get("name") if isinstance(tc.get("function"), dict) else None
                        )
                        if nm:
                            tool_names.append(str(nm))
                emit(
                    "react_llm_turn",
                    {
                        "conversation_id": conversation_id,
                        "step": _step,
                        "tool_call_count": n_tools,
                        "tool_names": tool_names[:16],
                        "finished_after_turn": n_tools == 0,
                    },
                    source="workflow.SimpleReactSearchAgent",
                    action_id=None,
                )

                if not tool_calls:
                    prediction = (resp_content or "").strip() or None
                    messages.append(
                        {
                            "role": "assistant",
                            "content": resp_content or "",
                        }
                    )
                    break

                messages.append(
                    {
                        "role": "assistant",
                        "content": resp_content or "",
                        "tool_calls": tool_calls,
                    }
                )

                for tc in tool_calls:
                    parsed, err = _parse_one_native_tool_call(tc)
                    if err or not parsed:
                        raw_id = tc.get("id") if isinstance(tc, dict) else None
                        tid = raw_id or str(uuid.uuid4().hex[:24])
                        nm = (
                            (tc.get("name") if isinstance(tc, dict) else None)
                            or (tc.get("function") or {}).get("name")
                            if isinstance(tc, dict)
                            else None
                        ) or "unknown"
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tid,
                                "name": nm,
                                "content": f"Tool call error: {err or 'parse failed'}",
                            }
                        )
                        continue

                    tool_name = parsed["name"]
                    tool_args = dict(parsed["arguments"])
                    call_id = parsed.get("tool_call_id") or str(uuid.uuid4().hex[:24])
                    try:
                        tool_result, new_found_evidence_ids = await execute_tool(
                            ExecuteToolConfig(
                                tool_map=tool_map,
                                tool_name=tool_name,
                                tool_args=tool_args,
                                config=tool_exec_config_base,
                                retrieval_settings=retrieval_settings,
                                action={},
                                new_found_evidence_ids=new_found_evidence_ids,
                            )
                        )
                        content = format_tool_result_for_message(tool_result)
                    except CustomValueException as e:
                        content = str(e)
                    except Exception as e:
                        content = f"Tool execution error: {e}"

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": tool_name,
                            "content": content,
                        }
                    )


            emit_messages_updated(
                source="workflow.SimpleReactSearchAgent",
                messages=messages,
                action_id=None,
                extra={
                    "conversation_id": conversation_id,
                    "phase": "final",
                    "agent": "simple_react_search",
                },
            )

            result = _save_and_return_search_final_result(
                SaveSearchFinalResultConfig(
                    question=message,
                    messages=messages,
                    prediction=prediction or "No answer found",
                    gold_answer=gold_answer,
                    termination=Termination.ANSWER if prediction else Termination.FAIL_LIMIT,
                    retrieved_evidence_ids=new_found_evidence_ids,
                    params={
                        "total_input_tokens": total_in_tok,
                        "total_output_tokens": total_out_tok,
                        "start_time": start_time,
                        "log_dir": log_dir,
                    },
                    config={"agent": "simple_react_search"},
                )
            )
            yield json.dumps(to_json_safe(result.model_dump()), ensure_ascii=False)
        finally:
            llm_context.reset(llm_token)


def parse_endnode_content(chunk: CustomSchema) -> dict | None:
    """
    解析 EndNode 返回的content, 返回可能得exception_info
    仅处理 agent == NodeId.END.value 且content非 "ALL END" 的情况。
    Args:
        chunk (CustomSchema): 流式输出的chunk
    Returns:
        dict: 如果解析到异常信息，返回 {"exception_info": ...}，否则返回 空
    """
    if isinstance(chunk, CustomSchema):
        chunk = chunk.model_dump()
    elif isinstance(chunk, dict):
        chunk = chunk
    else:
        return {}
    if chunk.get("agent", None) != NodeId.END.value:
        return {}
    content = chunk.get("content", "")
    if not content or content == "ALL END" or content == "SECTION END":
        return {}

    try:
        parsed_result = json.loads(content)
        if isinstance(parsed_result, dict) and "exception_info" in parsed_result:
            return parsed_result
        return {}
    except json.JSONDecodeError:
        logger.debug("[DeepResearchAgent.run] EndNode returned non-JSON content.")
        return {}
    except Exception as parse_err:
        if not LogManager.is_sensitive():
            logger.warning(f"[DeepResearchAgent.run] Failed to parse endnode content: {parse_err}")
        else:
            logger.warning(f"[DeepResearchAgent.run] Failed to parse endnode content.")
        return {}
