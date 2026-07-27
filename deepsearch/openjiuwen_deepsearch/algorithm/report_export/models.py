# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Define report export result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ReportStyleStatus = Literal["not_requested", "not_supported", "applied", "fallback"]


@dataclass(frozen=True, slots=True)
class ReportExportResult:
    """Describe a completed report export bundle.

    Attributes:
        convert_content: Base64 编码后的报告 ZIP bundle。
        style_applied: 是否成功注入 LLM 生成的 CSS。
        style_status: 样式处理状态。
    """

    convert_content: str
    style_applied: bool
    style_status: ReportStyleStatus
