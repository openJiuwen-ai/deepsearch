# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import logging
import re
import uuid
from typing import Any

from openjiuwen_deepsearch.algorithm.report.doc_prefilter import deduplicate_doc_infos
from openjiuwen_deepsearch.algorithm.user_feedback_processor.common import (
    UserFeedbackPromptInvoker,
    resolve_model_context_collector as _resolve_model_context_collector,
    resolve_session_collector as _resolve_session_collector,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.report_edit_utils import (
    strip_markup_in_range,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.section_locator import (
    heading_block_end,
    locate_enclosing_numbered_major_block,
    locate_section,
    parse_markdown_headings,
)
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

_TITLE_PREFIX_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[\.、\)]\s*)+")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_TITLE_PUNCT_TRANSLATION = str.maketrans("", "", " \t\r\n-_:：。.,，()（）[]【】")
_ALLOWED_CONCLUSIONS = {"supported", "partially_supported", "unsupported", "insufficient_evidence"}
_MAX_DISPLAY_EVIDENCE_LINKS = 10


def _get_field(data: Any, key: str, default: Any = None) -> Any:
    if isinstance(data, dict):
        return data.get(key, default)
    return getattr(data, key, default)


def _is_chinese_language(language: str) -> bool:
    return (language or "").lower().startswith("zh")


def _insufficient_evidence_fallback_text(language: str) -> str:
    if _is_chinese_language(language):
        return "**核验结论**：当前证据不足，暂无法对该表述作出可靠判断。"
    return (
        "**Verification conclusion**: Insufficient evidence; "
        "the claim cannot be reliably verified."
    )


def _build_empty_docs_assessment(language: str, *, need_more_search: bool = True) -> dict:
    """章节无可用参考资料时，跳过 LLM 核验并返回保守结论。"""
    return {
        "display_text": _insufficient_evidence_fallback_text(language),
        "conclusion": "insufficient_evidence",
        "evidences": [],
        "need_more_search": need_more_search,
    }


def _fallback_display_text_for_conclusion(conclusion: str, language: str) -> str:
    if _is_chinese_language(language):
        conclusion_text = {
            "supported": "主要内容有依据支持。",
            "partially_supported": "部分内容有依据支持，部分细节仍待确认。",
            "unsupported": "关键表述缺乏有效支持或与现有证据冲突。",
            "insufficient_evidence": "当前证据不足，暂无法对该表述作出可靠判断。",
        }
        label = "**核验结论**"
    else:
        conclusion_text = {
            "supported": "The main claims are substantiated.",
            "partially_supported": "Only part of the claims are substantiated.",
            "unsupported": "Key claims are contradicted or lack valid support.",
            "insufficient_evidence": "Available materials cannot verify the claim.",
        }
        label = "**Verification conclusion**"
    body = conclusion_text.get(conclusion, conclusion_text["insufficient_evidence"])
    return f"{label}：{body}" if _is_chinese_language(language) else f"{label}: {body}"


class TruthVerificationProcessor(UserFeedbackPromptInvoker):
    """在用户反馈环节对选中段落执行真实性核验。"""

    def __init__(self, llm_model_name: str):
        self.llm_model_name = llm_model_name

    @staticmethod
    def extract_verified_paragraph(selected_text: str) -> str:
        """按协议将选中文本裁剪为“首个换行前”的待核验段落。"""
        normalized = (selected_text or "").lstrip()
        if not normalized:
            return ""
        return normalized.splitlines()[0].strip()

    @staticmethod
    def _normalize_section_title(title: str) -> str:
        stripped_title = (title or "").strip()
        normalized = _TITLE_PREFIX_RE.sub("", stripped_title)
        if not normalized:
            normalized = stripped_title
        return normalized.casefold().translate(_TITLE_PUNCT_TRANSLATION)

    @staticmethod
    def _extract_heading_title(section_heading: str) -> str:
        match = _HEADING_RE.match((section_heading or "").strip())
        if match:
            return match.group(1).strip()
        return (section_heading or "").strip()

    def _match_sub_report(self, current_report, section_title: str):
        sub_reports = list(_get_field(current_report, "sub_reports", []) or [])
        if not sub_reports:
            return None

        for sub_report in sub_reports:
            if _get_field(sub_report, "section_task", "") == section_title:
                return sub_report

        normalized_target = self._normalize_section_title(section_title)
        for sub_report in sub_reports:
            if self._normalize_section_title(_get_field(sub_report, "section_task", "")) == normalized_target:
                return sub_report

        return None

    @staticmethod
    def _collect_enclosing_heading_titles(
        report_content: str,
        start_offset: int,
        end_offset: int,
    ) -> list[str]:
        headings = parse_markdown_headings(report_content)
        titles: list[str] = []
        for index, heading in enumerate(headings):
            block_end = heading_block_end(report_content, headings, index)
            if start_offset >= heading["start"] and end_offset <= block_end:
                titles.append(heading["title"].strip())
        return titles

    def _resolve_sub_report_match_title(
        self,
        report_content: str,
        feedback: dict,
        section_heading: str,
        current_report,
    ) -> str:
        candidate_titles = self._collect_enclosing_heading_titles(
            report_content=report_content,
            start_offset=feedback["start_offset"],
            end_offset=feedback["end_offset"],
        )
        major_block = locate_enclosing_numbered_major_block(
            report=report_content,
            start_offset=feedback["start_offset"],
            end_offset=feedback["end_offset"],
        )
        if major_block and major_block.title not in candidate_titles:
            candidate_titles.append(major_block.title)

        for title in reversed(candidate_titles):
            if self._match_sub_report(current_report, title):
                return title
        return section_heading

    def _collect_section_doc_infos(self, report_content: str, feedback: dict, current_report) -> tuple[str, list[dict]]:
        section = locate_section(report_content, feedback["start_offset"], feedback["end_offset"])
        section_heading = self._extract_heading_title(section.section_heading)
        sub_report_match_title = self._resolve_sub_report_match_title(
            report_content=report_content,
            feedback=feedback,
            section_heading=section_heading,
            current_report=current_report,
        )
        matched_sub_report = self._match_sub_report(current_report, sub_report_match_title)

        section_doc_infos: list[dict] = []
        if matched_sub_report:
            content = _get_field(matched_sub_report, "content")
            section_doc_infos = list(_get_field(content, "classified_content", []) or [])

            if not section_doc_infos:
                all_classified_contents = list(_get_field(current_report, "all_classified_contents", []) or [])
                sub_reports = list(_get_field(current_report, "sub_reports", []) or [])
                try:
                    sub_report_idx = sub_reports.index(matched_sub_report)
                except ValueError:
                    sub_report_idx = -1
                if 0 <= sub_report_idx < len(all_classified_contents):
                    section_doc_infos = list(all_classified_contents[sub_report_idx] or [])

        deduped_doc_infos = deduplicate_doc_infos(section_doc_infos)
        logger.info(
            "[TruthVerificationProcessor] section docs collected. section_heading=%s "
            "sub_report_match_title=%s doc_count=%s matched_sub_report=%s",
            section_heading,
            sub_report_match_title,
            len(deduped_doc_infos),
            matched_sub_report is not None,
        )
        return section_heading, deduped_doc_infos

    @staticmethod
    def _prepare_doc_infos_for_assessment(doc_infos: list[dict]) -> list[dict]:
        prepared_doc_infos: list[dict] = []
        for doc_info in doc_infos or []:
            if not isinstance(doc_info, dict):
                continue
            prepared_doc = dict(doc_info)
            if not str(prepared_doc.get("original_content", "")).strip():
                prepared_doc["original_content"] = (
                        prepared_doc.get("content")
                        or prepared_doc.get("core_content")
                        or ""
                )
            prepared_doc_infos.append(prepared_doc)
        return prepared_doc_infos

    async def _assess_paragraph_with_docs(
            self,
            verified_paragraph: str,
            section_heading: str,
            user_instruction: str,
            doc_infos: list[dict],
            language: str,
    ) -> dict:
        response = await self._invoke_prompt(
            "truth_verification_assessment",
            {
                "language": language,
                "verified_paragraph": verified_paragraph,
                "section_heading": section_heading,
                "user_instruction": user_instruction,
                "doc_infos": self._prepare_doc_infos_for_assessment(doc_infos),
            },
            AgentLlmName.USER_FEEDBACK_PROCESSOR_TRUTH_VERIFICATION_ASSESSMENT.value,
        )

        try:
            data = json.loads(response)
        except (TypeError, json.JSONDecodeError) as error:
            logger.warning(
                "[TruthVerificationProcessor] failed to parse assessment response, fallback to insufficient. error=%s",
                error,
            )
            return {
                "display_text": _insufficient_evidence_fallback_text(language),
                "conclusion": "insufficient_evidence",
                "evidences": [],
                "need_more_search": True,
            }

        conclusion = data.get("conclusion", "")
        if conclusion not in _ALLOWED_CONCLUSIONS:
            logger.warning(
                "[TruthVerificationProcessor] invalid conclusion=%s, fallback to insufficient_evidence",
                conclusion,
            )
            conclusion = "insufficient_evidence"

        display_text = str(data.get("display_text", "")).strip()
        if not display_text:
            logger.warning(
                "[TruthVerificationProcessor] empty display_text from model, fallback by conclusion=%s",
                conclusion,
            )
            display_text = _fallback_display_text_for_conclusion(conclusion, language)
        evidences = self._normalize_evidences(data.get("evidences", []))
        need_more_search = bool(data.get("need_more_search", False)) or conclusion == "insufficient_evidence"
        logger.info(
            "[TruthVerificationProcessor] assessment completed. conclusion=%s need_more_search=%s "
            "evidence_count=%s display_text_len=%s",
            conclusion,
            need_more_search,
            len(evidences),
            len(display_text),
        )

        return {
            "display_text": display_text,
            "conclusion": conclusion,
            "evidences": evidences,
            "need_more_search": need_more_search,
        }

    @staticmethod
    def _normalize_evidences(evidences: Any) -> list[dict]:
        normalized_evidences: list[dict] = []
        for evidence in evidences if isinstance(evidences, list) else []:
            if not isinstance(evidence, dict):
                continue
            normalized_evidences.append(
                {
                    "title": str(evidence.get("title", "")),
                    "url": str(evidence.get("url", "")),
                    "support": str(evidence.get("support", "related")),
                    "quote": str(evidence.get("quote", "")),
                }
            )
            if len(normalized_evidences) >= _MAX_DISPLAY_EVIDENCE_LINKS:
                break
        return normalized_evidences

    async def _build_search_task(
            self,
            verified_paragraph: str,
            section_heading: str,
            user_instruction: str,
            initial_summary: str,
            language: str,
    ) -> str:
        response = await self._invoke_prompt(
            "truth_verification_search_task",
            {
                "language": language,
                "verified_paragraph": verified_paragraph,
                "section_heading": section_heading,
                "user_instruction": user_instruction,
                "initial_summary": initial_summary,
            },
            AgentLlmName.USER_FEEDBACK_PROCESSOR_TRUTH_VERIFICATION_SEARCH_TASK.value,
        )
        search_task = response.strip()
        logger.info(
            "[TruthVerificationProcessor] supplementary search task generated. task_len=%s",
            len(search_task),
        )
        if not LogManager.is_sensitive():
            logger.debug("[TruthVerificationProcessor] supplementary search task: %s", search_task)
        return search_task

    async def _run_collection(self, research_task: str, language: str) -> dict:
        session = _resolve_session_collector()
        context = _resolve_model_context_collector()
        if session is None:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.errmsg.format(
                    e="Truth verification search requires session."
                ),
            )

        plan_id = f"truth_verification_{uuid.uuid4().hex}"
        logger.info(
            "[TruthVerificationProcessor] start supplementary collection. plan_id=%s language=%s",
            plan_id,
            language,
        )
        plan = Plan(
            id=plan_id,
            language=language,
            title="Truth verification search",
            thought="Collect focused evidence to verify one report paragraph.",
            is_research_completed=False,
            steps=[
                Step(
                    type=StepType.INFO_COLLECTING,
                    title="Truth verification search",
                    description=research_task,
                )
            ],
        )
        service = CollectorExecutionService()
        max_tool_call_turns_per_query = session.get_global_state(
            "config.info_collector_max_tool_call_turns_per_query"
        )
        result = await service.run_plan(
            plan=plan,
            run_config=CollectorRunPlanConfig(
                language=language,
                section_idx="truth_verification",
                max_search_query_count=session.get_global_state(
                    "config.info_collector_max_search_query_count"
                ),
                max_research_loops=session.get_global_state(
                    "config.info_collector_max_research_loops"
                ),
                max_tool_call_turns_per_query=max_tool_call_turns_per_query,
            ),
            session=session,
            context=context,
        )
        doc_infos = result.doc_infos or []
        logger.info(
            "[TruthVerificationProcessor] supplementary collection completed. doc_count=%s",
            len(doc_infos),
        )
        return {"doc_infos": doc_infos}

    async def truth_verification(
            self,
            feedback: dict,
            final_result: dict,
            current_report,
            language: str,
    ) -> dict:
        report_content = final_result.get("response_content", "") or ""
        stripped_report, _, _ = strip_markup_in_range(
            report_content,
            feedback["start_offset"],
            feedback["end_offset"],
        )
        clean_selected_end = feedback["end_offset"] - (len(report_content) - len(stripped_report))
        selected_text_clean = stripped_report[feedback["start_offset"]:clean_selected_end]
        verified_paragraph = self.extract_verified_paragraph(selected_text_clean)
        logger.info(
            "[TruthVerificationProcessor] start truth verification. language=%s start_offset=%s end_offset=%s "
            "verified_paragraph_len=%s",
            language,
            feedback["start_offset"],
            feedback["end_offset"],
            len(verified_paragraph),
        )
        if not LogManager.is_sensitive():
            logger.debug(
                "[TruthVerificationProcessor] verified_paragraph=%s user_instruction=%s",
                verified_paragraph,
                feedback.get("user_instruction", ""),
            )
        if not verified_paragraph:
            raise CustomValueException(
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.code,
                StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE.errmsg.format(
                    param="selected_text",
                    expected_type="non-empty paragraph",
                ),
            )

        section_heading, section_doc_infos = self._collect_section_doc_infos(
            report_content=report_content,
            feedback=feedback,
            current_report=current_report,
        )
        if section_doc_infos:
            initial_assessment = await self._assess_paragraph_with_docs(
                verified_paragraph=verified_paragraph,
                section_heading=section_heading,
                user_instruction=feedback.get("user_instruction", ""),
                doc_infos=section_doc_infos,
                language=language,
            )
        else:
            logger.info(
                "[TruthVerificationProcessor] skip initial assessment because section docs are empty. "
                "section_heading=%s",
                section_heading,
            )
            initial_assessment = _build_empty_docs_assessment(language)

        final_assessment = initial_assessment
        if initial_assessment["need_more_search"]:
            logger.info(
                "[TruthVerificationProcessor] initial assessment requires supplementary search. conclusion=%s",
                initial_assessment.get("conclusion"),
            )
            search_task = await self._build_search_task(
                verified_paragraph=verified_paragraph,
                section_heading=section_heading,
                user_instruction=feedback.get("user_instruction", ""),
                initial_summary=initial_assessment.get("display_text", ""),
                language=language,
            )
            search_collection = await self._run_collection(research_task=search_task, language=language)
            supplementary_doc_infos = list(search_collection.get("doc_infos", []) or [])
            merged_doc_infos = deduplicate_doc_infos([*section_doc_infos, *supplementary_doc_infos])
            logger.info(
                "[TruthVerificationProcessor] merged docs for final assessment. section_doc_count=%s "
                "supplementary_doc_count=%s merged_doc_count=%s",
                len(section_doc_infos),
                len(supplementary_doc_infos),
                len(merged_doc_infos),
            )
            if merged_doc_infos:
                final_assessment = await self._assess_paragraph_with_docs(
                    verified_paragraph=verified_paragraph,
                    section_heading=section_heading,
                    user_instruction=feedback.get("user_instruction", ""),
                    doc_infos=merged_doc_infos,
                    language=language,
                )
            else:
                logger.info(
                    "[TruthVerificationProcessor] skip final assessment because merged docs are empty."
                )
                final_assessment = _build_empty_docs_assessment(language, need_more_search=False)

        display_text = final_assessment.get("display_text", "").strip()
        if not display_text:
            display_text = _fallback_display_text_for_conclusion(
                final_assessment.get("conclusion", "insufficient_evidence"),
                language,
            )
        logger.info(
            "[TruthVerificationProcessor] truth verification completed. final_conclusion=%s display_text_len=%s "
            "used_supplementary_search=%s",
            final_assessment.get("conclusion"),
            len(display_text),
            initial_assessment["need_more_search"],
        )

        return {
            "read_only_result": True,
            "verification_result": {
                "display_text": display_text,
            },
        }
