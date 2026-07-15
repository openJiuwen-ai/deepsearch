# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Orchestrate standalone report export and optional LLM-generated CSS."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.report_style.context import build_style_context
from openjiuwen_deepsearch.algorithm.report_style.css import inject_css, normalize_css_output
from openjiuwen_deepsearch.algorithm.report_style.exceptions import (
    ReportStyleExportError,
    ReportStyleValidationError,
)
from openjiuwen_deepsearch.algorithm.report_style.export.html_export import convert_md_to_html
from openjiuwen_deepsearch.algorithm.report_style.export.report_bundle import (
    build_report_bundle,
    pack_bundle_to_base64,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StyledReportResult:
    """Describe the ZIP bundle returned by report styling.

    Attributes:
        convert_content: base64 编码后的报告 ZIP bundle。
        style_applied: 是否成功注入 LLM 生成的 CSS。
        style_status: 样式分支状态；仅为 `applied` 或 `fallback`。
    """

    convert_content: str
    style_applied: bool
    style_status: Literal["applied", "fallback"]


def _pack_result(bundle_root: Path, *, style_applied: bool) -> StyledReportResult:
    """Package a report bundle and expose its styling status.

    Args:
        bundle_root: `report_bundle` 根目录。
        style_applied: 是否已注入模型生成的样式。

    Returns:
        StyledReportResult: 可供 API 层直接映射的导出结果。

    Raises:
        ReportStyleExportError: ZIP 打包失败时抛出。
    """
    try:
        encoded = pack_bundle_to_base64(bundle_root)
    except Exception as exc:
        raise ReportStyleExportError("unable to package report bundle") from exc
    return StyledReportResult(
        convert_content=encoded,
        style_applied=style_applied,
        style_status="applied" if style_applied else "fallback",
    )


async def stylize_report(final_result: dict, llm: dict) -> StyledReportResult:
    """Generate a styled report bundle and fall back when only styling fails.

    Args:
        final_result: DeepSearch 工作流最终结果，包含 Markdown 和可选资源。
        llm: 已由 framework 初始化的 LLM 运行时对象。

    Returns:
        StyledReportResult: 应用样式后的 ZIP，或保持基础 HTML 的回退 ZIP。

    Raises:
        ReportStyleValidationError: `final_result` 不符合独立导出契约时抛出。
        ReportStyleExportError: 基础 HTML 或最终 ZIP 无法生成时抛出。
    """
    with TemporaryDirectory(prefix="report_style_") as temporary_dir:
        try:
            bundle = build_report_bundle(final_result, Path(temporary_dir))
            html_path = bundle.root_dir / "report.html"
            convert_md_to_html(bundle.markdown_path, html_path)
            baseline_html = html_path.read_text(encoding="utf-8")
            markdown = bundle.markdown_path.read_text(encoding="utf-8")
        except ReportStyleValidationError:
            raise
        except Exception as exc:
            raise ReportStyleExportError("unable to build base report export") from exc

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
            html_path.write_text(inject_css(baseline_html, css), encoding="utf-8")
            style_phase = "package_styled_bundle"
            result = _pack_result(bundle.root_dir, style_applied=True)
            logger.info("Completed report style export style_status=%s", result.style_status)
            return result
        except ReportStyleExportError:
            raise
        except Exception as exc:
            # LLM/CSS errors must not discard a successfully generated research report.
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
            html_path.write_text(baseline_html, encoding="utf-8")
            result = _pack_result(bundle.root_dir, style_applied=False)
            logger.info("Completed report style export style_status=%s", result.style_status)
            return result
