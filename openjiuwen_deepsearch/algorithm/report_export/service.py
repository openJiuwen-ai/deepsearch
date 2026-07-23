# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""编排通用报告 bundle 导出和可选 HTML 样式化。"""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.report_export.docx_export import convert_md_to_docx
from openjiuwen_deepsearch.algorithm.report_export.html_export import ConvertOptions, convert_md_to_html
from openjiuwen_deepsearch.algorithm.report_export.models import ReportExportResult
from openjiuwen_deepsearch.algorithm.report_export.report_bundle import (
    build_report_bundle,
    pack_bundle_to_base64,
)
from openjiuwen_deepsearch.algorithm.report_style.context import build_style_context
from openjiuwen_deepsearch.algorithm.report_style.css import inject_css, normalize_css_output
from openjiuwen_deepsearch.common.exception import CustomRuntimeException, CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


logger = logging.getLogger(__name__)
ReportExportFormat = Literal["html", "docx"]


async def _apply_html_style(baseline_html: str, markdown: str, llm: dict) -> tuple[str, bool]:
    """生成并注入报告 CSS，样式阶段失败时保留基础 HTML。

    Args:
        baseline_html: 已生成的语义化基础 HTML。
        markdown: bundle 中保留的 Markdown 原文。
        llm: 已由 framework 初始化的 LLM 运行时对象。

    Returns:
        tuple[str, bool]: 最终 HTML 及 CSS 是否成功注入。
    """
    style_phase = "build_style_context"
    raw_css: object | None = None
    raw_css_length = 0
    normalized_css_length: int | None = None
    try:
        context = build_style_context(markdown)
        style_phase = "apply_prompt"
        messages = apply_system_prompt("report_style_css", context.to_prompt_dict())
        style_phase = "invoke_llm"
        response = await ainvoke_llm_with_stats(llm=llm, messages=messages, llm_type="basic")
        style_phase = "extract_css_response"
        raw_css = (response or {}).get("content", "")
        raw_css_length = len(raw_css) if isinstance(raw_css, str) else 0
        style_phase = "normalize_css_output"
        css = normalize_css_output(raw_css)
        normalized_css_length = len(css)
        logger.info(
            "Report style CSS generated raw_css_length=%s normalized_css_length=%s",
            raw_css_length,
            normalized_css_length,
        )
        style_phase = "inject_css"
        return inject_css(baseline_html, css), True
    except Exception as exc:
        # 样式生成不能影响已经成功构建的研究报告。
        logger.warning(
            "Report style fallback phase=%s reason=%s raw_css_length=%s normalized_css_length=%s",
            style_phase,
            type(exc).__name__,
            raw_css_length,
            normalized_css_length,
        )
        if logger.isEnabledFor(logging.DEBUG) and not LogManager.is_sensitive():
            css_preview = raw_css[:512] if isinstance(raw_css, str) else None
            logger.debug(
                "Report style fallback diagnostics phase=%s reason=%s detail=%s css_preview=%r",
                style_phase,
                type(exc).__name__,
                str(exc),
                css_preview,
                exc_info=True,
            )
        return baseline_html, False


async def export_report(
    final_result: dict,
    export_format: ReportExportFormat,
    *,
    enable_html_styling: bool = False,
    llm: dict | None = None,
) -> ReportExportResult:
    """将 DeepSearch 最终结果导出为带样式状态的 Base64 ZIP bundle。

    Args:
        final_result: DeepSearch 工作流最终结果。
        export_format: 目标格式，仅支持 `html` 或 `docx`。
        enable_html_styling: 是否为 HTML 请求 LLM CSS 美化。
        llm: HTML 美化时由 framework 初始化的 LLM 运行时对象。

    Returns:
        ReportExportResult: 报告 ZIP 及样式处理状态。

    Raises:
        CustomValueException: 输入、格式或样式 LLM 不符合导出契约时抛出。
        CustomRuntimeException: 主报告或 ZIP 无法生成时抛出。
    """
    if export_format not in {"html", "docx"}:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            f"unsupported report export format: {export_format}",
        )
    if export_format == "html" and enable_html_styling and llm is None:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            "HTML styling requires an initialized LLM",
        )

    with TemporaryDirectory(prefix="report_export_") as temporary_dir:
        try:
            bundle = build_report_bundle(final_result, Path(temporary_dir))
            if export_format == "docx":
                convert_md_to_docx(bundle.markdown_path, bundle.root_dir / "report.docx")
                style_applied = False
                style_status = "not_supported"
            elif enable_html_styling:
                html_path = bundle.root_dir / "report.html"
                convert_md_to_html(
                    bundle.markdown_path,
                    html_path,
                    options=ConvertOptions(page_variant="styled"),
                )
                baseline_html = html_path.read_text(encoding="utf-8")
                markdown = bundle.markdown_path.read_text(encoding="utf-8")
                styled_html, style_applied = await _apply_html_style(baseline_html, markdown, llm)
                html_path.write_bytes(styled_html.encode("utf-8"))
                style_status = "applied" if style_applied else "fallback"
            else:
                convert_md_to_html(bundle.markdown_path, bundle.root_dir / "report.html")
                style_applied = False
                style_status = "not_requested"

            return ReportExportResult(
                convert_content=pack_bundle_to_base64(bundle.root_dir),
                style_applied=style_applied,
                style_status=style_status,
            )
        except CustomValueException:
            raise
        except Exception as exc:
            logger.exception("Report export failed format=%s", export_format)
            raise CustomRuntimeException(
                StatusCode.REPORT_GENERATE_ERROR.code,
                "unable to export report bundle",
            ) from exc
