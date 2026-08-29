# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Dict

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.report.report_common import EFFECT_SUB_REPORT_TAG
from openjiuwen_deepsearch.common.common_constants import CHINESE
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)


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


class VisualizationInsertionMixin:
    """Mixin for inserting visualization (Mermaid charts) into report body.

    Dependencies (provided by Reporter or other mixins):
        - self._llm: LLM instance (from Reporter.__init__)
        - self.gen_report_context: report context dict (from Reporter.__init__)
    """

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
                active_messages = base_messages + [
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
                plan = json.loads(normalize_json_output(raw))
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
                active_messages = base_messages + [
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

    @classmethod
    def _apply_visualization_insertions(
        cls,
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
            citation_indices = cls._normalize_citation_indices(
                title_meta.get("citation_indices")
            )

            if not citation_indices:
                citation_indices = cls._normalize_citation_indices(
                    [title_meta.get("citation_index")]
                )

            if not image_title:
                image_title = (
                    "图表标题" if context.language == CHINESE else "Image Title"
                )

            citation_text = "".join(
                f"[citation:{citation_index}]" for citation_index in citation_indices
            )
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

    @staticmethod
    def _complete_visualization_insertions(
        insertions: list[dict],
        mermaid_map: dict[int, str],
        report_lines: list[str],
        invalid_rows: set[int],
    ) -> list[dict]:
        """Ensure every generated visualization has an insertion anchor."""
        if not mermaid_map:
            return insertions

        valid_insertions = []
        for item in insertions:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get("after_row"), int):
                continue
            if not isinstance(item.get("index"), int):
                continue
            if item.get("index") not in mermaid_map:
                continue
            valid_insertions.append(item)
        used_indices = {item["index"] for item in valid_insertions}
        missing_indices = [
            index for index in sorted(mermaid_map) if index not in used_indices
        ]
        if not missing_indices:
            return valid_insertions

        if valid_insertions:
            fallback_row = valid_insertions[-1]["after_row"]
        else:
            fallback_row = next(
                (
                    row_idx
                    for row_idx in range(len(report_lines), 0, -1)
                    if row_idx not in invalid_rows and report_lines[row_idx - 1].strip()
                ),
                None,
            )
            if fallback_row is None:
                fallback_row = next(
                    (
                        row_idx
                        for row_idx in range(len(report_lines), 0, -1)
                        if row_idx not in invalid_rows
                    ),
                    None,
                )

        if fallback_row is None:
            return valid_insertions

        completed = list(valid_insertions)
        completed.extend(
            {"after_row": fallback_row, "index": index}
            for index in missing_indices
        )
        return completed

    async def insert_visualization(self, current_inputs: Dict) -> dict:
        """公开的可视化内容插入接口。"""
        return await self._insert_visualization(current_inputs)

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
            invalid_rows = self._get_invalid_rows_for_insertion(report_lines)
            numbered_lines = []
            for i, line in enumerate(report_lines, 1):
                line_clean = line.rstrip("\r\n")
                numbered_lines.append(f"[ROW:{i}] {line_clean}{newline}")
            numbered_report = "".join(numbered_lines)

            visualization_items = []
            mermaid_map: dict[int, str] = {}
            title_meta_map: dict[int, dict] = {}
            url_to_citation_index = {}
            for classified_item in current_inputs.get("classified_content", []):
                if isinstance(classified_item, dict) and "url" in classified_item:
                    item_url = classified_item.get("url", "")
                    url_to_citation_index[item_url] = classified_item.get(
                        "index", 0
                    )
            # Prompt contract in `insert_visualization.md` uses 1-based indices.
            placeholder_index = 1
            for item in visualization_list:
                has_url = "url" in item
                if isinstance(item, dict) and has_url and item.get("mermaid_content"):
                    viz_payload = (
                        item.get("sub_section_visualization_content") or ""
                    ).strip()
                    try:
                        viz_obj = json.loads(viz_payload) if viz_payload else None
                    except Exception:
                        viz_obj = None
                    if not isinstance(viz_obj, dict):
                        continue

                    citation_indices = self._normalize_citation_indices(
                        item.get("citation_indices")
                    )
                    if not citation_indices:
                        citation_index = url_to_citation_index.get(
                            item.get("url", ""),
                            item.get("index", 0),
                        )
                        citation_indices = self._normalize_citation_indices(
                            [citation_index]
                        )
                    mermaid_map[placeholder_index] = item.get("mermaid_content", "")
                    title_meta_map[placeholder_index] = {
                        "image_title": viz_obj.get("image_title", ""),
                        "citation_index": citation_indices[0] if citation_indices else 0,
                        "citation_indices": citation_indices,
                    }
                    placement_item = {
                        "index": placeholder_index,
                        "image_title": viz_obj.get("image_title", ""),
                        "image_type": viz_obj.get("image_type", ""),
                        "unit": viz_obj.get("unit", ""),
                        "records": viz_obj.get("records", []),
                    }
                    visualization_items.append(placement_item)
                    placeholder_index += 1

            if not mermaid_map:
                # No valid visualization blocks, return original content.
                return dict(rs_success=False, result=original_report)

            llm_input_message = numbered_report.rstrip("\r\n") + "\n\n"
            llm_input_message += "=== VISUALIZATION DATA ===\n"
            for visualization_item in visualization_items:
                llm_input_message += (
                    json.dumps(visualization_item, ensure_ascii=False)
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
            insertions = self._complete_visualization_insertions(
                insertions,
                mermaid_map,
                report_lines,
                invalid_rows,
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
