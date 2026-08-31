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
        report_content = (
            f"{'# ' + _outline_title}\n\n"  # Use outline title directly for report title
            f"{table_of_contents}\n\n"
            f"{self._post_process_abstract(abstract)}\n\n"
            f"{sub_reports_content}\n\n"
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

    async def _generate_with_llm(self, task_type, prompt, content):
        if isinstance(self.gen_report_context, dict):
            self.gen_report_context["CURRENT_TIME"] = datetime.now(
                tz=timezone.utc
            ).strftime("%a %b %d %H:%M:%S %Y %Z")
        llm_input = apply_system_prompt(prompt, self.gen_report_context)
        llm_input.append(dict(role="user", content=f"Main Content: {content}\n\n"))
        if not LogManager.is_sensitive():
            logger.debug(
                "llm input when generating %s with llm: %s", task_type, llm_input
            )
        agent_name_by_task_type = {
            "abstract": AgentLlmName.REPORTER_ABSTRACT.value,
            "conclusion": AgentLlmName.REPORTER_CONCLUSION.value,
        }
        agent_name = agent_name_by_task_type.get(task_type)
        if agent_name is None:
            raise KeyError(f"Unsupported report task type: {task_type}")
        llm_output = await ainvoke_llm_with_stats(
            llm=self._llm,
            messages=llm_input,
            agent_name=agent_name,
        )
        if not LogManager.is_sensitive():
            logger.debug(
                "llm output when generating %s with llm: %s", task_type, llm_output
            )
        return llm_output.get("content")

    @staticmethod
    def _build_table_of_contents(sub_reports_content: str, language: str) -> str:
        """Build a clickable level-one TOC from the final body headings."""
        headings = Reporter._extract_level_one_headings(sub_reports_content)
        toc_title = ArticlePart.get_title("toc", language).strip()
        if not headings:
            return toc_title

        toc_entries = "\n\n".join(
            "[{0}](#chapter-{1})".format(heading["title"], index)
            for index, heading in enumerate(headings, start=1)
        )
        return f"{toc_title}\n\n{toc_entries}"

    @staticmethod
    def _extract_level_one_headings(sub_reports_content: str) -> list[dict]:
        """Extract real Markdown H1 headings while ignoring fenced code blocks."""
        headings = []
        fence_char = ""
        fence_length = 0

        offset = 0
        for line in (sub_reports_content or "").splitlines(keepends=True):
            content_line = line.rstrip("\r\n")
            if fence_char:
                closing_fence = re.match(r"^\s{0,3}(`{3,}|~{3,})\s*$", content_line)
                if closing_fence:
                    marker = closing_fence.group(1)
                    if marker[0] == fence_char and len(marker) >= fence_length:
                        fence_char = ""
                        fence_length = 0
                offset += len(line)
                continue

            opening_fence = re.match(r"^\s{0,3}(`{3,}|~{3,})", content_line)
            if opening_fence:
                marker = opening_fence.group(1)
                fence_char = marker[0]
                fence_length = len(marker)
                offset += len(line)
                continue

            heading_match = re.match(r"^\s{0,3}#(?!#)\s+(.+?)\s*$", content_line)
            if heading_match:
                heading = re.sub(
                    r"[ \t]+#+[ \t]*$", "", heading_match.group(1)
                ).strip()
                if heading:
                    headings.append({"title": heading, "offset": offset})
            offset += len(line)

        return headings

    def _post_process_abstract(self, content: str) -> str:
        language = self.gen_report_context["language"]
        if content is None or content == "":
            return ArticlePart.get_not_found_prompt("abstract", language)

        header = ArticlePart.get_title("abstract", language)
        content = re.sub(r"\[?citation:\d+\]?", "", content)
        content = _convert_bold_formula_to_inline_math(content)

        if language == CHINESE:
            if content.startswith("摘要") and len(content) >= 2:
                content = content[2:]
                if content and content[0] in ["：", ":", "—", "–", " ", "　"]:
                    content = content[1:]
                content = content.lstrip()
        elif language == ENGLISH:
            if content.lower().startswith("abstract") and len(content) >= 8:
                content = content[8:]
                if content and content[0] in [":", " ", "-"]:
                    content = content[1:]
                content = content.lstrip()

        if content.startswith(header):
            return content
        return header + content

    def _post_process_conclusion(self, content: str) -> str:
        language = self.gen_report_context["language"]
        if content is None or content == "":
            return ArticlePart.get_not_found_prompt("conclusion", language)
        header = ArticlePart.get_title("conclusion", language)
        content = re.sub(r"\[?citation:\d+\]?", "", content)
        if content.startswith(header):
            return content
        return header + content

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

    async def _add_sub_report_transaction(self, current_inputs: dict):
        if not LogManager.is_sensitive():
            logger.debug(
                "%s [_generate_sub_report_transaction] Starting section_idx: %s, current_inputs: %s",
                EFFECT_SUB_REPORT_TAG,
                current_inputs.get("section_idx", 1),
                current_inputs,
            )
        summary_prev = current_inputs.get("summary_prev", "")
        summary_next = current_inputs.get("summary_next", "")
        if not summary_prev and not summary_next:
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [_generate_sub_report_transaction] section_idx:"
                f"{current_inputs.get('section_idx', 1)}, source summary are empty."
            )
            return current_inputs.get("content", "")

        try:
            llm_input = apply_system_prompt(
                "generate_transition_sentence",
                dict(
                    section_id=current_inputs.get("section_idx", 1),
                    language=current_inputs.get("language", "zh-CN"),
                    title_prev=current_inputs.get("title_prev", ""),
                    title_next=current_inputs.get("title_next", ""),
                    summary_prev=summary_prev,
                    summary_next=summary_next,
                    user_query=current_inputs.get("user_query", ""),
                ),
            )

            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [_generate_sub_report_transaction] section_idx: %s llm_input is %s",
                    EFFECT_SUB_REPORT_TAG,
                    current_inputs.get("section_idx", 1),
                    llm_input,
                )

            retry_num = Config().service_config.report_max_generate_retry_num
            for i in range(retry_num):
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.REPORTER_TRANSACTION.value,
                )
                if not LogManager.is_sensitive():
                    logger.debug(
                        "%s [_generate_sub_report_transaction] section_idx: %s llm_output is %s",
                        EFFECT_SUB_REPORT_TAG,
                        current_inputs.get("section_idx", 1),
                        llm_output,
                    )

                # Validate LLM output
                if not llm_output or not llm_output.get("content"):
                    if i == retry_num - 1:
                        logger.warning(
                            f"{EFFECT_SUB_REPORT_TAG} [_generate_sub_report_transaction] "
                            f"generate transaction reach max attempt times."
                            f"section id is {current_inputs.get('section_idx', 1)}"
                        )
                        return current_inputs.get("content", "")
                else:
                    content = current_inputs.get("content", "")
                    old = current_inputs.get("title_next", "")
                    new = old + "\n" + llm_output.get("content")
                    msg = content.replace(old, new, 1)
                    return msg
        except Exception as e:
            if LogManager.is_sensitive():
                error_msg = (
                    f"Error while generating section {current_inputs.get('section_idx', 1)}"
                    f"report's transaction."
                )
            else:
                error_msg = (
                    f"Error generating section {current_inputs.get('section_idx', 1)}"
                    f"report's transaction: {str(e)}"
                )
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [_generate_sub_report_transaction] {error_msg}",
                exc_info=True,
            )
            return current_inputs.get("content", "")

    def _build_reporter_compact_context(self, target: str) -> str:
        """Build compact chapter context for abstract or conclusion generation."""
        current_report = self.gen_report_context.get("current_report")
        if not current_report or not current_report.sub_reports:
            return ""

        report_task = (
            self.gen_report_context.get("report_task") or current_report.report_task
        )
        context_parts = []
        if report_task:
            context_parts.append(f"Report task: {report_task}")

        sub_reports = sorted(
            current_report.sub_reports,
            key=lambda item: Reporter._section_sort_key(item.section_id),
        )
        for sub_report in sub_reports:
            content = sub_report.content
            if not content:
                continue
            sidecar = content.sub_report_chapter_sidecar
            summary = (
                sidecar.chapter_summary
                if sidecar
                else content.sub_report_content_summary
            )
            if not summary:
                continue

            summary_label = "Summary" if sidecar else "Summary (fallback)"
            chapter_parts = [
                f"Chapter {sub_report.section_id} {sub_report.section_task}",
                f"{summary_label}: {summary}",
            ]
            if sidecar:
                findings = sidecar.key_findings
                if target == "abstract":
                    findings = findings[:3]
                if findings:
                    chapter_parts.append(
                        "Key findings:\n" + "\n".join(f"- {item}" for item in findings)
                    )
                if target == "conclusion" and sidecar.risk_points:
                    chapter_parts.append(
                        "Risk points:\n" + "\n".join(f"- {item}" for item in sidecar.risk_points)
                    )
            context_parts.append("\n".join(chapter_parts))

        if not any(part.startswith("Chapter ") for part in context_parts):
            return ""
        return "\n\n".join(context_parts)

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
            key=lambda x: Reporter._section_sort_key(x.section_id)
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
                        title_next=Reporter.get_section_title_by_id(
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
                elif 0 < index < len(sub_report_content_list):
                    current_inputs = dict(
                        title_prev=Reporter.get_section_title_by_id(
                            index - 1,
                            self.gen_report_context.get("current_outline", None),
                        ),
                        summary_prev=sub_report_content_list[index - 1].content_summary,
                        title_next=Reporter.get_section_title_by_id(
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

        return Reporter.refresh_reference(
            sub_reports_content, sub_references, all_classified_contents
        )

    async def _generate_section_rationales(self, current_inputs: dict) -> tuple[list, str]:
        """Generate section information dimensions (rationales).

        Inspired by METEORA: LLM generates rationales from section context +
        step_result + evaluation, grounded on actually collected information
        to ensure the coverage matrix evaluation is meaningful.

        Args:
            current_inputs: context containing section info and step_summaries.

        Returns:
            (rationale list, last_error). On success the error string is "";
            after retry exhaustion the list is [] and last_error carries the
            final failure detail. Each retry appends the previous failure as a
            data-bounded retry_feedback user message after the system prompt.
        """
        section_idx = current_inputs.get("section_idx", 1)
        section_task = self.strip_leading_number(current_inputs.get("section_task", ""))
        section_description = current_inputs.get("section_description", "")
        report_task = current_inputs.get("report_task", "")
        overall_outline = Reporter.export_outline_without_plans(
            current_inputs.get("current_outline", {})
        )
        # Expand section_local_contract (nested dict) into top-level fields via the shared helper,
        # consistent with other prompt sites (report.py:2148, 3097).
        contract_ctx = build_section_local_contract_prompt_context(
            current_inputs.get("section_local_contract")
        )
        section_focus = contract_ctx.get("section_focus", "")
        focus_dimensions = contract_ctx.get("allowed_dimensions", [])
        step_summaries = current_inputs.get("step_summaries", [])

        step_summaries_text = "\n".join(
            f"  - Step {s.get('plan_idx', '')}-{s.get('step_idx', '')}: {s.get('title', '')}\n"
            f"    Description: {s.get('description', '')}\n"
            f"    Collected: {s.get('step_result', '')}\n"
            f"    Evaluation: {s.get('evaluation', '')}"
            for s in step_summaries
        ) if step_summaries else "  No step summaries available."

        focus_dimensions_text = ", ".join(focus_dimensions) if focus_dimensions else "None specified"

        # Build user message with data (including untrusted step summaries)
        # separated from system prompt to prevent prompt injection.
        user_content = (
            f"Report task: {report_task}\n"
            f"Overall outline: {overall_outline}\n\n"
            f"Chapter title: {section_task}\n"
            f"Chapter description: {section_description}\n"
            f"Chapter focus: {section_focus}\n"
            f"Focus dimensions: {focus_dimensions_text}\n"
            f"Research step summaries:\n{step_summaries_text}\n\n"
            "Generate rationales for this chapter."
        )
        tmp_context = {
            "messages": [dict(role="user", content=user_content)],
        }

        max_retries = current_inputs.get("max_generate_retry_num", 3)
        last_error = None
        retry_feedback = ""
        for attempt_num in range(max_retries):
            llm_input = apply_system_prompt("rationale_generator", tmp_context)
            _append_retry_feedback_message(llm_input, retry_feedback)
            try:
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_RATIONALE_GENERATOR.value,
                )
            except Exception as e:
                last_error = f"LLM call failed: {e}"
                retry_feedback = (
                    "LLM call failed" if LogManager.is_sensitive() else (last_error or "")[:500]
                )
                logger.warning(
                    "%s [generate_rationales] section_idx: [%s] attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            if not llm_output or not llm_output.get("content"):
                last_error = "LLM returned empty content"
                retry_feedback = (last_error or "")[:500]
                logger.warning(
                    "%s [generate_rationales] section_idx: [%s] attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            try:
                data = json.loads(normalize_json_output(llm_output.get("content", "")))
                rationales = data.get("rationales", [])
                # Post-process: truncate overlong descriptions and enforce quantity limits
                rationales = _normalize_rationales(rationales, max_rationales=15)
                primary_count = sum(1 for r in rationales if r.get("priority") == "primary")
                supplementary_count = len(rationales) - primary_count
                logger.info(
                    "%s [generate_rationales] section_idx: [%s] generated %s rationales "
                    "(primary: %s, supplementary: %s) (attempt %s/%s)",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    len(rationales), primary_count, supplementary_count,
                    attempt_num + 1, max_retries,
                )
                return rationales, ""
            except Exception as e:
                last_error = f"failed to parse LLM output: {e}"
                retry_feedback = (
                    "failed to parse LLM output"
                    if LogManager.is_sensitive()
                    else (last_error or "")[:500]
                )
                logger.warning(
                    "%s [generate_rationales] section_idx: [%s] attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

        logger.error(
            "%s [generate_rationales] section_idx: [%s] failed after %s attempts: %s",
            EFFECT_SUB_REPORT_TAG, section_idx,
            max_retries, last_error,
        )
        return [], (last_error or "unknown rationale error")

    @staticmethod
    async def _gather_with_limit(tasks: list, limit: int) -> list:
        """Run async tasks with a concurrency limit.

        Args:
            tasks: list of coroutines.
            limit: maximum number of concurrent tasks.

        Returns:
            List of results in the same order as tasks.
        """
        if not tasks:
            return []
        semaphore = asyncio.Semaphore(limit)

        async def _run_with_sem(task):
            async with semaphore:
                return await task

        return await asyncio.gather(*[_run_with_sem(t) for t in tasks])

    async def _extract_and_score_documents(
        self, current_inputs: dict, raw_passages: list, rationales: list
    ) -> tuple[dict, str]:
        """Extract relevant passages from documents and score rationale coverage.

        Replaces COINS chunking + ngram filter + coverage matrix with a single
        LLM-based extractive summarization + scoring step. The LLM sees full
        document context (not isolated passages) and extracts verbatim
        passages relevant to any rationale, preserving precise numbers/tables.

        Flow: batch documents (EXTRACT_BATCH_SIZE per batch) → parallel LLM
        extract+score → merge into coverage_result compatible format.

        Args:
            current_inputs: context dict.
            raw_passages: original passage list (passage-level, not chunked).
            rationales: rationale list with id/description/type.

        Returns:
            (result_dict, last_error). result_dict format is compatible with
            _select_by_rationale_coverage:
            - filtered_passages: extracted passages (passage-level dicts with
              doc_url/doc_title/passage_text/source/publish_time/doc_time)
            - coverage_matrix: {passage_N: {rationale_id: score}}
            - dimension_scores: {passage_N: {rationale_id: {coverage, reliability, data_density}}}.
              reliability/data_density are document-level values mirrored into
              every rationale entry (assessed once per passage, not per rationale)
            On total failure, degrades to original docs as passages (each
            truncated to the first 500 chars) with empty coverage_matrix and
            carries the combined error.
        """
        section_idx = current_inputs.get("section_idx", 1)
        section_task = self.strip_leading_number(current_inputs.get("section_task", ""))
        section_description = current_inputs.get("section_description", "")

        if not raw_passages or not rationales:
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [extract_score] section_idx: [{section_idx}] "
                f"empty passages ({len(raw_passages)}) or rationales ({len(rationales)})"
            )
            return {}, ""

        rationales_text = "\n".join(
            f"  {r.get('id', '')}: {r.get('description', '')} (type: {r.get('type', 'unknown')})"
            for r in rationales
        )

        batches = [
            raw_passages[i:i + EXTRACT_BATCH_SIZE]
            for i in range(0, len(raw_passages), EXTRACT_BATCH_SIZE)
        ]

        logger.info(
            "%s [extract_score] section_idx: [%s] split %s passages into %s batch(es), "
            "batch_size=%s, %s rationales",
            EFFECT_SUB_REPORT_TAG, section_idx, len(raw_passages),
            len(batches), EXTRACT_BATCH_SIZE, len(rationales),
        )

        section_ctx = {
            "section_task": section_task,
            "section_description": section_description,
            "section_idx": section_idx,
            "max_retries": current_inputs.get("max_generate_retry_num", 3),
        }

        tasks = [
            self._extract_batch(batch, batch_idx, rationales_text, section_ctx)
            for batch_idx, batch in enumerate(batches)
        ]
        batch_results = await self._gather_with_limit(tasks, MAX_CONCURRENT_BATCHES)

        # Merge batch results into coverage_result-compatible format
        filtered_passages: list = []
        coverage_matrix: dict = {}
        dimension_scores: dict = {}
        global_passage_idx = 0
        all_errors: list = []

        for batch_idx, (data, batch_docs, error) in enumerate(batch_results):
            if error:
                all_errors.append(error)
                logger.warning(
                    "%s [extract_score] section_idx: [%s] batch %s failed: %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx, error[:200],
                )
                continue
            if not data:
                continue

            documents = data.get("documents", [])
            for doc_result in documents:
                if not isinstance(doc_result, dict):
                    logger.warning(
                        "%s [extract_score] section_idx: [%s] batch %s doc is not a dict "
                        "(type=%s), skipping: %s",
                        EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                        type(doc_result).__name__,
                        repr(doc_result)[:200],
                    )
                    continue
                passage_index = doc_result.get("doc_index", doc_result.get("passage_index"))
                if not isinstance(passage_index, int):
                    logger.warning(
                        "%s [extract_score] section_idx: [%s] batch %s doc missing "
                        "doc_index/passage_index, skipping to avoid misattribution",
                        EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    )
                    continue
                if passage_index < 0 or passage_index >= len(batch_docs):
                    logger.warning(
                        "%s [extract_score] section_idx: [%s] batch %s doc_index=%s "
                        "out of range (batch size %s), skipping",
                        EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                        passage_index, len(batch_docs),
                    )
                    continue
                parent_doc = batch_docs[passage_index]

                passages = doc_result.get("passages", [])
                for passage in passages:
                    if not isinstance(passage, dict):
                        continue
                    text = passage.get("text", "")
                    if not text or not str(text).strip():
                        continue

                    passage_key = f"passage_{global_passage_idx}"
                    passage_dict = {
                        "doc_url": parent_doc.get("url", "") or parent_doc.get("doc_url", ""),
                        "doc_title": parent_doc.get("title", "") or parent_doc.get("doc_title", ""),
                        "doc_time": parent_doc.get("doc_time", ""),
                        "publish_time": parent_doc.get("publish_time", ""),
                        "source": parent_doc.get("source", ""),
                        "passage_text": str(text),
                        "original_content": parent_doc.get("original_content", ""),
                    }
                    filtered_passages.append(passage_dict)

                    scores = passage.get("scores", {})
                    # New format: passage-level {"reliability", "data_density"} +
                    # per-rationale {"r1": {"coverage": 0.9}}.
                    # coverage_matrix stores coverage directly (used for top-k ranking);
                    # dimension_scores stores {coverage, reliability, data_density} per rationale.
                    passage_reliability = safe_float(
                        passage.get("reliability", 0.0)
                    )
                    passage_data_density = safe_float(
                        passage.get("data_density", 0.0)
                    )
                    cleaned = {}
                    dim_cleaned = {}
                    if isinstance(scores, dict):
                        for rid, dim_scores in scores.items():
                            if isinstance(dim_scores, dict):
                                c = safe_float(
                                    dim_scores.get("coverage", 0.0)
                                )
                                r = safe_float(
                                    dim_scores.get("reliability"),
                                    passage_reliability,
                                )
                                d = safe_float(
                                    dim_scores.get("data_density"),
                                    passage_data_density,
                                )
                                cleaned[str(rid)] = c
                                dim_cleaned[str(rid)] = {
                                    "coverage": c, "reliability": r,
                                    "data_density": d,
                                }
                            else:
                                # bool 是 int 子类但非合法分数，需显式排除
                                if isinstance(dim_scores, bool) or not isinstance(dim_scores, (int, float, str)):
                                    logger.warning(
                                        "Unexpected score type for rationale %s: %s, value=%s. Treating as 0.0.",
                                        rid,
                                        type(dim_scores).__name__,
                                        repr(dim_scores)[:200],
                                    )
                                    c = 0.0
                                else:
                                    c = safe_float(dim_scores)
                                cleaned[str(rid)] = c
                                dim_cleaned[str(rid)] = {"coverage": c}
                    coverage_matrix[passage_key] = cleaned
                    dimension_scores[passage_key] = dim_cleaned
                    # Document-level dimensions are assessed once per passage,
                    # not per rationale, and stored at the top level for
                    # visualization selection.
                    passage_dict["reliability"] = passage_reliability
                    passage_dict["data_density"] = passage_data_density
                    # Write per-rationale scores back to passage_dict so that
                    # build_classified_content can attach them to the citation
                    # block, enabling the writing LLM to use coverage scores
                    # for passage prioritization as declared in the prompt.
                    passage_dict["scores"] = dim_cleaned
                    global_passage_idx += 1

        logger.info(
            "%s [extract_score] section_idx: [%s] merged %s passages from %s passages, "
            "%s batch(es) failed",
            EFFECT_SUB_REPORT_TAG, section_idx, len(filtered_passages),
            len(raw_passages), len(all_errors),
        )

        # Degraded path: all batches failed or no valid passages extracted →
        # use original docs as passages, truncated to the first 500 chars
        # (bounded for the writing LLM).
        if not filtered_passages:
            logger.warning(
                "%s [extract_score] section_idx: [%s] all batches failed or no "
                "valid passages extracted, degrading to original docs as passages",
                EFFECT_SUB_REPORT_TAG, section_idx,
            )
            for passage in raw_passages:
                content = str(passage.get("original_content", "") or "")
                if not content.strip():
                    continue
                passage_dict = {
                    "doc_url": passage.get("url", "") or passage.get("doc_url", ""),
                    "doc_title": passage.get("title", "") or passage.get("doc_title", ""),
                    "doc_time": passage.get("doc_time", ""),
                    "publish_time": passage.get("publish_time", ""),
                    "source": passage.get("source", ""),
                    "passage_text": content[:500],
                    "original_content": content,
                    "reliability": 0.0,
                    "data_density": 0.0,
                }
                filtered_passages.append(passage_dict)

            return {
                "filtered_passages": filtered_passages,
                "coverage_matrix": {},
                "dimension_scores": {},
            }, "; ".join(all_errors)[:500]

        return {
            "filtered_passages": filtered_passages,
            "coverage_matrix": coverage_matrix,
            "dimension_scores": dimension_scores,
        }, ""

    async def _extract_batch(
        self, batch_docs: list, batch_idx: int,
        rationales_text: str, section_ctx: dict,
    ) -> tuple:
        """Extract passages and score rationales for a single batch (1 LLM call).

        Args:
            batch_docs: list of original documents in this batch (passage-level).
            batch_idx: batch index (for logging).
            rationales_text: rationale text.
            section_ctx: dict with section_task, section_description, section_idx.

        Returns:
            (parsed_result_dict, batch_docs, last_error) tuple. On success the
            error string is ""; on failure parsed_result is an empty dict and
            last_error carries the final failure detail. Each retry appends the
            previous failure as a data-bounded retry_feedback user message.
        """
        section_task = section_ctx.get("section_task", "")
        section_description = section_ctx.get("section_description", "")
        section_idx = section_ctx.get("section_idx", -1)

        # Build document text for LLM input (untrusted data in user message)
        doc_parts = []
        for i, passage in enumerate(batch_docs):
            title = passage.get("title", "") or passage.get("doc_title", "")
            url = passage.get("url", "") or passage.get("doc_url", "")
            content = str(passage.get("original_content", "") or passage.get("passage_text", "") or "")
            if len(content) > MAX_EXTRACT_DOC_CHARS:
                content = content[:MAX_EXTRACT_DOC_CHARS]
            doc_parts.append(f"Document {i}:\nTitle: {title}\nURL: {url}\nContent: {content}")
        docs_text = "\n\n".join(doc_parts)

        user_content = (
            f"Chapter title: {section_task}\n"
            f"Chapter description: {section_description}\n\n"
            f"Information dimensions (rationales):\n{rationales_text}\n\n"
            f"Documents:\n{docs_text}\n\n"
            "Extract relevant passages from the documents above and score "
            "rationale coverage. Output ONLY a JSON object."
        )
        tmp_context = {
            "messages": [dict(role="user", content=user_content)],
        }

        max_retries = section_ctx.get("max_retries", 3)
        last_error = None
        retry_feedback = ""
        for attempt_num in range(max_retries):
            llm_input = apply_system_prompt("passages_extractor", tmp_context)
            _append_retry_feedback_message(llm_input, retry_feedback)
            try:
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_PASSAGES_EXTRACTOR.value,
                )
            except Exception as e:
                last_error = f"LLM call failed: {e}"
                retry_feedback = (
                    "LLM call failed" if LogManager.is_sensitive() else (last_error or "")[:500]
                )
                logger.warning(
                    "%s [extract_score] section_idx: [%s] batch %s: attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            if not llm_output or not llm_output.get("content"):
                last_error = "LLM returned empty content"
                retry_feedback = (last_error or "")[:500]
                logger.warning(
                    "%s [extract_score] section_idx: [%s] batch %s: attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            try:
                data = json.loads(normalize_json_output(llm_output.get("content", "")))
                n_docs = len(data.get("documents", []))
                n_passages = sum(
                    len(d.get("passages", [])) for d in data.get("documents", [])
                    if isinstance(d, dict)
                )
                logger.info(
                    "%s [extract_score] section_idx: [%s] batch %s: parsed %s docs, %s passages (attempt %s/%s)",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    n_docs, n_passages, attempt_num + 1, max_retries,
                )
                return data, batch_docs, ""
            except Exception as e:
                last_error = f"failed to parse LLM output: {e}"
                retry_feedback = (
                    "failed to parse LLM output"
                    if LogManager.is_sensitive()
                    else (last_error or "")[:500]
                )
                logger.warning(
                    "%s [extract_score] section_idx: [%s] batch %s: attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

        logger.error(
            "%s [extract_score] section_idx: [%s] batch %s: failed after %s attempts: %s",
            EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
            max_retries, last_error,
        )
        return {}, batch_docs, (last_error or "unknown extraction error")

    @staticmethod
    def _select_by_rationale_coverage(
        passages: list, rationales: list, coverage_result: dict, top_k: int = 10,
    ) -> tuple:
        """Per-rationale top-k passage selection (0 LLM calls).

        For each rationale, sort passages by coverage score and take top-k.
        Deduplicate across rationales by passage identity (keep first occurrence).

        Args:
            passages: candidate passage list (already n-gram filtered).
            rationales: rationale list.
            coverage_result: coverage matrix evaluation result.
            top_k: maximum passages per rationale.

        Returns:
            (selected_passages, selected_passage_keys) tuple.
        """
        # Note: reliability and data_density are assessed per-passage but are NOT
        # used for rationale-based selection. Selection is driven by coverage scores
        # only. reliability/data_density are preserved on passage dicts for
        # downstream visualization selection and prompt enrichment.
        filtered_passages = coverage_result.get("filtered_passages", passages)
        coverage_matrix = coverage_result.get("coverage_matrix", {})

        rationale_ids = list(dict.fromkeys(r.get("id", "") for r in rationales))

        # Track selected passages by identity to deduplicate
        seen_ids: set[int] = set()
        selected_passages: list = []
        selected_indices: list[int] = []

        for rid in rationale_ids:
            # Sort passages by coverage score for this rationale (descending)
            scored = []
            for idx in range(len(filtered_passages)):
                passage_key = f"passage_{idx}"
                passage_cov = coverage_matrix.get(passage_key, {})
                if not isinstance(passage_cov, dict):
                    passage_cov = {}
                score = passage_cov.get(rid, 0.0)
                scored.append((score, idx))

            # Sort by score descending
            scored.sort(key=lambda x: x[0], reverse=True)

            # Take top-k for this rationale, dedup across rationales
            count = 0
            for score, idx in scored:
                if count >= top_k:
                    break
                if score > 0:  # skip 0-score passages (consistent with dedup_passages_by_rationale)
                    passage = filtered_passages[idx]
                    if id(passage) not in seen_ids:
                        seen_ids.add(id(passage))
                        selected_passages.append(passage)
                        selected_indices.append(idx)
                        count += 1

        logger.info(
            "%s [select_by_rationale] selected %s passages from %s candidates "
            "for %s rationales (top_k=%s per rationale)",
            EFFECT_SUB_REPORT_TAG, len(selected_passages), len(filtered_passages),
            len(rationale_ids), top_k,
        )

        # Keys are the indices into `filtered_passages` (i.e. coverage_matrix
        # keys), not indices into the selected subset, so downstream lookups
        # into coverage_matrix/dimension_scores stay aligned.
        return (
            selected_passages,
            [f"passage_{idx}" for idx in selected_indices],
        )


    @staticmethod
    def _write_doc_selection_debug(
        current_inputs: dict, ctx: PassageSelectionContext,
    ) -> None:
        """Pack doc-selection intermediate results into current_inputs.

        Stores debug data in current_inputs["doc_selection_debug"] so the caller
        (SubReporterNode → editor_team_manager_node._update_state) can write it
        back to Section.doc_selection_debug for ResultExporter to dump to JSON/Excel.
        """
        rationales = ctx.rationales
        coverage_result = ctx.coverage_result
        passages = ctx.passages
        selected_passages = ctx.selected_passages

        filtered_passages = coverage_result.get("filtered_passages", passages)
        doc_info_map = {
            f"passage_{i}": {
                "doc_title": d.get("doc_title", ""),
                "doc_url": d.get("doc_url", ""),
                "passage_text": (d.get("passage_text", "") or ""),
            }
            for i, d in enumerate(filtered_passages)
        }
        id_to_key = {id(d): f"passage_{i}" for i, d in enumerate(filtered_passages)}
        selected_summary = [
            {
                "passage_key": id_to_key.get(id(passage), ""),
                "doc_title": passage.get("doc_title", ""),
                "doc_url": passage.get("doc_url", ""),
                "passage_text": (passage.get("passage_text", "") or ""),
            }
            for passage in selected_passages
        ]

        current_inputs["doc_selection_debug"] = {
            "rationales": rationales,
            "doc_filter": {
                "before": len(passages),
                "after": len(filtered_passages),
            },
            "coverage_matrix": coverage_result.get("coverage_matrix", {}),
            "dimension_scores": coverage_result.get("dimension_scores", {}),
            "passage_info_map": doc_info_map,
            "selected_passages": selected_summary,
        }

    async def _generate_sub_section_outline(
        self, current_inputs: dict, failure_feedback: str = ""
    ) -> dict:
        """Generate subsection outline"""
        section_idx = current_inputs.get("section_idx", 1)  # Section index
        logger.info(
            f"{EFFECT_SUB_REPORT_TAG} [generate_sub_section_outline] Starting to generate sub section outline, "
            f"section_idx: [{section_idx}]"
        )
        # Extract section core information
        report_task = current_inputs.get("report_task", "")  # Report title
        section_task = self.strip_leading_number(
            current_inputs.get("section_task", "")
        )  # Current section title
        section_description = current_inputs.get(
            "section_description", ""
        )  # Section description
        section_format_requirements = current_inputs.get(
            "section_format_requirements", []
        )
        if not LogManager.is_sensitive():
            logger.debug(
                "%s [generate_sub_section_outline] section_idx: [%s], section description: [%s], "
                "format requirements: [%s]",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                section_description,
                section_format_requirements,
            )
        collected_infos = current_inputs.get(
            "sub_section_core_content", []
        )  # Core information

        # Validate required fields
        if not section_task or not collected_infos:
            error_msg = "Missing 'section_task' or 'sub_section_core_content' in context (section title required)"
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_section_outline] section_idx: [{section_idx}] "
                f"{error_msg}"
            )
            return dict(rs_success=False, sub_section_outline=error_msg)
        try:
            if current_inputs.get("sub_section_core_content_from_background_knowledge"):
                core_context = self._format_background_knowledge_for_prompt(collected_infos)
            else:
                core_context = f"Collected information is {collected_infos}"
            sub_content_message = (
                f"Section id is {section_idx},"
                f"Section title is {section_task},"
                f"Report task is {report_task},"
                f"{core_context},"
                f"Section description is {section_description},"
                f"Section format requirements are {section_format_requirements},"
            )
            structured_evidence_guide = current_inputs.get("structured_evidence_guide", "")
            if structured_evidence_guide:
                sub_content_message += f"\n\n# Structured Evidence Guidance\n{structured_evidence_guide}\n\n"
            tmp_context = {}
            tmp_context["messages"] = [dict(role="user", content=sub_content_message)]
            tmp_context["section_idx"] = section_idx
            tmp_context["language"] = current_inputs.get("language")
            tmp_context["has_template"] = current_inputs.get("has_template")
            tmp_context["section_title"] = section_task
            tmp_context["section_description"] = section_description
            tmp_context["section_format_requirements"] = section_format_requirements
            tmp_context["current_outline"] = Reporter.export_outline_without_plans(
                current_inputs.get("current_outline", {})
            )
            tmp_context["report_type"] = current_inputs.get("report_type", "professional")
            tmp_context["paragraph_style"] = current_inputs.get("paragraph_style", "detailed")
            tmp_context.update(
                build_section_local_contract_prompt_context(
                    current_inputs.get("section_local_contract")
                )
            )
            tmp_context.update(
                build_research_intent_prompt_context(
                    current_inputs.get("research_intent")
                )
            )
            logger.info(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_section_outline] has_template: "
                f"{tmp_context['has_template']}"
            )
            llm_input = apply_system_prompt("sub_section_outline", tmp_context)
            _append_retry_feedback_message(llm_input, failure_feedback)
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [generate_sub_section_outline] section_idx: [%s] llm_input is %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    llm_input,
                )
            llm_output = await ainvoke_llm_with_stats(
                llm=self._llm,
                messages=llm_input,
                agent_name=AgentLlmName.SUB_REPORTER_OUTLINE.value,
            )
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [generate_sub_section_outline] section_idx: [%s] llm_output is %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    llm_output,
                )
            # Validate LLM output
            if not llm_output or not llm_output.get("content"):
                raise CustomValueException(
                    error_code=StatusCode.LLM_RESPONSE_ERROR.code,
                    message=f"LLM returned empty content for the section {section_idx}",
                )
            return dict(rs_success=True, sub_section_outline=llm_output.get("content"))
        except Exception as e:
            error_detail = f"Error generating sub section outline: {type(e).__name__}: {str(e)[:500]}"
            if LogManager.is_sensitive():
                log_msg = "Error generating sub section outline"
                result_msg = "Error generating sub section outline"
            else:
                log_msg = error_detail
                result_msg = error_detail
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_section_outline] section_idx: [{section_idx}] "
                f"{log_msg}",
                exc_info=True,
            )
            return dict(rs_success=False, sub_section_outline=result_msg)

    async def _extract_data_from_text(
        self,
        visualization_dict: dict,
        validation_error: str = "",
        previous_records: str | None = None,
    ) -> dict:
        section_idx = visualization_dict.get("section_idx", 1)
        tmp_context = {
            "language": visualization_dict.get("language", "zh-CN"),
            "section_outline": visualization_dict.get("section_outline", ""),
            "desired_chart_type": visualization_dict.get("desired_chart_type", ""),
            "origin_content": visualization_dict.get("origin_content", ""),
        }
        validation_error = (validation_error or "").strip()
        if validation_error:
            tmp_context["messages"] = [
                dict(
                    role="user",
                    content=(
                        "Previously extracted data did not pass validation: "
                        f"{validation_error}\n"
                        + (
                            f"Previous extracted chart JSON: {previous_records}\n"
                            if previous_records
                            else ""
                        )
                        + "Do NOT reuse, copy, or edit the previous extracted data. "
                        "Re-extract strictly from origin_content and output a fresh JSON."
                    ),
                )
            ]

        try:
            llm_input = apply_system_prompt(
                "sub_section_visualization_content", tmp_context
            )
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [generate_sub_section_visualization_content] section_idx: [%s] llm_input is %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    llm_input,
                )
            llm_output = await ainvoke_llm_with_stats(
                llm=self._llm,
                messages=llm_input,
                agent_name=AgentLlmName.SUB_REPORTER_VISUALIZATION_CONTENT.value,
            )
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [generate_sub_section_visualization_content] section_idx: [%s] llm_output is %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    llm_output,
                )
            # Validate LLM output
            if not llm_output or not llm_output.get("content"):
                raise CustomValueException(
                    error_code=StatusCode.LLM_RESPONSE_ERROR.code,
                    message=f"LLM generated empty visualization content for section {section_idx}",
                )
            payload = (llm_output.get("content") or "").strip()
            return dict(rs_success=True, sub_section_visualization_content=payload)
        except Exception as e:
            if LogManager.is_sensitive():
                error_msg = "Error generating visualization content"
            else:
                error_msg = f"Error generating visualization content: {str(e)}"
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_section_visualization_content] section_idx: [{section_idx}] "
                f"{error_msg}",
                exc_info=True,
            )
            return dict(rs_success=False, visualization_content=error_msg)

    async def _validate_chart_compliance(
        self,
        extracted_chart_json: str,
        section_idx: int,
        section_outline: str,
        max_attempt_num: int,
    ) -> dict:
        """Validate extracted chart data with compliance prompt."""
        payload = (extracted_chart_json or "").strip()
        for attempt in range(max_attempt_num):
            try:
                llm_input = apply_system_prompt(
                    "chart_compliance_validate",
                    dict(
                        extracted_chart_json=payload,
                        section_outline=section_outline,
                    ),
                )
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_CHART_COMPLIANCE.value,
                )
                if not llm_output or not llm_output.get("content"):
                    logger.warning(
                        "%s [validate_chart_compliance] section_idx: [%s] "
                        "attempt %s/%s error: %s",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        attempt + 1,
                        max_attempt_num,
                        "LLM generated empty compliance content",
                    )
                    continue
                raw = (llm_output.get("content") or "").strip()
                result = json.loads(normalize_json_output(raw))
                if not isinstance(result, dict):
                    logger.warning(
                        "%s [validate_chart_compliance] section_idx: [%s] "
                        "attempt %s/%s error: %s",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        attempt + 1,
                        max_attempt_num,
                        "LLM returned non-object compliance JSON",
                    )
                    continue
                valid = bool(result.get("valid", False))
                error_msg = str(result.get("error_msg", "") or "").strip()
                if valid:
                    return dict(valid=True, error_msg="")
                return dict(valid=False, error_msg=error_msg)
            except Exception as e:
                if isinstance(e, (json.JSONDecodeError, TypeError, ValueError)):
                    error_msg = (
                        "LLM returned invalid compliance JSON"
                        if LogManager.is_sensitive()
                        else f"LLM returned invalid compliance JSON: {str(e)}"
                    )
                elif LogManager.is_sensitive():
                    error_msg = "chart compliance validation error"
                else:
                    error_msg = f"chart compliance validation error: {str(e)}"
                logger.warning(
                    "%s [validate_chart_compliance] section_idx: [%s] "
                    "attempt %s/%s error: %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    attempt + 1,
                    max_attempt_num,
                    error_msg,
                )
        return dict(valid=False, error_msg="")

    async def _validate_chart_traceability(
        self,
        extracted_chart_json: str,
        origin_content: str,
        section_idx: int,
        max_attempt_num: int,
    ) -> dict:
        """Validate extracted chart data traceability with origin content."""
        payload = (extracted_chart_json or "").strip()
        origin_text = (origin_content or "").strip()
        for attempt in range(max_attempt_num):
            try:
                llm_input = apply_system_prompt(
                    "chart_data_traceability_check",
                    dict(
                        extracted_chart_json=payload,
                        origin_content=origin_text,
                    ),
                )
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_CHART_TRACEABILITY.value,
                )
                if not llm_output or not llm_output.get("content"):
                    logger.warning(
                        "%s [validate_chart_traceability] section_idx: [%s] "
                        "attempt %s/%s error: %s",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        attempt + 1,
                        max_attempt_num,
                        "LLM generated empty traceability content",
                    )
                    continue
                raw = (llm_output.get("content") or "").strip()
                result = json.loads(normalize_json_output(raw))
                if not isinstance(result, dict):
                    logger.warning(
                        "%s [validate_chart_traceability] section_idx: [%s] "
                        "attempt %s/%s error: %s",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        attempt + 1,
                        max_attempt_num,
                        "LLM returned non-object traceability JSON",
                    )
                    continue
                valid = bool(result.get("valid", False))
                error_msg = str(result.get("error_msg", "") or "").strip()
                if valid:
                    return dict(valid=True, error_msg="")
                return dict(valid=False, error_msg=error_msg)
            except Exception as e:
                if isinstance(e, (json.JSONDecodeError, TypeError, ValueError)):
                    error_msg = (
                        "LLM returned invalid traceability JSON"
                        if LogManager.is_sensitive()
                        else f"LLM returned invalid traceability JSON: {str(e)}"
                    )
                elif LogManager.is_sensitive():
                    error_msg = "chart traceability validation error"
                else:
                    error_msg = f"chart traceability validation error: {str(e)}"
                logger.warning(
                    "%s [validate_chart_traceability] section_idx: [%s] "
                    "attempt %s/%s error: %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    attempt + 1,
                    max_attempt_num,
                    error_msg,
                )
        return dict(valid=False, error_msg="")

    async def _extract_visualization_data(
        self,
        visualization_dict: dict,
        visualization_content: dict,
        max_attempt_num: int,
        section_idx: int,
    ) -> tuple[bool, dict, dict | None]:
        extract_ok = False
        extracted_obj = None
        validation_error = ""
        previous_records: str | None = None
        for i in range(max_attempt_num):
            visualization_content = await self._extract_data_from_text(
                visualization_dict, validation_error, previous_records
            )
            if not LogManager.is_sensitive():
                logger.debug("%s [process_visualization_task] Extract data: %s.", EFFECT_SUB_REPORT_TAG,
                             visualization_content)
            raw_payload = (
                visualization_content.get("sub_section_visualization_content") or ""
            ).strip()
            if raw_payload:
                raw_payload = normalize_json_output(raw_payload).strip()
                visualization_content[
                    "sub_section_visualization_content"
                ] = raw_payload
            if raw_payload == "{}":
                validation_error = (
                    "Previous output was empty JSON. If origin_content contains at "
                    "least three traceable records for one metric, extract the best "
                    "valid chart JSON instead of returning {}. Return {} only when "
                    "no valid chartable dataset exists."
                )
                previous_records = raw_payload
                if i < max_attempt_num - 1:
                    logger.warning(
                        "%s [process_visualization_task] section_idx: [%s], "
                        "empty visualization JSON on attempt %s/%s, retry ...",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        i + 1,
                        max_attempt_num,
                    )
                    continue
                visualization_content["rs_success"] = False
                visualization_content["error_msg"] = "no_chart_data"
                return False, visualization_content, None
            try:
                extracted_obj = json.loads(raw_payload)
            except Exception:
                extracted_obj = None
                validation_error = (
                    "Previous output was not valid JSON. Output only one JSON object "
                    "matching the required visualization schema, with no markdown or "
                    "extra text."
                )
            extract_ok = isinstance(
                extracted_obj, dict
            ) and validate_visualization_extraction_schema(extracted_obj)
            if extract_ok:
                raw_payload = json.dumps(extracted_obj, ensure_ascii=False)
                visualization_content[
                    "sub_section_visualization_content"
                ] = raw_payload
                traceability = await self._validate_chart_traceability(
                    raw_payload,
                    visualization_dict.get("origin_content", ""),
                    section_idx,
                    max_attempt_num,
                )
                if not traceability.get("valid", False):
                    traceability_error = (
                        traceability.get("error_msg", "") or ""
                    ).strip()
                    logger.warning(
                        "%s [process_visualization_task] section_idx: [%s], "
                        "traceability check failed: %s",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        traceability_error,
                    )
                    validation_error = (
                        f"Traceability validation failed: {traceability_error}"
                        if traceability_error
                        else ""
                    )
                    validation_error += (
                        "\nYou must only extract complete records where every field"
                        "(category, value, unit) can be fully traced to the original content." 
                        " Do not invent, fabricate, or infer any data that does not"
                        " have a clear corresponding description in the source."
                    )
                    previous_records = raw_payload or None
                    extract_ok = False
                    continue
                compliance = await self._validate_chart_compliance(
                    raw_payload,
                    section_idx,
                    visualization_dict.get("section_outline", ""),
                    max_attempt_num,
                )
                if compliance.get("valid", False):
                    validation_error = ""
                    previous_records = None
                    break
                compliance_error = (compliance.get("error_msg", "") or "").strip()
                validation_error = (
                    f"Compliance/Relevance validation failed: {compliance_error}"
                    if compliance_error
                    else ""
                )
                validation_error += (
                    "\nIf the issue is chart type mismatch, reselect image_type "
                    "from the chart type rules based on the extracted records; "
                    "do not rely on downstream code to rewrite image_type."
                )
                # Provide previous extracted JSON to help the next extraction fix issues,
                # but explicitly forbid reuse/copying in the prompt message.
                previous_records = raw_payload or None
                logger.warning(
                    "%s [process_visualization_task] section_idx: [%s], "
                    "compliance check failed: %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    compliance_error,
                )
                extract_ok = False
                continue
            if not extract_ok and not validation_error:
                validation_error = (
                    "Previous output did not match the required visualization schema. "
                    "Keep only traceable records from origin_content and output a "
                    "single valid chart JSON, or {} if no valid chartable dataset exists."
                )
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [process_visualization_task] section_idx: [{section_idx}], "
                f"Warning: Extract data from text on attempt {i + 1}/{max_attempt_num}. retry ..."
            )

        if not extract_ok:
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [process_visualization_task] section_idx: [{section_idx}], "
                "Skip mermaid generation due to invalid extracted data."
            )
            visualization_content["rs_success"] = False
            visualization_content["error_msg"] = "extract_data_failed"
            return False, visualization_content, None

        return True, visualization_content, extracted_obj

    async def _build_visualization_mermaid(
        self,
        visualization_content: dict,
        extracted_obj: dict,
        visualization_dict: dict,
        max_attempt_num: int,
        section_idx: int,
    ) -> dict:
        normalized = await self._normalize_visualization_content(
            visualization_content,
            extracted_obj,
            visualization_dict,
            max_attempt_num,
            section_idx,
        )
        if not normalized:
            return visualization_content
        if not self._precheck_value_variation(visualization_content, section_idx):
            return visualization_content
        return self._generate_mermaid_code(visualization_content, section_idx)

    @staticmethod
    def _parse_visualization_number(value: str) -> int | float | None:
        normalized_value = value.strip().replace(",", "").replace("，", "")
        try:
            numeric_value = Decimal(normalized_value)
        except (InvalidOperation, ValueError):
            return None
        if not numeric_value.is_finite():
            return None
        if numeric_value == numeric_value.to_integral_value():
            return int(numeric_value)
        return float(numeric_value)

    @staticmethod
    def _scale_visualization_value(value: int | float, divisor: int) -> int | float:
        scaled = Decimal(str(value)) / Decimal(divisor)
        if scaled == scaled.to_integral_value():
            return int(scaled)
        return float(scaled)

    @classmethod
    def _normalize_same_unit_records_locally(
        cls,
        records: list,
        image_type: str,
    ) -> dict | None:
        if image_type not in ("bar", "line", "pie"):
            return None

        normalized_records = []
        normalized_unit = None
        for row in records:
            if not isinstance(row, list) or len(row) != 3:
                return None
            x_value, numeric_text, unit_text = row
            if not (
                isinstance(x_value, str)
                and isinstance(numeric_text, str)
                and isinstance(unit_text, str)
            ):
                return None
            x_value = x_value.strip()
            unit_text = unit_text.strip()
            if not x_value or not unit_text:
                return None
            if normalized_unit is None:
                normalized_unit = unit_text
            if unit_text != normalized_unit:
                return None

            parsed_value = cls._parse_visualization_number(numeric_text)
            if parsed_value is None:
                return None
            normalized_records.append([x_value, parsed_value])

        if normalized_unit is None:
            return None

        if normalized_unit.startswith("万"):
            max_abs_value = max(abs(float(row[1])) for row in normalized_records)
            if max_abs_value >= 10000:
                normalized_unit = "亿" + normalized_unit[1:]
                normalized_records = [
                    [row[0], cls._scale_visualization_value(row[1], 10000)]
                    for row in normalized_records
                ]

        return {"unit": normalized_unit, "records": normalized_records}

    async def _normalize_visualization_content(
        self,
        visualization_content: dict,
        extracted_obj: dict,
        visualization_dict: dict,
        max_attempt_num: int,
        section_idx: int,
    ) -> bool:
        # Extracted schema is valid here.
        image_title = extracted_obj.get("image_title", "")
        image_type = extracted_obj.get("image_type", "")
        extracted_records = extracted_obj.get("records", [])

        # Normalize units (non-timeline) or convert to final timeline schema.
        if image_type == "timeline":
            timeline_records = []
            for row in extracted_records:
                if not isinstance(row, list) or len(row) != 3:
                    visualization_content["rs_success"] = False
                    visualization_content["error_msg"] = "extract_data_failed"
                    return False
                timeline_records.append([row[0], row[1]])
            if len(timeline_records) != len(extracted_records):
                visualization_content["rs_success"] = False
                visualization_content["error_msg"] = "extract_data_failed"
                return False
            final_obj = {
                "image_title": image_title,
                "image_type": "timeline",
                "unit": "",
                "records": timeline_records,
            }
            visualization_content["sub_section_visualization_content"] = json.dumps(
                final_obj, ensure_ascii=False
            )
            return True

        final_obj = None
        locally_normalized = self._normalize_same_unit_records_locally(
            extracted_records,
            image_type,
        )
        if locally_normalized and validate_visualization_normalization_schema(
            locally_normalized, image_type
        ):
            final_obj = {
                "image_title": image_title,
                "image_type": image_type,
                "unit": locally_normalized.get("unit", ""),
                "records": locally_normalized.get("records", []),
            }

        if final_obj:
            visualization_content["sub_section_visualization_content"] = json.dumps(
                final_obj, ensure_ascii=False
            )
            return True

        records_json = json.dumps({"records": extracted_records}, ensure_ascii=False)
        normalize_context = {
            "language": visualization_dict.get("language", "zh-CN"),
            "records_json": records_json,
        }
        normalize_input = apply_system_prompt(
            "sub_section_visualization_normalize_units", normalize_context
        )
        for j in range(max_attempt_num):
            normalize_output = await ainvoke_llm_with_stats(
                llm=self._llm,
                messages=normalize_input,
                agent_name=AgentLlmName.SUB_REPORTER_VISUALIZATION_NORMALIZE.value,
            )
            if not normalize_output or not normalize_output.get("content"):
                continue
            normalized_payload = normalize_json_output(
                (normalize_output.get("content") or "").strip()
            ).strip()
            if normalized_payload == "{}":
                continue
            try:
                normalized_obj = json.loads(normalized_payload)
            except Exception as e:
                if not LogManager.is_sensitive():
                    logger.warning(
                        "%s [process_visualization_task] section_idx: [%s], "
                        "normalize_units json decode failed on attempt %s/%s: %s",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        j + 1,
                        max_attempt_num,
                        str(e),
                    )
                continue
            if not validate_visualization_normalization_schema(
                normalized_obj, image_type
            ):
                continue
            # Keep record count unchanged (prompt contract).
            if len(normalized_obj.get("records", [])) != len(extracted_records):
                continue
            final_obj = {
                "image_title": image_title,
                "image_type": image_type,
                "unit": normalized_obj.get("unit", ""),
                "records": normalized_obj.get("records", []),
            }
            break

        if not final_obj:
            visualization_content["rs_success"] = False
            visualization_content["error_msg"] = "normalize_failed"
            return False

        visualization_content["sub_section_visualization_content"] = json.dumps(
            final_obj, ensure_ascii=False
        )
        return True

    async def _process_visualization_task(self, visualization_dict: dict) -> dict:
        """Process one visualization task (LLM content + Mermaid generation)"""
        section_idx = visualization_dict.get("section_idx", 1)
        max_attempt_num = visualization_dict.get("max_attempt_num", 3)
        # Extract structured data
        visualization_content = dict(rs_success=True, visualization_content="")
        origin_content = (visualization_dict.get("origin_content") or "").strip()
        if not origin_content:
            visualization_content["rs_success"] = False
            visualization_content["error_msg"] = "origin_content_empty"
            return visualization_content
        extract_ok, visualization_content, extracted_obj = (
            await self._extract_visualization_data(
                visualization_dict,
                visualization_content,
                max_attempt_num,
                section_idx,
            )
        )
        if not extract_ok:
            return visualization_content

        return await self._build_visualization_mermaid(
            visualization_content,
            extracted_obj,
            visualization_dict,
            max_attempt_num,
            section_idx,
        )

    async def generate_content_for_visualization(self, current_inputs: dict) -> dict:
        """公开的可视化内容生成接口。"""
        return await self._generate_content_for_visualization(current_inputs)

    async def _generate_content_for_visualization(self, current_inputs: dict) -> dict:
        """Generate content for visualization with concurrent LLM calls"""
        section_idx = current_inputs.get("section_idx", 1)
        # Compliance validation depends on chapter outline; if outline is missing, skip visuals safely.
        section_outline = (current_inputs.get("sub_section_outline", "") or "").strip()
        if not section_outline:
            logger.warning(
                "%s [generate_sub_section_visualization_content] section_idx: [%s], "
                "missing sub_section_outline, skip visualization generation.",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
            )
            return dict(rs_success=True, visualization_content=[])

        # Section title is optional for visualization; keep for metadata/logging only.
        section_task = self.strip_leading_number(current_inputs.get("section_task", ""))
        logger.info(
            "%s [generate_sub_section_visualization_content] Start generating content, section_idx: [%s]",
            EFFECT_SUB_REPORT_TAG,
            section_idx,
        )
        desired_chart_type = self._infer_desired_chart_type(section_task, section_outline)

        classified_content_for_visualization = deepcopy(
            current_inputs.get("classified_content", [])
        )
        if not isinstance(classified_content_for_visualization, list):
            logger.warning(
                "%s [generate_sub_section_visualization_content] section_idx: [%s], "
                "classified_content is not a list, skip visualization.",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
            )
            return dict(rs_success=True, visualization_content=[])
        visualization_content = self._select_visualization_from_classified_content(
            classified_content_for_visualization
        )
        n = len(visualization_content)

        if n == 0:
            return dict(rs_success=True, visualization_content=visualization_content)
        # Build all async tasks
        tasks = []
        for i in range(n):
            visualization_dict = {
                "section_idx": section_idx,
                "title": visualization_content[i].get("title", ""),
                "origin_content": (
                    visualization_content[i].get("passage_text", "")
                    or visualization_content[i].get("original_content", "")
                ),
                "data_density": visualization_content[i].get("data_density", -1.0),
                "language": current_inputs.get("language", "zh-CN"),
                "section_title": section_task,
                "section_outline": section_outline,
                "desired_chart_type": desired_chart_type,
                "max_attempt_num": current_inputs.get("max_generate_retry_num", 3),
            }
            task = self._process_visualization_task(visualization_dict)
            tasks.append(task)

        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(
                    "%s [generate_sub_section_visualization_content] section_idx: [%s], "
                    "error in task [%s]: %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    i,
                    str(res),
                )
                visualization_content[i]["sub_section_visualization_content"] = ""
                visualization_content[i]["mermaid_content"] = ""
            else:
                if res.get("rs_success"):
                    visualization_content[i]["sub_section_visualization_content"] = res[
                        "sub_section_visualization_content"
                    ]
                    visualization_content[i]["mermaid_content"] = res["mermaid_content"]
                else:
                    visualization_content[i]["sub_section_visualization_content"] = ""
                    visualization_content[i]["mermaid_content"] = ""
                    logger.warning(
                        "%s [generate_sub_section_visualization_content] section_idx: [%s], reason: %s",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        res.get("error_msg", "Unknown"),
                    )
        return dict(rs_success=True, visualization_content=visualization_content)

    @staticmethod
    def _normalize_citation_indices(citations) -> list[int]:
        indices = []
        seen = set()
        for citation in citations or []:
            try:
                index = int(citation)
            except (TypeError, ValueError):
                continue
            if index > 0 and index not in seen:
                seen.add(index)
                indices.append(index)
        return indices

    async def _generate_sub_report_summary(self, current_inputs: dict):
        """generate sub report summary"""
        if not LogManager.is_sensitive():
            logger.debug(
                "%s [_generate_sub_report_summary] Starting section_idx: %s, current_inputs: %s",
                EFFECT_SUB_REPORT_TAG,
                current_inputs.get("section_idx", 1),
                current_inputs,
            )
        sub_report_content = current_inputs.get("sub_report_content", "")
        if not sub_report_content:
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [_generate_sub_report_summary] section_idx:"
                f"{current_inputs.get('section_idx', 1)}, sub report content is empty."
            )
            return dict(rs_success=True, result="")

        sub_content_message = f"sub report content is {sub_report_content}"
        current_outline = current_inputs.get("current_outline", {})
        current_outline_without_plans = Reporter.export_outline_without_plans(
            current_outline
        )

        try:
            llm_input = apply_system_prompt(
                "sub_report_summary",
                dict(
                    messages=[dict(role="user", content=sub_content_message)],
                    section_id=current_inputs.get("section_idx", 1),
                    language=current_inputs.get("language", "zh-CN"),
                    outline=current_outline_without_plans,
                    user_query=current_inputs.get("report_task", ""),
                    report_type=current_inputs.get("report_type", "professional"),
                    paragraph_style=current_inputs.get("paragraph_style", "detailed"),
                    audience_role=current_inputs.get("audience_role", ""),
                    tone=current_inputs.get("tone", ""),
                ),
            )
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [_generate_sub_report_summary] section_idx: %s llm_input is %s",
                    EFFECT_SUB_REPORT_TAG,
                    current_inputs.get("section_idx", 1),
                    llm_input,
                )

            retry_num = Config().service_config.report_max_generate_retry_num
            for i in range(retry_num):
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_SUMMARY.value,
                )
                if not LogManager.is_sensitive():
                    logger.debug(
                        "%s [_generate_sub_report_summary] section_idx: %s llm_output is %s",
                        EFFECT_SUB_REPORT_TAG,
                        current_inputs.get("section_idx", 1),
                        llm_output,
                    )

                # Validate LLM output
                if not llm_output or not llm_output.get("content"):
                    if i == retry_num - 1:
                        raise CustomValueException(
                            error_code=StatusCode.AGENT_RETRY_FAILED_ALL_ATTEMPTS.code,
                            message=f"return empty summary content for the section "
                            f"{current_inputs.get('section_idx', 1)}",
                        )
                else:
                    return dict(rs_success=True, result=llm_output.get("content"))

        except Exception as e:
            if LogManager.is_sensitive():
                error_msg = (
                    f"Error while generating section {current_inputs.get('section_idx', 1)}"
                    f"report's summary."
                )
            else:
                error_msg = (
                    f"Error generating section {current_inputs.get('section_idx', 1)}"
                    f"report's summary: {str(e)}"
                )
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [_generate_sub_report_summary] {error_msg}",
                exc_info=True,
            )
            return dict(rs_success=False, result="")

    @staticmethod
    def _normalize_sidecar_list(
        payload: dict,
        field_name: str,
        section_idx: str | int,
    ) -> list[str]:
        """Normalize an optional sidecar list field without coercing values."""
        value = payload.get(field_name, [])
        if not isinstance(value, list):
            logger.warning(
                "%s [_generate_sub_report_sidecar] section_idx: %s field %s is not a list; use empty list.",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                field_name,
            )
            return []
        normalized = [
            item.strip() for item in value if isinstance(item, str) and item.strip()
        ]
        if len(normalized) != len(value):
            logger.warning(
                "%s [_generate_sub_report_sidecar] section_idx: %s field %s contains invalid items; drop them.",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                field_name,
            )
        return normalized

    async def _generate_sub_report_sidecar(self, current_inputs: dict) -> dict:
        """Generate a structured reusable summary for one chapter."""
        section_idx = current_inputs.get("section_idx", 1)
        sub_report_content = current_inputs.get("sub_report_content", "") or ""
        if not sub_report_content:
            warning = f"section {section_idx} sidecar skipped because chapter body is empty"
            logger.warning("%s [_generate_sub_report_sidecar] %s", EFFECT_SUB_REPORT_TAG, warning)
            return dict(sidecar=None, summary="", warning=warning)

        try:
            current_outline_without_plans = Reporter.export_outline_without_plans(
                current_inputs.get("current_outline", {})
            )
            llm_input = apply_system_prompt(
                "sub_report_sidecar",
                dict(
                    messages=[
                        dict(role="user", content=f"Sub report content:\n{sub_report_content}")
                    ],
                    section_id=section_idx,
                    language=current_inputs.get("language", "zh-CN"),
                    outline=current_outline_without_plans,
                    user_query=current_inputs.get("report_task", ""),
                    report_type=current_inputs.get("report_type", "professional"),
                ),
            )
            retry_num = max(
                int(
                    current_inputs.get(
                        "max_generate_retry_num",
                        Config().service_config.report_max_generate_retry_num,
                    )
                ),
                1,
            )
        except Exception as error:
            warning = (
                f"section {section_idx} sidecar generation failed before retry: {error}; "
                "fallback uses full pre-reference chapter body"
            )
            logger.warning("%s [_generate_sub_report_sidecar] %s", EFFECT_SUB_REPORT_TAG, warning)
            return dict(sidecar=None, summary=sub_report_content, warning=warning)

        last_error = "unknown sidecar error"
        for attempt in range(retry_num):
            try:
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_SIDECAR.value,
                )
                raw_content = (llm_output or {}).get("content", "")
                if not raw_content:
                    raise ValueError("LLM returned empty sidecar content")
                payload = json.loads(normalize_json_output(raw_content))
                if not isinstance(payload, dict):
                    raise ValueError("sidecar result is not a JSON object")
                chapter_summary = payload.get("chapter_summary")
                if not isinstance(chapter_summary, str) or not chapter_summary.strip():
                    raise ValueError("chapter_summary is missing or empty")
                sidecar = ChapterSidecar(
                    chapter_summary=chapter_summary.strip(),
                    key_findings=self._normalize_sidecar_list(payload, "key_findings", section_idx),
                    risk_points=self._normalize_sidecar_list(payload, "risk_points", section_idx),
                )
                return dict(sidecar=sidecar, summary=sidecar.chapter_summary, warning="")
            except Exception as error:
                last_error = str(error)
                logger.warning(
                    "%s [_generate_sub_report_sidecar] section_idx: %s attempt %s/%s failed: %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    attempt + 1,
                    retry_num,
                    last_error,
                )

        warning = (
            f"section {section_idx} sidecar generation failed after {retry_num} attempts: "
            f"{last_error}; fallback uses full pre-reference chapter body"
        )
        logger.warning("%s [_generate_sub_report_sidecar] %s", EFFECT_SUB_REPORT_TAG, warning)
        return dict(sidecar=None, summary=sub_report_content, warning=warning)

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
