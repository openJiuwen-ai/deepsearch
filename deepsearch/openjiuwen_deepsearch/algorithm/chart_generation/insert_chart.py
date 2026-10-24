# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
图表插入报告模块

功能：
1. 将图表插入报告
2. 在图表下加入图表描述
"""

import logging
from typing import Dict, List, Any

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.text_utils import (
    escape_html_text,
)

logger = logging.getLogger(__name__)


class InsertChartNode:
    """将图表插入报告"""

    def __init__(self, output_dir: str):
        """
        初始化

        Args:
            output_dir: 图表输出目录。
        """
        self.report_content = None
        self.output_dir = output_dir
        self._log_prefix = "[InsertChartNode]"

    def run(
        self, report_content: str,
        charts_info: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        """
        将图表插入报告

        Args:
            report_content: 报告内容
            charts_info: 图表信息字典 {section_index: [chart1, chart2, ...]}

        Returns:
            str: 插入占位符后的报告内容
        """
        try:
            if not charts_info:
                raise ValueError(f"{self._log_prefix} charts_info is empty!")
            self.report_content = report_content

            modified_report = self.report_content

            for _, charts in charts_info.items():
                for chart in charts:
                    modified_report = self._insert_chart_placeholder(
                        modified_report, chart
                    )
        except Exception as e:
            error_msg = f"Error inserting chart tasks: {repr(e)}"
            logger.error(error_msg)
            raise CustomValueException(StatusCode.CHART_INSERT_ERROR.code,
                                       StatusCode.CHART_INSERT_ERROR.errmsg.format(e=error_msg)) from e


        return modified_report

    def _insert_chart_placeholder(
        self, report_content: str, chart: Dict[str, Any]
    ) -> str:
        """
        在报告中找到锚点文本并插入图表占位符和描述

        Args:
            report_content: 报告内容
            chart: 图表信息
        Returns:
            str: 修改后的报告内容
        """

        anchor_text = chart.get("anchor_match_para", "")
        chart_title = chart.get("chart_title", "")
        description = chart.get("description", "")
        chart_id = chart.get("chart_id", "")
        # 对图表标题和描述进行HTML转义，防止HTML/Markdown注入
        safe_chart_title = escape_html_text(chart_title)
        safe_description = escape_html_text(description)

        placeholder = f"(#insertChart:{chart_id})"
        insertion = f"{placeholder}\n<font size=2>**{safe_chart_title}**: {safe_description}</font>"

        # 在报告中插入图表占位符和描述
        if anchor_text in report_content:
            modified_content = report_content.replace(
                anchor_text, anchor_text + "\n\n" + insertion + "\n", 1
            )
            logger.debug("%s Inserted chart placeholder %s after anchor text",
                        self._log_prefix, placeholder)
            return modified_content
        else:
            logger.warning(f"{self._log_prefix} Anchor text not found in report: {anchor_text[:50]}...")
            return report_content
