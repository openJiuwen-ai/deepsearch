# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
import logging
import re
from datetime import datetime, timezone

from tenacity import (
    after_log,
    retry,
    stop_after_attempt,
    retry_if_exception_type,
)

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.report.config import ReportFormat
from openjiuwen_deepsearch.algorithm.report.markdown_utils import _convert_bold_formula_to_inline_math
from openjiuwen_deepsearch.algorithm.report.report_common import EFFECT_SUB_REPORT_TAG, build_citation_infos
from openjiuwen_deepsearch.algorithm.report.table_caption_utils import ensure_markdown_table_captions
from openjiuwen_deepsearch.algorithm.report.report_utils import (
    ArticlePart,
    _section_sort_key,
    export_outline_without_plans,
    resolve_current_subsection,
)
from openjiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import ChapterSidecar
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)


class ReportPartsMixin:
    """Report parts generation mixin: abstract, conclusion, sidecar, transition.

    Dependencies (provided by Reporter or other mixins):
        - self._llm: LLM instance (from Reporter.__init__)
        - self.gen_report_context: report context dict (from Reporter.__init__)
        - self._format_background_knowledge_for_prompt: (from BackgroundKnowledgeMixin)
    """

    @retry(
        stop=stop_after_attempt(Config().service_config.report_max_generate_retry_num),
        retry=retry_if_exception_type(Exception),
        after=after_log(logger, logging.WARNING),
    )
    async def generate_abstract(self, sub_reports_content: str) -> str:
        """Generate abstract for report"""
        logger.info(f"Start to generate abstract with llm...")
        report_format = ReportFormat.MARKDOWN
        prompt = f"report_abstract_{report_format.get_name()}"
        abstract = await self._generate_with_llm(
            "abstract", prompt, sub_reports_content
        )
        logger.info(f"Generating report abstract Done.")
        return abstract

    @retry(
        stop=stop_after_attempt(Config().service_config.report_max_generate_retry_num),
        retry=retry_if_exception_type(Exception),
        after=after_log(logger, logging.WARNING),
    )
    async def generate_conclusion(self, sub_reports_content: str) -> str:
        """Generate conclusion for report"""
        logger.info(f"Start to generate conclusion with llm...")
        report_format = ReportFormat.MARKDOWN
        prompt = f"report_implications_and_recommendations_{report_format.get_name()}"
        conclusion = await self._generate_with_llm(
            "conclusion", prompt, sub_reports_content
        )
        logger.info(f"Generating report conclusion Done.")
        return conclusion

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
                    new = old + "\n" + str(llm_output.get("content") or "")
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
        if (
            not current_report
            or not hasattr(current_report, "sub_reports")
            or not current_report.sub_reports
        ):
            return ""

        report_task = (
            self.gen_report_context.get("report_task") or current_report.report_task
        )
        context_parts = []
        if report_task:
            context_parts.append(f"Report task: {report_task}")

        sub_reports = sorted(
            current_report.sub_reports,
            key=lambda item: _section_sort_key(item.section_id),
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
        current_outline_without_plans = export_outline_without_plans(
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
            current_outline_without_plans = export_outline_without_plans(
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

    def _build_subsection_prompt(
        self, current_inputs: dict, section_task: str, background_knowledge_contents: list
    ) -> str:
        """Build the sub-section prompt message for LLM generation."""
        infos = build_citation_infos(current_inputs.get("classified_content", []))
        required_target_citations = current_inputs.get("required_target_citation_indexes", [])
        required_target_citation_instruction = (
            "The following citations are user-specified papers and MUST each be cited at least once "
            f"in this chapter body: {', '.join(f'[citation:{index}]' for index in required_target_citations)}.\n\n"
            if required_target_citations else ""
        )
        current_outline = current_inputs.get("current_outline", {})
        current_outline_without_plans = export_outline_without_plans(
            current_outline
        )
        background_knowledge_prompt = self._format_background_knowledge_for_prompt(
            background_knowledge_contents
        )
        current_section_description = current_inputs.get("section_description", "")
        current_section_format_requirements = current_inputs.get("section_format_requirements", [])
        current_chapter_outline = current_inputs.get("sub_section_outline", "")
        current_subsection = resolve_current_subsection(current_inputs)
        structured_evidence_guide = current_inputs.get("structured_evidence_guide", "")
        retry_feedback = self._sub_report_retry_feedback_from_failure(
            str(current_inputs.get("sub_report_retry_feedback", "") or "")
        )
        retry_feedback_prompt = ""
        if retry_feedback:
            retry_feedback_prompt = (
                "\n\n# Previous Attempt Feedback\n"
                "The previous chapter attempt failed validation. "
                "Use only the controlled fields below to correct the next draft; "
                "do not copy these fields into the report body.\n"
                f"{retry_feedback}\n\n"
            )
        structured_evidence_section = (
            f"# Structured Evidence Guidance\n{structured_evidence_guide}\n\n"
            if structured_evidence_guide
            else ""
        )
        background_knowledge_section = (
            f"# Background Knowledge\n{background_knowledge_prompt}\n\n"
            if background_knowledge_prompt
            else ""
        )
        sub_content_message = (
            "# Current Section\n"
            f"section_id: {current_inputs.get('section_idx', 1)}\n"
            f"title: {section_task}\n"
            f"description: {current_section_description}\n\n"
            "# Current Chapter Outline\n"
            f"{current_chapter_outline}\n\n"
            f"{structured_evidence_section}"
            f"{background_knowledge_section}"
            "# Collected Evidence\n"
            f"{infos}\n\n"
            f"{required_target_citation_instruction}"
            "# References\n"
            f"{current_inputs.get('sub_section_references', '')}\n\n"
            f"{retry_feedback_prompt}"
        )
        return sub_content_message

    async def _post_process_subsection(self, current_inputs: dict) -> tuple[bool, str]:
        """Post-process sub-section report: visualization insertion, table captions,
        markdown cleanup, heading validation, sidecar, references.

        Returns (success, error_message). Sets current_inputs["sub_report_content"].
        """
        current_inputs["sub_report_content"] = self._clean_internal_callback_labels(
            current_inputs.get("sub_report_content", "")
        )

        # Chart source belongs to the controlled chart pipeline rather than
        # the chapter body. Reject an invalid draft so the existing bounded
        # retry loop can regenerate it; do not strip arbitrary text after
        # the fact.
        if self._contains_mermaid_source(current_inputs["sub_report_content"]):
            logger.warning(
                "%s [write_subsection_reports] section_idx: [%s] "
                "rejected Mermaid/chart source in chapter draft; retry.",
                EFFECT_SUB_REPORT_TAG,
                current_inputs.get("section_idx", 1),
            )
            return False, (
                "generated chapter contains Mermaid or chart source; "
                "write prose, lists, or Markdown tables only"
            )

        # Insert visualization content
        if current_inputs.get("visualization_enable", True):
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [write_subsection_reports] section_idx: [%s] "
                    "sub_report_content before insert visualization: %s",
                    EFFECT_SUB_REPORT_TAG,
                    current_inputs.get("section_idx", 1),
                    current_inputs.get("sub_report_content", ""),
                )
            try:
                insert_result = await self._insert_visualization(current_inputs)
                if insert_result.get("rs_success", False):
                    current_inputs["sub_report_content"] = insert_result.get(
                        "result", ""
                    )
                else:
                    has_visuals = any(
                        isinstance(item, dict) and item.get("mermaid_content")
                        for item in current_inputs.get("visualization_result", [])
                    )
                    if has_visuals and not LogManager.is_sensitive():
                        logger.warning(
                            "%s [write_subsection_reports] section_idx: [%s] "
                            "insert visualization failed, use original content.",
                            EFFECT_SUB_REPORT_TAG,
                            current_inputs.get("section_idx", 1),
                        )
                    elif not has_visuals and not LogManager.is_sensitive():
                        logger.debug(
                            "%s [write_subsection_reports] section_idx: [%s] "
                            "no visualization data to insert.",
                            EFFECT_SUB_REPORT_TAG,
                            current_inputs.get("section_idx", 1),
                        )
            except Exception as e:
                logger.warning(
                    "%s [write_subsection_reports] section_idx: [%s] "
                    "insert visualization error, use original content: %s",
                    EFFECT_SUB_REPORT_TAG,
                    current_inputs.get("section_idx", 1),
                    str(e),
                )
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [write_subsection_reports] section_idx: [%s] "
                    "sub_report_content after insert visualization: %s",
                    EFFECT_SUB_REPORT_TAG,
                    current_inputs.get("section_idx", 1),
                    current_inputs.get("sub_report_content", ""),
                )

        current_inputs["sub_report_content"] = ensure_markdown_table_captions(
            current_inputs["sub_report_content"],
            current_inputs.get("language"),
            current_inputs.get("section_idx", ""),
        )

        current_inputs["sub_report_content"] = self.clean_markdown_headers(
            current_inputs["sub_report_content"]
        )
        ok, reason = self.validate_sub_report_headings_match_outline(
            current_inputs["sub_report_content"],
            current_inputs.get("sub_section_outline", ""),
        )
        if not ok:
            return False, f"generated report headings do not match outline: {reason}"
        sidecar_result = await self._generate_sub_report_sidecar(current_inputs)
        current_inputs["sub_report_chapter_sidecar"] = sidecar_result.get("sidecar")
        current_inputs["sub_report_summary"] = sidecar_result.get("summary", "")
        current_inputs["sub_report_sidecar_warning"] = sidecar_result.get("warning", "")
        current_inputs["sub_report_content"] = self.add_references(
            current_inputs["sub_report_content"],
            current_inputs.get("sub_section_references", []),
            current_inputs.get("language"),
        ).strip()

        # get sub report content
        if not current_inputs.get("sub_report_content", ""):
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} sub report content is blank, section_id: "
                f"{current_inputs.get('section_idx', 1)}"
            )
            return False, "no sub report content found"

        if not LogManager.is_sensitive():
            logger.debug(
                "%s[write_subsection_reports] success generate section [%s] sub_report, sub report content:\n[%s]",
                EFFECT_SUB_REPORT_TAG,
                current_inputs.get("section_idx", 1),
                current_inputs["sub_report_content"],
                extra={"skip_truncation": True},
            )
        return True, ""
