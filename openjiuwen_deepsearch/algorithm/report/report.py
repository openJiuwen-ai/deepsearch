# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import html
from datetime import datetime, timezone
from copy import deepcopy
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Tuple, List, Dict
from urllib.parse import urlparse

from tenacity import (
    RetryError,
    after_log,
    retry,
    stop_after_attempt,
    retry_if_exception_type,
)

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.report.compact_doc_info import (
    build_compact_classify_doc_infos_text,
    format_scores_inline,
    format_key_passage_block,
    get_numeric_score,
)
from openjiuwen_deepsearch.algorithm.report.ngram_utils import (
    extract_doc_ngrams,
    ngram_jaccard_similarity,
    prefilter_by_ngram_coverage,
)
from openjiuwen_deepsearch.algorithm.report.config import ReportFormat
from openjiuwen_deepsearch.algorithm.report.doc_prefilter import (
    build_doc_variant_key,
)
from openjiuwen_deepsearch.algorithm.report.report_utils import (
    ArticlePart,
    MarkdownOutlineRenumber,
    XYChartMermaidGenerator,
    PieChartMermaidGenerator,
    TimelineChartMermaidGenerator,
    validate_visualization_extraction_schema,
    validate_visualization_normalization_schema,
)
from openjiuwen_deepsearch.algorithm.report.table_caption_utils import ensure_markdown_table_captions
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode, format_exception_info
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ChapterSidecar,
    Outline,
    build_research_intent_prompt_context,
    build_section_local_contract_prompt_context,
)
from openjiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.utils.common_utils.stream_utils import get_current_time, MessageType, StreamEvent
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context, session_context

logger = logging.getLogger(__name__)


def _format_report_error(detail: str | BaseException) -> str:
    return format_exception_info(StatusCode.REPORT_GENERATE_ERROR, detail)


def _format_sub_report_error(detail: str | BaseException) -> str:
    return format_exception_info(StatusCode.SUB_REPORT_GENERATE_ERROR, detail)


EFFECT_SUB_REPORT_TAG = "### sub_report_tag ###"
BATCH_SIZE = 15
MAX_CONCURRENT_BATCHES = 5
LEADING_TITLE_NUMBER_PATTERN = re.compile(
    r"^(?:"
    r"[\（][一二三四五六七八九十\d]{1,2}[\）]\s*|"
    r"[\(][一二三四五六七八九十\d]{1,2}[\)]\s*|"
    r"第?[一二三四五六七八九十\d]+章\s*|"
    r"[一二三四五六七八九十]+、\s*|"
    r"\d{1,2}(?:\.\d{1,2})+\s*(?![\da-zA-Z.])|"
    r"\d{1,2}[\.、]\s*(?![\da-zA-Z.])|"
    r"(?:[1-9]|1\d)\s+|"
    r")"
)
INTERNAL_CALLBACK_LABEL_PATTERN = re.compile(
    r"\s*\["
    r"(?=[^\]]*(?:background|knowledge|parent|section|prior|summary|背景|知识))"
    r"[^\]]+"
    r"\]\s*",
    re.IGNORECASE,
)


@dataclass
class VisualizationInsertPlanContext:
    messages: list
    current_inputs: Dict
    report_lines: list[str]
    invalid_rows: set[int]
    mermaid_map: dict[int, str]
    original_report: str


@dataclass
class VisualizationInsertRenderContext:
    report_lines: list[str]
    insertions: list[dict]
    mermaid_map: dict[int, str]
    title_meta_map: dict[int, dict]
    newline: str
    language: str


@dataclass
class DocSelectionContext:
    """Encapsulates doc-selection intermediate results for debug export."""
    rationales: list
    coverage_result: dict
    doc_infos: list
    selected_docs: list
    selected_marginal_values: list
    verify_result: dict


class Reporter:
    def __init__(self, llm_model_name):
        # Keep consistent with other modules: workflow/template_generator registers
        # into llm_context at session; fetch by model name here.
        self._llm = llm_context.get().get(llm_model_name)
        self.gen_report_context = None

    @staticmethod
    def strip_leading_number(s: str) -> str:
        """移除标题前导编号并返回清洗后的文本。"""
        return LEADING_TITLE_NUMBER_PATTERN.sub("", s)

    @staticmethod
    def _section_sort_key(section_id) -> tuple[int, int | str]:
        """Keep report sections ordered numerically when section ids are strings."""
        text = str(section_id).strip()
        if text.isdigit():
            return 0, int(text)
        return 1, text

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
    def clean_markdown_headers(md_text: str) -> str:
        """
        Process Markdown text:
        1. Remove numbering from H1-H3 headers (e.g. "一、", "(一)", "1.", "(1)", "（1）").
        2. Convert H4+ headers to unordered list items and remove numbering.
        """

        def clean_header(line: str, level: int) -> str:
            """
            Generic header cleanup helper.
            level is the header level (number of '#').
            """
            content = re.sub(rf'^\s*{"#" * level}\s*', "", line).strip()
            content = Reporter.strip_leading_number(content).strip()
            return f'{"#" * level} {content}'.rstrip()

        lines = md_text.splitlines()
        new_lines = []

        for line in lines:
            stripped = line.strip()

            # Handle H1-H3 uniformly
            if stripped.startswith("# "):
                new_lines.append(clean_header(line, 1))
            elif stripped.startswith("## "):
                new_lines.append(clean_header(line, 2))
            elif stripped.startswith("### "):
                new_lines.append(clean_header(line, 3))

            # H4 and deeper headers
            elif re.match(r"^\s*#{4,}\s+", line):
                content = re.sub(r"^\s*#{4,}\s+", "", line).strip()
                content = Reporter.strip_leading_number(content).strip()
                transferred_header = f"- **{content}**"
                new_lines.append(transferred_header)

            else:
                new_lines.append(line)

        return "\n".join(new_lines)

    @staticmethod
    def _get_invalid_rows_for_insertion(report_lines: list[str]) -> set[int]:
        """
        Identify rows that must NOT be used as visualization insertion anchors.
        This follows `insert_visualization.md` forbidden insertion locations:
        - fenced code blocks (``` or ~~~) and their inner lines
        - indented code blocks (4 spaces or tab)
        - list items (ordered/unordered)
        - blockquotes ('>')
        - markdown tables (lines starting with '|', ignoring leading whitespace)
        """
        invalid_rows: set[int] = set()
        in_code_block = False
        for i, line in enumerate(report_lines, 1):
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                invalid_rows.add(i)
                in_code_block = not in_code_block
                continue
            if in_code_block:
                invalid_rows.add(i)
                continue
            if line.startswith("    ") or line.startswith("\t"):
                invalid_rows.add(i)
                continue
            if stripped.startswith(">"):
                invalid_rows.add(i)
                continue
            if re.match(r"^(\d+[.)]\s+|[-*+]\s+)", stripped):
                invalid_rows.add(i)
                continue
            if line.lstrip().startswith("|"):
                invalid_rows.add(i)
        return invalid_rows

    @staticmethod
    def _precheck_value_variation(
        visualization_content: dict, section_idx: int
    ) -> bool:
        # Pre-check value variation before Mermaid generation
        try:
            payload = json.loads(
                visualization_content.get("sub_section_visualization_content", "")
            )
            chart_type = payload.get("image_type", "")
            if chart_type in ("bar", "line"):
                records = payload.get("records", [])
                values: list[float] = []
                for row in records:
                    if (
                        isinstance(row, list)
                        and len(row) == 2
                        and isinstance(row[1], (int, float))
                    ):
                        values.append(float(row[1]))
                if values and len(set(values)) < 3:
                    visualization_content["rs_success"] = False
                    visualization_content["error_msg"] = "insufficient_value_variation"
                    return False
        except Exception as e:
            logger.warning(
                "%s [process_visualization_task] section_idx: [%s] "
                "value-variation precheck failed: %s",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                str(e),
            )
        return True

    @staticmethod
    def _generate_mermaid_code(visualization_content: dict, section_idx: int) -> dict:
        # Generate Mermaid code from data and chart type
        visualization_content["mermaid_content"] = ""
        mermaid_ok = False
        mermaid_type = None
        try:
            mermaid_type = json.loads(
                visualization_content.get("sub_section_visualization_content", "")
            ).get("image_type", "")
        except json.JSONDecodeError:
            mermaid_type = ""

        def _render_mermaid(chart_type: str, generator) -> bool:
            try:
                payload = json.loads(
                    visualization_content.get("sub_section_visualization_content", "")
                )
                records = payload.get("records", [])
                if not isinstance(records, list) or not (3 <= len(records) <= 12):
                    raise ValueError(f"{chart_type} records length out of range")
                mermaid_code = generator.generate_from_json(
                    json.dumps(payload, ensure_ascii=False)
                )
                visualization_content["mermaid_content"] = mermaid_code
                return True
            except Exception as e:
                logger.warning(
                    "%s [process_visualization_task] section_idx: [%s], %s mermaid generation failed: %s",
                    EFFECT_SUB_REPORT_TAG,
                    section_idx,
                    chart_type,
                    str(e),
                )
                return False

        if mermaid_type == "bar":
            mermaid_ok = _render_mermaid("bar", XYChartMermaidGenerator)
        elif mermaid_type == "line":
            mermaid_ok = _render_mermaid("line", XYChartMermaidGenerator)
        elif mermaid_type == "pie":
            mermaid_ok = _render_mermaid("pie", PieChartMermaidGenerator)
        elif mermaid_type == "timeline":
            mermaid_ok = _render_mermaid("timeline", TimelineChartMermaidGenerator)
        else:
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [process_visualization_task] section_idx: [{section_idx}], "
                f"unsupported mermaid_type: {mermaid_type}"
            )
        if not mermaid_ok:
            visualization_content["rs_success"] = False
            visualization_content["error_msg"] = "mermaid_generation_failed"
        return visualization_content

    @staticmethod
    def check_chapter_format(text, section_idx) -> tuple[bool, str]:
        """Validate subsection outline plain-text numbering"""
        try:
            n = section_idx
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not lines:
                return False, "outline is empty"

            # Subsection: "1.1 title".
            # Section: "1 title" (title may start with digits, e.g. 2025) or "1. title" but not "1.1".
            sub_pat = re.compile(rf"^\s*{n}\.(\d+)\s*")
            main_space_pat = re.compile(rf"^\s*{n}\s+.+")
            main_dot_pat = re.compile(rf"^\s*{n}\.(?!\d)\s*.+")
            third_pat = re.compile(r"\d+\.\d+\.\d+")

            has_main = False
            sub_numbers = []

            for line_no, ln in enumerate(lines, start=1):
                if ln.lstrip().startswith("#"):
                    preview = ln[:120] + ("..." if len(ln) > 120 else "")
                    return (
                        False,
                        f"line {line_no}: markdown heading not allowed "
                        f"(use plain '{n} title' / '{n}.1 title', not '#'): {preview!r}",
                    )
                if third_pat.search(ln):
                    preview = ln[:120] + ("..." if len(ln) > 120 else "")
                    return (
                        False,
                        f"line {line_no}: third-level numbering not allowed (e.g. {n}.1.1): {preview!r}",
                    )
                sub_match = sub_pat.match(ln)
                if sub_match:
                    sub_numbers.append(int(sub_match.group(1)))
                elif main_space_pat.match(ln) or main_dot_pat.match(ln):
                    if has_main:
                        preview = ln[:120] + ("..." if len(ln) > 120 else "")
                        return (
                            False,
                            f"line {line_no}: duplicate level-1 title for section {n}: {preview!r}",
                        )
                    has_main = True
                elif re.match(r"\d+", ln):
                    preview = ln[:120] + ("..." if len(ln) > 120 else "")
                    return (
                        False,
                        f"line {line_no}: line starts with digits but is not a valid "
                        f"'{n} title' or '{n}.x' subsection title: {preview!r}",
                    )

            sorted_subs = sorted(set(sub_numbers))
            if not sorted_subs:
                return (
                    False,
                    f"no valid subsection lines like '{n}.1 title' "
                    f"(found {len(lines)} non-empty line(s); level-1 present={has_main})",
                )
            if sorted_subs[0] != 1:
                return (
                    False,
                    f"first subsection must be {n}.1, got {n}.{sorted_subs[0]} "
                    f"(subsection indices found: {sorted_subs})",
                )
            if not has_main:
                return (
                    False,
                    f"missing level-1 title line like '{n} section title' "
                    f"(subsection indices found: {sorted_subs})",
                )
            return True, ""
        except Exception as e:
            if LogManager.is_sensitive():
                return False, f"format check exception for section_idx={section_idx}"
            return False, f"format check exception for section_idx={section_idx}: {e}"

    @staticmethod
    def _normalize_heading_title(title: str) -> str:
        title = Reporter.strip_leading_number(title or "")
        title = re.sub(r"\s+", " ", title).strip()
        return title

    @staticmethod
    def _extract_outline_heading_pairs(sub_section_outline: str) -> list[tuple[int, str]]:
        pairs: list[tuple[int, str]] = []
        for line in sub_section_outline.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            level = 1 if not pairs else 2
            pairs.append((level, Reporter._normalize_heading_title(stripped)))
        return pairs

    @staticmethod
    def _extract_markdown_heading_pairs(content: str) -> list[tuple[int, str]]:
        pairs: list[tuple[int, str]] = []
        for line in content.splitlines():
            match = re.match(r"^\s*(#{1,2})\s+(.+?)\s*$", line)
            if not match:
                continue
            level = len(match.group(1))
            pairs.append((level, Reporter._normalize_heading_title(match.group(2))))
        return pairs

    @staticmethod
    def validate_sub_report_headings_match_outline(
        content: str,
        sub_section_outline: str,
    ) -> tuple[bool, str]:
        """Ensure generated markdown headings strictly follow the approved subsection outline."""
        expected_pairs = Reporter._extract_outline_heading_pairs(sub_section_outline)
        actual_pairs = Reporter._extract_markdown_heading_pairs(content)

        if not expected_pairs:
            return False, "expected subsection outline headings are empty"
        if not actual_pairs:
            return False, "generated report headings are empty"

        if len(actual_pairs) != len(expected_pairs):
            return (
                False,
                f"heading count mismatch: expected {len(expected_pairs)}, got {len(actual_pairs)}",
            )

        for index, (expected, actual) in enumerate(
            zip(expected_pairs, actual_pairs),
            start=1,
        ):
            if expected[0] != actual[0]:
                return (
                    False,
                    f"heading level mismatch at position {index}: expected H{expected[0]}, got H{actual[0]}",
                )
            if expected[1] != actual[1]:
                return (
                    False,
                    f"heading title mismatch at position {index}: expected '{expected[1]}', got '{actual[1]}'",
                )

        if len({pair for pair in actual_pairs[1:]}) != len(actual_pairs[1:]):
            return False, "duplicate subsection headings detected in generated report"

        return True, ""

    @staticmethod
    def is_valid_chapter_format(text, section_idx) -> bool:
        """Check chapter format"""
        ok, reason = Reporter.check_chapter_format(text, section_idx)
        if not ok:
            logger.warning(
                "%s [is_valid_chapter_format] section_idx=%s invalid: %s",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                reason,
            )
        return ok

    @staticmethod
    def add_references(sub_section_content: str, references: list, language: str):
        """Add references for subsection content"""
        logger.info(f"Adding references to sub_section_content")
        if not references:
            logger.info(f"No references found. can not add references.")
            return sub_section_content
        if sub_section_content:
            if language == CHINESE:
                append = "\n## 参考文章\n"
            else:
                append = "\n## References\n"
            temp_ref = "\n".join(f"[{i + 1}] {s}" for i, s in enumerate(references))
            sub_section_content = sub_section_content + append + temp_ref
        return sub_section_content

    @staticmethod
    def refresh_reference(sub_reports_content, sub_references, all_classified_contents):
        """Refresh references"""
        refreshed_references = ""
        raw_references = "\n".join(sub_references) if sub_references else ""
        if raw_references:
            refreshed_references, ref_map = _deduplicate_and_renumber_ref(
                raw_references
            )
            if not LogManager.is_sensitive():
                logger.info("refreshed_references: [%s]", refreshed_references)
            sub_reports_content, all_classified_contents = (
                _replace_citations_and_classified_index(
                    sub_reports_content, all_classified_contents, ref_map
                )
            )

        return dict(
            sub_reports_content="\n\n".join(sub_reports_content),
            sub_references=refreshed_references,
            refreshed_all_classified_contents=all_classified_contents,
        )

    @staticmethod
    def _is_valid_insert_plan(
        plan_obj: object,
        report_lines: list[str],
        invalid_rows: set[int],
        mermaid_map: dict[int, str],
    ) -> tuple[bool, str]:
        if not isinstance(plan_obj, dict):
            return (
                False,
                "Plan must be a JSON object with an 'insertions' array.",
            )
        insertions = plan_obj.get("insertions")
        if not isinstance(insertions, list):
            return (
                False,
                "Invalid 'insertions': expected an array of {after_row, index} objects.",
            )
        used_indices: set[int] = set()
        for item in insertions:
            if not isinstance(item, dict):
                return (
                    False,
                    "Each insertion must be an object with 'after_row' and 'index' integers.",
                )
            after_row = item.get("after_row")
            index = item.get("index")
            if not isinstance(after_row, int) or not isinstance(index, int):
                return (
                    False,
                    "Fields 'after_row' and 'index' must both be integers.",
                )
            if after_row < 1 or after_row > len(report_lines):
                return (
                    False,
                    "after_row is out of range for the current report lines.",
                )
            if after_row in invalid_rows:
                return (
                    False,
                    "after_row points into a forbidden line (code block/list/table).",
                )
            if index not in mermaid_map:
                return (
                    False,
                    "index does not exist in the provided visualization data.",
                )
            if index in used_indices:
                return (
                    False,
                    "Duplicate index detected; each index can appear only once.",
                )
            used_indices.add(index)
        return True, ""

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
        if not outline or not isinstance(outline, (Outline, dict)):
            logger.warning(
                "export_outline_without_plans: unsupported outline type or empty outline."
            )
            return outline

        is_dict = isinstance(outline, dict)
        obj = Outline.model_validate(outline) if is_dict else outline

        data = obj.model_dump(exclude={"sections": {"__all__": {"plans"}}})

        return data if is_dict else Outline.model_validate(data)

    @staticmethod
    def _get_background_knowledge_contents(background_knowledge: list) -> list[dict[str, str]]:
        """Extract usable text snippets from dependency-writing background knowledge."""
        if not isinstance(background_knowledge, list):
            return []

        contents = []
        for item in background_knowledge:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content_summary", "") or "").strip()
            if not content:
                continue
            section_id = str(item.get("section_id", "") or "").strip()
            allowed_callback = (
                f"Refer to Section {section_id} in natural prose only; do not cite this context."
                if section_id
                else "Refer to prior sections in natural prose only; do not cite this context."
            )
            contents.append(
                {
                    "section_id": section_id,
                    "summary": content,
                    "allowed_callback": allowed_callback,
                }
            )

        return contents

    @staticmethod
    def _format_background_knowledge_for_prompt(background_knowledge_contents: list[dict[str, str]]) -> str:
        """Format prior-section background knowledge for model input without citation-like labels."""
        if not background_knowledge_contents:
            return (
                "Background Knowledge / prior-section continuity context "
                "(not citation sources): []"
            )
        payload = json.dumps(background_knowledge_contents, ensure_ascii=False, indent=2)
        return (
            "Background Knowledge / prior-section continuity context (not citation sources):\n"
            f"{payload}\n"
            "Rules for this context:\n"
            "- Use it only to maintain continuity with earlier sections.\n"
            "- You may refer to it in natural prose, such as \"as discussed in Section 2\" "
            "or \"结合第2章分析\".\n"
            "- Never cite it with `[citation:X]`.\n"
            "- Never output bracketed labels about this context."
        )

    @staticmethod
    def _clean_internal_callback_labels(content: str) -> str:
        """Remove leaked dependency-context labels while preserving natural callbacks."""
        if not content:
            return ""
        return INTERNAL_CALLBACK_LABEL_PATTERN.sub("", content)

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

    async def generate_report(self, gen_report_context: dict) -> Tuple[bool, str]:
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

        self.gen_report_context["current_outline"] = self.export_outline_without_plans(
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

        report_content = (
            f"{'# ' + current_outline.title}\n\n"  # Use outline title directly for report title
            f"{self._post_process_abstract(abstract)}\n\n"
            f"{sub_report_res.get('sub_reports_content')}\n\n"
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
        report_type = "professional"
        if isinstance(self.gen_report_context, dict):
            report_type = self.gen_report_context.get("report_type", "professional")
        if report_type == "brief":
            # Brief reports should output a pure conclusion section
            # without the implications/recommendations chapter.
            prompt = "report_conclusion_markdown"
        else:
            report_format = ReportFormat.MARKDOWN
            prompt = f"report_implications_and_recommendations_{report_format.get_name()}"
        conclusion = await self._generate_with_llm(
            "conclusion", prompt, sub_reports_content
        )
        logger.info(f"Generating report conclusion Done.")
        return conclusion

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
                f"doc infos len: {len(current_inputs.get('doc_infos', []))}"
            )
        else:
            logger.debug(
                "%s [generate_sub_report] section_idx: [%s], doc infos is %s",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                current_inputs.get("doc_infos", []),
            )
        rtp = current_inputs.get("report_type_policy") or {}
        if isinstance(rtp, dict):
            current_inputs.setdefault("report_type", rtp.get("report_type", "professional"))
            current_inputs.setdefault("paragraph_style", rtp.get("paragraph_style", "detailed"))
            current_inputs.setdefault("require_summary_first", rtp.get("require_summary_first", False))
            current_inputs.setdefault(
                "require_methodology_and_risk", rtp.get("require_methodology_and_risk", False)
            )
        doc_infos = current_inputs.get("doc_infos", [])
        background_contents = self._get_background_knowledge_contents(
            current_inputs.get("sub_report_background_knowledge", [])
        )
        if not doc_infos:
            if not background_contents:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] fail to generate subsection report, "
                    f"section_idx: [{section_idx}], not found doc infos"
                )
                return False, _format_sub_report_error("Not found doc infos"), "", []
            logger.info(
                "%s [generate_sub_report] section_idx: [%s], no doc_infos found, "
                "use dependency background knowledge as fallback.",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
            )
            current_inputs["sub_section_core_content"] = background_contents
            current_inputs["sub_section_core_content_from_background_knowledge"] = True
            current_inputs["sub_section_references"] = []
            current_inputs["classified_content"] = []
            classified_content = []
        else:
            # New flow: rationale generation → coverage matrix → greedy optimization → elbow cutoff → verify
            rationales = await self._generate_section_rationales(current_inputs)
            if not rationales:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"rationale generation failed"
                )
                return False, _format_sub_report_error("rationale generation fail"), "", []

            coverage_result = await self._evaluate_coverage_matrix(
                current_inputs, doc_infos, rationales
            )
            if not coverage_result:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"coverage matrix evaluation failed"
                )
                return False, _format_sub_report_error("coverage matrix evaluation fail"), "", []

            classify_doc_infos_res_top_k_num = current_inputs.get(
                "classify_doc_infos_res_top_k_num", 20
            )

            selected_docs, marginal_values = self._optimize_document_set(
                doc_infos, rationales, coverage_result,
                top_k=classify_doc_infos_res_top_k_num
            )

            # Build marginal_value map by object identity so _elbow_cutoff's subset can be aligned back
            mv_by_id = {id(doc): mv for doc, mv in zip(selected_docs, marginal_values)}

            selected_docs = self._elbow_cutoff(
                selected_docs, marginal_values, classify_doc_infos_res_top_k_num,
                coverage_ctx={"coverage_result": coverage_result, "rationales": rationales},
                fallback_docs=doc_infos,
            )

            selected_marginal_values = [mv_by_id.get(id(doc), 0.0) for doc in selected_docs]

            verify_result = self._verify_coverage(
                selected_docs, rationales, coverage_result, section_idx,
                fallback_docs=doc_infos,
            )

            # Write doc-selection debug info back to Section for ResultExporter
            # Placed before early returns so debug data is captured on all exit paths
            self._write_doc_selection_debug(
                current_inputs,
                DocSelectionContext(
                    rationales=rationales,
                    coverage_result=coverage_result,
                    doc_infos=doc_infos,
                    selected_docs=selected_docs,
                    selected_marginal_values=selected_marginal_values,
                    verify_result=verify_result,
                ),
            )

            if not selected_docs:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"no docs selected after optimization"
                )
                return False, _format_sub_report_error("no docs selected after optimization"), "", []

            selected_urls = list(dict.fromkeys(
                doc.get("url", "") for doc in selected_docs if doc.get("url")
            ))
            if not selected_urls:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"no valid URLs in selected docs"
                )
                return False, _format_sub_report_error("no valid URLs in selected docs"), "", []

            classified_infos, classified_doc_infos = _get_classified_infos(
                selected_docs,
                selected_marginal_values,
                max_source_id_count=classify_doc_infos_res_top_k_num,
            )
            current_inputs["sub_section_core_content"] = classified_infos.get(
                "core_content_list", []
            )
            current_inputs["sub_section_core_content_from_background_knowledge"] = False
            current_inputs["sub_section_references"] = classified_infos.get(
                "references", []
            )
            for idx, doc_info in enumerate(classified_doc_infos):
                doc_info.pop("query", None)
                doc_info["index"] = idx + 1
            current_inputs["classified_content"] = classified_doc_infos
            classified_content = classified_doc_infos
            if LogManager.is_sensitive():
                logger.info(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"selected_content len: {len(classified_content)}"
                )
        classified_content = current_inputs.get("classified_content", [])
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
        for attempt_num in range(max_attempt_num):
            gen_sub_res = await self._generate_sub_section_outline(current_inputs)
            outline_text = gen_sub_res.get("sub_section_outline") or ""
            if gen_sub_res["rs_success"]:
                ok, reason = self.check_chapter_format(outline_text, section_idx)
                if ok:
                    current_inputs["sub_section_outline"] = outline_text
                    break
                fail_detail = f"outline format invalid: {reason}"
            else:
                fail_detail = f"LLM outline generation failed: {outline_text[:200]}"

            if LogManager.is_sensitive():
                outline_log = f"<{len(outline_text)} chars>"
            else:
                preview = outline_text.replace("\n", "\\n")
                outline_log = preview[:500] + ("..." if len(preview) > 500 else "")
            logger.warning(
                "%s [generate_sub_report] section_idx: [%s], "
                "section outline failed on attempt %s/%s: %s | outline=%s",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                attempt_num + 1,
                max_attempt_num,
                fail_detail,
                outline_log,
            )
            if attempt_num == max_attempt_num - 1:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"Error: Generate section outline failed, reach the max_attempt_num: {max_attempt_num}."
                )
                return False, _format_sub_report_error("generate section outline fail"), "", classified_content

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

        session = session_context.get()
        stream_id = str(uuid.uuid4())
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
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                f"Warning: Generate section report failed on attempt {attempt_num + 1}/{max_attempt_num}. retry ..."
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

    def _post_process_abstract(self, content: str) -> str:
        language = self.gen_report_context["language"]
        if content is None or content == "":
            return ArticlePart.get_not_found_prompt("abstract", language)

        header = ArticlePart.get_title("abstract", language)
        content = re.sub(r"\[?citation:\d+\]?", "", content)

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

    async def _generate_section_rationales(self, current_inputs: dict) -> list:
        """Generate section information dimensions (rationales).

        Inspired by METEORA: LLM generates rationales from section context +
        step_result + evaluation, grounded on actually collected information
        to ensure the coverage matrix evaluation is meaningful.

        Args:
            current_inputs: context containing section info and step_summaries.

        Returns:
            rationale list, each with id/description/type.
        """
        section_idx = current_inputs.get("section_idx", 1)
        section_task = self.strip_leading_number(current_inputs.get("section_task", ""))
        section_description = current_inputs.get("section_description", "")
        # Expand section_local_contract (nested dict) into top-level fields via the shared helper,
        # consistent with other prompt sites (report.py:2148, 3097).
        contract_ctx = build_section_local_contract_prompt_context(
            current_inputs.get("section_local_contract")
        )
        section_focus = contract_ctx.get("section_focus", "")
        focus_dimensions = contract_ctx.get("allowed_dimensions", [])
        report_task = current_inputs.get("report_task", "")
        overall_outline = current_inputs.get("current_outline", "")
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
            f"User query: {report_task}\n"
            f"Chapter title: {section_task}\n"
            f"Chapter description: {section_description}\n"
            f"Chapter focus: {section_focus}\n"
            f"Focus dimensions: {focus_dimensions_text}\n"
            f"Overall outline: {overall_outline}\n\n"
            f"Research step summaries:\n{step_summaries_text}\n\n"
            "Generate rationales for this chapter."
        )
        tmp_context = {
            "messages": [dict(role="user", content=user_content)],
        }

        llm_input = apply_system_prompt("rationale_generator", tmp_context)
        max_retries = current_inputs.get("max_generate_retry_num", 3)
        last_error = None
        for attempt_num in range(max_retries):
            try:
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_RATIONALE_GENERATOR.value,
                )
            except Exception as e:
                last_error = f"LLM call failed: {e}"
                logger.warning(
                    "%s [generate_rationales] section_idx: [%s] attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            if not llm_output or not llm_output.get("content"):
                last_error = "LLM returned empty content"
                logger.warning(
                    "%s [generate_rationales] section_idx: [%s] attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            try:
                data = json.loads(normalize_json_output(llm_output.get("content", "")))
                rationales = data.get("rationales", [])
                primary_count = sum(1 for r in rationales if r.get("priority") == "primary")
                supplementary_count = len(rationales) - primary_count
                logger.info(
                    "%s [generate_rationales] section_idx: [%s] generated %s rationales "
                    "(primary: %s, supplementary: %s) (attempt %s/%s)",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    len(rationales), primary_count, supplementary_count,
                    attempt_num + 1, max_retries,
                )
                return rationales
            except Exception as e:
                last_error = f"failed to parse LLM output: {e}"
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
        return []

    async def _evaluate_coverage_matrix(
        self, current_inputs: dict, doc_infos: list, rationales: list
    ) -> dict:
        """Evaluate coverage matrix: LLM evaluates each document's coverage of each rationale.

        Flow: n-gram coarse filter → max doc count cutoff → batched parallel LLM evaluation → merge results.

        Args:
            current_inputs: context.
            doc_infos: deduplicated document list.
            rationales: rationale list.

        Returns:
            Coverage matrix evaluation result dict, containing coverage_matrix/reliability_scores/noise_scores.
            Returns empty dict on failure.
        """
        section_idx = current_inputs.get("section_idx", 1)
        section_task = self.strip_leading_number(current_inputs.get("section_task", ""))
        section_description = current_inputs.get("section_description", "")

        if not doc_infos or not rationales:
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [coverage_matrix] section_idx: [{section_idx}] "
                f"empty doc_infos ({len(doc_infos)}) or rationales ({len(rationales)})"
            )
            return {}

        # n-gram coarse filter (0 LLM calls)
        filtered_docs = prefilter_by_ngram_coverage(doc_infos, rationales)
        if not filtered_docs:
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [coverage_matrix] section_idx: [{section_idx}] "
                f"n-gram coarse filter removed all docs, using original list"
            )
            filtered_docs = doc_infos

        logger.info(
            f"{EFFECT_SUB_REPORT_TAG} [coverage_matrix] section_idx: [{section_idx}] "
            f"n-gram filter: {len(doc_infos)} → {len(filtered_docs)} docs"
        )

        # Build rationale text
        rationales_text = "\n".join(
            f"  {r.get('id', '')}: {r.get('description', '')} (type: {r.get('type', 'unknown')})"
            for r in rationales
        )

        # Batched parallel LLM evaluation
        batches = [
            filtered_docs[i:i + BATCH_SIZE]
            for i in range(0, len(filtered_docs), BATCH_SIZE)
        ]

        logger.info(
            "%s [coverage_matrix] section_idx: [%s] split into %s batch(es), "
            "batch_size=%s, sending %s docs × %s rationales to LLM",
            EFFECT_SUB_REPORT_TAG, section_idx, len(batches),
            BATCH_SIZE, len(filtered_docs), len(rationales),
        )

        section_ctx = {
            "section_task": section_task,
            "section_description": section_description,
            "section_idx": section_idx,
            "max_retries": current_inputs.get("max_generate_retry_num", 3),
        }

        tasks = [
            self._eval_coverage_batch(
                batch, batch_idx, rationales_text, section_ctx,
            )
            for batch_idx, batch in enumerate(batches)
        ]
        # Limit concurrent LLM calls to avoid overwhelming the provider
        batch_results = await self._gather_with_limit(tasks, MAX_CONCURRENT_BATCHES)

        # Merge batch results, map in-batch doc_X to global doc_{offset + X}
        merged_coverage: dict = {}
        merged_reliability: dict = {}
        merged_noise: dict = {}

        for batch_idx, (batch_result, _batch_docs) in enumerate(batch_results):
            if not batch_result:
                continue
            offset = batch_idx * BATCH_SIZE
            for doc_key, scores in batch_result.get("coverage_matrix", {}).items():
                try:
                    local_idx = int(doc_key.split("_")[1])
                    merged_coverage[f"doc_{offset + local_idx}"] = scores
                except (ValueError, IndexError):
                    merged_coverage[doc_key] = scores
            for doc_key, score in batch_result.get("reliability_scores", {}).items():
                try:
                    local_idx = int(doc_key.split("_")[1])
                    merged_reliability[f"doc_{offset + local_idx}"] = score
                except (ValueError, IndexError):
                    merged_reliability[doc_key] = score
            for doc_key, score in batch_result.get("noise_scores", {}).items():
                try:
                    local_idx = int(doc_key.split("_")[1])
                    merged_noise[f"doc_{offset + local_idx}"] = score
                except (ValueError, IndexError):
                    merged_noise[doc_key] = score

        logger.info(
            f"{EFFECT_SUB_REPORT_TAG} [coverage_matrix] section_idx: [{section_idx}] "
            f"merged {len(merged_coverage)} docs × {len(rationales)} rationales "
            f"from {len(batches)} batch(es)"
        )

        return {
            "coverage_matrix": merged_coverage,
            "reliability_scores": merged_reliability,
            "noise_scores": merged_noise,
            "filtered_docs": filtered_docs,
        }

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

    async def _eval_coverage_batch(
        self, batch_docs: list, batch_idx: int,
        rationales_text: str, section_ctx: dict,
    ) -> tuple:
        """Evaluate coverage matrix for a single batch of documents (1 LLM call).

        Args:
            batch_docs: list of documents in this batch.
            batch_idx: batch index (for logging).
            rationales_text: rationale text.
            section_ctx: dict with section_task, section_description, section_idx.

        Returns:
            (parsed_result_dict, batch_docs) tuple. parsed_result is empty dict on failure.
        """
        section_task = section_ctx.get("section_task", "")
        section_description = section_ctx.get("section_description", "")
        section_idx = section_ctx.get("section_idx", -1)
        compact_text = build_compact_classify_doc_infos_text(batch_docs, start=0)

        # Build user message with untrusted data (doc content, rationales)
        # separated from system prompt to prevent prompt injection.
        user_content = (
            f"Chapter title: {section_task}\n"
            f"Chapter description: {section_description}\n\n"
            f"Information dimensions (rationales):\n{rationales_text}\n\n"
            f"Documents:\n{compact_text}\n\n"
            "Please evaluate the coverage matrix for the documents above."
        )
        tmp_context = {
            "messages": [dict(role="user", content=user_content)],
        }

        llm_input = apply_system_prompt("coverage_matrix_evaluator", tmp_context)
        max_retries = section_ctx.get("max_retries", 3)
        last_error = None
        for attempt_num in range(max_retries):
            try:
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_COVERAGE_MATRIX_EVALUATOR.value,
                )
            except Exception as e:
                last_error = f"LLM call failed: {e}"
                logger.warning(
                    "%s [coverage_matrix] section_idx: [%s] batch %s: attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            if not llm_output or not llm_output.get("content"):
                last_error = "LLM returned empty content"
                logger.warning(
                    "%s [coverage_matrix] section_idx: [%s] batch %s: attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            try:
                data = json.loads(normalize_json_output(llm_output.get("content", "")))
                logger.info(
                    "%s [coverage_matrix] section_idx: [%s] batch %s: parsed %s docs (attempt %s/%s)",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    len(data.get("coverage_matrix", {})),
                    attempt_num + 1, max_retries,
                )
                return data, batch_docs
            except Exception as e:
                last_error = f"failed to parse LLM output: {e}"
                logger.warning(
                    "%s [coverage_matrix] section_idx: [%s] batch %s: attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

        logger.error(
            "%s [coverage_matrix] section_idx: [%s] batch %s: failed after %s attempts: %s",
            EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
            max_retries, last_error,
        )
        return {}, batch_docs

    @staticmethod
    def _optimize_document_set(
        doc_infos: list, rationales: list, coverage_result: dict, top_k: int = 20
    ) -> tuple:
        """Greedy submodular document selection (0 LLM calls).

        Inspired by TREC submodular selection + PureCover noise penalty.
        Each round selects the document with the highest marginal value:
            marginal_value = coverage_gain - β×redundancy - γ×noise - δ×untrustworthy

        Args:
            doc_infos: candidate document list (already n-gram filtered).
            rationales: rationale list.
            coverage_result: coverage matrix evaluation result.
            top_k: maximum number of documents to select.

        Returns:
            (selected_docs, marginal_values) tuple.
        """
        filtered_docs = coverage_result.get("filtered_docs", doc_infos)
        coverage_matrix = coverage_result.get("coverage_matrix", {})
        reliability_scores = coverage_result.get("reliability_scores", {})
        noise_scores = coverage_result.get("noise_scores", {})

        beta = 0.3   # redundancy penalty weight
        gamma = 0.3   # noise penalty weight
        delta = 0.2   # untrustworthy penalty weight

        # Precompute n-grams for redundancy detection
        doc_ngrams = [extract_doc_ngrams(d) for d in filtered_docs]

        rationale_ids = list(dict.fromkeys(r.get("id", "") for r in rationales))

        covered = {rid: 0.0 for rid in rationale_ids}
        selected_indices: list = []
        selected_ngrams: list = []
        marginal_values: list = []

        for _ in range(min(top_k, len(filtered_docs))):
            best_idx = -1
            best_value = 0.0

            for idx in range(len(filtered_docs)):
                if idx in selected_indices:
                    continue

                doc_key = f"doc_{idx}"
                doc_cov = coverage_matrix.get(doc_key, {})

                # Coverage gain
                gain = sum(
                    max(0.0, doc_cov.get(rid, 0.0) - covered.get(rid, 0.0))
                    for rid in rationale_ids
                )

                # Redundancy penalty (weighted n-gram Jaccard)
                redundancy = 0.0
                if selected_ngrams:
                    redundancy = max(
                        ngram_jaccard_similarity(doc_ngrams[idx], sn)
                        for sn in selected_ngrams
                    )

                # Noise penalty
                noise = noise_scores.get(doc_key, 0.0)

                # Untrustworthy penalty
                reliability = reliability_scores.get(doc_key, 0.5)
                untrustworthy = 1.0 - reliability

                marginal_value = gain - beta * redundancy - gamma * noise - delta * untrustworthy

                if marginal_value > best_value:
                    best_value = marginal_value
                    best_idx = idx

            if best_idx < 0 or best_value <= 0:
                break

            selected_indices.append(best_idx)
            marginal_values.append(best_value)
            selected_ngrams.append(doc_ngrams[best_idx])

            doc_key = f"doc_{best_idx}"
            doc_cov = coverage_matrix.get(doc_key, {})
            for rid in rationale_ids:
                covered[rid] = max(covered.get(rid, 0.0), doc_cov.get(rid, 0.0))

        selected_docs = [filtered_docs[i] for i in selected_indices]
        logger.info(
            "%s [optimize_docs] selected %s docs from %s candidates, covered %s/%s rationales",
            EFFECT_SUB_REPORT_TAG, len(selected_docs), len(filtered_docs),
            sum(1 for v in covered.values() if v >= 0.3), len(rationale_ids),
        )

        return selected_docs, marginal_values

    @staticmethod
    def _elbow_cutoff(
        selected_docs: list,
        marginal_values: list,
        top_k: int = 20,
        coverage_ctx: dict | None = None,
        fallback_docs: list | None = None,
    ) -> list:
        """Elbow detection + rationale-coverage-aware adaptive cutoff (0 LLM calls).

        First detects the marginal value drop (elbow). After the elbow, instead
        of cutting immediately, checks each subsequent document: if it covers
        any rationale better than the current max coverage from kept docs,
        keep it and continue; otherwise stop.

        Args:
            selected_docs: greedily selected document list.
            marginal_values: marginal value of each document.
            top_k: maximum count upper limit.
            coverage_ctx: dict with 'coverage_result' and 'rationales' for rationale check.
            fallback_docs: fallback doc list when coverage_result lacks filtered_docs.

        Returns:
            Cutoff document list.
        """
        if len(selected_docs) <= 3:
            return selected_docs

        # Compute adjacent marginal value differences
        diffs = [
            marginal_values[i] - marginal_values[i + 1]
            for i in range(len(marginal_values) - 1)
        ]

        if not diffs:
            return selected_docs[:top_k]

        # Find max difference point (elbow)
        max_diff = max(diffs)
        mean_diff = sum(diffs) / len(diffs)

        # Only cut off when max diff is significantly larger than mean diff
        if not (max_diff > mean_diff * 2 and max_diff > 0.05):
            return selected_docs[:top_k]

        elbow_idx = diffs.index(max_diff)
        # Start from the first doc after elbow (the first dropped doc)
        cutoff = elbow_idx + 1

        # --- Rationale-coverage-aware extension ---
        # All pre-elbow docs are kept. Then iterate through ALL post-elbow docs:
        # keep any doc that is the best for at least one rationale (higher than
        # current max across all kept docs so far).
        if coverage_ctx is None:
            coverage_result = None
            rationales = None
        else:
            coverage_result = coverage_ctx.get("coverage_result")
            rationales = coverage_ctx.get("rationales")

        if coverage_result is None or rationales is None:
            if cutoff < len(selected_docs):
                logger.info(
                    "%s [elbow_cutoff] elbow at index %s, cutting from %s to %s docs (no coverage check)",
                    EFFECT_SUB_REPORT_TAG, elbow_idx, len(selected_docs), cutoff,
                )
                return selected_docs[:cutoff]
            return selected_docs[:top_k]

        coverage_matrix = coverage_result.get("coverage_matrix", {})
        filtered_docs = coverage_result.get("filtered_docs", fallback_docs or selected_docs)
        rationale_ids = list(dict.fromkeys(r.get("id", "") for r in rationales))

        # Build doc→index map using object identity (not URL) to correctly
        # handle same-URL different-content doc variants in filtered_docs.
        doc_to_idx = {id(doc): idx for idx, doc in enumerate(filtered_docs)}

        def _get_doc_cov(doc: dict) -> dict:
            """Get coverage scores for a doc from the coverage matrix."""
            idx = doc_to_idx.get(id(doc))
            if idx is None:
                return {}
            return coverage_matrix.get(f"doc_{idx}", {})

        # Start with all pre-elbow docs
        kept_docs = list(selected_docs[:cutoff])

        # Compute max coverage per rationale from pre-elbow docs
        max_covered = {rid: 0.0 for rid in rationale_ids}
        for doc in kept_docs:
            doc_cov = _get_doc_cov(doc)
            for rid in rationale_ids:
                cov = doc_cov.get(rid, 0.0)
                if cov > max_covered[rid]:
                    max_covered[rid] = cov

        # Iterate through ALL post-elbow docs, keep any that improves a rationale
        extra_kept = 0
        for i in range(cutoff, len(selected_docs)):
            doc_cov = _get_doc_cov(selected_docs[i])
            improves = False
            for rid in rationale_ids:
                cov = doc_cov.get(rid, 0.0)
                if cov > max_covered[rid]:
                    improves = True
                    break
            if improves:
                kept_docs.append(selected_docs[i])
                extra_kept += 1
                for rid in rationale_ids:
                    cov = doc_cov.get(rid, 0.0)
                    if cov > max_covered[rid]:
                        max_covered[rid] = cov
                logger.debug(
                    "%s [elbow_cutoff] keeping doc at index %s (improves rationale coverage)",
                    EFFECT_SUB_REPORT_TAG, i,
                )

        logger.info(
            "%s [elbow_cutoff] elbow at index %s, pre-elbow=%s docs, coverage-aware kept %s extra, total=%s docs",
            EFFECT_SUB_REPORT_TAG, elbow_idx, cutoff, extra_kept, len(kept_docs),
        )

        # Enforce top_k upper limit
        if len(kept_docs) > top_k:
            dropped = len(kept_docs) - top_k
            kept_docs = kept_docs[:top_k]
            logger.info(
                f"{EFFECT_SUB_REPORT_TAG} [elbow_cutoff] capped to top_k={top_k}, "
                f"dropped {dropped} docs"
            )

        return kept_docs

    @staticmethod
    def _verify_coverage(
        selected_docs: list, rationales: list, coverage_result: dict, section_idx,
        fallback_docs: list | None = None,
    ) -> dict:
        """Coverage verification + debug output (0 LLM calls).

        Check whether all rationales are covered, print dimension-document matching relationships.

        Args:
            selected_docs: selected document list.
            rationales: rationale list.
            coverage_result: coverage matrix evaluation result.
            section_idx: section index (for logging).

        Returns:
            Verification result dict, containing uncovered/weak/coverage_rate/limitations.
        """
        coverage_matrix = coverage_result.get("coverage_matrix", {})
        filtered_docs = coverage_result.get("filtered_docs", fallback_docs or selected_docs)
        reliability_scores = coverage_result.get("reliability_scores", {})

        # Build doc→index map using object identity (not URL) to correctly
        # handle same-URL different-content doc variants in filtered_docs.
        doc_to_idx = {id(doc): idx for idx, doc in enumerate(filtered_docs)}

        # Compute coverage for each rationale using ONLY selected docs
        covered = {}
        for r in rationales:
            rid = r.get("id", "")
            max_cov = 0.0
            for doc in selected_docs:
                idx = doc_to_idx.get(id(doc))
                if idx is None:
                    continue
                doc_key = f"doc_{idx}"
                doc_cov = coverage_matrix.get(doc_key, {})
                cov = doc_cov.get(rid, 0.0)
                if cov > max_cov:
                    max_cov = cov
            covered[rid] = max_cov

        uncovered = [r for r in rationales if covered.get(r.get("id", ""), 0.0) < 0.3]
        weak = [r for r in rationales if 0.3 <= covered.get(r.get("id", ""), 0.0) < 0.6]

        # ===== Debug output: dimension-document matching (DEBUG level) =====
        logger.debug(
            "%s ===== dimension-document coverage (section_idx=%s) =====",
            EFFECT_SUB_REPORT_TAG, section_idx,
        )
        for r in rationales:
            rid = r.get("id", "")
            cov_score = covered.get(rid, 0.0)
            status = "✓covered" if cov_score >= 0.6 else ("△weak" if cov_score >= 0.3 else "✗uncovered")

            # Find top-3 selected documents covering this dimension
            doc_scores = []
            for doc in selected_docs:
                idx = doc_to_idx.get(id(doc))
                if idx is None:
                    continue
                doc_key = f"doc_{idx}"
                doc_cov = coverage_matrix.get(doc_key, {})
                score = doc_cov.get(rid, 0.0)
                if score > 0:
                    rel = reliability_scores.get(doc_key, 0.0)
                    doc_scores.append((doc, score, rel))
            doc_scores.sort(key=lambda x: x[1], reverse=True)

            logger.debug(
                "%s   %s [%s] coverage=%.2f",
                EFFECT_SUB_REPORT_TAG, r.get("description", ""), status, cov_score,
            )
            for doc, score, rel in doc_scores[:3]:
                title = str(doc.get("title", ""))[:40]
                url = str(doc.get("url", ""))[:60]
                logger.debug(
                    "%s     ← %s (url=%s, coverage=%.2f, reliability=%.2f)",
                    EFFECT_SUB_REPORT_TAG, title, url, score, rel,
                )

        # Summary log (INFO level, single line)
        covered_count = sum(1 for v in covered.values() if v >= 0.6)
        weak_count = sum(1 for v in covered.values() if 0.3 <= v < 0.6)
        uncovered_count = len(uncovered)
        logger.info(
            "%s [verify_coverage] section_idx: [%s] candidates: %s → selected: %s | "
            "total_rationales: %s → covered: %s weak: %s uncovered: %s",
            EFFECT_SUB_REPORT_TAG, section_idx, len(filtered_docs),
            len(selected_docs), len(rationales),
            covered_count, weak_count, uncovered_count,
        )
        if uncovered:
            logger.warning(
                "%s [verify_coverage] section_idx: [%s] ⚠ uncovered dimensions: %s",
                EFFECT_SUB_REPORT_TAG, section_idx,
                [r.get("description", "") for r in uncovered],
            )

        limitations = [
            f"This section does not sufficiently cover the following key information: {r.get('description', '')}"
            for r in uncovered
        ]

        return {
            "uncovered_rationales": uncovered,
            "weak_rationales": weak,
            "coverage_rate": 1 - len(uncovered) / max(len(rationales), 1),
            "limitations": limitations,
        }

    @staticmethod
    def _write_doc_selection_debug(
        current_inputs: dict, ctx: DocSelectionContext,
    ) -> None:
        """Pack doc-selection intermediate results into current_inputs.

        Stores debug data in current_inputs["doc_selection_debug"] so the caller
        (SubReporterNode → editor_team_manager_node._update_state) can write it
        back to Section.doc_selection_debug for ResultExporter to dump to JSON/Excel.
        """
        rationales = ctx.rationales
        coverage_result = ctx.coverage_result
        doc_infos = ctx.doc_infos
        selected_docs = ctx.selected_docs
        selected_marginal_values = ctx.selected_marginal_values
        verify_result = ctx.verify_result

        filtered_docs = coverage_result.get("filtered_docs", doc_infos)
        doc_info_map = {
            f"doc_{i}": {"title": d.get("title", ""), "url": d.get("url", "")}
            for i, d in enumerate(filtered_docs)
        }
        id_to_key = {id(d): f"doc_{i}" for i, d in enumerate(filtered_docs)}
        selected_summary = [
            {
                "doc_key": id_to_key.get(id(doc), ""),
                "title": doc.get("title", ""),
                "url": doc.get("url", ""),
                "marginal_value": mv,
            }
            for doc, mv in zip(selected_docs, selected_marginal_values)
        ]

        current_inputs["doc_selection_debug"] = {
            "rationales": rationales,
            "ngram_filter": {
                "before": len(doc_infos),
                "after": len(filtered_docs),
            },
            "coverage_matrix": coverage_result.get("coverage_matrix", {}),
            "reliability_scores": coverage_result.get("reliability_scores", {}),
            "noise_scores": coverage_result.get("noise_scores", {}),
            "doc_info_map": doc_info_map,
            "selected_docs": selected_summary,
            "verify_result": verify_result or {},
        }

    async def _generate_sub_section_outline(self, current_inputs: dict) -> dict:
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
            tmp_context = {}
            tmp_context["messages"] = [dict(role="user", content=sub_content_message)]
            tmp_context["section_idx"] = section_idx
            tmp_context["language"] = current_inputs.get("language")
            tmp_context["has_template"] = current_inputs.get("has_template")
            tmp_context["section_title"] = section_task
            tmp_context["section_description"] = section_description
            tmp_context["section_format_requirements"] = section_format_requirements
            tmp_context["current_outline"] = current_inputs.get("current_outline", "")
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
            if LogManager.is_sensitive():
                error_msg = "Error generating sub section outline"
            else:
                error_msg = f"Error generating sub section outline: {str(e)}"
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_section_outline] section_idx: [{section_idx}] "
                f"{error_msg}",
                exc_info=True,
            )
            return dict(rs_success=False, sub_section_outline=error_msg)

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
                result = json.loads(raw)
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
                result = json.loads(raw)
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
            if raw_payload == "{}":
                visualization_content["rs_success"] = False
                visualization_content["error_msg"] = "no_chart_data"
                return False, visualization_content, None
            try:
                extracted_obj = json.loads(raw_payload)
            except Exception:
                extracted_obj = None
            extract_ok = isinstance(
                extracted_obj, dict
            ) and validate_visualization_extraction_schema(extracted_obj)
            if extract_ok:
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
            normalized_payload = (normalize_output.get("content") or "").strip()
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
            data_density_score = get_numeric_score(visualization_content[i], "data_density")
            visualization_dict = {
                "section_idx": section_idx,
                "title": visualization_content[i].get("title", ""),
                "origin_content": visualization_content[i].get("original_content", ""),
                "data_density": data_density_score if data_density_score is not None else -1.0,
                "language": current_inputs.get("language", "zh-CN"),
                "section_title": section_task,
                "section_outline": section_outline,
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
            error_msg = (
                "Missing 'section_task' or sub section outline or collected infos/background knowledge in context."
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

        infos = ""
        for item in current_inputs.get("classified_content", []):
            infos += (
                f"\n[citation:{item.get('index', 1)} begin]time: {item.get('doc_time', '')}|||"
                f"scores: {format_scores_inline(item)}|||"
                f"content: {item.get('original_content', '')}[citation:{item.get('index', 1)} end]"
            )
        current_outline = current_inputs.get("current_outline", {})
        current_outline_without_plans = Reporter.export_outline_without_plans(
            current_outline
        )
        background_knowledge_prompt = self._format_background_knowledge_for_prompt(
            background_knowledge_contents
        )
        current_section_description = current_inputs.get("section_description", "")
        current_section_format_requirements = current_inputs.get("section_format_requirements", [])
        current_chapter_outline = current_inputs.get("sub_section_outline", "")
        current_subsection = current_inputs.get(
            "current_subsection",
            "Full current chapter; follow each Level 2 heading in the current chapter outline.",
        )
        sub_content_message = (
            "# Current Top-Level Section\n"
            f"section_id: {current_inputs.get('section_idx', 1)}\n"
            f"title: {section_task}\n"
            f"description: {current_section_description}\n\n"
            f"format_requirements: {current_section_format_requirements}\n\n"
            "# Overall Outline\n"
            f"{current_outline_without_plans}\n\n"
            "# Current Chapter Outline\n"
            f"{current_chapter_outline}\n\n"
            "# Current Subsection\n"
            f"{current_subsection}\n\n"
            "# Collected Evidence\n"
            f"{infos}\n\n"
            "# References\n"
            f"{current_inputs.get('sub_section_references', '')}\n\n"
            f"{background_knowledge_prompt}"
        )
        try:
            report_type = current_inputs.get("report_type", "professional")
            sub_report_prompt = (
                "sub_report_brief_markdown"
                if report_type == "brief"
                else "sub_report_markdown"
            )
            llm_input = apply_system_prompt(
                sub_report_prompt,
                dict(
                    messages=[dict(role="user", content=sub_content_message)],
                    language=current_inputs.get("language"),
                    section_iscore=current_inputs.get("section_iscore", False),
                    report_type=report_type,
                    paragraph_style=current_inputs.get("paragraph_style", "detailed"),
                    require_summary_first=current_inputs.get("require_summary_first", False),
                    require_methodology_and_risk=current_inputs.get("require_methodology_and_risk", False),
                    audience_role=current_inputs.get("audience_role", ""),
                    tone=current_inputs.get("tone", ""),
                    outline=current_outline_without_plans,
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

            current_inputs["sub_report_content"] = self._clean_internal_callback_labels(
                llm_output.get("content", "")
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
                return dict(
                    success=False,
                    result=f"generated report headings do not match outline: {reason}",
                )
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
                    f"{EFFECT_SUB_REPORT_TAG} sub report content is blank， section_id: "
                    f"{current_inputs.get('section_idx', 1)}"
                )
                return dict(success=False, result="no sub report content found")

            if not LogManager.is_sensitive():
                logger.debug(
                    "%s[write_subsection_reports] success generate section [%s] sub_report, sub report content:\n[%s]",
                    EFFECT_SUB_REPORT_TAG,
                    current_inputs.get("section_idx", 1),
                    current_inputs["sub_report_content"],
                    extra={"skip_truncation": True},
                )
            return dict(success=True, result="success")
        except Exception as e:
            current_inputs["sub_report_content"] = ""
            if LogManager.is_sensitive():
                error_msg = f"Error generating section {current_inputs.get('section_idx', 1)} report"
            else:
                error_msg = f"Error generating section {current_inputs.get('section_idx', 1)} report: {str(e)}"
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [write_subsection_reports] {error_msg}",
                exc_info=True,
            )
            return dict(success=False, result=error_msg)

    @staticmethod
    def _select_visualization_from_classified_content(
        classified_content_for_visualization,
    ):
        selected_visualizations = []
        for item in classified_content_for_visualization:
            if not isinstance(item, dict):
                continue
            point = get_numeric_score(item, "data_density")
            if point is not None and point >= 9.0:
                selected_visualizations.append(item)
        return selected_visualizations

    async def _request_visualization_insert_plan(
        self, context: VisualizationInsertPlanContext
    ) -> dict:
        base_messages = list(context.messages)
        active_messages = base_messages
        max_attempt_num = context.current_inputs.get("max_generate_retry_num", 3)
        for attempt in range(max_attempt_num):
            llm_input = apply_system_prompt(
                "insert_visualization",
                dict(
                    messages=active_messages,
                    language=context.current_inputs.get("language"),
                ),
            )

            try:
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER.value,
                    need_stream_out=False,
                )
            except Exception as e:
                logger.error(
                    "%s LLM error when inserting visualization for section [%s]: %s",
                    EFFECT_SUB_REPORT_TAG,
                    context.current_inputs.get("section_idx", 1),
                    str(e),
                )
                return dict(rs_success=False, plan=None, result=context.original_report)

            if not llm_output or not llm_output.get("content"):
                logger.warning(
                    "%s [insert_visualization] section_idx: [%s] empty output, retrying (%s/%s).",
                    EFFECT_SUB_REPORT_TAG,
                    context.current_inputs.get("section_idx", 1),
                    attempt + 1,
                    max_attempt_num,
                )
                active_messages = base_messages[:1] + [
                    dict(
                        role="user",
                        content=(
                            "Your output is empty or invalid. Return JSON only with schema: "
                            '{"insertions":[{"after_row":int,"index":int},...]}'
                        ),
                    )
                ]
                continue

            raw = (llm_output.get("content") or "").strip()
            try:
                plan = json.loads(raw)
            except Exception:
                plan = None

            is_valid, error_msg = self._is_valid_insert_plan(
                plan, context.report_lines, context.invalid_rows, context.mermaid_map
            )
            if not is_valid:
                logger.warning(
                    "%s [insert_visualization] section_idx: [%s] "
                    "invalid insertion plan, retrying (%s/%s).",
                    EFFECT_SUB_REPORT_TAG,
                    context.current_inputs.get("section_idx", 1),
                    attempt + 1,
                    max_attempt_num,
                )
                active_messages = base_messages[:1] + [
                    dict(
                        role="user",
                        content=(
                            "Your previous output is invalid. Return JSON only with schema: "
                            '{"insertions":[{"after_row":int,"index":int},...]} '
                            "Issue: "
                            f"{error_msg}. "
                            "Ensure after_row is valid and index exists in visualization data."
                        ),
                    )
                ]
                continue

            return dict(rs_success=True, plan=plan, result="")

        return dict(rs_success=False, plan=None, result=context.original_report)

    @staticmethod
    def _apply_visualization_insertions(
        context: VisualizationInsertRenderContext,
    ) -> str:
        out_lines = list(context.report_lines)
        offset = 0
        for ins in context.insertions:
            after_row = ins["after_row"]
            index = ins["index"]
            mermaid_code = context.mermaid_map.get(index, "")
            if not mermaid_code:
                continue
            block = [
                context.newline,
                f"```mermaid{context.newline}",
                *[f"{line}{context.newline}" for line in mermaid_code.splitlines()],
                f"```{context.newline}",
            ]
            title_meta = context.title_meta_map.get(index, {})
            image_title = (title_meta.get("image_title") or "").strip()
            citation_index = int(title_meta.get("citation_index", 0) or 0)
            if not image_title:
                image_title = (
                    "图表标题" if context.language == CHINESE else "Image Title"
                )
            citation_text = f"[citation:{citation_index}]" if citation_index > 0 else ""
            safe_image_title = html.escape(image_title, quote=True)
            title_with_citation = f"{safe_image_title}{citation_text}".strip()
            if title_with_citation:
                block.append(
                    f'<div style="text-align: center;">{context.newline}{context.newline}'
                    f"**{title_with_citation}**{context.newline}{context.newline}</div>"
                    f"{context.newline}{context.newline}"
                )
            insert_at = after_row + offset
            prev_index = insert_at - 1
            if 0 <= prev_index < len(out_lines):
                if not out_lines[prev_index].endswith(("\n", "\r\n")):
                    out_lines[prev_index] += context.newline
            out_lines[insert_at:insert_at] = block
            offset += len(block)

        return "".join(out_lines)

    async def _insert_visualization(self, current_inputs: Dict) -> dict:
        """
        Insert placeholders for visualization content in the markdown report.
        """
        try:
            report_markdown = current_inputs.get("sub_report_content", "")
            if not isinstance(report_markdown, str):
                report_markdown = str(report_markdown or "")

            original_report = report_markdown
            visualization_list = current_inputs.get("visualization_result", [])
            if not isinstance(visualization_list, list) or not visualization_list:
                return dict(rs_success=False, result=original_report)

            report_lines = report_markdown.splitlines(keepends=True)
            newline = "\r\n" if "\r\n" in report_markdown else "\n"
            invalid_rows = Reporter._get_invalid_rows_for_insertion(report_lines)
            numbered_lines = []
            for i, line in enumerate(report_lines, 1):
                line_clean = line.rstrip("\r\n")
                numbered_lines.append(f"[ROW:{i}] {line_clean}{newline}")
            numbered_report = "".join(numbered_lines)

            visualization_dict = {}
            mermaid_map: dict[int, str] = {}
            title_meta_map: dict[int, dict] = {}
            url_to_citation_index = {}
            for classified_item in current_inputs.get("classified_content", []):
                if isinstance(classified_item, dict) and "url" in classified_item:
                    url_to_citation_index[classified_item["url"]] = classified_item.get(
                        "index", 0
                    )
            # Prompt contract in `insert_visualization.md` uses 1-based indices.
            placeholder_index = 1
            for item in visualization_list:
                if (
                    isinstance(item, dict)
                    and "url" in item
                    and item.get("mermaid_content")
                ):
                    viz_payload = (
                        item.get("sub_section_visualization_content") or ""
                    ).strip()
                    try:
                        viz_obj = json.loads(viz_payload) if viz_payload else None
                    except Exception:
                        viz_obj = None
                    if not isinstance(viz_obj, dict):
                        continue

                    mermaid_map[placeholder_index] = item.get("mermaid_content", "")
                    title_meta_map[placeholder_index] = {
                        "image_title": viz_obj.get("image_title", ""),
                        "citation_index": url_to_citation_index.get(
                            item.get("url", ""), 0
                        ),
                    }
                    placement_item = {
                        "index": placeholder_index,
                        "image_title": viz_obj.get("image_title", ""),
                        "image_type": viz_obj.get("image_type", ""),
                        "unit": viz_obj.get("unit", ""),
                        "records": viz_obj.get("records", []),
                    }
                    visualization_dict[item["url"]] = placement_item
                    placeholder_index += 1

            if not mermaid_map:
                # No valid visualization blocks, return original content.
                return dict(rs_success=False, result=original_report)

            llm_input_message = numbered_report.rstrip("\r\n") + "\n\n"
            llm_input_message += "=== VISUALIZATION DATA ===\n"
            for item in current_inputs.get("classified_content", []):
                if (
                    isinstance(item, dict)
                    and "url" in item
                    and item["url"] in visualization_dict
                ):
                    llm_input_message += (
                        json.dumps(visualization_dict[item["url"]], ensure_ascii=False)
                        + "\n"
                    )
            llm_input_message += "=== END VISUALIZATION DATA ===\n"
            messages = [dict(role="user", content=llm_input_message)]
            plan_result = await self._request_visualization_insert_plan(
                VisualizationInsertPlanContext(
                    messages=messages,
                    current_inputs=current_inputs,
                    report_lines=report_lines,
                    invalid_rows=invalid_rows,
                    mermaid_map=mermaid_map,
                    original_report=original_report,
                )
            )
            if not plan_result.get("rs_success") or not plan_result.get("plan"):
                return dict(rs_success=False, result=original_report)
            plan = plan_result["plan"]

            insertions = sorted(
                plan.get("insertions", []), key=lambda x: x["after_row"]
            )
            rendered = self._apply_visualization_insertions(
                VisualizationInsertRenderContext(
                    report_lines=report_lines,
                    insertions=insertions,
                    mermaid_map=mermaid_map,
                    title_meta_map=title_meta_map,
                    newline=newline,
                    language=current_inputs.get("language"),
                )
            )
            return dict(rs_success=True, result=rendered)
        except Exception as e:
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} Unexpected error when inserting visualization for the section "
                f"{current_inputs.get('section_idx', 1)}: {str(e)}",
                exc_info=True,
            )
            return dict(rs_success=False, result=original_report)


def _deduplicate_and_renumber_ref(raw_text: str) -> Tuple[str, Dict[str, int]]:
    lines = raw_text.splitlines()
    seen = {}
    result = []
    mapping = {}
    index = 1
    paragraph_id = 0

    for line in lines:
        line = line.strip()
        if not line:
            paragraph_id += 1  # empty line is one section too
            continue

        # test if new section（start with [1]）
        if re.match(r"^\[1\]", line):
            ref_index = 1
            paragraph_id += 1
        else:
            # get original ref no
            match = re.match(r"^\[(\d+)\]", line)
            if match:
                ref_index = int(match.group(1))
            else:
                continue

        # remove original no
        content = re.sub(r"^\[\d+\]\s*", "", line).strip()

        key = f"{paragraph_id}-{ref_index}"
        # add ref content to non-duplicate array
        if content not in seen:
            seen[content] = index
            result.append(f"[{index}] {content}")
            index += 1

        mapping[key] = seen[content]

    return "\n\n".join(result), mapping


def _replace_citations_and_classified_index(
    paragraphs: List[str],
    classified_contents: List[List[Dict]],
    ref_map: Dict[str, int],
) -> Tuple[List[str], List[List[Dict]]]:
    if not ref_map or not classified_contents:
        return paragraphs, classified_contents

    updated_paragraphs: List[str] = []
    updated_classified_contents: List[List[Dict]] = []

    for i, para in enumerate(paragraphs):
        sub_classified_contents = classified_contents[i]
        if not sub_classified_contents:
            updated_paragraphs.append(para)
            updated_classified_contents.append([])
            continue

        # Build index mapping: original index -> new number
        index_map = {
            str(item["index"]): ref_map.get(f"{i + 1}-{item['index']}")
            for item in sub_classified_contents
        }

        # Replace citations in the loop without a closure
        updated_para = para
        for original_index, final_index in index_map.items():
            if final_index is not None:
                updated_para = re.sub(
                    rf"\[citation:{original_index}\]",
                    f"[citation:{final_index}]",
                    updated_para,
                )
        updated_paragraphs.append(updated_para)

        # Update index field in reference entries
        updated_sub_classified_content: List[Dict] = []
        for item in sub_classified_contents:
            updated_item = item.copy()
            final_index = index_map.get(str(item["index"]))
            if final_index is not None:
                updated_item["index"] = final_index
            updated_sub_classified_content.append(updated_item)

        updated_classified_contents.append(updated_sub_classified_content)

    return updated_paragraphs, updated_classified_contents


def _get_classified_infos(
    selected_docs: list[dict],
    marginal_values: list[float],
    max_source_id_count: int | None = 10,
):
    """Extract downstream writing inputs from matrix-selected doc variants.

    Args:
        selected_docs: concrete doc variants selected by the matrix pipeline.
            Reverse-looked-up by object identity without expanding to other
            variants under the same URL, so matrix-rejected variants cannot
            re-enter writing and citation.
        marginal_values: marginal value list from greedy matrix selection,
            index-aligned with selected_docs. Replaces the original doc composite
            score when picking representatives within the same source_key group,
            better matching the coverage semantics of the matrix.
        max_source_id_count: max number of content variants to keep.

    Returns:
        Tuple of (classified_infos with references and core_content_list,
        classified_doc_infos list).
    """
    def escape_markdown_text(value: object) -> str:
        text = str(value or "")
        text = re.sub(r"[\r\n\t]+", " ", text)
        return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>])", r"\\\1", text)

    def format_reference_link(title_value: object, url_value: object) -> str:
        title = escape_markdown_text(title_value)
        url = str(url_value or "").strip()
        if not url or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
            escaped_url = escape_markdown_text(url)
            return f"{title} ({escaped_url})" if title and escaped_url else title or escaped_url

        parsed_url = urlparse(url)
        scheme = parsed_url.scheme.lower()
        is_allowed_url = scheme in {"http", "https", "localdataset"} and (
            bool(parsed_url.netloc) if scheme in {"http", "https"} else bool(parsed_url.netloc or parsed_url.path)
        )
        if not is_allowed_url:
            escaped_url = escape_markdown_text(url)
            return f"{title} ({escaped_url})" if title and escaped_url else title or escaped_url

        escaped_url = url.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return f"[{title}]({escaped_url})"

    if not selected_docs:
        logger.error(
            f"{EFFECT_SUB_REPORT_TAG} No selected docs found. can not get classified infos."
        )
        return {}, []

    # Use only matrix-selected concrete variants; do not expand to other
    # variants under the same URL, otherwise matrix-rejected low-coverage /
    # high-noise variants may re-enter writing and citation.
    effective_urls = [str(d.get("url") or "") for d in selected_docs if d.get("url")]
    if not effective_urls:
        logger.error(
            f"{EFFECT_SUB_REPORT_TAG} No urls found. can not get classified infos."
        )
        return {}, []
    classified_infos = {"references": [], "core_content_list": []}
    classified_doc_infos = []

    matched_items: list[dict] = []
    matched_order: dict[int, int] = {}
    matched_by_url: dict[str, list[dict]] = {}
    for item in selected_docs:
        url = str(item.get("url") or "")
        if not url:
            continue
        matched_order[id(item)] = len(matched_items)
        matched_items.append(item)
        matched_by_url.setdefault(url, []).append(item)

    # marginal_value map: id(doc) -> greedy selection marginal value, index-aligned with selected_docs
    mv_map = {id(doc): mv for doc, mv in zip(selected_docs, marginal_values)}

    def source_key_for(item: dict) -> str:
        # Writing stage looks up original doc_infos, so reuse the pre-filter
        # content variant key here; otherwise same-content duplicates without
        # source_id may bypass pre-filter dedup and re-enter writing inputs.
        return build_doc_variant_key(item)

    def item_rank_key(item: dict) -> tuple[float, int, int]:
        return (
            mv_map.get(id(item), 0.0),
            len(str(item.get("original_content") or "")),
            -matched_order.get(id(item), 0),
        )

    def best_representatives(items: list[dict]) -> list[dict]:
        source_representatives: dict[str, dict] = {}
        for item in items:
            source_key = source_key_for(item)
            current = source_representatives.get(source_key)
            if current is None or item_rank_key(item) > item_rank_key(current):
                source_representatives[source_key] = item
        return sorted(source_representatives.values(), key=item_rank_key, reverse=True)

    selected_items: list[dict] = []
    selected_source_keys: set[str] = set()
    max_count = None if max_source_id_count is None else max(0, int(max_source_id_count))

    if max_count is not None:
        for url in effective_urls:
            if len(selected_items) >= max_count:
                break
            representatives = best_representatives(matched_by_url.get(url, []))
            if not representatives:
                continue
            top_item = representatives[0]
            source_key = source_key_for(top_item)
            if source_key in selected_source_keys:
                continue
            selected_items.append(top_item)
            selected_source_keys.add(source_key)

    remaining_representatives = best_representatives(matched_items)
    if max_count is None:
        selected_items = remaining_representatives
    else:
        for item in remaining_representatives:
            if len(selected_items) >= max_count:
                break
            source_key = source_key_for(item)
            if source_key in selected_source_keys:
                continue
            selected_items.append(item)
            selected_source_keys.add(source_key)

    if max_count is not None:
        selected_items = selected_items[:max_count]

    seen_reference_urls: set[str] = set()
    for item in selected_items:
        item_url = str(item.get("url") or "")
        if item_url not in seen_reference_urls:
            classified_infos["references"].append(
                format_reference_link(item.get("title", ""), item_url)
            )
            seen_reference_urls.add(item_url)
        classified_infos["core_content_list"].append(
            format_key_passage_block(item, len(classified_doc_infos) + 1)
        )
        classified_doc_infos.append(item)
    return classified_infos, classified_doc_infos
