# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from server.deepsearch.core.manager.report_manager.report_processor import ReportHtml, ReportWord, \
    DefaultReportFormatProcessor

_report_format_map = {
    "html": ReportHtml,
    "docx": ReportWord,
}


class ReportFormat(str, Enum):
    HTML = "html"
    DOCX = "docx"

    def get_processor(self) -> DefaultReportFormatProcessor:
        """Return the format processor for the current export type.

        Returns:
            DefaultReportFormatProcessor: 当前格式对应的处理器实例。
        """
        try:
            processor = _report_format_map[self.value]
            return processor()
        except KeyError:
            return ReportHtml()


class ReportConvertReq(BaseModel):
    """Describe the request payload for report conversion.

    Attributes:
        final_result (dict): 工作流最终结果快照。
        convert_type (ReportFormat): 目标导出格式。
    """

    final_result: dict = Field(..., description='DeepSearch final_result对象')
    convert_type: ReportFormat


class ReportConvertRes(BaseModel):
    """Describe the response payload for report conversion.

    Attributes:
        code (int): 错误码。
        msg (str): 结果信息。
        convert_content (str): base64编码后的ZIP压缩包内容。
    """

    code: int = Field(..., description='错误码')
    msg: str = Field(..., description='结果信息')
    convert_content: str = Field(..., description='base64编码过的转换格式后的zip压缩包')


class ReportStylizeReq(BaseModel):
    """Describe a report styling request.

    Attributes:
        final_result: 工作流最终结果快照。
        llm_config: 报告样式化使用的 LLM 配置。
    """

    final_result: dict = Field(..., description="DeepSearch final_result对象")
    llm_config: dict = Field(..., description="报告样式化使用的 LLM 配置")


class ReportStylizeRes(BaseModel):
    """Describe a styled report export response.

    Attributes:
        code: HTTP 风格的业务状态码。
        msg: 结果信息。
        convert_content: base64 编码后的报告 ZIP bundle。
        style_applied: 是否成功注入 LLM 生成的 CSS。
        style_status: 样式状态，成功为 applied，回退为 fallback。
    """

    code: int = Field(..., description="错误码")
    msg: str = Field(..., description="结果信息")
    convert_content: str = Field(..., description="base64 编码后的报告 ZIP bundle")
    style_applied: bool = Field(..., description="是否成功应用报告样式")
    style_status: Literal["applied", "fallback"] = Field(..., description="报告样式处理状态")
