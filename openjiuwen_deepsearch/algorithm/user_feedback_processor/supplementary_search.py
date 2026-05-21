# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import re
from dataclasses import dataclass, field

from openjiuwen_deepsearch.algorithm.user_feedback_processor.common import (
    UserFeedbackPromptInvoker,
    resolve_model_context_collector as _resolve_model_context_collector,
    resolve_session_collector as _resolve_session_collector,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.report_edit_utils import (
    strip_markup_in_range,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.section_locator import locate_section
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.collector_execution_service import (
    CollectorExecutionService,
    CollectorRunPlanConfig,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Plan, Step, StepType
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SupplementaryRewriteContext:
    """封装补充搜索重写阶段所需的相关上下文。"""

    user_instruction: str
    selected_text_clean: str
    section_text_clean: str
    collector_summary: str
    doc_infos: list = field(default_factory=list)
    language: str = "zh-CN"


class SupplementarySearcher(UserFeedbackPromptInvoker):
    """先按选区/章节触发信息采集，再基于检索结果改写报告（补充搜索）。"""

    def __init__(self, llm_model_name: str):
        self.llm_model_name = llm_model_name

    @staticmethod
    def _restore_original_section_separator(
        rewritten_section: str,
        original_section_text: str,
        trailing_content: str,
    ) -> str:
        """Restore the original section separator before trailing content.

        当 ``selected_and_related`` 改写整段 section 时，原 section 末尾通常包含与下一标题
        之间的一个或多个换行。LLM 输出经 ``strip()`` 后可能丢失这些分隔符，导致后续标题
        直接拼接到段落末尾。本方法仅在后方仍有内容时，将原 section 末尾的连续换行原样补回。

        Args:
            rewritten_section: LLM 返回的改写 section 文本。
            original_section_text: 原 section 去标记后的完整文本，用于提取末尾换行。
            trailing_content: 原报告中位于 section 之后、待重新拼接的尾部内容。

        Returns:
            str: 经过边界换行修正后的 section 文本。
        """
        if not trailing_content:
            return rewritten_section

        match = re.search(r"(\n+)$", original_section_text)
        if match:
            normalized_section = rewritten_section.rstrip("\n")
            return f"{normalized_section}{match.group(1)}"
        return rewritten_section

    async def supplementary_search(
        self,
        feedback: dict,
        final_result: dict,
        language: str,
    ) -> dict:
        """根据 ``rewrite_scope`` 仅改写选区或改写整段最小 enclosing 章节，并返回与同义改写一致的 result 字典结构。

        Args:
            feedback: 用户反馈信息，包含 action、rewrite_scope、selected_text 等字段。
            final_result: 当前报告的完整结果，包含 response_content、citation_messages 等。
            language: 当前报告的语言标识。

        Returns:
            dict: 改写结果字典，包含 new_report、original_text、rewritten_text 等字段。
        """
        report_content = final_result.get("response_content", "") or ""
        rewrite_scope = feedback.get("rewrite_scope") or "selected_only"
        if rewrite_scope == "selected_and_related":
            return await self._run_selected_and_related(
                feedback=feedback,
                report_content=report_content,
                final_result=final_result,
                language=language,
            )
        return await self._run_selected_only(
            feedback=feedback,
            report_content=report_content,
            final_result=final_result,
            language=language,
        )

    async def _run_selected_only(
        self,
        feedback: dict,
        report_content: str,
        final_result: dict,
        language: str,
    ) -> dict:
        """仅在用户选区内替换文本，并保留 citation / infer 元数据。

        Args:
            feedback: 用户反馈信息。
            report_content: 当前报告正文。
            final_result: 当前报告的完整结果。
            language: 当前报告的语言标识。

        Returns:
            dict: 改写结果字典，包含 new_report、original_text、rewritten_text 等字段。
        """
        # 定位包含用户选区的最小章节范围
        section = locate_section(report_content, feedback["start_offset"], feedback["end_offset"])

        # 剥离选区内的标记，避免将 checked_citation / inference 标记送入重写链路
        stripped_selection_report, _, _ = strip_markup_in_range(
            report_content,
            feedback["start_offset"],
            feedback["end_offset"],
        )
        # 计算因标记移除导致的长度变化，并获取清理后的选区文本
        delta_sel = len(report_content) - len(stripped_selection_report)
        cleaned_selected_end = feedback["end_offset"] - delta_sel
        selected_text_clean = stripped_selection_report[feedback["start_offset"]:cleaned_selected_end]

        # 剥离整个章节内的标记，用于构建检索任务的上下文
        section_stripped, _, _ = strip_markup_in_range(
            report_content,
            section.section_start_offset,
            section.section_end_offset,
        )
        delta_sec = len(report_content) - len(section_stripped)
        cleaned_section_end = section.section_end_offset - delta_sec
        section_text_clean = section_stripped[section.section_start_offset:cleaned_section_end]

        # 调用LLM生成检索任务描述
        research_task = await self._build_research_task(
            user_instruction=feedback.get("user_instruction", ""),
            selected_text_clean=selected_text_clean,
            section_text_clean=section_text_clean,
            language=language,
        )
        # 执行信息采集，获取相关文档和摘要
        collection = await self._run_collection(
            research_task=research_task,
            language=language,
        )
        rewrite_context = SupplementaryRewriteContext(
            user_instruction=feedback.get("user_instruction", ""),
            selected_text_clean=selected_text_clean,
            section_text_clean=section_text_clean,
            collector_summary=collection.get("info_summary", ""),
            doc_infos=collection.get("doc_infos", []),
            language=language,
        )
        # 基于采集结果重写选区文本
        rewritten_selected = await self._rewrite_selected_only(rewrite_context)

        # 拼接生成新的报告内容
        new_report = (
            stripped_selection_report[: feedback["start_offset"]]
            + rewritten_selected
            + stripped_selection_report[cleaned_selected_end:]
        )
        rewritten_end_offset = feedback["start_offset"] + len(rewritten_selected)

        # 获取原始选区文本用于返回
        original_sel = report_content[feedback["start_offset"]:feedback["end_offset"]]
        logger.debug(
            "[SupplementarySearcher] original_len=%s, rewritten_len=%s",
            len(original_sel),
            len(rewritten_selected),
        )
        if not LogManager.is_sensitive():
            logger.debug("[SupplementarySearcher] original_text: %s", original_sel)
            logger.debug("[SupplementarySearcher] rewritten_text: %s", rewritten_selected)
        return {
            "new_report": new_report,
            "original_text": original_sel,
            "original_start_offset": feedback["start_offset"],
            "original_end_offset": feedback["end_offset"],
            "original_text_clean": selected_text_clean,
            "rewritten_text": rewritten_selected,
            "rewritten_start_offset": feedback["start_offset"],
            "rewritten_end_offset": rewritten_end_offset,
            "section_start_offset": section.section_start_offset,
            "section_end_offset": section.section_end_offset,
            "collector_summary": collection.get("info_summary", ""),
        }

    async def _run_selected_and_related(
        self,
        feedback: dict,
        report_content: str,
        final_result: dict,
        language: str,
    ) -> dict:
        """以 ``locate_section`` 得到的最小标题块为替换范围，并保留 citation / infer 元数据。

        Args:
            feedback: 用户反馈信息。
            report_content: 当前报告正文。
            final_result: 当前报告的完整结果。
            language: 当前报告的语言标识。

        Returns:
            dict: 改写结果字典，包含 new_report、original_text、rewritten_text 等字段。
        """
        # 定位包含用户选区的最小章节范围
        section = locate_section(report_content, feedback["start_offset"], feedback["end_offset"])
        # 剥离整个章节内的标记，避免将 checked_citation / inference 标记送入重写链路
        stripped_report, _, _ = strip_markup_in_range(
            report_content,
            section.section_start_offset,
            section.section_end_offset,
        )
        # 剥离选区内的标记，用于获取选区纯文本
        stripped_selection_report, _, _ = strip_markup_in_range(
            report_content,
            feedback["start_offset"],
            feedback["end_offset"],
        )

        # 计算因标记移除导致的长度变化，并获取清理后的章节和选区文本
        cleaned_section_end = section.section_end_offset - (len(report_content) - len(stripped_report))
        cleaned_selected_end = feedback["end_offset"] - (len(report_content) - len(stripped_selection_report))
        section_text_clean = stripped_report[section.section_start_offset:cleaned_section_end]
        selected_text_clean = stripped_selection_report[feedback["start_offset"]:cleaned_selected_end]

        # 调用LLM生成检索任务描述
        research_task = await self._build_research_task(
            user_instruction=feedback.get("user_instruction", ""),
            selected_text_clean=selected_text_clean,
            section_text_clean=section_text_clean,
            language=language,
        )
        # 执行信息采集，获取相关文档和摘要
        collection = await self._run_collection(
            research_task=research_task,
            language=language,
        )
        rewrite_context = SupplementaryRewriteContext(
            user_instruction=feedback.get("user_instruction", ""),
            selected_text_clean=selected_text_clean,
            section_text_clean=section_text_clean,
            collector_summary=collection.get("info_summary", ""),
            doc_infos=collection.get("doc_infos", []),
            language=language,
        )
        # 基于采集结果重写整个章节
        rewritten_section = await self._rewrite_selected_and_related(rewrite_context)
        trailing_content = stripped_report[cleaned_section_end:]
        # 保留原 section 与后续内容之间的换行数量，避免 Markdown 标题黏连且格式漂移。
        rewritten_section = self._restore_original_section_separator(
            rewritten_section=rewritten_section,
            original_section_text=section_text_clean,
            trailing_content=trailing_content,
        )

        # 拼接生成新的报告内容
        new_report = (
            stripped_report[: section.section_start_offset]
            + rewritten_section
            + trailing_content
        )
        rewritten_end_offset = section.section_start_offset + len(rewritten_section)

        return {
            "new_report": new_report,
            "original_text": section.section_text,
            "original_start_offset": section.section_start_offset,
            "original_end_offset": section.section_end_offset,
            "original_text_clean": selected_text_clean,
            "rewritten_text": rewritten_section,
            "rewritten_start_offset": section.section_start_offset,
            "rewritten_end_offset": rewritten_end_offset,
            "section_start_offset": section.section_start_offset,
            "section_end_offset": section.section_end_offset,
            "collector_summary": collection.get("info_summary", ""),
        }

    async def _build_research_task(
        self,
        user_instruction: str,
        selected_text_clean: str,
        section_text_clean: str,
        language: str,
    ) -> str:
        """调用 LLM 生成供信息采集子图使用的自然语言检索任务描述。

        Args:
            user_instruction: 用户输入的补充指令。
            selected_text_clean: 剥离标记后的选中纯文本。
            section_text_clean: 剥离标记后的章节纯文本。
            language: 当前报告的语言标识。

        Returns:
            str: LLM 生成的检索任务描述。
        """
        response = await self._invoke_prompt(
            "supplementary_search_task",
            {
                "language": language,
                "user_instruction": user_instruction,
                "selected_text_clean": selected_text_clean,
                "section_text_clean": section_text_clean,
            },
            AgentLlmName.USER_FEEDBACK_PROCESSOR_SUPPLEMENTARY_SEARCH_TASK.value,
        )
        return response.strip()

    async def _run_collection(self, research_task: str, language: str) -> dict:
        """依赖 ``session_context`` 中的会话执行单步 ``INFO_COLLECTING`` 计划并汇总摘要与文档。

        ``model_context`` 可为 ``None``：采集子图当前仅依赖 session 与 ``llm_context`` 等，
        openjiuwen 子图 ``invoke(..., context=None)`` 可执行。

        Args:
            research_task: 供信息采集子图执行的检索任务描述。
            language: 当前报告语言。

        Returns:
            dict: 包含 ``info_summary`` 和 ``doc_infos`` 的采集结果快照。

        Raises:
            CustomValueException: 当上下文中缺少 session，无法执行采集子图时抛出。
        """
        # 从上下文变量中解析当前会话和模型上下文
        session = _resolve_session_collector()
        context = _resolve_model_context_collector()
        # 仅需会话；ModelContext 允许为 None 并原样传入子图 invoke
        if session is None:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(
                    e="Supplementary search requires session."
                ),
            )

        # 获取当前反馈交互的计数，用于生成唯一的计划ID
        feedback_interaction_count = session.get_global_state("search_context.feedback_interaction_count")
        # 构建单步信息采集计划，包含一个INFO_COLLECTING类型的步骤
        plan = Plan(
            id=str(feedback_interaction_count),
            language=language,
            title="Supplementary search",
            thought="Collect focused evidence for the selected report section.",
            is_research_completed=False,
            steps=[
                Step(
                    type=StepType.INFO_COLLECTING,
                    title="Supplementary search",
                    description=research_task,
                )
            ],
        )
        # 创建采集执行服务实例并运行计划
        service = CollectorExecutionService()
        result = await service.run_plan(
            plan=plan,
            run_config=CollectorRunPlanConfig(
                language=language,
                section_idx="supplementary_search",
                initial_search_query_count=session.get_global_state(
                    "config.info_collector_initial_search_query_count"
                ),
                max_research_loops=session.get_global_state(
                    "config.info_collector_max_research_loops"
                ),
                max_react_recursion_limit=session.get_global_state(
                    "config.info_collector_max_react_recursion_limit"
                ),
            ),
            session=session,
            context=context,
        )
        # 返回采集结果，包含信息摘要和文档信息列表
        return {
            "info_summary": result.info_summary or "",
            "doc_infos": result.doc_infos or [],
        }

    async def _rewrite_selected_only(
        self,
        rewrite_context: SupplementaryRewriteContext,
    ) -> str:
        """在 ``selected_only`` 模式下生成仅替换选区的新正文。

        Args:
            rewrite_context: 补充搜索重写上下文。

        Returns:
            str: 用于替换原选区的新正文。
        """
        response = await self._invoke_prompt(
            "supplementary_search_rewrite_selected_only",
            {
                "language": rewrite_context.language,
                "user_instruction": rewrite_context.user_instruction,
                "selected_text_clean": rewrite_context.selected_text_clean,
                "section_text_clean": rewrite_context.section_text_clean,
                "collector_summary": rewrite_context.collector_summary,
                "doc_infos": rewrite_context.doc_infos,
            },
            AgentLlmName.USER_FEEDBACK_PROCESSOR_SUPPLEMENTARY_SEARCH_REWRITE_SELECTED_ONLY.value,
        )
        return response.strip()

    async def _rewrite_selected_and_related(
        self,
        rewrite_context: SupplementaryRewriteContext,
    ) -> str:
        """在 ``selected_and_related`` 模式下生成整段章节的新正文。

        Args:
            rewrite_context: 补充搜索重写上下文。

        Returns:
            str: 用于替换 enclosing 章节的完整新正文。
        """
        response = await self._invoke_prompt(
            "supplementary_search_rewrite_selected_and_related",
            {
                "language": rewrite_context.language,
                "user_instruction": rewrite_context.user_instruction,
                "selected_text_clean": rewrite_context.selected_text_clean,
                "section_text_clean": rewrite_context.section_text_clean,
                "collector_summary": rewrite_context.collector_summary,
                "doc_infos": rewrite_context.doc_infos,
            },
            AgentLlmName.USER_FEEDBACK_PROCESSOR_SUPPLEMENTARY_SEARCH_REWRITE_SELECTED_AND_RELATED.value,
        )
        return response.strip()
