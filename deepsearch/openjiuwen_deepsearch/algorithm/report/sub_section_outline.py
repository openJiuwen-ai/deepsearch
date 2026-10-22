# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
import logging
import re
from calendar import monthrange
from collections.abc import Callable
from datetime import date

from openjiuwen_deepsearch.utils.moderation.retry import moderation_retry_scope
from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages
from openjiuwen_deepsearch.algorithm.report.report_common import EFFECT_SUB_REPORT_TAG
from openjiuwen_deepsearch.algorithm.report.report_utils import export_outline_without_plans
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    build_section_local_contract_prompt_context,
    build_research_intent_prompt_context,
    build_time_contract,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)


def _validate_outline_years(outline_text: str, time_contract: dict) -> tuple[bool, str]:
    """Reject explicit dates or time ranges broader than the content-time contract."""
    if not isinstance(time_contract, dict):
        return True, ""
    scope = time_contract.get("content_date_scope") or {}
    if not isinstance(scope, dict):
        return True, ""
    if not scope:
        return True, ""
    start_text = str(scope.get("start") or "")
    end_text = str(scope.get("end") or "")
    try:
        start_date = date.fromisoformat(start_text[:10]) if start_text else None
    except ValueError:
        start_date = None
    try:
        end_date = date.fromisoformat(end_text[:10]) if end_text else None
    except ValueError:
        end_date = None
    if start_date is None and end_date is None:
        return True, ""

    remaining = outline_text
    intervals = []
    invalid_expressions = []

    # Standard identifiers and hardware model numbers are not fact-time
    # expressions. Mask the identifier (including an optional standard edition)
    # without hiding other years in the same heading.
    identifier_pattern = re.compile(
        r"(?<![A-Za-z0-9_])(?:ISO(?:\s*/\s*IEC)?|IEC|RFC|IEEE|DIN|EN|GB(?:\s*/\s*T)?|RTX|GTX)"
        r"\s*[-:]?\s*\d+(?::\d{4})?(?!\d)",
        re.IGNORECASE,
    )
    remaining = identifier_pattern.sub(lambda match: " " * len(match.group(0)), remaining)

    def _mask_matches(
        pattern: re.Pattern[str], parse_interval: Callable[[re.Match[str]], tuple[date, date]],
    ) -> None:
        """Parse and mask each expression once, including invalid intervals."""
        nonlocal remaining
        chars = list(remaining)
        for match in pattern.finditer(remaining):
            try:
                interval_start, interval_end = parse_interval(match)
                if interval_start > interval_end:
                    raise ValueError("reversed time interval")
            except ValueError:
                invalid_expressions.append(match.group(0))
            else:
                intervals.append((match.group(0), interval_start, interval_end))
            chars[match.start():match.end()] = " " * (match.end() - match.start())
        remaining = "".join(chars)

    def _normalize_date(expression: str) -> str:
        if "/" not in expression:
            return expression
        year, month, day = expression.split("/")
        return f"{year}-{int(month):02d}-{int(day):02d}"

    date_token = r"\d{4}(?:-\d{2}-\d{2}|/\d{1,2}/\d{1,2})"
    date_range_pattern = re.compile(
        rf"(?<!\d)({date_token})\s*(?:至|到|~|～|–|—|－|to|-)\s*"
        rf"({date_token})(?!\d)",
        re.IGNORECASE,
    )
    # Date ranges are handled before individual dates, so each range is checked
    # as one interval rather than as two unrelated points.
    _mask_matches(date_range_pattern, lambda match: (
        date.fromisoformat(_normalize_date(match.group(1))),
        date.fromisoformat(_normalize_date(match.group(2))),
    ))

    def _parse_chinese_date(expression: str) -> tuple[date, date]:
        match = re.fullmatch(r"(\d{4})年(\d{1,2})月(?:(\d{1,2})日?)?", expression)
        if not match:
            raise ValueError(f"unsupported date expression: {expression}")
        year, month = int(match.group(1)), int(match.group(2))
        day_text = match.group(3)
        if day_text:
            parsed = date(year, month, int(day_text))
            return parsed, parsed
        return date(year, month, 1), date(year, month, monthrange(year, month)[1])

    chinese_date_token = r"\d{4}年\d{1,2}月(?:\d{1,2}日?)?"
    chinese_range_pattern = re.compile(
        rf"(?<!\d)({chinese_date_token})\s*(?:至|到|~|～|–|—|－|to|-)\s*"
        rf"({chinese_date_token})(?!\d)",
        re.IGNORECASE,
    )
    _mask_matches(chinese_range_pattern, lambda match: (
        _parse_chinese_date(match.group(1))[0], _parse_chinese_date(match.group(2))[1],
    ))

    chinese_date_pattern = re.compile(rf"(?<!\d){chinese_date_token}(?!\d)")
    _mask_matches(chinese_date_pattern, lambda match: _parse_chinese_date(match.group(0)))

    def _parse_iso_date(match: re.Match[str]) -> tuple[date, date]:
        parsed = date.fromisoformat(_normalize_date(match.group(1)))
        return parsed, parsed

    _mask_matches(re.compile(rf"(?<!\d)({date_token})(?!\d)"), _parse_iso_date)

    # Prefer calendar months over abbreviated years for the ambiguous YYYY-MM
    # and YYYY/MM forms. Other range separators are unambiguously year ranges.
    month_pattern = re.compile(r"(?<!\d)(\d{4})[-/](0[1-9]|1[0-2])(?!\d|/\d|-\d)")

    def _parse_month(match: re.Match[str]) -> tuple[date, date]:
        year, month = int(match.group(1)), int(match.group(2))
        return date(year, month, 1), date(year, month, monthrange(year, month)[1])

    _mask_matches(month_pattern, _parse_month)

    # Accept abbreviated ranges such as 2020-21 and 2020/21. The end year is
    # interpreted within the start year's century, rolling forward if needed
    # (for example, 1999-00 means 1999 through 2000).
    abbreviated_year_range_pattern = re.compile(
        r"(?<!\d)(\d{4})\s*(?:-|/|–|—|－|至|到|~|～|to)\s*(\d{2})(?!\d)",
        re.IGNORECASE,
    )

    def _parse_abbreviated_range(match: re.Match[str]) -> tuple[date, date]:
        start_year = int(match.group(1))
        end_year = (start_year // 100) * 100 + int(match.group(2))
        if end_year < start_year:
            end_year += 100
        return date(start_year, 1, 1), date(end_year, 12, 31)

    _mask_matches(abbreviated_year_range_pattern, _parse_abbreviated_range)

    _mask_matches(re.compile(
        r"(?<!\d)(\d{4})\s*(?:至|到|~|～|–|—|－|to|-)\s*(\d{4})(?!\d)",
        re.IGNORECASE,
    ), lambda match: (date(int(match.group(1)), 1, 1), date(int(match.group(2)), 12, 31)))

    year_pattern = re.compile(r"(?<!\d)(\d{4})(?!\d)")
    for match in year_pattern.finditer(remaining):
        before = remaining[:match.start()]
        after = remaining[match.end():]
        # A year suffix is explicit time; units and alphanumeric identifiers
        # instead describe quantities/models. Preserve nearby independent years.
        explicit_year = re.match(r"\s*年", after) is not None
        quantity = re.match(
            r"\s*(?:[万亿千百]?(?:元|美元|欧元|吨|人|个|台|件|辆|户|家|米|瓦|字节)|"
            r"[万亿]|%|％|GB|TB|MB|KB|MHz|GHz|kWh|MW|GW|kg|km)", after, re.IGNORECASE,
        )
        identifier = re.search(r"[A-Za-z][A-Za-z0-9_.-]*$", before) or re.match(r"[A-Za-z_]", after)
        identifier_context = re.search(
            r"(?:型号|编号|货号|model|type|HTTP|port|buffer|part\s*(?:no\.?|number))\s*[:：#-]?\s*$",
            before, re.IGNORECASE,
        )
        currency = re.search(r"(?:[$€£¥￥]|USD|EUR|CNY)\s*$", before, re.IGNORECASE)
        non_temporal_context = any((quantity, identifier, identifier_context, currency))
        if not explicit_year and non_temporal_context:
            continue
        year = int(match.group(1))
        # Bare four-digit values outside the common calendar window are
        # ambiguous identifiers, not sufficient evidence of a fact-time claim.
        # Explicit dates/ranges and the 年 suffix remain unrestricted.
        if not explicit_year and not 1900 <= year <= 2100:
            continue
        try:
            intervals.append((match.group(1), date(year, 1, 1), date(year, 12, 31)))
        except ValueError:
            invalid_expressions.append(match.group(1))

    invalid = list(invalid_expressions)
    for label, interval_start, interval_end in intervals:
        if start_date is not None and interval_start < start_date:
            invalid.append(label)
        elif end_date is not None and interval_end > end_date:
            invalid.append(label)
    if invalid:
        return False, (
            "explicit outline date/year range(s) " + ", ".join(dict.fromkeys(invalid))
            + f" fall outside content_date_scope {start_text or '-inf'}~{end_text or '+inf'}"
        )
    return True, ""


def _validate_user_time_constraints(current_inputs: dict, time_contract: dict) -> tuple[bool, str]:
    """Compatibility hook: inherited titles never block chapter generation.

    Fact-time constraints govern the body and model-added subheadings, not
    the authoritative chapter title. Descriptions may also mention exclusions.
    """
    return True, ""


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
        outline_evidence_cards = current_inputs.get("outline_evidence_cards", [])
        time_contract = current_inputs.get("time_contract") or build_time_contract(
            current_inputs.get("research_intent")
        )
        rationale_contract = current_inputs.get("rationale_contract", {"items": []})

        # Validate required fields
        if not section_task or not outline_evidence_cards:
            error_msg = "Missing 'section_task' or 'outline_evidence_cards' in context (section title required)"
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_section_outline] section_idx: [{section_idx}] "
                f"{error_msg}"
            )
            return dict(rs_success=False, sub_section_outline=error_msg)
        llm_input = retry_messages
        llm_request_in_progress = False
        try:
            tmp_context = {
                "report_task": report_task,
                "core_context": json.dumps(outline_evidence_cards, ensure_ascii=False),
                "time_contract_json": json.dumps(time_contract, ensure_ascii=False),
                "rationale_contract_json": json.dumps(rationale_contract, ensure_ascii=False),
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
            tmp_context["time_contract"] = time_contract
            tmp_context["rationale_contract"] = rationale_contract
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

    @moderation_retry_scope
    async def _generate_outline_with_retry(
        self, current_inputs: dict, section_idx, max_attempt_num: int
    ) -> tuple[bool, str]:
        """Generate sub-section outline with retry and format validation.

        Returns (success, error_message). On success, sets current_inputs["sub_section_outline"].
        """
        outline_retry_feedback = ""
        retry_messages = None
        time_contract = current_inputs.get("time_contract") or build_time_contract(
            current_inputs.get("research_intent")
        )
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
                    ok, reason = _validate_outline_years(
                        "\n".join(outline_text.splitlines()[1:]),
                        time_contract,
                    )
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
