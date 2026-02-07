# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import logging
import uuid

from openjiuwen.core.component.workflow_comp import SUB_WORKFLOW_COMPONENT
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.runtime.workflow import WorkflowRuntime
from openjiuwen.core.stream.base import BaseStreamMode, CustomSchema, OutputSchema

from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.framework.jiuwen.agent.base_node import BaseNode
from jiuwen_deepsearch.framework.jiuwen.agent.reasoning_writing_graph.editor_team_nodes import \
    build_editor_team_workflow
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import Section, Outline, Report, SubReport, \
    SubReportContent
from jiuwen_deepsearch.utils.debug_utils.node_debug import NodeType, add_debug_log_wrapper
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from jiuwen_deepsearch.utils.common_utils.stream_utils import StreamEvent, MessageType

logger = logging.getLogger(__name__)


class EditorTeamNode(BaseNode):

    def __init__(self):
        super().__init__()
        self.log_prefix = ""

    def graph_invoker(self) -> bool:
        """图执行器"""
        return True

    def component_type(self) -> str:
        """返回Jiuwen组件类型"""
        return SUB_WORKFLOW_COMPONENT

    def _pre_handle(self, inputs: Input, runtime: Runtime, context: Context):
        self.log_prefix = f"[{self.__class__.__name__}]"
        logger.info(f"{self.log_prefix} Start {self.__class__.__name__}.")
        language = runtime.get_global_state("search_context.language")
        messages = runtime.get_global_state("search_context.messages")
        outline = runtime.get_global_state("search_context.current_outline")
        history_outlines = runtime.get_global_state("search_context.history_outlines")
        report_template = runtime.get_global_state("search_context.report_template")
        history_reports = runtime.get_global_state("search_context.history_reports")
        config = runtime.get_global_state("config")

        return dict(language=language, messages=messages, outline=outline, history_outlines=history_outlines,
                    report_template=report_template, history_reports=history_reports, config=config)

    async def _do_invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        # 1. 从上下文中获取大纲，并初始化报告
        state = self._pre_handle(inputs, runtime, context)
        logger.info(f"{self.log_prefix} current_inputs: {'*' if LogManager.is_sensitive() else state}")
        current_outline = state.get("outline")
        if not current_outline:
            msg = (
                f"[{StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE.code}] "
                f"{self.log_prefix} {StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE.errmsg}"
            )
            self._handle_warning_exception_info(runtime, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)
        sections = current_outline.sections
        if not sections:
            msg = (
                f"[{StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION.code}] "
                f"{self.log_prefix} {StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION.errmsg}"
            )
            self._handle_warning_exception_info(runtime, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)
        current_report = Report(
            id=str(uuid.uuid4()),
            report_task=current_outline.title,
            report_template=state.get("report_template", "")
        )
        state["report"] = current_report
        sub_reports = []

        # 2. 并发执行各章节
        tasks = []
        for index, section in enumerate(sections):
            section.id = str(index + 1)
            sub_report = SubReport(id=str(uuid.uuid4()), section_id=section.id, section_task=section.title)
            sub_reports.append(sub_report)
            sub_workflow = build_editor_team_workflow()
            section_state = self._create_section_state_from_state(
                state, current_outline, section
            )
            tasks.append(
                self._run_section_sub_graph_await(
                    runtime, sub_workflow, section_state)
            )
        tasks_results = await asyncio.gather(*tasks)

        # 3. 结果汇聚，更新上下文
        updated_state = self._update_state(state, sections, sub_reports, tasks_results)
        results = self._post_handle(inputs, updated_state, runtime, context)
        return results

    def _post_handle(self, inputs: Input, state: dict, runtime: Runtime, context: Context):
        algorithm_output = {
            "search_context.current_report": state.get("report"),
            "search_context.current_outline": state.get("outline"),
            "search_context.history_outlines": state.get("history_outlines"),
            "search_context.history_reports": state.get("history_reports"),
        }
        runtime.update_global_state(algorithm_output)

        # 添加debug日志
        add_debug_log_wrapper(runtime, NodeId.EDITOR_TEAM.value, 0, NodeType.MAIN.value,
                              output_content=str(algorithm_output).replace("\\n", "\n"))

        next_node = NodeId.REPORTER.value
        current_report: Report = state.get("report")
        warning_info = state.get('warning_info', '')
        exception_info = state.get('exception_info', '')

        if not current_report or not current_report.sub_reports or not any(
                sub_report.content.sub_report_content_text.strip() for sub_report in current_report.sub_reports
        ):
            error_msg = (
                f"[{StatusCode.EDITORTEAM_MANAGER_EMPTY_SUB_REPORT.code}] "
                f"{self.log_prefix} {StatusCode.EDITORTEAM_MANAGER_EMPTY_SUB_REPORT.errmsg}"
            )
            warning_info += '\n' + error_msg
            exception_info += '\n' + error_msg
            next_node = NodeId.END.value

        if warning_info or exception_info:
            self._handle_warning_exception_info(runtime, added_warning=warning_info, added_exception=exception_info)
        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")

        return dict(next_node=next_node)

    def _create_section_state_from_state(self, state: dict, outline: Outline, section: Section):
        # 为子图创建section_state
        messages = [
            state.get("messages", [])[0],
            dict(
                role="user",
                content=(
                    f"# Research Requirements\n\n"
                    f"## Task\n\n"
                    f"{outline.title}\n\n"
                    f"## Current Section Title\n\n"
                    f"{section.title}\n\n"
                    f"## Current Section Description\n\n"
                    f"{section.description}"
                ),
                name="outliner"
            )
        ]
        section_state = {
            "language": state.get("language", "zh-CN"),
            "messages": messages,
            "current_outline": outline,
            "report_task": outline.title,
            "report_template": state.get("report_template", ""),
            "section_idx": section.id,
            "section_task": section.title,
            "section_description": section.description,
            "section_iscore": section.is_core_section,
            "parent_section_steps": state.get("parent_section_steps", []),
            "config": state.get("config", {}),
        }

        return section_state

    async def _run_section_sub_graph_await(self, workflow_runtime, sub_workflow, input_state):
        section_idx = input_state.get("section_idx", "0")
        # 执行每个子图，得到每个section的结果
        logger.info(
            f"{self.log_prefix} Start Section {section_idx}: Start the sub graph.")
        sub_workflow_runtime = WorkflowRuntime()
        async for chunk in Runner.run_workflow_streaming(
                workflow=sub_workflow,
                inputs=input_state,
                runtime=sub_workflow_runtime,
                stream_modes=[BaseStreamMode.CUSTOM, BaseStreamMode.OUTPUT]
        ):
            if not LogManager.is_sensitive():
                logger.debug("%s Section_idx: %s Received subgraph message: chunk: %s",
                             self.log_prefix, section_idx, chunk)
            if isinstance(chunk, CustomSchema):
                output_message = {
                    "message_id": getattr(chunk, "message_id", ""),
                    "section_idx": str(section_idx),
                    "plan_idx": getattr(chunk, "plan_idx", "0"),
                    "step_idx": getattr(chunk, "step_idx", "0"),
                    "agent": getattr(chunk, "agent", "Default"),
                    "role": "assistant",
                    "content": getattr(chunk, "content", ""),
                    "message_type": getattr(chunk, "message_type", ""),
                    "event": getattr(chunk, "event", ""),
                    "created_time": getattr(chunk, "created_time", ""),
                }
                if hasattr(chunk, "finish_reason"):
                    output_message["finish_reason"] = getattr(
                        chunk, "finish_reason")
                await workflow_runtime.write_custom_stream(output_message)
            elif isinstance(chunk, OutputSchema):
                if hasattr(chunk, "type") and getattr(chunk, "type") == "workflow_final":
                    await workflow_runtime.write_custom_stream(
                        {
                            "message_id": str(uuid.uuid4()),
                            "section_idx": str(section_idx),
                            "plan_idx": getattr(chunk, "plan_idx", "0"),
                            "step_idx": getattr(chunk, "step_idx", "0"),
                            "agent": NodeId.END.value,
                            "content": "SECTION END",
                            "message_type": MessageType.MESSAGE_CHUNK.value,
                            "event": StreamEvent.SUMMARY_RESPONSE.value,
                            "created_time": getattr(chunk, "created_time", ""),
                        }
                    )
                    logger.info(f"{self.log_prefix} End Section {section_idx} : Completed the sub graph.")

                    if not LogManager.is_sensitive():
                        logger.info(f"{self.log_prefix} Section {section_idx} sub graph result is {chunk}")
                    section_state = getattr(chunk, "payload", "")

                    return self._parse_section_state(section_state)

    def _parse_section_state(self, section_state: dict):
        sub_report_content_obj = section_state.get("sub_report_content", SubReportContent())
        return dict(
            trace_source_datas=sub_report_content_obj.sub_report_trace_source_datas if sub_report_content_obj else [],
            classified_content=sub_report_content_obj.classified_content if sub_report_content_obj else [],
            plans=section_state.get("plans", []),
            sub_report_content=sub_report_content_obj,
            warning_infos=section_state.get("warning_infos", []),
            exception_infos=section_state.get("exception_infos", []),
        )

    def _update_state(self, state: dict, sections: list[Section], sub_reports: list[SubReport], task_results: list):
        outline: Outline = state.get("outline")
        history_outlines = state.get("history_outlines", [])
        report: Report = state.get("report")
        history_reports = state.get("history_reports", [])
        warning_info = ""
        exception_info = ""

        merged_trace_source_datas = []
        all_classified_contents = []
        for section, sub_report, result in zip(sections, sub_reports, task_results):
            section.plans = result.get("plans")
            sub_report.content = result.get("sub_report_content")
            warning_info += '\n'.join(result.get("warning_infos", ""))
            exception_info += '\n'.join(result.get("exception_infos", ""))
            merged_trace_source_datas.extend(result.get("trace_source_datas", []))
            all_classified_contents.append(result.get("classified_content", []))

        outline.sections = sections
        report.sub_reports = sub_reports
        report.all_classified_contents = all_classified_contents
        report.merged_trace_source_datas = merged_trace_source_datas
        history_outlines.append(outline)
        history_reports.append(report)
        state["outline"] = outline
        state["report"] = report
        state["history_outlines"] = history_outlines
        state["history_reports"] = history_reports
        state["warning_info"] = warning_info
        state["exception_info"] = exception_info

        return state

    def _handle_warning_exception_info(self, runtime: Runtime, added_warning: str, added_exception: str):
        """统一处理异常告警信息"""
        if added_warning:
            warning_info = runtime.get_global_state("search_context.final_result.warning_info")
            runtime.update_global_state(
                {"search_context.final_result.warning_info": warning_info + '\n' + added_warning})
            logger.warning(f"{added_warning}")

        if added_exception:
            exception_info = runtime.get_global_state("search_context.final_result.exception_info")
            runtime.update_global_state(
                {"search_context.final_result.exception_info": exception_info + '\n' + added_exception})
            logger.error(f"{added_exception}")


class DependencyReasoningTeamNode(EditorTeamNode):
    def __init__(self):
        super().__init__()

    def _post_handle(self, inputs: Input, algorithm_output: object, runtime: Runtime, context: Context):
        return dict(next_node=NodeId.DEPENDENCY_WRITING_TEAM.value)


class DependencyWritingTeamNode(EditorTeamNode):
    def __init__(self):
        super().__init__()

    def _post_handle(self, inputs: Input, algorithm_output: object, runtime: Runtime, context: Context):
        return dict(next_node=NodeId.REPORTER.value)
