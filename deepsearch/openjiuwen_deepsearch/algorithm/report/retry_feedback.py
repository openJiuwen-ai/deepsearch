# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import logging
import re

logger = logging.getLogger(__name__)


class RetryFeedbackMixin:
    """Controlled retry feedback construction mixin.

    Dependencies (provided by Reporter or other mixins):
        - self.gen_report_context: report context dict (from Reporter.__init__)
    """

    @staticmethod
    def _build_sub_report_retry_feedback(
        error_code: str,
        location: str,
        fields: dict | None = None,
    ) -> str:
        """Build controlled retry feedback without echoing model/provider text."""
        allowed_codes = {
            "HEADING_COUNT_MISMATCH",
            "HEADING_TITLE_MISMATCH",
            "HEADING_MISSING",
            "OUTLINE_HEADING_MISSING",
            "DUPLICATE_SUBSECTION_HEADINGS",
            "SUB_REPORT_CONTENT_EMPTY",
            "MERMAID_OUTPUT_FORBIDDEN",
            "MISSING_SECTION_CONTEXT",
            "MISSING_REQUIRED_TARGET_CITATIONS",
            "SUB_REPORT_GENERATION_EXCEPTION",
            "SUB_REPORT_RETRY_REQUIRED",
        }
        error_code = error_code if error_code in allowed_codes else "SUB_REPORT_RETRY_REQUIRED"
        lines = [f"error_code: {error_code}", f"location: {location}"]
        for key in (
            "expected_heading_count",
            "actual_heading_count",
            "expected_heading_level",
        ):
            value = (fields or {}).get(key)
            if value is None:
                continue
            match = re.match(r"^H?(\d+)$", str(value).strip(), flags=re.IGNORECASE)
            if not match:
                continue
            safe_value = (
                f"H{int(match.group(1))}"
                if key.endswith("_level")
                else str(int(match.group(1)))
            )
            lines.append(f"{key}: {safe_value}")
        missing_citation_indexes = (fields or {}).get("missing_citation_indexes")
        if missing_citation_indexes is not None:
            match = re.fullmatch(
                r"[1-9]\d*(?:\s*,\s*[1-9]\d*)*",
                str(missing_citation_indexes).strip(),
            )
            if match:
                safe_indexes = ",".join(
                    str(int(value.strip()))
                    for value in match.group(0).split(",")
                )
                lines.append(f"missing_citation_indexes: {safe_indexes}")
        return "\n".join(lines)

    @classmethod
    def _sub_report_retry_feedback_from_failure(cls, failure_reason: str) -> str:
        """Convert raw failure text into a prompt-safe retry hint."""
        reason = str(failure_reason or "").strip()
        if not reason:
            return ""

        code_match = re.search(r"(?m)^\s*error_code:\s*([A-Z0-9_]+)\s*$", reason)
        if code_match:
            fields = {}
            for key in (
                "expected_heading_count",
                "actual_heading_count",
                "expected_heading_level",
            ):
                field_match = re.search(rf"(?m)^\s*{key}:\s*(H?\d+)\s*$", reason)
                if field_match:
                    fields[key] = field_match.group(1)
            error_code = code_match.group(1)
            citation_match = re.search(
                r"(?m)^\s*missing_citation_indexes:\s*"
                r"([1-9]\d*(?:\s*,\s*[1-9]\d*)*)\s*$",
                reason,
            )
            if citation_match:
                fields["missing_citation_indexes"] = citation_match.group(1)
            if error_code.startswith("HEADING") or error_code == "DUPLICATE_SUBSECTION_HEADINGS":
                location = "markdown_headings"
            elif error_code == "MERMAID_OUTPUT_FORBIDDEN":
                location = "chapter_visualization"
            elif error_code == "MISSING_REQUIRED_TARGET_CITATIONS":
                location = "chapter_citations"
            else:
                location = "chapter"
            return cls._build_sub_report_retry_feedback(error_code, location, fields)

        heading_patterns = [
            (
                r"heading count insufficient:\s*expected at least\s*(\d+),\s*got\s*(\d+)",
                "HEADING_COUNT_MISMATCH",
                ("expected_heading_count", "actual_heading_count"),
            ),
            (
                r"outline heading not found:\s*expected\s*H?(\d+)\s+'[^']*'",
                "HEADING_TITLE_MISMATCH",
                ("expected_heading_level",),
            ),
        ]
        for pattern, error_code, field_names in heading_patterns:
            match = re.search(pattern, reason, flags=re.IGNORECASE)
            if match:
                return cls._build_sub_report_retry_feedback(
                    error_code,
                    "markdown_headings",
                    dict(zip(field_names, match.groups())),
                )

        reason_lower = reason.lower()
        if "generated report headings are empty" in reason_lower:
            return cls._build_sub_report_retry_feedback(
                "HEADING_MISSING",
                "markdown_headings",
            )
        if "expected subsection outline headings are empty" in reason_lower:
            return cls._build_sub_report_retry_feedback(
                "OUTLINE_HEADING_MISSING",
                "markdown_headings",
            )
        if "duplicate headings" in reason_lower:
            return cls._build_sub_report_retry_feedback(
                "DUPLICATE_SUBSECTION_HEADINGS",
                "markdown_headings",
            )
        if (
            "no sub report content found" in reason_lower
            or "sub report content is blank" in reason_lower
        ):
            return cls._build_sub_report_retry_feedback(
                "SUB_REPORT_CONTENT_EMPTY",
                "chapter",
            )
        if "mermaid" in reason_lower or "chart source" in reason_lower:
            return cls._build_sub_report_retry_feedback(
                "MERMAID_OUTPUT_FORBIDDEN",
                "chapter_visualization",
            )
        if (
            "missing 'section_task'" in reason_lower
            or "missing 'section_task' or sub section outline" in reason_lower
        ):
            return cls._build_sub_report_retry_feedback(
                "MISSING_SECTION_CONTEXT",
                "chapter_context",
            )
        if (
            "error generating section" in reason_lower
            or "llm returned empty content" in reason_lower
        ):
            return cls._build_sub_report_retry_feedback(
                "SUB_REPORT_GENERATION_EXCEPTION",
                "chapter_generation",
            )

        return cls._build_sub_report_retry_feedback("SUB_REPORT_RETRY_REQUIRED", "chapter")
