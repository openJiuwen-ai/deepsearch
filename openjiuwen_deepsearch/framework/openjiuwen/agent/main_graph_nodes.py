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
from openjiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH, MAX_QUERY_LENGTH, \
    FINISH_TASK_FEEDBACK
from openjiuwen_deepsearch.common.exception import CustomException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import Config, WebSearchEngineConfig, LocalSearchEngineConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import SearchContext, Message, Outline
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
            agent_config["outliner_max_section_num"] = origin_agent_config.get("outliner_max_section_num", 5)
            agent_config["source_tracer_research_trace_source_switch"] = origin_agent_config.get(
                "source_tracer_research_trace_source_switch", True)
            agent_config["llm_config"] = origin_agent_config.get("llm_config", {})
            agent_config["info_collector_search_method"] = origin_agent_config.get(
                "info_collector_search_method", "web")
            agent_config["web_search_engine_config"] = WebSearchEngineConfig(search_engine_name=origin_agent_config.get(
                "web_search_engine_config", {}).get("search_engine_name", ""))
            agent_config["local_search_engine_config"] = LocalSearchEngineConfig(
                search_engine_name=origin_agent_config.get(
                    "local_search_engine_config", {}).get("search_engine_name", ""))

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
            return await session.interact(prompt)
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

        return dict(
            messages=messages,
            user_feedback=user_feedback,
            questions=questions,
            language=language,
            max_section_num=max_section_num,
            max_outline_retry_num=max_outline_retry_num,
            llm_model_name=llm_model_name,
            report_template=report_template
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_inputs = self._pre_handle(inputs, session, context)
        report_template = current_inputs.get("report_template", "")
        if report_template:
            outliner = Outliner(llm_model_name=current_inputs.get("llm_model_name"), prompt_name="outliner_template")
        else:
            outliner = Outliner(llm_model_name=current_inputs.get("llm_model_name"), prompt_name="outliner")
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

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        if algorithm_output.get("success_flag"):
            next_node = NodeId.EDITOR_TEAM.value
            outline = algorithm_output.get("current_outline", None)
            session.update_global_state({'search_context.current_outline': outline})

            add_debug_log_wrapper(session, NodeDebugData(NodeId.OUTLINE.value, 0, NodeType.MAIN.value,
                                  output_content=str(outline).replace("\\n", "\n")))
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

    def _post_handle(self, inputs: Input, algorithm_output: object, session: Session, context: ModelContext):
        return dict(next_node=NodeId.DEPENDENCY_REASONING_TEAM.value)


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

        return dict(next_node=NodeId.END.value)
