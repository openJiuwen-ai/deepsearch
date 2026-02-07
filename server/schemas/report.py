# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from enum import Enum

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
        try:
            processor = _report_format_map[self.value]
            return processor()
        except KeyError:
            return ReportHtml()


class ReportConvertReq(BaseModel):
    report_content: str = Field(..., min_length=1, max_length=1000 * 1000,
                                description='base64编码过的原markdown报告内容')
    convert_type: ReportFormat


class ReportConvertRes(BaseModel):
    code: int = Field(..., description='错误码')
    msg: str = Field(..., description='结果信息')
    convert_content: str = Field(..., description='base64编码过的转换格式后报告内容')
