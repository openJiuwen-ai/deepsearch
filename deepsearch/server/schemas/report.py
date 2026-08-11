# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ReportFormat(str, Enum):
    HTML = "html"
    DOCX = "docx"


class ReportConvertReq(BaseModel):
    """Describe the request payload for report conversion.

    Attributes:
        final_result (dict): 工作流最终结果快照。
        convert_type (ReportFormat): 目标导出格式。
        enable_html_styling (bool): 是否为 HTML 应用 LLM CSS 美化。
        llm_config (dict | None): HTML 美化使用的直接或分类 LLM 配置。
    """

    final_result: dict = Field(..., description='DeepSearch final_result对象')
    convert_type: ReportFormat
    enable_html_styling: bool = Field(False, description="是否启用 HTML 样式美化")
    llm_config: dict | None = Field(None, description="HTML 样式美化使用的直接或分类 LLM 配置")


class ReportConvertRes(BaseModel):
    """Describe the response payload for report conversion.

    Attributes:
        code (int): 错误码。
        msg (str): 结果信息。
        convert_content (str): base64编码后的ZIP压缩包内容。
        style_applied (bool): 是否成功应用 HTML 样式。
        style_status (str): 样式处理状态。
    """

    code: int = Field(..., description='错误码')
    msg: str = Field(..., description='结果信息')
    convert_content: str = Field(..., description='base64编码过的转换格式后的zip压缩包')
    style_applied: bool = Field(..., description="是否成功应用报告样式")
    style_status: Literal["not_requested", "not_supported", "applied", "fallback"] = Field(
        ...,
        description="报告样式处理状态",
    )
