# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from datetime import datetime, timezone
from copy import deepcopy
import json
import logging
import re
from dataclasses import dataclass
from typing import Tuple, List, Dict

from tenacity import (
    RetryError,
    after_log,
    retry,
    stop_after_attempt,
    retry_if_exception_type,
)

from jiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from jiuwen_deepsearch.algorithm.report.config import ReportFormat
from jiuwen_deepsearch.algorithm.report.report_utils import (
    ArticlePart,
    MarkdownOutlineRenumber,
    XYChartMermaidGenerator,
    PieChartMermaidGenerator,
    TimelineChartMermaidGenerator,
    validate_visualization_extraction_schema,
    validate_visualization_normalization_schema,
)
from jiuwen_deepsearch.common.exception import CustomException, CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.config.config import Config
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import Outline
from jiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH
from jiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from jiuwen_deepsearch.utils.constants_utils.runtime_contextvars import llm_context

logger = logging.getLogger(__name__)

EFFECT_SUB_REPORT_TAG = "### sub_report_tag ###"
MAX_LOOP_ROUND = 99


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


class Reporter:
    def __init__(self, llm_model_name):
        # Keep consistent with other modules: workflow/template_generator registers
        # into llm_context at runtime; fetch by model name here.
        self._llm = llm_context.get().get(llm_model_name)
        self.gen_report_context = None

    @staticmethod
    def strip_leading_number(s: str) -> str:
        return re.sub(
            r"^(?:\d+(?:[.\-\s]\d+)*|第?[一二三四五六七八九十\d]+[、章])\s*", "", s
        )

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
            pattern = rf'^\s*{"#" * level}\s*[\(\（]?[一二三四五六七八九十0-9]+[\.、\)\）]?\s*'
            return re.sub(pattern, f'{"#" * level} ', line)

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
                # Remove numbering (same rule as H1-H3)
                content = re.sub(
                    r"^[\(\（]?[一二三四五六七八九十0-9]+[\.、\)\）]?\s*", "", content
                )
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
    def is_valid_chapter_format(text, section_idx) -> bool:
        """Check chapter format"""
        try:
            n = section_idx
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not lines:
                return False

            main_pat = re.compile(rf"^\s*(?:{n}\.(?!\d)\s*|{n}\s+)(?!\d).*")
            sub_pat = re.compile(rf"^\s*{n}\.(\d+)\s*")
            third_pat = re.compile(r"\d+\.\d+\.\d+")

            has_main = False
            sub_numbers = []

            for ln in lines:
                if third_pat.search(ln):
                    return False
                if main_pat.match(ln):
                    if has_main:
                        return False
                    has_main = True
                elif sub_pat.match(ln):
                    num = int(sub_pat.match(ln).group(1))
                    sub_numbers.append(num)
                elif re.match(r"\d+", ln):
                    return False

            sorted_subs = sorted(set(sub_numbers))
            if not sorted_subs or sorted_subs[0] != 1:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} No valid sub-sections found or first sub-section is not 1."
                )
                return False

            if not has_main:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} sub-section {section_idx} outline has no main chapter title."
                )
            return has_main
        except Exception as e:
            if LogManager.is_sensitive():
                error_msg = f"Error check section outliner format, section_idx: {str(section_idx)}"
            else:
                error_msg = f"Error check section outliner format, section_idx: {str(section_idx)}: {str(e)}"
            logger.warning(f"[subsection] {error_msg}")
            return False

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
        if not current_outline or not isinstance(current_outline, Outline):
            logger.warning("can not get section title for current outline is invalid.")
            return ""
        if index < 0 or index >= len(current_outline.sections):
            logger.warning("can not get section title for index is out of range.")
            return ""
        return current_outline.sections[index].title

    @staticmethod
    def export_outline_without_plans(outline: Outline | dict):
        if not outline or not isinstance(outline, (Outline, dict)):
            logger.warning(
                "export_outline_without_plans: unsupported outline type or empty outline."
            )
            return outline

        is_dict = isinstance(outline, dict)
        obj = Outline.model_validate(outline) if is_dict else outline

        data = obj.model_dump(exclude={"sections": {"__all__": {"plans"}}})

        return data if is_dict else Outline.model_validate(data)

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
            return False, "Error: Set context variables failed"

        self.gen_report_context["current_outline"] = self.export_outline_without_plans(
            self.gen_report_context.get("current_outline", {})
        )
        sub_report_res = await self._process_sub_report()
        if not sub_report_res.get("sub_reports_content"):
            logger.error(f"[generate_report] Error: No sub-reports content found")
            return False, "Error: No sub-reports content found"
        gen_report_context["all_classified_contents"] = sub_report_res.get(
            "refreshed_all_classified_contents"
        )

        abstract_task = asyncio.create_task(
            self.generate_abstract(sub_report_res.get("sub_reports_content"))
        )
        conclusion_task = asyncio.create_task(
            self.generate_conclusion(sub_report_res.get("sub_reports_content"))
        )

        try:
            abstract = await abstract_task
            conclusion = await conclusion_task
        except RetryError as retry_err:
            logger.error(
                f"[generate_report] Report generation failed after retries: {retry_err}"
            )
            return False, f"Report generation failed after retries: {retry_err}"
        except Exception as e:
            if LogManager.is_sensitive():
                logger.error(
                    f"[generate_report] Unexpected error during report generation"
                )
                return False, f"Unexpected error during report generation"
            logger.error(
                f"[generate_report] Unexpected error during report generation: {e}"
            )
            return False, f"Unexpected error during report generation: {e}"

        current_outline = self.gen_report_context.get("current_outline", "")
        if not current_outline:
            error_message = "has no current outline"
            logger.error(f"[generate_report] Generate report error: {error_message}")
            return False, error_message

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
            return False, "md report content empty."

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
        """General subsection report"""
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
        if not current_inputs.get("doc_infos", []):
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] fail to generate subsection report, "
                f"section_idx: [{section_idx}], not found doc infos"
            )
            return False, "Not found doc infos", "", []

        classify_success, classified_content = await self._classify_doc_infos(
            current_inputs
        )
        if LogManager.is_sensitive():
            logger.info(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                f"classified_content len: {len(classified_content)}"
            )
        else:
            logger.debug(
                "%s [generate_sub_report] section_idx: [%s], classified_content is %s",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                classified_content,
            )

        if classify_success:
            core_content_urls = classified_content.get("core_content_url_list", [])
            core_content_urls = list(dict.fromkeys(core_content_urls))
            if not core_content_urls:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    "no core content urls returned from classification"
                )
                return False, "no core content urls from classification", "", []
            classified_infos, classified_doc_infos = _get_classified_infos(
                current_inputs.get("doc_infos", []), core_content_urls
            )
            current_inputs["sub_section_core_content"] = classified_infos.get(
                "core_content_list", []
            )
            current_inputs["sub_section_references"] = classified_infos.get(
                "references", []
            )
            for idx, doc_info in enumerate(classified_doc_infos):
                doc_info.pop("query", None)
                doc_info["index"] = idx + 1
            current_inputs["classified_content"] = classified_doc_infos
        else:
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] Error: Classify doc information failed for "
                f"[{classified_content}], section_idx: [{section_idx}]"
            )
            return False, "classify_doc_infos fail", "", []
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
            if gen_sub_res["rs_success"] and self.is_valid_chapter_format(
                gen_sub_res["sub_section_outline"], section_idx
            ):
                current_inputs["sub_section_outline"] = gen_sub_res[
                    "sub_section_outline"
                ]
                break
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                f"Warning: Generate section outline failed on attempt {attempt_num + 1}/{max_attempt_num}. retry ..."
            )
            if attempt_num == max_attempt_num - 1:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"Error: Generate section outline failed, reach the max_attempt_num: {max_attempt_num}."
                )
                return False, "generate section outline fail", "", classified_content

        if Config().service_config.visualization_enable:
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
            if attempt_num == max_attempt_num - 1:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                    f"Error: Generate section report failed, reach the max_attempt_num: {max_attempt_num}."
                )
        return False, "generate section report fail", "", classified_content

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
        llm_output = await ainvoke_llm_with_stats(
            llm=self._llm,
            messages=llm_input,
            agent_name=NodeId.REPORTER.value + "_" + task_type,
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
        return True

    async def _add_sub_report_transaction(self, current_inputs: dict):
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

            if LogManager.is_sensitive():
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
                    agent_name=NodeId.REPORTER.value + "_" + "transaction",
                )
                if LogManager.is_sensitive():
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

        # Sort sub-reports by id
        sub_report_content_list.sort(key=lambda x: x.section_id)

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

    async def _classify_with_llm(
        self, current_inputs: dict, section_task: str, doc_infos: List[Dict]
    ) -> Tuple[bool, str]:
        section_idx = current_inputs.get("section_idx", 1)
        section_description = current_inputs.get(
            "section_description", ""
        )  # Section description
        subsection_outline = current_inputs.get("sub_section_outline", "")
        max_attempt_num = current_inputs.get("max_generate_retry_num", 3)

        for attempt in range(1, max_attempt_num + 1):
            try:
                infos_for_llm = (
                    f"Section title is {section_task},"
                    f"User query is {current_inputs.get('report_task', '')},"
                    f"Document infos is {doc_infos},"
                    f"Section description is {section_description},"
                    f"Subsection outline is {subsection_outline}"
                )
                tmp_context = {
                    "messages": [dict(role="user", content=infos_for_llm)],
                    "top_k": current_inputs.get("classify_doc_infos_res_top_k_num", 10),
                }
                llm_input = apply_system_prompt("classify_doc_infos", tmp_context)
                if not LogManager.is_sensitive():
                    logger.debug(
                        "%s [classify_with_llm] section_idx: [%s], llm_input is %s",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        llm_input,
                    )
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=NodeId.SUB_REPORTER.value + "_classify_doc_infos",
                )
                if not LogManager.is_sensitive():
                    logger.debug(
                        "%s [classify_with_llm] section_idx: [%s], llm_output is %s",
                        EFFECT_SUB_REPORT_TAG,
                        section_idx,
                        llm_output,
                    )
                # Validate LLM output
                if not llm_output or not llm_output.get("content"):
                    error_msg = "LLM returned empty content for the section"
                    logger.error(
                        f"{EFFECT_SUB_REPORT_TAG} [classify_with_llm] section_idx: [{section_idx}] try the {attempt} "
                        f"times, error: {error_msg}"
                    )
                    raise CustomValueException(
                        error_code=StatusCode.LLM_RESPONSE_ERROR.code, message=error_msg
                    )
                return True, llm_output.get("content")
            except Exception as e:
                if LogManager.is_sensitive():
                    error_msg = f"Error classify doc infos"
                else:
                    error_msg = f"Error classify doc infos: {str(e)}"
                logger.warning(
                    f"{EFFECT_SUB_REPORT_TAG} [classify_with_llm] section_idx: [{section_idx}] "
                    f"retry the {attempt}/{max_attempt_num} times, {error_msg}",
                    exc_info=True,
                )
                if attempt >= max_attempt_num:
                    logger.error(
                        f"{EFFECT_SUB_REPORT_TAG} [classify_with_llm] section_idx: [{section_idx}] "
                        f"retry reach the max_attempt_num: {max_attempt_num}"
                    )
                    return False, error_msg

        return (
            False,
            f"classify doc_infos failed after retry max_attempt_num: {max_attempt_num}",
        )

    async def _classify_doc_infos(self, current_inputs: dict):
        """Classify doc infos"""
        section_idx = current_inputs.get("section_idx", 1)
        logger.info(
            f"{EFFECT_SUB_REPORT_TAG} [classify_doc_infos] Starting to classify doc infos, section_idx: "
            f"[{section_idx}]"
        )
        section_task = self.strip_leading_number(
            current_inputs.get("section_task", "")
        )  # Current section title
        doc_infos = current_inputs.get("doc_infos", [])
        classify_doc_infos_single_time_num = current_inputs.get(
            "classify_doc_infos_single_time_num", 60
        )

        # Validate required fields
        if not section_task or not doc_infos:
            error_msg = "Missing 'section_task' or 'doc_infos' in context (section title required)"
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [classify_doc_infos] section_idx: [{section_idx}] {error_msg}"
            )
            return False, error_msg

        round_count = 0
        # Process in batches of 10 with concurrent LLM calls until results
        # converge to 10 or max iterations reached (prevent infinite loop).
        while round_count < MAX_LOOP_ROUND:
            round_count += 1
            # NOTE: keep keywords separated by spaces for readability.
            logger.info(
                "%s [classify_doc_infos] section_idx: [%s] start round NO. [%s]",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                round_count,
            )

            # Split into batches
            batches = [
                doc_infos[i:i + classify_doc_infos_single_time_num]
                for i in range(0, len(doc_infos), classify_doc_infos_single_time_num)
            ]

            # Run concurrently
            results = await asyncio.gather(
                *[
                    self._classify_with_llm(current_inputs, section_task, batch)
                    for batch in batches
                ],
                return_exceptions=True,
            )

            # Aggregate results
            merged_urls = set()
            for res in results:
                if isinstance(res, Exception):
                    logger.warning(
                        f"{EFFECT_SUB_REPORT_TAG} [classify_doc_infos] section_idx: [{section_idx}] "
                        f"round:[{round_count}], classify task raised exception: {str(res)}",
                        exc_info=True,
                    )
                    continue
                res_flag, json_str = res
                if not res_flag:
                    logger.warning(
                        f"{EFFECT_SUB_REPORT_TAG} [classify_doc_infos] section_idx: [{section_idx}] "
                        f"round:[{round_count}], partly classify doc_infos with llm failed, failed reason: "
                        f"{json_str}"
                    )
                    continue
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError as e:
                    if LogManager.is_sensitive():
                        logger.warning(
                            f"{EFFECT_SUB_REPORT_TAG} [classify_doc_infos] section_idx: [{section_idx}] "
                            f"round:[{round_count}], partly classify doc_infos with llm failed, "
                            f"failed reason: parse classified doc information failed"
                        )
                    else:
                        logger.warning(
                            f"{EFFECT_SUB_REPORT_TAG} [classify_doc_infos] section_idx: [{section_idx}] "
                            f"round:[{round_count}], partly classify doc_infos with llm failed, "
                            f"failed reason: parse classified doc information failed: {e}"
                        )
                    continue
                merged_urls.update(data.get("core_content_url_list", []))

            # Convergence check
            if not merged_urls:
                logger.error(
                    f"{EFFECT_SUB_REPORT_TAG} [classify_doc_infos] section_idx: [{section_idx}] "
                    f"round:[{round_count}], no core content urls returned."
                )
                return False, "no core content urls from classification"

            if len(merged_urls) <= current_inputs.get(
                "classify_doc_infos_res_top_k_num", 10
            ):
                logger.info(
                    f"{EFFECT_SUB_REPORT_TAG} [classify_doc_infos] section_idx: [{section_idx}] successfully "
                    f"ended on the NO.[{round_count}] round"
                )
                return True, {"core_content_url_list": list(merged_urls)}

            # If > 10, trace back to original doc_infos and iterate on a smaller set
            doc_infos = [doc for doc in doc_infos if doc.get("url") in merged_urls]
        return False, f"Exceeded max loop round: {MAX_LOOP_ROUND}"

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
        if not LogManager.is_sensitive():
            logger.debug(
                "%s [generate_sub_section_outline] section_idx: [%s], section description: [%s]",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                section_description,
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
            sub_content_message = (
                f"Section id is {section_idx},"
                f"Section title is {section_task},"
                f"User query is {report_task},"
                f"Collected information is {collected_infos},"
                f"Section description is {section_description},"
            )
            tmp_context = {}
            tmp_context["messages"] = [dict(role="user", content=sub_content_message)]
            tmp_context["section_idx"] = section_idx
            tmp_context["language"] = current_inputs.get("language")
            tmp_context["has_template"] = current_inputs.get("has_template")
            tmp_context["section_title"] = section_task
            tmp_context["section_description"] = section_description
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
                agent_name=NodeId.SUB_REPORTER.value + "_outline",
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
                agent_name=NodeId.SUB_REPORTER.value + "_visualization_content",
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
                    agent_name=NodeId.SUB_REPORTER.value + "_chart_compliance",
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
                    agent_name=NodeId.SUB_REPORTER.value + "_chart_traceability",
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
                logger.debug(
                    f"{EFFECT_SUB_REPORT_TAG} [process_visualization_task] Extract data: {visualization_content}."
                )
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
                agent_name=NodeId.SUB_REPORTER.value + "_visualization_normalize",
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
            visualization_dict = {
                "section_idx": section_idx,
                "title": visualization_content[i].get("title", ""),
                "origin_content": visualization_content[i].get("original_content", ""),
                "data_density": visualization_content[i].get("data_density", -1.0),
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
                    agent_name=NodeId.SUB_REPORTER.value + "_" + "summary",
                )
                if LogManager.is_sensitive():
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
        if (
            not section_task
            or not current_inputs.get("sub_section_outline", "")
            or not current_inputs.get("classified_content", [])
        ):
            error_msg = "Missing 'section_task' or sub section outline or collected infos in context."
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
                f"\n[citation:{item.get('index', 1)} begin]网页时间: {item.get('doc_time', '')}|||"
                f"网页权威性：{item.get('source_authority', '')}|||网页相关性：{item.get('task_relevance', '')}|||"
                f"网页内容：{item.get('original_content', '')}[citation:{item.get('index', 1)} end]"
            )

        current_outline = current_inputs.get("current_outline", {})
        current_outline_without_plans = Reporter.export_outline_without_plans(
            current_outline
        )
        sub_content_message = (
            f"Section id is {current_inputs.get('section_idx', 1)},"
            f"Section title is {section_task},"
            f"User query is {current_inputs.get('report_task', '')},"
            f"Collected information is {infos},"
            f"Overall outline is {current_outline_without_plans},"
            f"References is {current_inputs.get('sub_section_references', '')},"
            f"Current Chapter Outline is "
            f"{current_inputs.get('sub_section_outline', '')}"
        )
        try:
            llm_input = apply_system_prompt(
                "sub_report_markdown",
                dict(
                    messages=[dict(role="user", content=sub_content_message)],
                    language=current_inputs.get("language"),
                    section_iscore=current_inputs.get("section_iscore", False),
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
                agent_name=NodeId.SUB_REPORTER.value,
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

            current_inputs["sub_report_content"] = llm_output.get("content")

            # Insert visualization content
            if Config().service_config.visualization_enable:
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

            sub_report_summary = await self._generate_sub_report_summary(current_inputs)
            current_inputs["sub_report_summary"] = sub_report_summary.get("result", "")
            current_inputs["sub_report_content"] = self.add_references(
                self.clean_markdown_headers(current_inputs["sub_report_content"]),
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
            item_data_density = item.get("data_density")
            if item_data_density is not None:
                try:
                    if isinstance(item_data_density, (int, float)):
                        point = float(item_data_density)
                    else:
                        density_str = str(item_data_density)
                        if "：" in density_str:
                            point_str = density_str.split("：", 1)[1]
                        elif ":" in density_str:
                            point_str = density_str.split(":", 1)[1]
                        else:
                            point_str = density_str
                        point = float(point_str.strip())
                    if point >= 9.0:
                        selected_visualizations.append(item)
                except (ValueError, IndexError):
                    logger.warning(
                        "%s [select_visualization] invalid data_density: %s",
                        EFFECT_SUB_REPORT_TAG,
                        item_data_density,
                    )
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
                    agent_name=NodeId.SUB_REPORTER.value,
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
            title_with_citation = f"{image_title}{citation_text}".strip()
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


def _get_classified_infos(doc_infos: list, urls: list):
    """Get classified infos"""
    if not doc_infos:
        logger.error(
            f"{EFFECT_SUB_REPORT_TAG} No classified infos found. can not get classified infos."
        )
        return {}, []
    if not urls:
        logger.error(
            f"{EFFECT_SUB_REPORT_TAG} No urls found. can not get classified infos."
        )
        return {}, []
    classified_infos = {"references": [], "core_content_list": []}
    classified_doc_infos = []

    doc_dict = {item["url"]: item for item in doc_infos}
    for url in urls:
        item = doc_dict.get(url)
        if item:
            classified_infos["references"].append(f"[{item['title']}]({item['url']})")
            classified_infos["core_content_list"].append(item.get("core_content", ""))
            classified_doc_infos.append(item)
    return classified_infos, classified_doc_infos
