# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import logging
import uuid

from collections import defaultdict, deque
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.session.node import Session
from openjiuwen.core.session.stream.base import BaseStreamMode, CustomSchema, OutputSchema
from openjiuwen.core.workflow.components.flow.workflow_comp import SUB_WORKFLOW_COMPONENT

from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import \
    build_editor_team_workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_reasoning_team_nodes import \
    build_dependency_reasoning_workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_writing_team_nodes import \
    build_dependency_writing_workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Section, Outline, Report, SubReport, \
    SubReportContent
from openjiuwen_deepsearch.utils.common_utils.stream_utils import StreamEvent, MessageType
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.debug_utils.node_debug import NodeType, add_debug_log_wrapper, NodeDebugData
from openjiuwen_deepsearch.utils.debug_utils.result_exporter import ResultExporter
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

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

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        self.log_prefix = f"[{self.__class__.__name__}]"
        logger.info(f"{self.log_prefix} Start {self.__class__.__name__}.")
        language = session.get_global_state("search_context.language")
        messages = session.get_global_state("search_context.messages")
        outline = session.get_global_state("search_context.current_outline")
        history_outlines = session.get_global_state("search_context.history_outlines")
        report_template = session.get_global_state("search_context.report_template")
        history_reports = session.get_global_state("search_context.history_reports")
        config = session.get_global_state("config")
        session_id = session.get_global_state("search_context.session_id")

        return dict(language=language, messages=messages, outline=outline, history_outlines=history_outlines,
                    report_template=report_template, history_reports=history_reports, session_id=session_id,
                    config=config)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # 1. 从上下文中获取大纲，并初始化报告
        state = self._pre_handle(inputs, session, context)
        logger.info(f"{self.log_prefix} current_inputs: {'*' if LogManager.is_sensitive() else state}")
        current_outline = state.get("outline")
        if not current_outline:
            msg = (
                f"[{StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE.code}] "
                f"{self.log_prefix} {StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE.errmsg}"
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)
        sections = current_outline.sections
        if not sections:
            msg = (
                f"[{StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION.code}] "
                f"{self.log_prefix} {StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION.errmsg}"
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
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
                    session, sub_workflow, section_state)
            )
        tasks_results = await asyncio.gather(*tasks)

        # 3. 填充结果字段，并更新在state中
        state = self._update_state(state, sections, sub_reports, tasks_results)

        # 4. 导出outline完整信息
        ResultExporter.export_outline(state.get("outline"), state.get("session_id"))

        # 5. 上下文更新
        results = self._post_handle(inputs, state, session, context)
        return results

    def _post_handle(self, inputs: Input, state: dict, session: Session, context: ModelContext):
        algorithm_output = {
            "search_context.current_report": state.get("report"),
            "search_context.current_outline": state.get("outline"),
            "search_context.history_outlines": state.get("history_outlines"),
            "search_context.history_reports": state.get("history_reports"),
        }
        session.update_global_state(algorithm_output)

        # 添加debug日志
        add_debug_log_wrapper(session, NodeDebugData(NodeId.EDITOR_TEAM.value, 0, NodeType.MAIN.value,
                              output_content=str(algorithm_output).replace("\\n", "\n")))

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
            self._handle_warning_exception_info(session, added_warning=warning_info, added_exception=exception_info)
        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")

        return dict(next_node=next_node)

    def _create_section_state_from_state(
            self,
            state: dict,
            outline: Outline,
            section: Section,
            background_knowledge=None,
    ):
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
            "sub_report_background_knowledge": background_knowledge if background_knowledge else [],
            "history_plans": section.plans,
            "session_id": state.get("session_id", "")
        }

        return section_state

    async def _run_section_sub_graph_await(self, workflow_session, sub_workflow, input_state):
        section_idx = input_state.get("section_idx", "0")
        # 执行每个子图，得到每个section的结果
        logger.info(
            f"{self.log_prefix} Start Section {section_idx}: Start the sub graph.")
        async for chunk in Runner.run_workflow_streaming(
                workflow=sub_workflow,
                inputs=input_state,
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
                await workflow_session.write_custom_stream(output_message)
            elif isinstance(chunk, OutputSchema):
                if hasattr(chunk, "type") and getattr(chunk, "type") == "workflow_final":
                    await workflow_session.write_custom_stream(
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

    def _handle_warning_exception_info(self, session: Session, added_warning: str, added_exception: str):
        """统一处理异常告警信息"""
        if added_warning:
            warning_info = session.get_global_state("search_context.final_result.warning_info")
            session.update_global_state(
                {"search_context.final_result.warning_info": warning_info + '\n' + added_warning})
            logger.warning(f"{added_warning}")

        if added_exception:
            exception_info = session.get_global_state("search_context.final_result.exception_info")
            session.update_global_state(
                {"search_context.final_result.exception_info": exception_info + '\n' + added_exception})
            logger.error(f"{added_exception}")


class DependencyReasoningTeamNode(EditorTeamNode):
    def __init__(self):
        super().__init__()

    async def _do_invoke(
            self, inputs: Input, session: Session, context: ModelContext
    ) -> Output:
        # 1.从上下文中获取带依赖关系的大纲
        state = self._pre_handle(inputs, session, context)
        logger.info(f"{self.log_prefix} current_inputs: {'*' if LogManager.is_sensitive() else state}")
        current_outline = state.get("outline")
        if not current_outline:
            msg = (
                f"[{StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE.code}] "
                f"{self.log_prefix} {StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE.errmsg}"
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)
        sections = current_outline.sections
        if not sections:
            msg = (
                f"[{StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION.code}] "
                f"{self.log_prefix} {StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION.errmsg}"
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)
        outline_num = len(sections)
        current_report = Report(
            id=str(uuid.uuid4()),
            report_task=current_outline.title,
            report_template=state.get("report_template", "")
        )
        state["report"] = current_report
        sub_reports = []

        # 2. 依赖关系执行各章节
        history_section_plans = {}
        tasks_results = []
        while len(history_section_plans) < outline_num:
            tasks = []
            # 查找所有可执行的section
            for section in current_outline.sections:
                if section.id in history_section_plans:
                    continue
                # 检查section的依赖是否都已完成
                section_parents_is_finished = all(
                    parent in history_section_plans for parent in section.parent_ids
                )

                if section_parents_is_finished:
                    sub_report = SubReport(id=str(uuid.uuid4()), section_id=section.id, section_task=section.title)
                    sub_reports.append(sub_report)
                    parent_section_steps = []
                    for parent in section.parent_ids:
                        parent_section_plans = history_section_plans.get(parent, [])
                        for plan in parent_section_plans:
                            parent_section_steps.extend(plan.steps)
                    sub_workflow = build_dependency_reasoning_workflow()

                    section_state = self._create_section_state_from_state(
                        state, current_outline, section
                    )
                    section_state["parent_section_steps"] = parent_section_steps
                    tasks.append(
                        self._run_section_sub_graph_await(
                            session, sub_workflow, section_state)
                    )
            current_results = await asyncio.gather(*tasks)
            tasks_results.extend(current_results)

            for result in current_results:
                section_idx = result.get("section_idx")
                history_section_plans[section_idx] = result.get("plans", [])

        # 3. 填充结果字段，并更新在state中
        state = self._update_state(state, sections, sub_reports, tasks_results)

        # 4. 导出outline完整信息
        ResultExporter.export_outline(state.get("outline"), state.get("session_id"))

        # 5. 上下文更新
        results = self._post_handle(inputs, state, session, context)
        return results

    async def _run_section_sub_graph_await(self, workflow_runtime, sub_workflow, input_state):
        result = await super()._run_section_sub_graph_await(workflow_runtime, sub_workflow, input_state)
        section_idx = input_state.get("section_idx", "0")
        result["section_idx"] = section_idx
        return result

    def _update_state(self, state: dict, sections: list[Section], sub_reports: list[SubReport], task_results: list):
        state = super()._update_state(state, sections, sub_reports, task_results)
        report: Report = state.get("report")
        # 依赖关系推理节点输出只包含plans和steps，classified_content和trace_source_datas保存为空列表
        report.all_classified_contents = []
        report.merged_trace_source_datas = []
        state["report"] = report
        return state

    def _post_handle(self, inputs: Input, state: dict, session: Session, context: ModelContext):
        algorithm_output = {
            "search_context.current_report": state.get("report"),
            "search_context.current_outline": state.get("outline"),
            "search_context.history_outlines": state.get("history_outlines"),
            "search_context.history_reports": state.get("history_reports"),
        }
        session.update_global_state(algorithm_output)

        # 添加debug日志
        add_debug_log_wrapper(session, NodeDebugData(NodeId.DEPENDENCY_REASONING_TEAM.value, 0, NodeType.MAIN.value,
                              output_content=str(algorithm_output).replace("\\n", "\n")))

        next_node = NodeId.DEPENDENCY_WRITING_TEAM.value
        warning_info = state.get('warning_info', '')
        exception_info = state.get('exception_info', '')

        if warning_info or exception_info:
            self._handle_warning_exception_info(session, added_warning=warning_info, added_exception=exception_info)
        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")

        return dict(next_node=next_node)


class DependencyWritingTeamNode(EditorTeamNode):
    def __init__(self):
        super().__init__()

    def get_task_execute_sequence(self, outline: Outline):
        """获取并行任务序列"""
        all_section_parent_infos = []
        for item in outline.sections:
            if isinstance(item, Section):
                node_item = {"id": item.id, "parent_ids": item.parent_ids}
                all_section_parent_infos.append(node_item)

        indegree = defaultdict(int)
        child_node = defaultdict(list)
        nodes = set()
        for node_info in all_section_parent_infos:
            node_id = node_info["id"]
            nodes.add(node_id)
            indegree[node_id] = len(node_info.get("parent_ids", []))
            for v in node_info.get("parent_ids", []):
                child_node[v].append(node_id)

        execute_sequence = []
        # 把入度为0的节点都放到队列里
        queue = deque([n for n in nodes if indegree[n] == 0])
        while queue:
            execute_sequence.append(list(queue))  # 当前层所有节点
            for _ in range(len(queue)):
                u = queue.popleft()
                # 所有子节点的入度减1
                for v in child_node[u]:
                    indegree[v] -= 1
                    # 如果入度为0放到队列中
                    if indegree[v] == 0:
                        queue.append(v)
        return execute_sequence

    def get_background_knowledge(self, parent_ids, pre_results):
        background_knowledge = []
        for result in pre_results:
            if result["id"] in parent_ids:
                sub_report_content = result["result"].get("sub_report_content")
                knowledge = {
                    "section_id": result.get("id"),
                    "content_summary": sub_report_content.sub_report_content_summary if sub_report_content else "",
                }
                background_knowledge.append(knowledge)
        return background_knowledge

    def get_parent_ids(self, section_id, outline):
        """通过section_id获取指定section的依赖section id"""
        if not outline:
            return []
        for section in outline.sections:
            if section:
                if section.id == section_id:
                    return section.parent_ids
        return []

    def add_background_knowledge(self, section_id, background_knowledge, results):
        """给结果拼接背景信息"""
        for index, item in enumerate(results):
            if item.get("id") == section_id:
                results[index]["background_knowledge"] = background_knowledge

    def get_section_by_id(self, section_id, outline):
        """通过section_id从outline中获取section"""
        if not outline:
            return None
        for section in outline.sections:
            if section_id == section.id:
                return section
        return None


    async def execute_tasks(self, session, execute_sequence: list[list], state, current_outline):
        """ 执行子报告生成任务 """
        results = []
        # 多层可并行的任务
        for execute_list in execute_sequence:
            tasks = []
            # 并行执行
            for section_id in execute_list:
                sub_workflow = build_dependency_writing_workflow()

                # 获取章节的parent_ids
                parent_ids = self.get_parent_ids(section_id, current_outline)
                # 获取章节的背景信息
                background_knowledge = self.get_background_knowledge(parent_ids, results)
                section = self.get_section_by_id(section_id, current_outline)
                if not section:
                    logger.error(f"Can't find section with id {section_id}")
                    continue
                section_state = self._create_section_state_from_state(
                    state, current_outline, section, background_knowledge
                )
                tasks.append(
                    self._run_section_sub_graph_await(session, sub_workflow, section_state)
                )
            task_results = await asyncio.gather(*tasks)
            # 暂存子章节信息
            for index, item in enumerate(execute_list):
                results.append({"id": item, "result": task_results[index]})

        # 子章节结果添加背景信息
        flat = [item for sub in execute_sequence for item in sub]
        for section_id in flat:
            # 获取章节的parent_ids
            parent_ids = self.get_parent_ids(section_id, current_outline)
            # 获取章节的背景信息
            background_knowledge = self.get_background_knowledge(parent_ids, results)
            self.add_background_knowledge(section_id, background_knowledge, results)
        # 按id把结果排序
        sorted_data = sorted(results, key=lambda x: int(x["id"]))
        return sorted_data


    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # 1. 从上下文中获取大纲
        state = self._pre_handle(inputs, session, context)
        state["report"] = session.get_global_state("search_context.current_report")
        current_outline = state.get("outline")
        current_report = state.get("report")
        if not current_outline:
            msg = (
                f"[{StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE.code}] "
                f"{self.log_prefix} {StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE.errmsg}"
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)
        sections = current_outline.sections
        if not sections:
            msg = (
                f"[{StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION.code}] "
                f"{self.log_prefix} {StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION.errmsg}"
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)

        # 获取子任务执行的顺序
        execute_sequence = self.get_task_execute_sequence(current_outline)
        logger.info(f"[DependencyWritingTeamNode] execute sequence is {execute_sequence}")
        # 执行子任务
        tasks_results = await self.execute_tasks(session, execute_sequence, state, current_outline)
        # 3. 填充结果字段，并更新在state中
        existing_sub_reports = current_report.sub_reports if current_report else []
        state = self._update_state(state, sections, existing_sub_reports, tasks_results)

        # 4. 导出outline完整信息
        ResultExporter.export_outline(state.get("outline"), state.get("session_id"))

        # 5. 上下文更新
        results = self._post_handle(inputs, state, session, context)
        return results

    def _update_state(self, state: dict, sections: list[Section], sub_reports: list[SubReport], tasks_results: list):
        algorithm_output = [item["result"] for item in tasks_results]
        state = super()._update_state(state, sections, sub_reports, algorithm_output)
        report: Report = state.get("report")
        # 回填子报告背景知识
        for sub_report in report.sub_reports:
            for result in tasks_results:
                if result.get("id") == sub_report.section_id:
                    sub_report.background_knowledge = result.get("background_knowledge", [])
        state["report"] = report
        return state

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        super()._post_handle(inputs, algorithm_output, session, context)
        return dict(next_node=NodeId.REPORTER.value)
