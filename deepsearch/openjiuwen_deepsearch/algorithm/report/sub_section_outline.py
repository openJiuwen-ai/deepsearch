# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import logging

from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages
from openjiuwen_deepsearch.algorithm.report.report_common import EFFECT_SUB_REPORT_TAG
from openjiuwen_deepsearch.algorithm.report.report_utils import export_outline_without_plans
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    build_section_local_contract_prompt_context,
    build_research_intent_prompt_context,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)


class SubSectionOutlineMixin:
    """Mixin providing subsection outline generation for report parts.

    Dependencies (provided by Reporter or other mixins):
        - self._llm: LLM instance (from Reporter.__init__)
        - self.strip_leading_number: (from MarkdownProcessorMixin)
        - self._format_background_knowledge_for_prompt: (from BackgroundKnowledgeMixin)
        - self.check_chapter_format: (from MarkdownProcessorMixin)
    """

    async def _generate_sub_section_outline(
        self, current_inputs: dict, failure_feedback: str = "", *, retry_messages: list | None = None
    ) -> dict:
        """生成章节大纲，并为内容失败保留纠错机会。

        Args:
            current_inputs: 章节范围、证据及格式约束。
            failure_feedback: 上一次内容生成或校验失败的反馈。
            retry_messages: 上一次请求异常时保留的消息。

        Returns:
            成功状态及大纲；仅请求调用抛出异常时附带 retry_messages。
        """
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
        llm_input = retry_messages
        llm_request_in_progress = False
        try:
            if current_inputs.get("sub_section_core_content_from_background_knowledge"):
                core_context = self._format_background_knowledge_for_prompt(collected_infos)
            else:
                core_context = collected_infos
            tmp_context = {
                "report_task": report_task,
                "core_context": core_context,
                "core_from_background": current_inputs.get(
                    "sub_section_core_content_from_background_knowledge", False
                ),
                "structured_evidence_guide": current_inputs.get("structured_evidence_guide", ""),
                "retry_feedback": (failure_feedback or "").strip()[:500],
            }
            tmp_context["section_idx"] = section_idx
            tmp_context["language"] = current_inputs.get("language")
            tmp_context["has_template"] = current_inputs.get("has_template")
            tmp_context["section_title"] = section_task
            tmp_context["section_description"] = section_description
            tmp_context["section_format_requirements"] = section_format_requirements
            tmp_context["current_outline"] = export_outline_without_plans(
                current_inputs.get("current_outline", {})
            )
            tmp_context["report_type"] = current_inputs.get("report_type", "professional")
            tmp_context["paragraph_style"] = current_inputs.get("paragraph_style", "detailed")
            tmp_context["section_iscore"] = current_inputs.get("section_iscore", False)
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
            if llm_input is None:
                llm_input = build_prompt_messages("sub_section_outline", tmp_context)
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [generate_sub_section_outline] section_idx: [%s] llm_input is %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    llm_input,
                )
            llm_request_in_progress = True
            llm_output = await ainvoke_llm_with_stats(
                llm=self._llm,
                messages=llm_input,
                agent_name=AgentLlmName.SUB_REPORTER_OUTLINE.value,
            )
            llm_request_in_progress = False
            if not LogManager.is_sensitive():
                logger.debug(
                    "%s [generate_sub_section_outline] section_idx: [%s] llm_output is %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    llm_output,
                )
            # Validate LLM output
            if not llm_output or not llm_output.get("content"):
                return dict(
                    rs_success=False,
                    sub_section_outline=f"LLM returned empty content for the section {section_idx}",
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
            # 请求异常可原样重发；空内容及处理异常应让外层更新纠错反馈。
            result = dict(rs_success=False, sub_section_outline=result_msg)
            if llm_request_in_progress:
                result["retry_messages"] = llm_input
            return result

    async def _generate_outline_with_retry(
        self, current_inputs: dict, section_idx, max_attempt_num: int
    ) -> tuple[bool, str]:
        """Generate sub-section outline with retry and format validation.

        Returns (success, error_message). On success, sets current_inputs["sub_section_outline"].
        """
        outline_retry_feedback = ""
        retry_messages = None
        for attempt_num in range(max_attempt_num):
            retry_options = {"retry_messages": retry_messages} if retry_messages is not None else {}
            gen_sub_res = await self._generate_sub_section_outline(
                current_inputs, outline_retry_feedback, **retry_options
            )
            retry_messages = gen_sub_res.get("retry_messages")
            outline_text = gen_sub_res.get("sub_section_outline") or ""
            if gen_sub_res["rs_success"]:
                ok, reason = self.check_chapter_format(outline_text, section_idx)
                if ok:
                    current_inputs["sub_section_outline"] = outline_text
                    break
                fail_detail = f"outline format invalid: {reason}"
            else:
                fail_detail = f"LLM outline generation failed: {outline_text[:500]}"

            if retry_messages is None:
                outline_retry_feedback = fail_detail
            if LogManager.is_sensitive():
                outline_log = f"<{len(outline_text)} chars>"
            else:
                preview = outline_text.replace("\n", "\\n")
                outline_log = preview[:500] + ("..." if len(preview) > 500 else "")
            fail_detail_log = "<detail masked>" if LogManager.is_sensitive() else fail_detail
            logger.warning(
                "%s [generate_sub_report] section_idx: [%s], "
                "section outline failed on attempt %s/%s: %s | outline=%s",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                attempt_num + 1,
                max_attempt_num,
                fail_detail_log,
                outline_log,
            )
            if attempt_num == max_attempt_num - 1:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"Error: Generate section outline failed, reach the max_attempt_num: {max_attempt_num}."
                )
                return False, "generate section outline fail"
        return True, ""
