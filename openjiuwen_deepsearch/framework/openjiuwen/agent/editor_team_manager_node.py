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

from openjiuwen_deepsearch.common.status_code import StatusCode, format_exception_info
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import \
    build_editor_team_workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_reasoning_team_nodes import \
    build_dependency_reasoning_workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.dependency_writing_team_nodes import \
    build_dependency_writing_workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Section,
    Outline,
    Report,
    SectionLocalContract,
    SubReport,
    SubReportContent,
)
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

    @staticmethod
    def dedupe_preserve_order(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            value = (item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    @staticmethod
    def _build_section_local_contract(section: Section) -> dict:
        """基于 Outliner LLM 生成的 section_focus 和 focus_dimensions 构建章节局部合同。

        当 LLM 未输出 section_focus 时，使用空默认值。
        """
        dedupe = EditorTeamNode.dedupe_preserve_order

        focus = (section.section_focus or "").strip() or "section_specific_analysis"
        allowed_dimensions = dedupe([d for d in (section.focus_dimensions or []) if d])
        is_final_decision_section = (focus == "recommendation_and_ranking")

        contract = SectionLocalContract(
            section_focus=focus,
            allowed_dimensions=allowed_dimensions,
            is_final_decision_section=is_final_decision_section,
        )
        return contract.model_dump()

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
                    config=config,
                    report_type_policy=session.get_global_state("search_context.report_type_policy") or {},
                    research_intent=session.get_global_state("search_context.research_intent") or {})

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # 1. 从上下文中获取大纲，并初始化报告
        state = self._pre_handle(inputs, session, context)
        logger.info(f"{self.log_prefix} current_inputs: {'*' if LogManager.is_sensitive() else state}")
        current_outline = state.get("outline")
        if not current_outline:
            msg = format_exception_info(
                StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE,
                prefix=self.log_prefix,
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)
        sections = current_outline.sections
        if not sections:
            msg = format_exception_info(
                StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION,
                prefix=self.log_prefix,
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
            error_msg = format_exception_info(
                StatusCode.EDITORTEAM_MANAGER_EMPTY_SUB_REPORT,
                prefix=self.log_prefix,
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
            "section_format_requirements": section.format_requirements,
            "section_iscore": section.is_core_section,
            "parent_section_steps": state.get("parent_section_steps", []),
            "config": state.get("config", {}),
            "sub_report_background_knowledge": background_knowledge if background_knowledge else [],
            "history_plans": section.plans,
            "session_id": state.get("session_id", ""),
            "report_type_policy": state.get("report_type_policy") or {},
            "research_intent": state.get("research_intent") or {},
            "section_local_contract": self._build_section_local_contract(section),
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


class DependencyEditorTeamNode(EditorTeamNode):
    """依赖驱动编辑团队节点：按层流水线并行执行「推理+写作」."""

    def __init__(self):
        super().__init__()

    def get_task_execute_sequence(self, outline: Outline):
        """按依赖关系获取层级执行序列，每层内 section 可并行。"""
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
        queue = deque([n for n in nodes if indegree[n] == 0])
        executed_nodes = 0
        while queue:
            execute_sequence.append(list(queue))
            for _ in range(len(queue)):
                u = queue.popleft()
                executed_nodes += 1
                for v in child_node[u]:
                    indegree[v] -= 1
                    if indegree[v] == 0:
                        queue.append(v)
        if executed_nodes != len(nodes):
            logger.error(
                "[DependencyEditorTeamNode] Invalid dependency graph detected, executed_nodes=%s, total_nodes=%s",
                executed_nodes,
                len(nodes),
            )
            return []
        return execute_sequence

    def get_parent_ids(self, section_id, outline: Outline):
        """通过 section_id 获取该 section 的依赖 section id 列表。"""
        if not outline:
            return []
        for section in outline.sections:
            if section and section.id == section_id:
                return section.parent_ids
        return []

    def get_section_by_id(self, section_id, outline: Outline):
        """通过 section_id 从 outline 中获取 Section。"""
        if not outline:
            return None
        for section in outline.sections:
            if section_id == section.id:
                return section
        return None

    def _get_background_knowledge_from_writing_results(self, parent_ids, writing_results: dict):
        """从 writing_results 字典按 parent_ids 构造 background_knowledge 列表。"""
        background_knowledge = []
        for pid in parent_ids:
            wr = writing_results.get(pid)
            if not wr:
                continue
            sub_report_content = wr.get("sub_report_content")
            summary = ""
            if sub_report_content and hasattr(sub_report_content, "sub_report_content_summary"):
                summary = sub_report_content.sub_report_content_summary or ""
            background_knowledge.append({"section_id": pid, "content_summary": summary})
        return background_knowledge

    async def _run_reasoning_await(self, session: Session, sub_workflow, section_state: dict):
        """跑推理子图并给结果补上 section_idx."""
        result = await self._run_section_sub_graph_await(session, sub_workflow, section_state)
        result["section_idx"] = section_state.get("section_idx", "0")
        return result

    async def _do_invoke(
            self, inputs: Input, session: Session, context: ModelContext
    ) -> Output:
        state = self._pre_handle(inputs, session, context)
        logger.info(f"{self.log_prefix} current_inputs: {'*' if LogManager.is_sensitive() else state}")
        current_outline = state.get("outline")
        if not current_outline:
            msg = format_exception_info(
                StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE,
                prefix=self.log_prefix,
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)
        sections = current_outline.sections
        if not sections:
            msg = format_exception_info(
                StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION,
                prefix=self.log_prefix,
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)

        current_report = Report(
            id=str(uuid.uuid4()),
            report_task=current_outline.title,
            report_template=state.get("report_template", ""),
        )
        state["report"] = current_report
        sub_reports = [
            SubReport(id=str(uuid.uuid4()), section_id=section.id, section_task=section.title)
            for section in sections
        ]

        execute_sequence = self.get_task_execute_sequence(current_outline)
        logger.info(f"[DependencyEditorTeamNode] execute sequence is {execute_sequence}")
        if not execute_sequence:
            detail = "Invalid dependency graph or empty execution sequence."
            msg = format_exception_info(
                StatusCode.EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION,
                detail,
                prefix=self.log_prefix,
            )
            self._handle_warning_exception_info(session, added_warning=msg, added_exception=msg)
            logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
            return dict(next_node=NodeId.END.value)
        reasoning_results = {}
        writing_results = {}

        for level_idx, section_ids_in_level in enumerate(execute_sequence):
            if level_idx == 0:
                tasks = []
                valid_ids_level0 = []
                for section_id in section_ids_in_level:
                    section = self.get_section_by_id(section_id, current_outline)
                    if not section:
                        logger.error("Can't find section with id %s", section_id)
                        continue
                    section_state = self._create_section_state_from_state(
                        state, current_outline, section
                    )
                    section_state["parent_section_steps"] = []
                    valid_ids_level0.append(section_id)
                    tasks.append(
                        self._run_reasoning_await(
                            session, build_dependency_reasoning_workflow(), section_state
                        )
                    )
                if tasks:
                    current_results = await asyncio.gather(*tasks)
                    for i, section_id in enumerate(valid_ids_level0):
                        if i < len(current_results):
                            reasoning_results[section_id] = current_results[i]
            else:
                prev_level_ids = execute_sequence[level_idx - 1]
                writing_tasks = []
                valid_writing_ids = []
                for section_id in prev_level_ids:
                    section = self.get_section_by_id(section_id, current_outline)
                    if not section:
                        continue
                    section.plans = reasoning_results.get(section_id, {}).get("plans", [])
                    parent_ids = self.get_parent_ids(section_id, current_outline)
                    background_knowledge = self._get_background_knowledge_from_writing_results(
                        parent_ids, writing_results
                    )
                    section_state = self._create_section_state_from_state(
                        state, current_outline, section, background_knowledge
                    )
                    valid_writing_ids.append(section_id)
                    writing_tasks.append(
                        self._run_section_sub_graph_await(
                            session, build_dependency_writing_workflow(), section_state
                        )
                    )
                reasoning_tasks = []
                valid_reasoning_ids = []
                for section_id in section_ids_in_level:
                    section = self.get_section_by_id(section_id, current_outline)
                    if not section:
                        continue
                    parent_section_steps = []
                    for parent in section.parent_ids:
                        for plan in reasoning_results.get(parent, {}).get("plans", []):
                            parent_section_steps.extend(plan.steps)
                    section_state = self._create_section_state_from_state(
                        state, current_outline, section
                    )
                    section_state["parent_section_steps"] = parent_section_steps
                    valid_reasoning_ids.append(section_id)
                    reasoning_tasks.append(
                        self._run_reasoning_await(
                            session, build_dependency_reasoning_workflow(), section_state
                        )
                    )
                all_tasks = writing_tasks + reasoning_tasks
                if all_tasks:
                    results = await asyncio.gather(*all_tasks)
                    n_w = len(writing_tasks)
                    for i, section_id in enumerate(valid_writing_ids):
                        if i < n_w:
                            writing_results[section_id] = results[i]
                    for j, section_id in enumerate(valid_reasoning_ids):
                        if n_w + j < len(results):
                            reasoning_results[section_id] = results[n_w + j]

        # 最后一层仅写作（并行执行）
        last_level_ids = execute_sequence[-1]
        last_level_tasks = []
        valid_section_ids = []
        for section_id in last_level_ids:
            section = self.get_section_by_id(section_id, current_outline)
            if not section:
                continue
            section.plans = reasoning_results.get(section_id, {}).get("plans", [])
            parent_ids = self.get_parent_ids(section_id, current_outline)
            background_knowledge = self._get_background_knowledge_from_writing_results(
                parent_ids, writing_results
            )
            section_state = self._create_section_state_from_state(
                state, current_outline, section, background_knowledge
            )
            valid_section_ids.append(section_id)
            last_level_tasks.append(
                self._run_section_sub_graph_await(
                    session, build_dependency_writing_workflow(), section_state
                )
            )
        if last_level_tasks:
            last_level_results = await asyncio.gather(*last_level_tasks)
            for i, section_id in enumerate(valid_section_ids):
                if i < len(last_level_results):
                    writing_results[section_id] = last_level_results[i]

        task_results = []
        for section in sections:
            wr = writing_results.get(section.id, {})
            result = dict(wr)
            result["plans"] = reasoning_results.get(section.id, {}).get("plans", result.get("plans", []))
            result["background_knowledge"] = self._get_background_knowledge_from_writing_results(
                self.get_parent_ids(section.id, current_outline), writing_results
            )
            task_results.append(result)

        state = self._update_state(state, sections, sub_reports, task_results)
        ResultExporter.export_outline(state.get("outline"), state.get("session_id"))
        return self._post_handle(inputs, state, session, context)

    def _update_state(self, state: dict, sections: list[Section], sub_reports: list[SubReport], task_results: list):
        state = super()._update_state(state, sections, sub_reports, task_results)
        report: Report = state.get("report")
        for sub_report in report.sub_reports:
            for i, section in enumerate(sections):
                if section.id == sub_report.section_id and i < len(task_results):
                    sub_report.background_knowledge = task_results[i].get("background_knowledge", [])
                    break
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
        add_debug_log_wrapper(
            session,
            NodeDebugData(NodeId.DEPENDENCY_EDITOR_TEAM.value, 0, NodeType.MAIN.value,
                          output_content=str(algorithm_output).replace("\\n", "\n")),
        )
        next_node = NodeId.REPORTER.value
        current_report: Report = state.get("report")
        warning_info = state.get("warning_info", "")
        exception_info = state.get("exception_info", "")
        has_any_content = any(
            sub_report.content and (getattr(sub_report.content, "sub_report_content_text", "") or "").strip()
            for sub_report in (current_report.sub_reports or [])
        )
        if not current_report or not current_report.sub_reports or not has_any_content:
            empty_msg = format_exception_info(
                StatusCode.EDITORTEAM_MANAGER_EMPTY_SUB_REPORT,
                prefix=self.log_prefix,
            )
            warning_info = (warning_info + "\n" + empty_msg).strip()
            exception_info = (exception_info + "\n" + empty_msg).strip()
            next_node = NodeId.END.value
        if warning_info or exception_info:
            self._handle_warning_exception_info(session, added_warning=warning_info, added_exception=exception_info)
        logger.info(f"{self.log_prefix} End {self.__class__.__name__}.")
        return dict(next_node=next_node)
