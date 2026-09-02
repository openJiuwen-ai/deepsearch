# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Report generation orchestrator.

The ``Reporter`` class is split across mixin modules by responsibility:

- :mod:`markdown_utils` — heading cleanup, format validation, table of contents
- :mod:`visualization` — chart data extraction and Mermaid code generation
- :mod:`visualization_insertion` — chart insertion into report body
- :mod:`evidence` — rationale generation, extractive summarization, scoring
- :mod:`report_parts` — abstract, conclusion, sidecar, transition generation
- :mod:`sub_section_outline` — subsection outline generation
- :mod:`reference_utils` — reference deduplication and renumbering
- :mod:`retry_feedback` — controlled retry feedback construction
- :mod:`background_knowledge` — background knowledge extraction and formatting

This module keeps only the core orchestration: ``__init__``, report/sub-report
generation flow, and small utility static methods.
"""

import asyncio
import logging
import re
import uuid

from tenacity import RetryError

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.report.report_common import (
    EFFECT_SUB_REPORT_TAG,
    _format_report_error,
    _format_sub_report_error,
)
from openjiuwen_deepsearch.algorithm.report.report_utils import (
    ArticlePart,
    MarkdownOutlineRenumber,
    _section_sort_key,
    export_outline_without_plans,
    resolve_current_subsection,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Outline,
    build_research_intent_prompt_context,
    build_section_local_contract_prompt_context,
    build_temporal_scope_prompt_context,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.common_utils.stream_utils import (
    get_current_time,
    MessageType,
    StreamEvent,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    llm_context,
    session_context,
)

# ── Mixin imports ───────────────────────────────────────────────────────────
from openjiuwen_deepsearch.algorithm.report.markdown_utils import MarkdownProcessorMixin
from openjiuwen_deepsearch.algorithm.report.visualization import VisualizationMixin
from openjiuwen_deepsearch.algorithm.report.visualization_insertion import (
    VisualizationInsertionMixin,
    VisualizationInsertPlanContext,  # noqa: F401  re-export for backward compat
    VisualizationInsertRenderContext,  # noqa: F401  re-export for backward compat
)
from openjiuwen_deepsearch.algorithm.report.evidence import (
    EvidenceMixin,
    PassageSelectionContext,  # noqa: F401  re-export for backward compat
    TemporalSelectionOptions,  # noqa: F401  re-export for backward compat
    ensure_exact_target_documents,  # noqa: F401  re-export for backward compat
)
from openjiuwen_deepsearch.algorithm.report.report_parts import ReportPartsMixin
from openjiuwen_deepsearch.algorithm.report.sub_section_outline import SubSectionOutlineMixin
from openjiuwen_deepsearch.algorithm.report.reference_utils import (
    ReferenceMixin,
    _deduplicate_and_renumber_ref,  # noqa: F401  re-export for backward compat
    _replace_citations_and_classified_index,  # noqa: F401  re-export for backward compat
)
from openjiuwen_deepsearch.algorithm.report.retry_feedback import RetryFeedbackMixin
from openjiuwen_deepsearch.algorithm.report.background_knowledge import BackgroundKnowledgeMixin

logger = logging.getLogger(__name__)


class Reporter(
    MarkdownProcessorMixin,
    VisualizationMixin,
    VisualizationInsertionMixin,
    EvidenceMixin,
    ReportPartsMixin,
    SubSectionOutlineMixin,
    ReferenceMixin,
    RetryFeedbackMixin,
    BackgroundKnowledgeMixin,
):
    """Core report orchestrator.

    All methods are provided by the mixin classes listed above.  This class
    adds only the top-level orchestration flow and small utility statics.
    """

    def __init__(self, llm_model_name):
        # Keep consistent with other modules: workflow/template_generator registers
        # into llm_context at session; fetch by model name here.
        self._llm = llm_context.get().get(llm_model_name)
        self.gen_report_context = None

    # ── Utility statics ────────────────────────────────────────────────────

    @staticmethod
    def _make_payload(message_id: str, event: str, content: str = "") -> dict:
        payload = {
            "message_id": message_id,
            "agent": NodeId.SUB_REPORTER.value,
            "content": content,
            "message_type": MessageType.MESSAGE_CHUNK.value,
            "event": event,
            "created_time": get_current_time()
        }
        return payload

    @staticmethod
    def get_section_title_by_id(index, current_outline):
        """根据 section id 从大纲中获取章节标题。"""
        if not current_outline or not isinstance(current_outline, Outline):
            logger.warning("can not get section title for current outline is invalid.")
            return ""
        if index < 0 or index >= len(current_outline.sections):
            logger.warning("can not get section title for index is out of range.")
            return ""
        return current_outline.sections[index].title

    @staticmethod
    def export_outline_without_plans(outline: Outline | dict):
        """导出不包含执行计划信息的大纲结构。"""
        return export_outline_without_plans(outline)

    @staticmethod
    def _is_missing_subsection_report_context(
        section_task: str,
        sub_section_outline: str,
        has_collected_infos: bool,
        has_background_knowledge: bool,
    ) -> bool:
        """Check whether subsection report generation lacks required context."""
        if not section_task:
            return True
        if not sub_section_outline:
            return True
        return not (has_collected_infos or has_background_knowledge)

    # ── Report-level orchestration ──────────────────────────────────────────

    async def generate_report(self, gen_report_context: dict) -> tuple[bool, str]:
        """
        generate general report according to report_style/report_format/report_lang.

        Args:
            gen_report_context: the context which generate report needed

        Returns:
            tuple[bool, str]: The response.
                bool: Is request success.
                str: Success: Report path (maybe empty), Error: Error messages.
        """
        if LogManager.is_sensitive():
            logger.debug("[generate_report] generate start")
        else:
            logger.debug(
                "[generate_report] generate start, gen_report_context: %s",
                gen_report_context,
            )
        if not self._set_context_variables(gen_report_context):
            logger.error(f"[generate_report] Error: Set context variables failed")
            return False, _format_report_error("Set context variables failed")

        self.gen_report_context["current_outline"] = export_outline_without_plans(
            self.gen_report_context.get("current_outline", {})
        )
        sub_report_res = await self._process_sub_report()
        if not sub_report_res.get("sub_reports_content"):
            logger.error(f"[generate_report] Error: No sub-reports content found")
            return False, _format_report_error("No sub-reports content found")
        gen_report_context["all_classified_contents"] = sub_report_res.get(
            "refreshed_all_classified_contents"
        )

        abstract_context = self._build_reporter_compact_context("abstract")
        conclusion_context = self._build_reporter_compact_context("conclusion")
        sub_reports_content = sub_report_res.get("sub_reports_content")
        abstract_task = asyncio.create_task(
            self.generate_abstract(abstract_context or sub_reports_content)
        )
        conclusion_task = asyncio.create_task(
            self.generate_conclusion(conclusion_context or sub_reports_content)
        )

        try:
            abstract = await abstract_task
            conclusion = await conclusion_task
        except RetryError as retry_err:
            logger.error(
                f"[generate_report] Report generation failed after retries: {retry_err}"
            )
            return False, _format_report_error(retry_err)
        except Exception as e:
            if LogManager.is_sensitive():
                logger.error(
                    f"[generate_report] Unexpected error during report generation"
                )
                return False, _format_report_error("Unexpected error during report generation")
            logger.error(
                f"[generate_report] Unexpected error during report generation: {e}"
            )
            return False, _format_report_error(e)

        current_outline = self.gen_report_context.get("current_outline", "")
        if not current_outline:
            error_message = "has no current outline"
            logger.error(f"[generate_report] Generate report error: {error_message}")
            return False, _format_report_error(error_message)

        if isinstance(current_outline, dict):
            _outline_title = current_outline.get("title", "")
        else:
            _outline_title = getattr(current_outline, "title", "")
        table_of_contents = self._build_table_of_contents(
            sub_reports_content,
            gen_report_context["language"],
        )
        # 给正文每个一级章节插入与目录链接对应的 #chapter-N 锚点，使原生 Markdown
        # 报告的目录可点击跳转（导出层会把锚点替换为 {#chapter-N} 属性）
        anchored_sub_reports_content = self._add_chapter_anchor_ids(
            sub_reports_content
        )
        report_content = (
            f"{'# ' + _outline_title}\n\n"  # Use outline title directly for report title
            f"{table_of_contents}\n\n"
            f"{self._post_process_abstract(abstract)}\n\n"
            f"{anchored_sub_reports_content}\n\n"
            f"{self._post_process_conclusion(conclusion)}\n\n"
            f"{ArticlePart.get_title('reference', gen_report_context['language'])}"
            f"{sub_report_res.get('sub_references')}\n\n"
        )

        self.gen_report_context["report"] = report_content
        if LogManager.is_sensitive():
            logger.debug("[generate_report] generate success")
        else:
            logger.debug(
                "[generate_report] generate success, general report content:\n[%s]",
                report_content,
            )

        if not report_content.strip():
            logger.error("[generate_report] md report content is empty.")
            return False, _format_report_error("md report content empty")

        return True, "success"

    def _set_context_variables(self, gen_report_context: dict) -> bool:
        """Set context to instance"""
        if gen_report_context is None:
            return False
        self.gen_report_context = gen_report_context
        rtp = self.gen_report_context.get("report_type_policy")
        if isinstance(rtp, dict):
            self.gen_report_context.setdefault("report_type", rtp.get("report_type", "professional"))
            self.gen_report_context.setdefault("paragraph_style", rtp.get("paragraph_style", "detailed"))
            self.gen_report_context.setdefault(
                "require_summary_first", rtp.get("require_summary_first", False)
            )
            self.gen_report_context.setdefault(
                "require_methodology_and_risk", rtp.get("require_methodology_and_risk", False)
            )
        self.gen_report_context.update(
            build_research_intent_prompt_context(
                self.gen_report_context.get("research_intent")
            )
        )
        return True

    async def _process_sub_report(self) -> dict:
        """Process sub reports"""
        sub_reports_content = []
        sub_references = []
        all_classified_contents = self.gen_report_context.get(
            "all_classified_contents", []
        )
        # 从 Report 对象中获取 sub_reports
        current_report = self.gen_report_context.get("current_report")
        if (
            not current_report
            or not hasattr(current_report, "sub_reports")
            or not current_report.sub_reports
        ):
            logger.error(
                "Current_report not found in context or sub_reports is empty; use empty content."
            )
            return dict(
                sub_reports_content="",
                sub_references="",
                refreshed_all_classified_contents=[],
            )

        # 从 Report.sub_reports 构建 sub_report_content_list
        sub_report_content_list = []
        for sub_report in current_report.sub_reports:
            sub_report_item = type(
                "SubReportItem",
                (),
                {
                    "section_id": sub_report.section_id,
                    "content": (
                        sub_report.content.sub_report_content_text
                        if sub_report.content
                        else ""
                    ),
                    "content_summary": (
                        sub_report.content.sub_report_content_summary
                        if sub_report.content
                        else ""
                    ),
                },
            )()
            sub_report_content_list.append(sub_report_item)

        if not sub_report_content_list or all(
            not item.content for item in sub_report_content_list
        ):
            logger.error("All content in sub_reports is empty; use empty content.")
            return dict(
                sub_reports_content="",
                sub_references="",
                refreshed_all_classified_contents=[],
            )

        outline_renum = MarkdownOutlineRenumber()

        # Keep section ordering stable when section ids are stored as strings.
        sub_report_content_list.sort(
            key=lambda x: _section_sort_key(x.section_id)
        )

        transition_tasks = []
        transition_indices = []
        for index, item in enumerate(sub_report_content_list):
            if not item or not item.content:
                logger.error(
                    f"sub report content is empty and sub report index is {index + 1}"
                )
                continue
            section_content = item.content
            if section_content:
                # Renumber subsection indices
                section_content = outline_renum.renumber_headers(section_content)
                if index == 0:
                    current_inputs = dict(
                        title_prev="",
                        summary_prev="",
                        title_next=self.get_section_title_by_id(
                            index, self.gen_report_context.get("current_outline", None)
                        ),
                        summary_next=item.content_summary,
                        language=self.gen_report_context.get("language", "zh-CN"),
                        user_query=self.gen_report_context.get("report_task", ""),
                        content=section_content,
                        section_idx=index + 1,
                    )
                    transition_tasks.append(
                        asyncio.create_task(
                            self._add_sub_report_transaction(current_inputs)
                        )
                    )
                    transition_indices.append(index)
                elif index > 0:
                    current_inputs = dict(
                        title_prev=self.get_section_title_by_id(
                            index - 1,
                            self.gen_report_context.get("current_outline", None),
                        ),
                        summary_prev=sub_report_content_list[index - 1].content_summary,
                        title_next=self.get_section_title_by_id(
                            index, self.gen_report_context.get("current_outline", None)
                        ),
                        summary_next=item.content_summary,
                        language=self.gen_report_context.get("language", "zh-CN"),
                        user_query=self.gen_report_context.get("report_task", ""),
                        content=section_content,
                        section_idx=index + 1,
                    )
                    transition_tasks.append(
                        asyncio.create_task(
                            self._add_sub_report_transaction(current_inputs)
                        )
                    )
                    transition_indices.append(index)
        tasks_results = await asyncio.gather(*transition_tasks)
        for index, section_content in zip(transition_indices, tasks_results):
            if not section_content:
                logger.error(
                    f"section content is empty and sub report index is {index + 1}"
                )
                continue
            sub_report_content_list[index].content = section_content
            # Split sub-report content and references
            ref_split = re.split(
                r"#+\s*[0-9.]*\s*(参考文章|References)\s*",
                section_content,
                flags=re.IGNORECASE,
            )
            if len(ref_split) >= 3:
                content_part = ref_split[0].strip()
                references = ref_split[2].strip()
                sub_references.append(references if references else "")
                sub_reports_content.append(content_part)
            else:
                sub_references.append("")
                sub_reports_content.append(section_content.strip())
        logger.info(f"子章节标题重排记录：{outline_renum.history}")

        return self.refresh_reference(
            sub_reports_content, sub_references, all_classified_contents
        )

    async def generate_sub_report(
        self, current_inputs: dict
    ) -> tuple[bool, str, str, list]:
        """生成子章节报告。

        Args:
            current_inputs: 子章节生成所需的上下文参数。

        Returns:
            元组，依次表示是否成功、报告内容、子报告内容和分类后的内容列表。
        """
        section_idx = current_inputs.get("section_idx", 1)
        logger.info(
            f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] start to generate subsection report, "
            f"section_idx: [{section_idx}]"
        )
        if LogManager.is_sensitive():
            logger.info(
                f"{EFFECT_SUB_REPORT_TAG} section_idx: [{section_idx}], "
                f"passages len: {len(current_inputs.get('passages', []))}"
            )
        else:
            logger.debug(
                "%s [generate_sub_report] section_idx: [%s], passages is %s",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                current_inputs.get("passages", []),
            )
        rtp = current_inputs.get("report_type_policy") or {}
        if isinstance(rtp, dict):
            current_inputs.setdefault("report_type", rtp.get("report_type", "professional"))
            current_inputs.setdefault("paragraph_style", rtp.get("paragraph_style", "detailed"))
            current_inputs.setdefault("require_summary_first", rtp.get("require_summary_first", False))
            current_inputs.setdefault(
                "require_methodology_and_risk", rtp.get("require_methodology_and_risk", False)
            )
        raw_passages = current_inputs.get("passages", [])
        background_contents = self._get_background_knowledge_contents(
            current_inputs.get("sub_report_background_knowledge", [])
        )
        if not raw_passages:
            if not background_contents:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] fail to generate subsection report, "
                    f"section_idx: [{section_idx}], not found passages"
                )
                return False, _format_sub_report_error("Not found passages"), "", []
            logger.info(
                "%s [generate_sub_report] section_idx: [%s], no passages found, "
                "use dependency background knowledge as fallback.",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
            )
            current_inputs["sub_section_core_content"] = background_contents
            current_inputs["sub_section_core_content_from_background_knowledge"] = True
            current_inputs["sub_section_references"] = []
            current_inputs["classified_content"] = []
            current_inputs["structured_evidence_guide"] = ""
            classified_content = []
        else:
            ev_ok, ev_err, classified_content = await self._prepare_evidence(current_inputs, raw_passages, section_idx)
            if not ev_ok:
                return False, _format_sub_report_error(ev_err), "", []
        if not LogManager.is_sensitive():
            logger.debug(
                "%s [generate_sub_report] section_idx: [%s], sub section content is: [%s], "
                "sub section references: [%s], classified content: [%s]",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                current_inputs.get("sub_section_core_content", []),
                current_inputs.get("sub_section_references", []),
                current_inputs.get("classified_content", []),
            )

        max_attempt_num = current_inputs.get("max_generate_retry_num", 3)
        outline_ok, outline_err = await self._generate_outline_with_retry(current_inputs, section_idx, max_attempt_num)
        if not outline_ok:
            return False, _format_sub_report_error(outline_err), "", classified_content

        if current_inputs.get("visualization_enable", True):
            try:
                visualization_result = await self._generate_content_for_visualization(
                    current_inputs
                )
                current_inputs["visualization_result"] = visualization_result[
                    "visualization_content"
                ]
            except Exception as e:
                logger.warning(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}] "
                    f"visualization generation failed, skip visuals: {str(e)}"
                )
                current_inputs["visualization_result"] = []

        return await self._write_with_retry(current_inputs, max_attempt_num, section_idx, classified_content)

    async def _write_with_retry(
        self, current_inputs: dict, max_attempt_num: int, section_idx, classified_content: list
    ) -> tuple[bool, str, str, list]:
        """Write sub-section report with retry loop.

        Returns (success, result, sub_report_content, classified_content).
        """
        session = session_context.get()
        stream_id = str(uuid.uuid4())
        write_retry_feedback = ""
        for attempt_num in range(max_attempt_num):
            write_res = await self._write_subsection_reports(current_inputs)
            if write_res["success"]:
                if LogManager.is_sensitive():
                    logger.info(
                        f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                        f"reports generated: successfully"
                    )
                else:
                    logger.info(
                        f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                        f"reports generated: {write_res['result']}"
                    )
                await session.write_custom_stream(
                    self._make_payload(stream_id, StreamEvent.SUMMARY_RESPONSE.value, "SUCCESS"))
                return (
                    True,
                    write_res["result"],
                    current_inputs.get("sub_report_content", ""),
                    classified_content,
                )
            write_retry_feedback = write_res.get("result", "") or ""
            detail = "" if LogManager.is_sensitive() else f": {write_retry_feedback}"
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                f"Warning: Generate section report failed on attempt {attempt_num + 1}/{max_attempt_num}"
                f"{detail}. retry ..."
            )
            current_inputs["sub_report_retry_feedback"] = (
                self._sub_report_retry_feedback_from_failure(write_retry_feedback)
            )
            await session.write_custom_stream(
                self._make_payload(
                    stream_id,
                    StreamEvent.SUMMARY_RESPONSE.value,
                    "generate section report fail"
                )
            )
            if attempt_num == max_attempt_num - 1:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"Error: Generate section report failed, reach the max_attempt_num: {max_attempt_num}."
                )
        return False, _format_sub_report_error("generate section report fail"), "", classified_content

    async def _write_subsection_reports(self, current_inputs: dict) -> dict:
        """Write subsection report to disk"""
        if LogManager.is_sensitive():
            logger.info(
                f"{EFFECT_SUB_REPORT_TAG} [write_subsection_reports] Starting section_idx: "
                f"{current_inputs.get('section_idx', 1)}"
            )
        else:
            logger.debug(
                "%s [write_subsection_reports] Starting section_idx: %s, current_inputs: %s",
                EFFECT_SUB_REPORT_TAG,
                current_inputs.get("section_idx", 1),
                current_inputs,
            )
        # Extract section core information
        section_task = self.strip_leading_number(
            current_inputs.get("section_task", "")
        )  # Current section title
        # Validate required fields
        has_collected_infos = bool(current_inputs.get("classified_content", []))
        background_knowledge_contents = self._get_background_knowledge_contents(
            current_inputs.get("sub_report_background_knowledge", [])
        )
        # 输出背景知识分析
        logger.debug(
            "%s [write_subsection_reports] section_idx: %s, background_knowledge_contents: %s",
            EFFECT_SUB_REPORT_TAG,
            current_inputs.get("section_idx", 1),
            background_knowledge_contents,
            extra={"skip_truncation": True},
        )
        has_background_knowledge = bool(background_knowledge_contents)
        if self._is_missing_subsection_report_context(
            section_task=section_task,
            sub_section_outline=current_inputs.get("sub_section_outline", ""),
            has_collected_infos=has_collected_infos,
            has_background_knowledge=has_background_knowledge,
        ):
            missing_contexts = []
            if not section_task:
                missing_contexts.append("section_task")
            if not current_inputs.get("sub_section_outline", ""):
                missing_contexts.append("sub_section_outline")
            if not has_collected_infos and not has_background_knowledge:
                missing_contexts.append("classified_content/sub_report_background_knowledge")
            error_msg = (
                "Missing required context for sub report generation: "
                + ", ".join(missing_contexts)
            )
            current_inputs["sub_report_content"] = ""
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [write_subsection_reports] section_idx: "
                f"{current_inputs.get('section_idx', 1)} {error_msg}"
            )
            return dict(success=False, result=error_msg)

        if not LogManager.is_sensitive():
            logger.debug(
                "%s [write_subsection_reports] Processing section %s: %s, (total report: %s),",
                EFFECT_SUB_REPORT_TAG,
                current_inputs.get("section_idx", 1),
                section_task,
                (current_inputs.get("report_task", "") or "unknown"),
            )
            logger.debug(
                "%s sub section outline: %s, section id is %s, classified content is %s",
                EFFECT_SUB_REPORT_TAG,
                current_inputs.get("sub_section_outline", ""),
                current_inputs.get("section_idx", 1),
                current_inputs.get("classified_content", []),
            )

        sub_content_message = self._build_subsection_prompt(current_inputs, section_task, background_knowledge_contents)
        current_section_description = current_inputs.get("section_description", "")
        current_section_format_requirements = current_inputs.get("section_format_requirements", [])
        current_chapter_outline = current_inputs.get("sub_section_outline", "")
        current_subsection = resolve_current_subsection(current_inputs)
        try:
            sub_report_prompt = "sub_report_markdown"
            llm_input = apply_system_prompt(
                sub_report_prompt,
                dict(
                    messages=[dict(role="user", content=sub_content_message)],
                    language=current_inputs.get("language"),
                    section_iscore=current_inputs.get("section_iscore", False),
                    audience_role=current_inputs.get("audience_role", ""),
                    tone=current_inputs.get("tone", ""),
                    current_section=section_task,
                    current_section_description=current_section_description,
                    current_section_format_requirements=current_section_format_requirements,
                    current_chapter_outline=current_chapter_outline,
                    current_subsection=current_subsection,
                    **build_section_local_contract_prompt_context(
                        current_inputs.get("section_local_contract")
                    ),
                    **build_research_intent_prompt_context(
                        current_inputs.get("research_intent")
                    ),
                    **build_temporal_scope_prompt_context(
                        current_inputs.get("research_intent")
                    ),
                ),
            )

            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [write_subsection_reports] section_idx: %s llm_input is %s",
                    EFFECT_SUB_REPORT_TAG,
                    current_inputs.get("section_idx", 1),
                    llm_input,
                )
            llm_output = await ainvoke_llm_with_stats(
                llm=self._llm,
                messages=llm_input,
                agent_name=AgentLlmName.SUB_REPORTER.value,
                need_stream_out=True,
            )
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [write_subsection_reports] section_idx: %s llm_output is %s",
                    EFFECT_SUB_REPORT_TAG,
                    current_inputs.get("section_idx", 1),
                    llm_output,
                )
            # Validate LLM output
            if not llm_output or not llm_output.get("content"):
                raise CustomValueException(
                    error_code=StatusCode.LLM_RESPONSE_ERROR.code,
                    message=f"LLM returned empty content for the section {current_inputs.get('section_idx', 1)}",
                )

            current_inputs["sub_report_content"] = llm_output.get("content", "")
            pp_ok, pp_err = await self._post_process_subsection(current_inputs)
            if not pp_ok:
                return dict(success=False, result=pp_err)
            return dict(success=True, result="success")
        except Exception as e:
            current_inputs["sub_report_content"] = ""
            error_detail = (
                f"Error generating section {current_inputs.get('section_idx', 1)} report: "
                f"{type(e).__name__}: {str(e)[:500]}"
            )
            if LogManager.is_sensitive():
                log_msg = f"Error generating section {current_inputs.get('section_idx', 1)} report"
                result_msg = log_msg
            else:
                log_msg = error_detail
                result_msg = error_detail
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [write_subsection_reports] {log_msg}",
                exc_info=True,
            )
            return dict(success=False, result=result_msg)

