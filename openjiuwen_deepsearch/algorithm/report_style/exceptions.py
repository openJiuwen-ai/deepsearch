# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""报告样式化算法的领域异常。"""


class ReportStyleError(Exception):
    """报告样式化算法异常基类。"""


class ReportStyleValidationError(ReportStyleError):
    """报告样式化输入或 LLM 配置不合法时抛出。"""


class ReportStyleExportError(ReportStyleError):
    """基础报告 bundle 或 HTML 导出失败时抛出。"""
