# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import json
import logging
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.report.report_common import EFFECT_SUB_REPORT_TAG
from openjiuwen_deepsearch.algorithm.report.report_utils import (
    XYChartMermaidGenerator,
    PieChartMermaidGenerator,
    TimelineChartMermaidGenerator,
    validate_visualization_extraction_schema,
    validate_visualization_normalization_schema,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output, safe_float
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)


class VisualizationMixin:
    """Chart data extraction and Mermaid code generation mixin.

    Dependencies (provided by Reporter or other mixins):
        - self._llm: LLM instance (from Reporter.__init__)
        - self.gen_report_context: report context dict (from Reporter.__init__)
        - self.strip_leading_number: (from MarkdownProcessorMixin)
    """

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
    def _infer_desired_chart_type(*texts: str, explicit_only: bool = False) -> str:
        """
        Extract a lightweight chart-type hint from explicit or structural cues.

        The baseline visualization prompt remains responsible for selecting the
        best chart type from traceable source records. This helper deliberately
        avoids domain-specific keyword lists because
        report topics are open-ended. It only preserves explicit chart requests
        and obvious year-sequence structure as lightweight input for the
        extraction prompt, without becoming a topic classifier.
        """
        context = " ".join(str(text or "") for text in texts).lower()
        if not context:
            return ""

        explicit_patterns = (
            ("line", (r"折线图", r"折线", r"走势图", r"line\s+chart", r"line\s+graph")),
            ("bar", (r"柱状图", r"柱形图", r"条形图", r"柱状", r"bar\s+chart")),
            ("pie", (r"饼图", r"环形图", r"pie\s+chart")),
            ("timeline", (r"时间线", r"timeline")),
        )
        for chart_type, patterns in explicit_patterns:
            if any(re.search(pattern, context) for pattern in patterns):
                return chart_type

        if explicit_only:
            return ""

        year_mentions = set(re.findall(r"(?:19|20)\d{2}", context))
        has_year_range = (
            re.search(r"(?:19|20)\d{2}\s*(?:至|到|[-—–~～])\s*(?:19|20)\d{2}", context)
            or re.search(r"(?:19|20)\d{2}\s*[,，、/]\s*(?:19|20)\d{2}", context)
        )
        if len(year_mentions) >= 3 or has_year_range:
            return "line"
        return ""

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
    def _select_visualization_from_classified_content(
        classified_content_for_visualization,
    ):
        selected_visualizations = []
        fallback_visualizations = []
        for item in classified_content_for_visualization:
            if not isinstance(item, dict):
                continue
            dd = safe_float(item.get("data_density"), default=-1.0)
            if dd >= 0.9:
                selected_visualizations.append(item)
            elif dd >= 0.8:
                fallback_visualizations.append(item)
        return selected_visualizations or fallback_visualizations
