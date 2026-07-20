# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Keep the legacy report styling algorithm entry point."""

from __future__ import annotations

from openjiuwen_deepsearch.algorithm.report_export.models import ReportExportResult
from openjiuwen_deepsearch.algorithm.report_export.service import export_report


StyledReportResult = ReportExportResult


# 保留该入口以兼容直接调用 algorithm 层样式化能力的集成方；
# HTTP `/reports/stylize`已删除，但在完成外部 SDK 兼容性评估前不得移除此函数。
async def stylize_report(final_result: dict, llm: dict) -> StyledReportResult:
    """生成带 LLM CSS 的 HTML 报告 bundle。

    该兼容入口保留原有调用契约，实际导出由统一的 `export_report` 完成。

    Args:
        final_result: DeepSearch 工作流最终结果，包含 Markdown 和可选资源。
        llm: 已由 framework 初始化的 LLM 运行时对象。

    Returns:
        StyledReportResult: 应用样式后的 ZIP，或保持基础 HTML 的回退 ZIP。
    """
    return await export_report(
        final_result,
        "html",
        enable_html_styling=True,
        llm=llm,
    )
