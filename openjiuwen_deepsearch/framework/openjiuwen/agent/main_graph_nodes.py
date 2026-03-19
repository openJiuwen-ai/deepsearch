# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import logging
import uuid

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.flow.end_comp import End
from openjiuwen.core.workflow.components.flow.start_comp import Start

from openjiuwen_deepsearch.algorithm.query_understanding.interpreter import query_interpreter
from openjiuwen_deepsearch.algorithm.query_understanding.outliner import Outliner
from openjiuwen_deepsearch.algorithm.query_understanding.router import classify_query
from openjiuwen_deepsearch.algorithm.report.config import ReportStyle, ReportFormat
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.algorithm.source_trace.checker import postprocess_by_citation_checker, preprocess_info
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer import SourceTracerInfer
from openjiuwen_deepsearch.algorithm.user_feedback_processor.user_feedback_processor import (
    UserFeedbackProcessor,
)
from openjiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH, MAX_QUERY_LENGTH, \
    FINISH_TASK_FEEDBACK
from openjiuwen_deepsearch.common.exception import CustomException, CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import Config, WebSearchEngineConfig, LocalSearchEngineConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import SearchContext, Message, Outline, \
    OutlineInteraction, Report
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import adapt_llm_model_name
from openjiuwen_deepsearch.utils.common_utils.stream_utils import get_current_time, MessageType, StreamEvent, \
    custom_stream_output
from openjiuwen_deepsearch.utils.common_utils.text_utils import truncate_string
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context
from openjiuwen_deepsearch.utils.debug_utils.node_debug import add_debug_log_wrapper, NodeType, NodeDebugData
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


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
        search_context = SearchContext(
            query=inputs.get("query", ""),
            session_id=inputs.get("thread_id", ""),
            messages=[Message(role="user", content=inputs.get("query", ""))],
            search_mode=inputs.get("search_mode", "research"),
            report_template=inputs.get("report_template", "")
        )

        session.update_global_state({"search_context": search_context.model_dump()})

        origin_agent_config = inputs.get("agent_config", {})
        agent_config = dict()
        if origin_agent_config:
            agent_config["execute_mode"] = origin_agent_config.get("execute_mode", "commercial")
            agent_config["workflow_human_in_the_loop"] = origin_agent_config.get("workflow_human_in_the_loop", True)
            agent_config["outline_interaction_enabled"] = origin_agent_config.get("outline_interaction_enabled", True)
            agent_config["outline_interaction_max_rounds"] = origin_agent_config.get(
                "outline_interaction_max_rounds", 3)
            agent_config["outliner_max_section_num"] = origin_agent_config.get("outliner_max_section_num", 5)
            agent_config["source_tracer_research_trace_source_switch"] = origin_agent_config.get(
                "source_tracer_research_trace_source_switch", True)
            agent_config["source_tracer_infer_switch"] = origin_agent_config.get("source_tracer_infer_switch", True)
            agent_config["llm_config"] = origin_agent_config.get("llm_config", {})
            agent_config["info_collector_search_method"] = origin_agent_config.get(
                "info_collector_search_method", "web")
            agent_config["web_search_engine_config"] = WebSearchEngineConfig(search_engine_name=origin_agent_config.get(
                "web_search_engine_config", {}).get("search_engine_name", ""))
            agent_config["local_search_engine_config"] = LocalSearchEngineConfig(
                search_engine_name=origin_agent_config.get(
                    "local_search_engine_config", {}).get("search_engine_name", ""))
            agent_config["user_feedback_processor_enable"] = origin_agent_config.get(
                "user_feedback_processor_enable", False)
            agent_config["user_feedback_processor_max_interactions"] = origin_agent_config.get(
                "user_feedback_processor_max_interactions", 3)

        service_config = Config().service_config.model_dump()
        service_config["thread_id"] = inputs.get("thread_id", "")
        service_config["interrupt_feedback"] = inputs.get("interrupt_feedback", "")
        merge_config = agent_config | service_config
        session.update_global_state({
            "config": merge_config
        })


class EntryNode(BaseNode):

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info(f"[EntryNode] Start EntryNode.")

        messages = session.get_global_state("search_context.messages")
        llm_model_name = adapt_llm_model_name(session, NodeId.ENTRY.value)

        return dict(messages=messages, llm_model_name=llm_model_name)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        current_inputs = self._pre_handle(inputs, session, context)

        classify_query_output = await classify_query(current_inputs)

        result = self._post_handle(inputs, classify_query_output, session, context)
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        human_in_the_loop = session.get_global_state("config.workflow_human_in_the_loop")
        lang = algorithm_output.get("lang", "zh-CN").lower()
        llm_result = algorithm_output.get("llm_result", "")
        error_msg = algorithm_output.get("error_msg", "")

        if "zh" in lang or "chinese" in lang or "中文" in lang:
            lang = CHINESE
        if "en" in lang or "english" in lang or "英文" in lang:
            lang = ENGLISH

        # 更新session
        session.update_global_state({"search_context.language": lang})

        # 决定下一个节点
        next_node = NodeId.GENERATE_QUESTIONS.value if human_in_the_loop else NodeId.OUTLINE.value

        if error_msg:
            session.update_global_state({"search_context.final_result.response_content": llm_result})
            session.update_global_state({"search_context.final_result.exception_info": error_msg})
            next_node = NodeId.END.value

        # 添加EntryNode debug日志
        add_debug_log_wrapper(session, NodeDebugData(NodeId.ENTRY.value, 0,
                              NodeType.MAIN.value, output_content=str(algorithm_output)))
        logger.info(f"[EntryNode] End EntryNode.")
        return dict(language=lang,
                    human_in_the_loop=human_in_the_loop,
                    next_node=next_node)


class FeedbackHandlerNode(BaseNode):

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info(f"[FeedbackHandlerNode] Start FeedbackHandlerNode.")
        feedback_mode = session.get_global_state("config.workflow_feedback_mode")
        return dict(feedback_mode=feedback_mode)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        current_inputs = self._pre_handle(inputs, session, context)
        feedback_mode = current_inputs.get("feedback_mode", "cmd")

        user_feedback = await self._get_user_feedback(feedback_mode, session)
        standardized_feedback = truncate_string(user_feedback, max_length=MAX_QUERY_LENGTH)
        if not standardized_feedback:
            logger.error("[FeedbackHandlerNode] Invalid feedback, length or type is invalid")
            standardized_feedback = "Invalid feedback, length is 0 or type is invalid"

        algorithm_output = dict(user_feedback=standardized_feedback)
        result = self._post_handle(inputs, algorithm_output, session, context)
        return result

    async def _get_user_feedback(self, feedback_mode: str, session: Session) -> str:
        """获取用户反馈"""
        prompt = "\nEnter your feedback: "

        if feedback_mode == "cmd":
            return input(prompt)
        if feedback_mode == "web":
            # session.interact本质上是raise Exception的方式，FeedbackHandlerNode内不能使用try except
            user_input = await session.interact(prompt)
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
            exception_info = (f"[{StatusCode.FEEDBACK_HANDLER_INVALID_MODE_ERROR.code}]"
                              f"{StatusCode.FEEDBACK_HANDLER_INVALID_MODE_ERROR.errmsg}")
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            # 添加FeedbackHandlerNode debug日志
            add_debug_log_wrapper(session, NodeDebugData(NodeId.FEEDBACK_HANDLER.value, 0,
                                  NodeType.MAIN.value, output_content=str(exception_info).replace("\\n", "\n")))
            return dict(next_node=NodeId.END.value)
        if user_feedback == "Invalid feedback, length is 0 or type is invalid":
            exception_info = (f"[{StatusCode.FEEDBACK_HANDLER_INVALID_FEEDBACK_ERROR.code}]"
                              f"{StatusCode.FEEDBACK_HANDLER_INVALID_FEEDBACK_ERROR.errmsg}")
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            # 添加FeedbackHandlerNode debug日志
            add_debug_log_wrapper(session, NodeDebugData(NodeId.FEEDBACK_HANDLER.value, 0,
                                  NodeType.MAIN.value, output_content=str(exception_info).replace("\\n", "\n")))
            return dict(next_node=NodeId.END.value)
        if user_feedback == FINISH_TASK_FEEDBACK:
            logger.info(f"[FeedbackHandlerNode] user feedback is FINISH TASK, we will try to finish workflow.")
            # 这里是正常走到结束的，不需要填充exception_info
            return dict(next_node=NodeId.END.value)

        session.update_global_state({"search_context.user_feedback": user_feedback})

        add_debug_log_wrapper(session, NodeDebugData(NodeId.FEEDBACK_HANDLER.value, 0,
                              NodeType.MAIN.value, output_content=user_feedback))
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
            report_task = current_report.report_task if hasattr(current_report, 'report_task') else ""
            all_classified_contents = (
                current_report.all_classified_contents
                if hasattr(current_report, 'all_classified_contents')
                else []
            )
        llm_model_name = adapt_llm_model_name(session, NodeId.REPORTER.value)
        return dict(
            thread_id=session.get_global_state("config.thread_id") or "",
            report_style=session.get_global_state("config.report_style") or ReportStyle.SCHOLARLY.value,
            report_format=session.get_global_state("config.report_format") or ReportFormat.MARKDOWN,
            current_outline=session.get_global_state("search_context.current_outline"),
            all_classified_contents=all_classified_contents,
            current_report=current_report,
            language=session.get_global_state("search_context.language") or CHINESE,
            report_task=report_task,
            user_query=session.get_global_state("search_context.query"),
            llm_model_name=llm_model_name
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext):
        current_inputs = self._pre_handle(inputs, session, context)
        reporter = Reporter(current_inputs.get("llm_model_name"))
        success, report_str = await reporter.generate_report(current_inputs)
        algorithm_output = dict(success=success, report_str=report_str, report=current_inputs.get("report"),
                                all_classified_contents=current_inputs.get("all_classified_contents"))

        return self._post_handle(inputs, algorithm_output, session, context)

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        # generate fail
        if not algorithm_output.get("success"):
            current_report = session.get_global_state("search_context.current_report")
            if current_report:
                current_report.report_content = "error: " + algorithm_output.get("report_str")
                session.update_global_state({"search_context.current_report": current_report})
            logger.error("[ReporterNode] ReporterNode ended with fail.")
            exception_info = f"[{StatusCode.REPORT_GENERATE_ERROR.code}] {algorithm_output.get('report_str')}"
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            add_debug_log_wrapper(session, NodeDebugData(NodeId.REPORTER.value, 0,
                                  NodeType.MAIN.value, output_content=exception_info))
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
            "all_classified_contents": current_report.all_classified_contents if current_report else []
        }
        add_debug_log_wrapper(session, NodeDebugData(NodeId.REPORTER.value, 0, NodeType.MAIN.value,
                              output_content=str(debug_content).replace("\\n", "\n")))
        return dict(next_node=NodeId.SOURCE_TRACER.value)


class EndNode(End):
    """
    图结束节点
    """

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """ invoke 方法"""
        logger.info(f"[EndNode] Start EndNode.")
        final_result = session.get_global_state("search_context.final_result")
        logger.info(f"[EndNode] Get final result: {'***' if LogManager.is_sensitive() else final_result}")
        final_result_json = json.dumps(final_result, ensure_ascii=False)
        if final_result.get("exception_info", "") == "":
            await session.write_custom_stream({"message_id": str(uuid.uuid4()), "agent": NodeId.END.value,
                                               "content": final_result_json,
                                               "message_type": MessageType.MESSAGE_CHUNK.value,
                                               "event": StreamEvent.SUMMARY_RESPONSE.value,
                                               "created_time": get_current_time()})
        else:
            await session.write_custom_stream({"message_id": str(uuid.uuid4()), "agent": NodeId.END.value,
                                               "content": final_result_json,
                                               "message_type": MessageType.MESSAGE_CHUNK.value,
                                               "event": StreamEvent.ERROR.value,
                                               "created_time": get_current_time()})
        await session.write_custom_stream({"message_id": str(uuid.uuid4()), "agent": NodeId.END.value,
                                           "content": "ALL END",
                                           "message_type": MessageType.MESSAGE_CHUNK.value,
                                           "event": StreamEvent.SUMMARY_RESPONSE.value,
                                           "created_time": get_current_time()})
        # 添加End节点debug日志
        add_debug_log_wrapper(session, NodeDebugData(NodeId.END.value, 0,
                              NodeType.MAIN.value, output_content=final_result_json))
        logger.info(f"[EndNode] End EndNode.")

        return dict(final_result=final_result_json)


class GenerateQuestionsNode(BaseNode):

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        logger.info(f"[GenerateQuestionsNode] Start GenerateQuestionsNode.")
        language = session.get_global_state("search_context.language")
        query = session.get_global_state("search_context.query")
        max_gen_question_retry_num = session.get_global_state("config.workflow_max_gen_question_retry_num")
        llm_model_name = adapt_llm_model_name(session, NodeId.GENERATE_QUESTIONS.value)
        return dict(language=language, query=query, max_gen_question_retry_num=max_gen_question_retry_num,
                    llm_model_name=llm_model_name)

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
            msg = (f"[GenerateQuestionsNode] Generate questions failed, retry generating query interpretation "
                   f"({current_executed_num}/{max_gen_question_retry_num}) times.")
            if current_executed_num < max_gen_question_retry_num:
                logger.warning(msg)
            else:
                logger.error(msg)
        result = self._post_handle(inputs, algorithm_output, session, context)
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        if algorithm_output.get("exception_info"):
            exception_info = algorithm_output.get("exception_info")
            logger.error(f"[GenerateQuestionsNode] exception: {'*' if LogManager.is_sensitive() else exception_info}")
            session.update_global_state({"search_context.final_result.exception_info": exception_info})

            add_debug_log_wrapper(session, NodeDebugData(NodeId.GENERATE_QUESTIONS.value, 0,
                                  NodeType.MAIN.value,
                                  output_content=str(exception_info).replace("\\n", "\n")))
            return dict(next_node=NodeId.END.value)
        if not algorithm_output.get("result"):
            exception_info = "Query Interpreter result is empty."
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            logger.error(f"[GenerateQuestionsNode] {exception_info}")
            add_debug_log_wrapper(session, NodeDebugData(NodeId.GENERATE_QUESTIONS.value, 0,
                                  NodeType.MAIN.value,
                                  output_content=str(exception_info).replace("\\n", "\n")))
            return dict(next_node=NodeId.END.value)

        session.update_global_state({"search_context.questions": algorithm_output.get("result")})
        add_debug_log_wrapper(session, NodeDebugData(NodeId.GENERATE_QUESTIONS.value, 0,
                              NodeType.MAIN.value,
                              output_content=algorithm_output.get("result")))
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
        max_section_num = session.get_global_state("config.outliner_max_section_num")
        max_outline_retry_num = session.get_global_state("config.outliner_max_generate_outline_retry_num")
        llm_model_name = adapt_llm_model_name(session, NodeId.OUTLINE.value)
        report_template = session.get_global_state("search_context.report_template")
        outline_interactions = session.get_global_state("search_context.outline_interactions") or []
        outline_interaction_mode = ""
        previous_feedback_list = []
        current_interaction_feedback = ""
        outline_interactions = [
            OutlineInteraction(**i) if isinstance(i, dict) else i
            for i in outline_interactions
        ]

        if outline_interactions:
            last = outline_interactions[-1]
            outline_interaction_mode = last.interaction_mode
            current_interaction_feedback = last.feedback

            previous_feedback_list = [
                i.feedback
                for i in outline_interactions
                if i.interaction_mode == "revise_comment" and i.feedback
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

        return dict(
            messages=messages,
            user_feedback=user_feedback,
            questions=questions,
            language=language,
            max_section_num=max_section_num,
            max_outline_retry_num=max_outline_retry_num,
            llm_model_name=llm_model_name,
            report_template=report_template,
            outline_interaction_mode=outline_interaction_mode,
            current_outline=current_outline,
            outline_interaction_enabled=outline_interaction_enabled,
            previous_feedback=previous_feedback,
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_inputs = self._pre_handle(inputs, session, context)
        prompt_name = self._select_prompt_name(current_inputs)
        outliner = Outliner(llm_model_name=current_inputs.get("llm_model_name"), prompt_name=prompt_name)
        outliner.with_dep_driving = self.with_dep_driving
        max_outline_retry_num = current_inputs.get("max_outline_retry_num", 1)

        success_flag = False
        error_msg = ""
        outline_executed_num = 0
        algorithm_output = None
        while not success_flag:
            if outline_executed_num >= max_outline_retry_num:
                error_msg += f"{self.log_prefix} Reached max outline retry num: {max_outline_retry_num}"
                logger.error(error_msg)
                algorithm_output = {
                    "llm_result": "",
                    "current_outline": None,
                    "outline_executed_num": outline_executed_num,
                    "success_flag": False,
                    "error_msg": error_msg
                }
                break
            if outline_executed_num > 0:
                logger.warning(f"{self.log_prefix} Failed to generate Outline , retry generating outline for the "
                               f"{outline_executed_num}/{max_outline_retry_num} times.")
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
            if self.with_dep_driving:
                return "dep_driving_outliner_interaction"
            return "outliner_interaction"
        if outline_interaction_mode == "revise_outline":
            try:
                current_inputs["user_outline"] = Outline.model_validate_json(feedback)
            except Exception as e:
                logger.error(f"{self.log_prefix} Failed to parse user outline JSON: {e}")
            return "outliner_user_revised"
        return self.outline_prompt

    def _get_next_node_after_outline(self) -> str:
        """获取大纲生成成功后的下一个节点"""
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
                    f"{self.log_prefix} Outline generation failed in interaction mode, "
                    "fallback to previous outline."
                )
                algorithm_output["current_outline"] = current_outline
                algorithm_output["success_flag"] = True
                success_flag = True

        if success_flag:
            outline = algorithm_output.get("current_outline", None)
            session.update_global_state({'search_context.current_outline': outline})

            add_debug_log_wrapper(session, NodeDebugData(NodeId.OUTLINE.value, 0, NodeType.MAIN.value,
                                  output_content=str(outline).replace("\\n", "\n")))
            
            outline_interaction_enabled = session.get_global_state("config.outline_interaction_enabled")
            if outline_interaction_enabled:
                next_node = NodeId.OUTLINE_INTERACTION.value
                logger.info(f"{self.log_prefix} Outline generated, go to OutlineInteractionNode.")
            else:
                next_node = self._get_next_node_after_outline()
                logger.info(f"{self.log_prefix} Successfully generate outline, go to {next_node}.")
        else:
            next_node = NodeId.END.value
            error_msg = algorithm_output.get("error_msg")
            session.update_global_state({"search_context.final_result.exception_info": error_msg})

            add_debug_log_wrapper(session, NodeDebugData(NodeId.OUTLINE.value, 0, NodeType.MAIN.value,
                                  output_content=error_msg))
            logger.error(f"{self.log_prefix} Failed to generate outline, go to {next_node}.")
        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")

        return dict(next_node=next_node)


class DependencyOutlineNode(OutlineNode):
    def __init__(self):
        super().__init__()
        self.outline_prompt = "dep_driving_outliner"
        self.with_dep_driving = True

    def _get_next_node_after_outline(self) -> str:
        """依赖驱动模式下的下一个节点"""
        return NodeId.DEPENDENCY_REASONING_TEAM.value


class SourceTracerNode(BaseNode):
    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    async def build_citation_checker_result(citation_checker_info, datas, llm_model):
        """
        构建溯源校验结果
        """
        processed_report = citation_checker_info.get("response_content", {})
        citation_checker_result_str = await postprocess_by_citation_checker(
            processed_report, datas, llm_model)

        result_dict = dict(
            check_result=True,
            citation_checker_result_str=citation_checker_result_str
        )

        return result_dict

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info(f"[SourceTracerNode] Start SourceTracerNode.")
        search_mode = session.get_global_state("search_context.search_mode")
        current_report = session.get_global_state("search_context.current_report")
        # 从 Report 对象中获取内容
        report = getattr(current_report, "report_content", "") if current_report else ""
        merged_trace_source_datas = getattr(
            current_report, "merged_trace_source_datas", []) if current_report else []
        all_classified_contents = getattr(
            current_report, "all_classified_contents", []) if current_report else []
        language = session.get_global_state("search_context.language")

        research_trace_source_switch = session.get_global_state(
            "config.source_tracer_research_trace_source_switch")
        llm_model_name = adapt_llm_model_name(session, NodeId.SOURCE_TRACER.value)

        need_exit = False
        if (search_mode == "research") and (research_trace_source_switch is False):
            logger.info(
                f'[SourceTracerNode] research_trace_source_switch is False, skip trace source.')
            need_exit = True

        # 封装为本节点的Input对象
        return dict(need_exit=need_exit, search_mode=search_mode, report=report,
                    merged_trace_source_datas=merged_trace_source_datas,
                    all_classified_contents=all_classified_contents,
                    research_trace_source_switch=research_trace_source_switch,
                    language=language, llm_model_name=llm_model_name)

    def _skip_trace_source_handle(
            self, inputs: Input, session: Session,
            context: ModelContext, current_inputs: dict
    ) -> dict:
        """
        不需要溯源的场景直接跳到后处理
        """
        origin_report = current_inputs.get("report", "")
        search_mode = current_inputs.get("search_mode", "research")
        algorithm_output = dict(
            need_exit=True, origin_report=origin_report, search_mode=search_mode)
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
            citation_checker_info = preprocess_info(
                modified_report, datas, language)
            need_check = citation_checker_info.get("need_check", True)
            if need_check is False:
                return self._skip_trace_source_handle(inputs, session, context, current_inputs)

            # 溯源验证
            check_result_dict = await self.build_citation_checker_result(citation_checker_info, datas,
                                                                         current_inputs.get("llm_model_name", ""))
        except CustomException as e:
            # 溯源异常的情况下，设置check_result为False，让post_handle记录异常信息返回出去
            if LogManager.is_sensitive():
                logger.error(f'[SourceTracerNode] trace source failed.')
            else:
                logger.error(f'[SourceTracerNode] trace source failed. {str(e)}')
            check_result_dict = {"check_result": False,
                                 "citation_checker_result_str": str(e)}
        except Exception as e:
            if LogManager.is_sensitive():
                logger.error(f'[SourceTracerNode] trace source failed.')
            else:
                logger.error(f'[SourceTracerNode] trace source failed. {str(e)}')
            errmsg = StatusCode.SOURCE_TRACER_NODE_ERROR.errmsg.format(e=e)
            errmsg = f"[{StatusCode.SOURCE_TRACER_NODE_ERROR.code}] {errmsg}\t"
            check_result_dict = {"check_result": False,
                                 "citation_checker_result_str": errmsg}

        algorithm_output = {"check_result_dict": check_result_dict,
                            "origin_report": current_inputs.get("report", "")}
        result = self._post_handle(inputs, algorithm_output, session, context)

        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> dict:
        origin_report = algorithm_output.get("origin_report", "")
        need_exit = algorithm_output.get("need_exit", False)
        check_result_dict = algorithm_output.get("check_result_dict", {})
        citation_checker_result_str = check_result_dict.get("citation_checker_result_str", "")
        check_result = check_result_dict.get("check_result", False)
        if need_exit:
            source_tracer_result = json.dumps(
                {"checked_trace_source_report_content": origin_report, "citation_messages": {}}, ensure_ascii=False)
        else:
            if check_result is True:
                source_tracer_result = citation_checker_result_str
            else:
                source_tracer_result = json.dumps(
                    {"checked_trace_source_report_content": origin_report, "citation_messages": {}}, ensure_ascii=False)
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
            {"search_context.final_result.response_content": checked_trace_source_report_content})
        session.update_global_state({"search_context.final_result.citation_messages": citation_messages})
        # 添加SourceTracerNode debug日志
        add_debug_log_wrapper(session, NodeDebugData(NodeId.SOURCE_TRACER.value, 0, NodeType.MAIN.value,
                              output_content=str(source_tracer_result_dict).replace("\\n", "\n")))

        logger.info(f"[SourceTracerNode] End SourceTracerNode.")
        logger.info(f"[SourceTracerNode] source_tracer_result: "
                    f"{'*' if LogManager.is_sensitive() else source_tracer_result}")

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
            current_round=current_round
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        current_inputs = self._pre_handle(inputs, session, context)

        if not current_inputs.get("outline_interaction_enabled"):
            logger.info(f"{self.log_prefix} Outline interaction is disabled, skip to editor team.")
            return dict(next_node=NodeId.EDITOR_TEAM.value)

        max_rounds = current_inputs.get("max_rounds", 5)
        current_round = current_inputs.get("current_round", 0)

        if current_round >= max_rounds:
            logger.info(f"{self.log_prefix} Reached max rounds: {max_rounds}")
            await self._notify_user(session, "Maximum interaction rounds reached.", StreamEvent.USER_INPUT_ENDED)
            return dict(next_node=NodeId.EDITOR_TEAM.value)

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
            feedback=feedback,
            interaction_mode=interaction_mode,
            outline_before=current_outline
        )
        outline_interactions.append(new_interaction)
        session.update_global_state({
            "search_context.outline_interactions": outline_interactions
        })

    async def _notify_user(self, session: Session, message: str, event: StreamEvent):
        """通知用户"""
        await session.write_custom_stream({
            "message_id": str(uuid.uuid4()),
            "agent": NodeId.OUTLINE_INTERACTION.value,
            "content": message,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": event.value,
            "created_time": get_current_time()
        })

    async def _get_user_input(self, feedback_mode: str, message: str, session: Session) -> dict:
        """获取用户输入"""
        prompt = f"Round {message}: waiting for user feedback."

        if feedback_mode == "web":
            user_input = await session.interact(prompt)
        else:
            user_input = input(prompt)
        try:
            logger.info(f"{self.log_prefix} Received user input: {'***' if LogManager.is_sensitive() else user_input}")
            return json.loads(user_input)
        except json.JSONDecodeError:
            exception_info = (f"[{StatusCode.FEEDBACK_HANDLER_INVALID_MODE_ERROR.code}]"
                              f"{StatusCode.FEEDBACK_HANDLER_INVALID_MODE_ERROR.errmsg}")
            session.update_global_state({"search_context.final_result.exception_info": exception_info})
            # 添加FeedbackHandlerNode debug日志
            add_debug_log_wrapper(session, NodeDebugData(NodeId.OUTLINE_INTERACTION.value, 0,
                                                         NodeType.MAIN.value,
                                                         output_content=str(exception_info).replace("\\n", "\n")))
            return {}

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        action = algorithm_output.get("interrupt_feedback", "")
        feedback = algorithm_output.get("feedback", "")

        if action == "accepted":
            logger.info(f"{self.log_prefix} User accepted the outline")
            next_node = NodeId.EDITOR_TEAM.value
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

        add_debug_log_wrapper(session, NodeDebugData(NodeId.OUTLINE_INTERACTION.value, 0,
                              NodeType.MAIN.value, output_content=str(algorithm_output).replace("\\n", "\n")))
        logger.info(f"{self.log_prefix} End OutlineInteractionNode.")
        return next_node


class DependencyOutlineInteractionNode(OutlineInteractionNode):
    def __init__(self):
        super().__init__()
        self.log_prefix = "[DependencyOutlineInteractionNode]"

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        result = await super()._do_invoke(inputs, session, context)
        if result.get("next_node") == NodeId.EDITOR_TEAM.value:
            result["next_node"] = NodeId.DEPENDENCY_REASONING_TEAM.value
        return result
    

class SourceTracerInferNode(BaseNode):
    def __init__(self) -> None:
        super().__init__()
        self.log_prefix = '[SourceTracerInferNode]'

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info(f"{self.log_prefix} Start SourceTracerInferNode.")
        search_mode = session.get_global_state("search_context.search_mode")
        llm_model_name = adapt_llm_model_name(session, NodeId.SOURCE_TRACER_INFER.value)
        source_tracer_infer_switch = session.get_global_state(
            "config.source_tracer_infer_switch")

        language = session.get_global_state("search_context.language")
        current_report = session.get_global_state("search_context.current_report")
        source_tracer_response = getattr(
            current_report, "checked_trace_source_report_content", "") if current_report else ""
        all_classified_contents = getattr(
            current_report, "all_classified_contents", []) if current_report else []

        # 封装本节点的Input对象
        return dict(source_tracer_infer_switch=source_tracer_infer_switch,
                    search_mode=search_mode,
                    llm_model_name=llm_model_name, language=language,
                    source_tracer_response=source_tracer_response,
                    all_classified_contents=all_classified_contents)

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

        source_tracer_infer_result_dict = dict(response=response,
                                               infer_messages=infer_messages,
                                               scores=scores)

        session.update_global_state({"search_context.final_result.response_content": response,
                                     "search_context.final_result.infer_messages": infer_messages})

        if error_msg:
            session.update_global_state({"search_context.final_result.exception_info": error_msg})

        # 添加SourceTracerInferNode debug日志
        add_debug_log_wrapper(session, NodeDebugData(NodeId.SOURCE_TRACER.value, 0, NodeType.MAIN.value,
                              output_content=str(source_tracer_infer_result_dict).replace("\\n", "\n")))

        logger.info(f"{self.log_prefix} End SourceTracerInferNode.")
        logger.info(f"{self.log_prefix} source_tracer_infer_result:"
                    f"{'*' if LogManager.is_sensitive() else source_tracer_infer_result_dict}")

        return dict(next_node=NodeId.END.value)

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

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext):

        scores = [(0, 0)]
        try:
            current_inputs = self._pre_handle(inputs, session, context)

            source_tracer_infer_switch = current_inputs.get("source_tracer_infer_switch", False)
            if not source_tracer_infer_switch:
                algorithm_output = dict(source_tracer_infer_switch=source_tracer_infer_switch,
                                        response=current_inputs.get("source_tracer_response", ""))
                return self._post_handle(inputs, algorithm_output, session, context)

            # 溯源推理
            infer_result_dict = await self.build_source_tracer_infer_result(current_inputs)

            # 溯源推理校验
            check_infos = infer_result_dict.get("check_infos", {})
            check_infos["llm_model_name"] = current_inputs.get("llm_model_name", "")
            check_infos["language"] = current_inputs.get("language", "zh")

            # 这里添加溯源推理校验模块
            infer_result_dict["scores"] = scores
            infer_result_dict["source_tracer_infer_switch"] = current_inputs.get("source_tracer_infer_switch", False)
            infer_result_dict["infer_success"] = True

        except Exception as e:
            error_msg = f"source_tracer_infer failed."
            if LogManager.is_sensitive():
                logger.error(f"{self.log_prefix} {error_msg}")
            else:
                logger.error(f"{self.log_prefix} {error_msg} {e}")
            errcode = StatusCode.SOURCE_TRACER_INFER_ERROR.code
            errmsg = StatusCode.SOURCE_TRACER_INFER_ERROR.errmsg.format(e=e)
            infer_result_dict = dict(infer_success=False, response=current_inputs.get("source_tracer_response", ""),
                                     infer_messages=[], scores=[(0, 0)], error_msg=f"[{errcode}] {errmsg}", 
                                     source_tracer_infer_switch=current_inputs.get("source_tracer_infer_switch", False)
                                     )

        algorithm_output = infer_result_dict
        result = self._post_handle(inputs, algorithm_output, session, context)
        return result


class UserFeedbackProcessorNode(BaseNode):
    """在报告生成完成后，处理用户对局部文本的迭代改写请求。"""

    def __init__(self):
        super().__init__()

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info("[UserFeedbackProcessorNode] Start UserFeedbackProcessorNode.")
        enable = session.get_global_state("config.user_feedback_processor_enable")
        if not enable:
            return dict(disabled=True)

        return dict(
            disabled=False,
            max_interactions=session.get_global_state("config.user_feedback_processor_max_interactions"),
            max_text_length=session.get_global_state("config.user_feedback_processor_max_text_length"),
            feedback_mode=session.get_global_state("config.workflow_feedback_mode"),
            interaction_count=session.get_global_state("search_context.feedback_interaction_count") or 0,
            language=session.get_global_state("search_context.language"),
            final_result=session.get_global_state("search_context.final_result"),
            llm_model_name=adapt_llm_model_name(session, NodeId.USER_FEEDBACK_PROCESSOR.value),
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        current_inputs = self._pre_handle(inputs, session, context)

        # 确定 algorithm_output，包含 next_node 以供 _post_handle 路由
        algorithm_output = await self._build_algorithm_output(current_inputs, session)

        return self._post_handle(inputs, algorithm_output, session, context)

    async def _build_algorithm_output(self, current_inputs: dict, session: Session) -> dict:
        """执行业务逻辑，并返回包含路由信息的节点输出。"""
        if current_inputs.get("disabled"):
            logger.info("[UserFeedbackProcessorNode] Feature disabled, routing to EndNode.")
            return dict(next_node=NodeId.END.value)

        interaction_count = current_inputs["interaction_count"]
        max_interactions = current_inputs["max_interactions"]
        if interaction_count >= max_interactions:
            logger.info(f"[UserFeedbackProcessorNode] Max interactions reached: {max_interactions}")
            await self._notify_user(session, "Maximum interaction rounds reached.", StreamEvent.USER_INPUT_ENDED)
            return dict(next_node=NodeId.END.value)

        final_result = current_inputs["final_result"]

        # 首次进入用户反馈阶段时，先把当前完整报告推给前端。
        if interaction_count == 0:
            if final_result:
                final_result_json = json.dumps(final_result, ensure_ascii=False)
                await custom_stream_output(session, str(uuid.uuid4()), final_result_json,
                NodeId.USER_FEEDBACK_PROCESSOR.value)
            else:
                logger.error("[UserFeedbackProcessorNode] Final result not found")
                return dict(next_node=NodeId.END.value)

        report_content = final_result.get("response_content", "") or ""

        raw_feedback = await self._get_user_feedback(current_inputs["feedback_mode"], session)
        try:
            feedback = UserFeedbackProcessor.parse_feedback(raw_feedback)
            UserFeedbackProcessor.validate(feedback, report_content, current_inputs["max_text_length"])

            action = feedback.get("action", "")
            if action == "finish":
                logger.info("[UserFeedbackProcessorNode] User finished feedback, routing to EndNode.")
                await self._notify_user(session, "User feedback finished.", StreamEvent.USER_INPUT_ENDED)
                return dict(next_node=NodeId.END.value)

            processor = UserFeedbackProcessor(current_inputs["llm_model_name"])
            action_result = await processor.execute(
                feedback=feedback,
                final_result=final_result,
                language=current_inputs["language"],
            )
        except CustomException as e:
            logger.warning(f"[UserFeedbackProcessorNode] User feedback failed: {e}")
            await UserFeedbackProcessor.send_error(session, e)
            return dict(
                next_node=NodeId.USER_FEEDBACK_PROCESSOR.value,
                interaction_count=interaction_count,
                exception_info=str(e),
            )
        except Exception as e:
            logger.error(f"[UserFeedbackProcessorNode] Action failed: {e}")
            wrapped_error = CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(e=str(e)),
            )
            await UserFeedbackProcessor.send_error(session, wrapped_error)
            return dict(
                next_node=NodeId.USER_FEEDBACK_PROCESSOR.value,
                interaction_count=interaction_count,
                exception_info=str(wrapped_error),
            )

        stream_result = UserFeedbackProcessor.build_stream_result(feedback, action_result)
        updated_final_result = dict(final_result or {})
        updated_final_result.update({
            "response_content": action_result["new_report"],
            "citation_messages": action_result["updated_citation_messages"],
            "infer_messages": action_result["updated_infer_messages"],
        })
        await UserFeedbackProcessor.send_result(
            session=session,
            feedback=feedback,
            result=stream_result,
            final_result=updated_final_result,
        )

        return dict(
            next_node=NodeId.USER_FEEDBACK_PROCESSOR.value,
            interaction_count=interaction_count,
            feedback=feedback,
            **action_result,
        )

    async def _get_user_feedback(self, feedback_mode: str, session: Session) -> str:
        """按交互模式获取原始用户反馈。"""
        prompt = "\nProvide your feedback: "
        if feedback_mode == "cmd":
            return input(prompt)
        if feedback_mode == "web":
            return await session.interact(prompt)
        logger.error(f"[UserFeedbackProcessorNode] Invalid feedback_mode: {feedback_mode}")
        return ""

    async def _notify_user(self, session: Session, message: str, event: StreamEvent):
        await session.write_custom_stream({
            "message_id": str(uuid.uuid4()),
            "agent": NodeId.USER_FEEDBACK_PROCESSOR.value,
            "content": message,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": event.value,
            "created_time": get_current_time()
        })

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session,
                     context: ModelContext) -> dict:
        next_node = algorithm_output["next_node"]
        interaction_count = algorithm_output.get("interaction_count")
        if next_node == NodeId.USER_FEEDBACK_PROCESSOR.value and interaction_count is not None:
            session.update_global_state({"search_context.feedback_interaction_count": interaction_count + 1})

        exception_info = algorithm_output.get("exception_info")
        if exception_info is not None:
            session.update_global_state({"search_context.final_result.exception_info": exception_info})

        # 非改写成功路径（disabled / finish / error）不需要更新报告状态，直接按 next_node 路由。
        if "new_report" not in algorithm_output:
            return dict(next_node=next_node)

        new_report = algorithm_output["new_report"]
        rewritten_text = algorithm_output["rewritten_text"]
        rewritten_start_offset = algorithm_output["start_offset"]
        rewritten_end_offset = algorithm_output["new_end_offset"]
        updated_citation_messages = algorithm_output["updated_citation_messages"]
        updated_infer_messages = algorithm_output.get("updated_infer_messages")
        feedback = algorithm_output["feedback"]
        selected_text_clean = algorithm_output.get("original_text_clean", feedback.get("selected_text"))

        session.update_global_state({"search_context.final_result.response_content": new_report})
        session.update_global_state({"search_context.final_result.citation_messages": updated_citation_messages})
        session.update_global_state({"search_context.final_result.infer_messages": updated_infer_messages})

        # 记录每次局部改写的关键信息，便于问题排查和后续审计。
        history = session.get_global_state("search_context.rewrite_history") or []
        history.append({
            "action": feedback.get("action"),
            "selected_text": feedback.get("selected_text"),
            "selected_text_clean": selected_text_clean,
            "original_start_offset": feedback.get("start_offset"),
            "original_end_offset": feedback.get("end_offset"),
            "rewritten_text": rewritten_text,
            "rewritten_start_offset": rewritten_start_offset,
            "rewritten_end_offset": rewritten_end_offset,
            "user_instruction": feedback.get("user_instruction", ""),
        })
        session.update_global_state({"search_context.rewrite_history": history})

        add_debug_log_wrapper(session, NodeDebugData(
            NodeId.USER_FEEDBACK_PROCESSOR.value, 0, NodeType.MAIN.value,
            output_content=json.dumps({"start_offset": rewritten_start_offset, "end_offset": rewritten_end_offset},
                                      ensure_ascii=False)
        ))

        logger.info("[UserFeedbackProcessorNode] Rewrite completed, loop back for next interaction.")
        return dict(next_node=next_node)
