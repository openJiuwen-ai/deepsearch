# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import uuid
from typing import Type

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.base import CONFIG_KEY, INPUTS_KEY
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.flow.end_comp import End
from openjiuwen.core.workflow.components.flow.start_comp import Start
from openjiuwen.core.workflow.components.flow.workflow_comp import SUB_WORKFLOW_COMPONENT
from openjiuwen.core.workflow.workflow import Workflow

from openjiuwen_deepsearch.algorithm.query_understanding.planner import Planner, PlannerConfig
from openjiuwen_deepsearch.algorithm.report.config import ReportStyle, ReportFormat
from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.algorithm.source_trace.source_tracer import SourceTracer
from openjiuwen_deepsearch.common.common_constants import CHINESE
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode, init_router
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder import \
    build_info_collector_sub_graph
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.section_context import SectionContext
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Message, StepType, Step, SubReportContent, \
    Plan
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import adapt_llm_model_name
from openjiuwen_deepsearch.utils.common_utils.llm_utils import messages_to_json
from openjiuwen_deepsearch.utils.common_utils.stream_utils import custom_stream_output
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context
from openjiuwen_deepsearch.utils.debug_utils.node_debug import add_debug_log_wrapper, NodeType, NodeDebugData
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


class SectionStartNode(Start):
    """
    起始节点，初始化inputs到子图session的section_state中
    """

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """执行起始节点"""
        section_idx = inputs.get("section_idx", '1')
        self.log_prefix = f"section_idx: {section_idx} | [{self.__class__.__name__}] "
        logger.info(f"{self.log_prefix} Start {self.__class__.__name__}.")

        # 初始化section_context
        section_context = SectionContext(
            language=inputs.get("language", "zh-CN"),
            messages=inputs.get("messages", []),
            section_idx=inputs.get("section_idx", '1'),
            current_outline=inputs.get("current_outline", ""),
            report_task=inputs.get("report_task"),
            section_task=inputs.get("section_task", ""),
            section_description=inputs.get("section_description", ""),
            section_iscore=inputs.get("section_iscore", False),
            report_template=inputs.get("report_template", ""),
        )
        config = inputs.get("config")
        session.update_global_state({"section_context": section_context.model_dump(),
                                     "config": config})

        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")

        return inputs


class BasePlanReasoningNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.prompt: str = "planner"
        self.planner_class: Type[Planner] = Planner  # 默认planner类
        self.log_prefix = ""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        # 获取搜索规划在运行上下文中所需信息
        section_idx = session.get_global_state("section_context.section_idx") or 1
        self.log_prefix = f"section_idx: {section_idx} | [{self.__class__.__name__}] "
        logger.info(f"{self.log_prefix} | Start {self.__class__.__name__}")
        language = session.get_global_state("section_context.language")
        messages = session.get_global_state("section_context.messages")
        plan_executed_num = session.get_global_state("section_context.plan_executed_num")
        collected_doc_num = session.get_global_state("section_context.collected_doc_num")
        warning_infos = session.get_global_state("section_context.warning_infos")
        exception_infos = session.get_global_state("section_context.exception_infos")
        llm_model_name = adapt_llm_model_name(session, NodeId.PLAN_REASONING.value)

        max_step_num = session.get_global_state("config.planner_max_step_num")
        max_retry_num = session.get_global_state("config.planner_max_retry_num")
        max_plan_executed_num = session.get_global_state("config.workflow_max_plan_executed_num")

        # 封装入参
        return dict(
            section_idx=section_idx,
            language=language,
            messages=messages,
            plan_executed_num=plan_executed_num,
            max_step_num=max_step_num,
            max_retry_num=max_retry_num,
            max_plan_executed_num=max_plan_executed_num,
            collected_doc_num=collected_doc_num,
            warning_infos=warning_infos,
            exception_infos=exception_infos,
            agent_name=NodeId.PLAN_REASONING.value,
            llm_model_name=llm_model_name,
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        current_input = self._pre_handle(inputs, session, context)
        logger.info(f"{self.log_prefix}current input: {'*' if LogManager.is_sensitive() else current_input}")

        # 最大plan次数
        max_plan_executed_num = current_input.get("max_plan_executed_num", 3)
        # 已经执行次数
        plan_executed_num = current_input.get("plan_executed_num", 0)
        logger.info(f"{self.log_prefix}current plan executed num = {plan_executed_num}")
        # Section收集的信息数量
        collected_doc_num = current_input.get("collected_doc_num", 0)

        # 达到最大次数
        if plan_executed_num >= max_plan_executed_num:
            limited_msg = f"Plan reasoning reached the max_plan_executed_num = {max_plan_executed_num} set."
            if self.prompt == "dep_driving_planner":
                return dict(next_node=NodeId.END.value)
            if collected_doc_num > 0:
                # 已收集到信息，跳转子报告撰写
                next_node = NodeId.SUB_REPORTER.value
                logger.info(f"{self.log_prefix} {limited_msg} "
                            f"Section have collected doc_infos_num = {collected_doc_num} infos, go to {next_node}.")
                logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            else:
                # 未收集到信息，跳转到End结束工作流
                next_node = NodeId.END.value
                error_msg = (f"[{StatusCode.SECTION_INFOS_EMPTY.code}] "
                             f"{self.log_prefix} {limited_msg} {StatusCode.SECTION_INFOS_EMPTY.errmsg}")
                _handle_warning_exception_info(session, added_warning=error_msg, added_exception=error_msg)
                logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=next_node)

        # 初始化planner（动态选择Planner）
        planner = self.planner_class(
            PlannerConfig(
                prompt=self.prompt,
                max_retry_num=current_input.get("max_retry_num", 2),
                llm_model_name=current_input.get("llm_model_name")
            )
        )

        # 执行planner
        planner_result = await planner.generate_plan(current_input)

        # 手动流式输出plan结果
        if planner_result.plan_success:
            plan = planner_result.plan
            stream_meta = {"plan_idx": str(plan_executed_num + 1)}
            await custom_stream_output(session, str(uuid.uuid4()), plan.model_dump_json(), NodeId.PLAN_REASONING.value,
                                       stream_meta)

        # 封装算法结果
        algorithm_output = dict(
            success=planner_result.plan_success,
            plan=planner_result.plan,
            response_messages=planner_result.response_messages,
            error_msg=planner_result.error_msg,
            extra_body=planner_result.extra_body,
        )

        result = self._post_handle(inputs, algorithm_output, session, context)
        logger.info(f"{self.log_prefix}End {self.__class__.__name__}.")
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        plan_executed_num = session.get_global_state("section_context.plan_executed_num") + 1
        messages = session.get_global_state("section_context.messages")
        plan = algorithm_output.get("plan")
        plan_success = algorithm_output.get("success", False)
        response_messages = algorithm_output.get("response_messages")
        error_detail = algorithm_output.get('error_msg')

        log_prefix = f"{self.log_prefix} plan_executed_num = {plan_executed_num}"
        if plan_success and plan:
            plan.id = f"{plan_executed_num}"
            if not plan.is_research_completed:
                next_node = NodeId.INFO_COLLECTOR.value
                logger.info(
                    f"{log_prefix} Research not completed, go to {next_node}")
            else:
                next_node = NodeId.END.value
                logger.info(
                    f"{log_prefix} Research completed, go to {next_node}")
        else:
            error_msg = (f"[{StatusCode.PLANNER_GENERATE_ERROR.code}] {log_prefix} "
                         f"{StatusCode.PLANNER_GENERATE_ERROR.errmsg.format(e=error_detail)}")
            _handle_warning_exception_info(session, added_warning=error_msg, added_exception=error_msg)
            next_node = NodeId.END.value

        # 运行上下文中添加规划结果
        session.update_global_state({
            "section_context.current_plan": plan,
            "section_context.messages": messages + response_messages,
            "section_context.plan_executed_num": plan_executed_num,
        })
        logger.info(f"{log_prefix} End {self.__class__.__name__}.")

        return dict(next_node=next_node)


class ResearchPlanReasoningNode(BasePlanReasoningNode):

    def __init__(self):
        super().__init__()
        self.prompt = "planner"
        self.log_prefix = ""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        current_inputs = super()._pre_handle(inputs, session, context)
        section_idx = session.get_global_state("section_context.section_idx") or 0
        current_inputs["section_idx"] = section_idx
        self.log_prefix = f"section_idx: {section_idx} | [{self.__class__.__name__}] "
        return current_inputs

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        result = await super()._do_invoke(inputs, session, context)
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        plan_executed_num = session.get_global_state("section_context.plan_executed_num") + 1
        collected_doc_num = session.get_global_state("section_context.collected_doc_num")
        messages = session.get_global_state("section_context.messages")
        plan = algorithm_output.get("plan")
        response_messages = algorithm_output.get("response_messages")
        plan_success = algorithm_output.get("success", False)

        log_prefix = f"{self.log_prefix} plan_executed_num = {plan_executed_num}"
        # 1. plan生成成功时
        if plan_success and plan:
            plan.id = f"{plan_executed_num}"
            debug_info = messages_to_json(response_messages)
            if not plan.is_research_completed:
                # 信息不充足
                next_node = NodeId.INFO_COLLECTOR.value
                logger.info(
                    f"{log_prefix} Research not completed, go to {next_node}")
            else:
                # 信息充足
                next_node = NodeId.SUB_REPORTER.value
                logger.info(
                    f"{log_prefix} Research completed, go to {next_node}")
        # 2. plan生成失败时
        else:
            debug_info = algorithm_output.get("error_msg")
            failed_info = (f"[{StatusCode.PLANNER_GENERATE_ERROR.code}] {log_prefix} "
                           f"{StatusCode.PLANNER_GENERATE_ERROR.errmsg.format(e=debug_info)}")
            if collected_doc_num > 0:
                # 已经收集到信息
                _handle_warning_exception_info(session, added_warning=failed_info)
                next_node = NodeId.SUB_REPORTER.value
                logger.info(f"{log_prefix} Section have collected {collected_doc_num} infos, go to {next_node}")
            else:
                # 未收集到信息
                _handle_warning_exception_info(session, added_warning=failed_info, added_exception=failed_info)
                error_msg = (f"[{StatusCode.SECTION_INFOS_EMPTY.code}] {log_prefix} "
                             f"{StatusCode.SECTION_INFOS_EMPTY.errmsg}")
                _handle_warning_exception_info(session, added_warning=error_msg, added_exception=error_msg)
                next_node = NodeId.END.value

        # 添加ResearchPlanReasoningNode debug日志
        add_debug_log_wrapper(session, NodeDebugData(NodeId.PLAN_REASONING.value, 0, NodeType.SUB.value,
                              output_content=debug_info))

        # 运行上下文中添加规划结果
        session.update_global_state({
            "section_context.current_plan": plan,
            "section_context.messages": messages + response_messages,
            "section_context.plan_executed_num": plan_executed_num
        })

        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
        return dict(next_node=next_node)


class SubReporterNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.log_prefix = ""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state("section_context.section_idx") or "1"
        self.log_prefix = f"section_idx: {section_idx} | [{self.__class__.__name__}] "
        logger.info(f"{self.log_prefix} Start [{self.__class__.__name__}].")

        classify_doc_infos_single_time_num = session.get_global_state(
            "config.sub_report_classify_doc_infos_single_time_num")
        if not classify_doc_infos_single_time_num or classify_doc_infos_single_time_num <= 60:
            classify_doc_infos_single_time_num = 60

        # 提取doc_infos并去重
        history_plans = session.get_global_state("section_context.history_plans")
        doc_infos = []
        for plan in history_plans:
            steps = plan.steps if hasattr(plan, 'steps') else plan.get("steps", [])
            for step in steps:
                retrieval_queries = step.retrieval_queries if hasattr(step, 'retrieval_queries') else step.get(
                    "retrieval_queries", [])
                for query in retrieval_queries:
                    query_doc_infos = query.doc_infos if hasattr(query, 'doc_infos') else query.get("doc_infos", [])
                    doc_infos.extend(query_doc_infos)
        doc_infos = list({(doc["title"], doc["url"]): doc for doc in doc_infos}.values())

        llm_model_name = adapt_llm_model_name(session, NodeId.SUB_REPORTER.value)

        return dict(
            thread_id=session.get_global_state("section_context.session_id"),
            has_template=bool(session.get_global_state("section_context.report_template")),
            language=session.get_global_state("section_context.language") or CHINESE,
            report_template=session.get_global_state("section_context.report_template"),
            report_format=session.get_global_state("section_context.report_format") or ReportFormat.MARKDOWN,
            report_style=session.get_global_state("config.report_style") or ReportStyle.SCHOLARLY.value,
            section_idx=section_idx,  # 章节序号,
            report_task=session.get_global_state("section_context.report_task"),  # 总报告标题
            section_task=session.get_global_state("section_context.section_task"),  # 当前章节标题
            section_iscore=session.get_global_state("section_context.section_iscore") or False,  # 是否核心章节
            section_description=session.get_global_state("section_context.section_description"),  # 章节描述
            doc_infos=doc_infos,
            current_outline=session.get_global_state("section_context.current_outline")
            if session.get_global_state("section_context.current_outline") else "",
            max_generate_retry_num=session.get_global_state("config.report_max_generate_retry_num") or 3,
            classify_doc_infos_res_top_k_num=session.get_global_state(
                "config.sub_report_classify_doc_infos_res_top_k_num") or 10,
            classify_doc_infos_single_time_num=classify_doc_infos_single_time_num,
            llm_model_name=llm_model_name,
            sub_report_background_knowledge=session.get_global_state(
                "section_context.sub_report_background_knowledge") or [],
        )

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        session_context.set(session)
        updating_state = self._pre_handle(inputs, session, context)
        logger.info(f"f{self.log_prefix} current node inputs is "
                    f"{'*' if LogManager.is_sensitive() else updating_state.get('current_outline')}")

        reporter = Reporter(updating_state.get('llm_model_name'))
        success, msg, sub_report_content, classified_content = await reporter.generate_sub_report(updating_state)

        algorithm_output = dict(success=success, msg=msg,
                                classified_content=classified_content,
                                sub_report_content=sub_report_content)
        updating_state.update(algorithm_output)

        return self._post_handle(inputs, updating_state, session, context)

    def _post_handle(self, inputs: Input, updating_state: dict, session: Session, context: ModelContext):
        doc_infos = updating_state.get("doc_infos")
        sub_report_success = updating_state.get("success")
        generate_sub_report_msg = updating_state.get("msg")
        classified_content = updating_state.get("classified_content", [])
        sub_report_content_text = updating_state.get("sub_report_content", "")
        sub_report_content_summary = updating_state.get("sub_report_summary", "")

        detail_msg = (f"{generate_sub_report_msg}, doc_infos_num:{len(doc_infos)}, "
                      f"classified_content_num:{len(classified_content)}")
        if sub_report_success and sub_report_content_text:
            next_node = NodeId.SUB_SOURCE_TRACER.value
            logger.info(f"{self.log_prefix} Success to generate sub_report, detail: {detail_msg}, go to {next_node}")
        else:
            error_msg = (f"[{StatusCode.SUB_REPORT_GENERATE_ERROR.code}] {self.log_prefix} "
                         f"{StatusCode.SUB_REPORT_GENERATE_ERROR.errmsg.format(e=detail_msg)}")
            _handle_warning_exception_info(session, added_warning=error_msg, added_exception=error_msg)
            next_node = NodeId.END.value

        sub_report_debug_info_input = dict(
            section_idx=updating_state.get("section_idx"),
            report_task=updating_state.get("report_task"),
            section_task=updating_state.get("section_task"),
            doc_infos=doc_infos,
        )
        sub_report_content = SubReportContent(
            classified_content=classified_content,
            sub_report_content_text=sub_report_content_text,
            sub_report_content_summary=sub_report_content_summary,
        )
        sub_report_debug_info_output = sub_report_content.model_dump()
        # 添加SubReporterNode debug日志
        add_debug_log_wrapper(session, NodeDebugData(NodeId.SUB_REPORTER.value, 0, NodeType.SUB.value,
                              input_content=str(sub_report_debug_info_input).replace("\\n", "\n"),
                              output_content=str(sub_report_debug_info_output).replace("\\n", "\n")))

        # 更新上下文子报告内容信息
        session.update_global_state({"section_context.sub_report_content": sub_report_content})

        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
        return dict(next_node=next_node)


class SubSourceTracerNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.log_prefix = ""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state("section_context.section_idx") or 1
        self.log_prefix = f"section_idx: {section_idx} | [{self.__class__.__name__}] "
        logger.info(f"{self.log_prefix} Start [{self.__class__.__name__}].")
        research_trace_source_switch = session.get_global_state("config.source_tracer_research_trace_source_switch")
        language = session.get_global_state("section_context.language")
        llm_model_name = adapt_llm_model_name(session, NodeId.SUB_SOURCE_TRACER.value)

        # 获取子报告内容
        sub_report_content_obj = session.get_global_state("section_context.sub_report_content")
        if sub_report_content_obj and isinstance(sub_report_content_obj, SubReportContent):
            report = sub_report_content_obj.sub_report_content_text
            classified_content = sub_report_content_obj.classified_content
        else:
            report = ""
            classified_content = []

        return dict(
            report=report,
            classified_content=classified_content,
            research_trace_source_switch=research_trace_source_switch,
            language=language, llm_model_name=llm_model_name, section_idx=section_idx
        )

    def _skip_trace_source_handle(
            self, inputs: Input, session: Session,
            context: ModelContext, current_inputs: dict
    ) -> dict:
        """
        不需要溯源的场景直接跳到后处理
        """
        origin_report = current_inputs.get("report", "")
        algorithm_output = dict(trace_source_datas=[], modified_report=origin_report)
        result = self._post_handle(inputs, algorithm_output, session, context)
        return result

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        current_inputs = self._pre_handle(inputs, session, context)
        research_trace_source_switch = current_inputs.get("research_trace_source_switch", False)

        if research_trace_source_switch is False:
            logger.info(f"{self.log_prefix} research_trace_source_switch is False, skip trace source.")
            return self._skip_trace_source_handle(inputs, session, context, current_inputs)

        source_tracer = SourceTracer(current_inputs)
        await source_tracer.research_trace_source()
        source_tracer_result_dict = source_tracer.add_source_to_report()

        modified_report = source_tracer_result_dict.get("modified_report", "")
        datas = source_tracer_result_dict.get("datas", [])
        algorithm_output = dict(
            modified_report=modified_report,
            trace_source_datas=datas
        )
        result = self._post_handle(inputs, algorithm_output, session, context)
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        trace_source_datas = algorithm_output.get("trace_source_datas", [])
        modified_report = algorithm_output.get("modified_report", "")

        # 获取现有的 sub_report_content 对象并更新
        sub_report_content_obj = session.get_global_state("section_context.sub_report_content")
        if sub_report_content_obj and isinstance(sub_report_content_obj, SubReportContent):
            sub_report_content_obj.sub_report_content_text = modified_report
            sub_report_content_obj.sub_report_trace_source_datas = trace_source_datas
        else:
            # 如果不存在，创建新对象
            sub_report_content_obj = SubReportContent(
                sub_report_content_text=modified_report,
                sub_report_trace_source_datas=trace_source_datas
            )

        session.update_global_state({
            "section_context.sub_report_content": sub_report_content_obj
        })
        next_node = NodeId.END.value
        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")

        return dict(next_node=next_node)


class InfoCollectorNode(BaseNode):

    def __init__(self):
        self.log_prefix = ""
        super().__init__()

    def graph_invoker(self) -> bool:
        """
        Returns: bool
        """
        return True

    def component_type(self) -> str:
        """
        节点类型
        Returns: str
        """
        return SUB_WORKFLOW_COMPONENT

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state("section_context.section_idx")
        logger.info(f"section_idx: {section_idx} | [{self.__class__.__name__}] Start {self.__class__.__name__}.")
        language = session.get_global_state("section_context.language")
        messages = session.get_global_state("section_context.messages")
        current_plan = session.get_global_state("section_context.current_plan")
        history_plans = session.get_global_state("section_context.history_plans")
        collected_doc_num = session.get_global_state("section_context.collected_doc_num")
        warning_infos = session.get_global_state("section_context.warning_infos")

        initial_search_query_count = session.get_global_state("config.info_collector_initial_search_query_count")
        max_research_loops = session.get_global_state("config.info_collector_max_research_loops")
        max_react_recursion_limit = session.get_global_state("config.info_collector_max_react_recursion_limit")

        return dict(messages=messages, current_plan=current_plan, section_idx=section_idx,
                    language=language, initial_search_query_count=initial_search_query_count,
                    max_research_loops=max_research_loops, max_react_recursion_limit=max_react_recursion_limit,
                    history_plans=history_plans, doc_num=collected_doc_num, warning_infos=warning_infos)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        state = self._pre_handle(inputs, session, context)
        current_plan = state.get("current_plan")
        if not current_plan:
            return dict(next_node=NodeId.PLAN_REASONING.value)

        self.log_prefix = (f"section_idx: {state.get('section_idx', 0)} | plan_idx: {current_plan.id} | "
                           f"[{self.__class__.__name__}] |")
        logger.info(f"{self.log_prefix} Current plan is: {'*' if LogManager.is_sensitive() else current_plan}")

        collect_steps: list[Step] = []
        current_doc_num = 0
        messages = state.get("messages", [])
        for idx, step in enumerate(current_plan.steps):
            step.id = f"{idx + 1}"
            if step.type == StepType.INFO_COLLECTING and not step.step_result:
                sub_inputs = self._input_build(state, step)
                inputs.update({INPUTS_KEY: sub_inputs})

                logger.info(
                    f"{self.log_prefix} Start step {step.id}: The input is"
                    f"{'*' if LogManager.is_sensitive() else sub_inputs}"
                )

                collector_context = await self._run_collector_graph(inputs, session, context)
                step.step_result = collector_context.get("info_summary")
                step.evaluation = collector_context.get("evaluation")
                step.retrieval_queries = collector_context.get("history_queries")
                current_doc_num += len(collector_context.get("doc_infos", []))
                collect_steps.append(step)

                logger.info(
                    f"{self.log_prefix} End step {step.id}: The result is: "
                    f"{'*' if LogManager.is_sensitive() else step.model_dump()}"
                )

                messages.append(
                    Message(
                        role="assistant",
                        content=step.step_result,
                    )
                )

        if current_doc_num == 0:
            collector_warning = (f"[{StatusCode.INFO_COLLECTING_EMPTY.code}] {self.log_prefix} "
                                 f"{StatusCode.INFO_COLLECTING_EMPTY.errmsg}")
            warning_infos = state.get("warning_infos", [])
            warning_infos.append(collector_warning)
            logger.warning(collector_warning)

        state["collected_doc_num"] = state.get("collected_doc_num", 0) + current_doc_num
        current_plan.steps = collect_steps
        history_plans = state.get("history_plans", [])
        history_plans.append(current_plan)
        state["messages"] = messages
        result = self._post_handle(inputs, state, session, context)

        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        session.update_global_state({"section_context.messages": algorithm_output.get("messages")})
        session.update_global_state({"section_context.history_plans": algorithm_output.get("history_plans")})
        session.update_global_state({"section_context.collected_doc_num": algorithm_output.get("collected_doc_num")})
        session.update_global_state({"section_context.warning_infos": algorithm_output.get("warning_infos")})
        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")

        # InfoCollectorNode debug日志
        add_debug_log_wrapper(session, NodeDebugData(NodeId.INFO_COLLECTOR.value, 0, NodeType.SUB.value,
                              output_content=str(algorithm_output).replace("\\n", "\n")))

        return dict(next_node=NodeId.PLAN_REASONING.value)

    async def _run_collector_graph(self, inputs: dict, session: Session, context: ModelContext):
        collector_graph = build_info_collector_sub_graph()
        await collector_graph.invoke(inputs.get(INPUTS_KEY), session, context, is_sub=True,
                                     config=inputs.get(CONFIG_KEY))
        collector_context: dict = session.get_global_state("collector_context")
        return collector_context

    def _input_build(self, state: dict, step: Step):
        plan: Plan = state.get("current_plan")
        # 构造上下文message
        message = f"Now deal with the task: \n"
        message += f"You should focus on [Topic]: {plan.title}\n"
        message += f"pay attention to [Condition]: {plan.thought}"
        message += f":\n[Task Title]: {step.title}\n[Problem]: {step.description}"
        message += "\nPlease analyze this task and start your ReAct process:\n"
        message += "1. Reason about what information you need to gather\n"
        message += "2. Use appropriate tools to get that information\n"
        message += "3. Continue reasoning and acting until you have sufficient information\n"
        message += "4. Call info_seeker_task_done when ready to provide your complete findings\n\n"
        message += "Begin with your initial reasoning about the task."

        initial_search_query_count = state.get("initial_search_query_count", 2)
        max_research_loops = state.get("max_research_loops", 2)
        max_react_recursion_limit = state.get("max_react_recursion_limit", 8)

        agent_input = {
            "language": state.get("language", "zh-CN"),
            "messages": [Message(role="user", content=message)],
            "section_idx": state.get("section_idx", 0),
            "plan_idx": plan.id,
            "step_idx": step.id,
            "step_title": step.title,
            "step_description": step.description,
            "initial_search_query_count": initial_search_query_count,
            "max_research_loops": max_research_loops,
            "max_react_recursion_limit": max_react_recursion_limit,
        }

        return agent_input


class SectionEndNode(End):
    """
    子图结束节点，将子图的session中的section_state的内容返回到主图
    """

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """
        执行结束节点
        """
        section_idx = session.get_global_state("section_context.section_idx")
        self.log_prefix = f"section_idx: {section_idx} | [{self.__class__.__name__}] "
        logger.info(f"{self.log_prefix} Start {self.__class__.__name__}.")

        # 从 section_context 获取 sub_report_content 对象
        sub_report_content_obj = session.get_global_state("section_context.sub_report_content")
        if not sub_report_content_obj or not isinstance(sub_report_content_obj, SubReportContent):
            sub_report_content_obj = SubReportContent()

        section_state = {
            "plans": session.get_global_state("section_context.history_plans") or [],
            "sub_report_content": sub_report_content_obj,
            "sub_report_background_knowledge": session.get_global_state("section_context"
                                                                        ".sub_report_background_knowledge") or [],
            "warning_infos": session.get_global_state("section_context.warning_infos") or [],
            "exception_infos": session.get_global_state("section_context.exception_infos") or [],
        }
        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
        return section_state


def _handle_warning_exception_info(session: Session, added_warning: str = None, added_exception: str = None):
    """统一处理异常告警信息"""
    if added_warning:
        warning_infos = session.get_global_state("section_context.warning_infos")
        warning_infos.append(added_warning)
        session.update_global_state({"section_context.warning_infos": warning_infos})
        logger.warning(f"{added_warning}")

    if added_exception:
        exception_infos = session.get_global_state("section_context.exception_infos")
        exception_infos.append(added_exception)
        session.update_global_state({"section_context.exception_infos": exception_infos})
        logger.error(f"{added_exception}")


def build_editor_team_workflow():
    """ 创建research模式下子图节点图 """
    sub_workflow = Workflow()
    sub_workflow.set_start_comp(
        NodeId.START.value,
        SectionStartNode(),
        inputs_schema={
            "language": "${language}",
            "messages": "${messages}",
            "section_idx": "${section_idx}",
            "current_outline": "${current_outline}",
            "report_task": "${report_task}",
            "section_task": "${section_task}",
            "section_description": "${section_description}",
            "section_iscore": "${section_iscore}",
            "report_template": "${report_template}",
            "config": "${config}",
        }
    )

    sub_workflow.add_workflow_comp(NodeId.PLAN_REASONING.value, ResearchPlanReasoningNode())
    sub_workflow.add_workflow_comp(NodeId.INFO_COLLECTOR.value, InfoCollectorNode())
    sub_workflow.add_workflow_comp(NodeId.SUB_REPORTER.value, SubReporterNode())
    sub_workflow.add_workflow_comp(NodeId.SUB_SOURCE_TRACER.value, SubSourceTracerNode())
    plan_reasoning_router = init_router(NodeId.PLAN_REASONING.value,
                                        [NodeId.INFO_COLLECTOR.value, NodeId.SUB_REPORTER.value,
                                         NodeId.END.value])
    sub_reporter_router = init_router(NodeId.SUB_REPORTER.value,
                                      [NodeId.SUB_SOURCE_TRACER.value, NodeId.END.value])
    sub_workflow.add_connection(NodeId.START.value, NodeId.PLAN_REASONING.value)
    sub_workflow.add_connection(NodeId.INFO_COLLECTOR.value, NodeId.PLAN_REASONING.value)
    sub_workflow.add_conditional_connection(NodeId.PLAN_REASONING.value, router=plan_reasoning_router)
    sub_workflow.add_conditional_connection(NodeId.SUB_REPORTER.value, router=sub_reporter_router)
    sub_workflow.add_connection(NodeId.SUB_SOURCE_TRACER.value, NodeId.END.value)

    sub_workflow.set_end_comp(NodeId.END.value, SectionEndNode())

    return sub_workflow
