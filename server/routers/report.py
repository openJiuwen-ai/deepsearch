# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Expose report conversion HTTP endpoints."""

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

import server.deepsearch.core.manager.report as mgr
from server.deepsearch.common.exception.exceptions import ReportConvertBasicException
from server.routers.common import validate_request
from server.schemas.report import ReportConvertReq, ReportConvertRes


reports_router = APIRouter()


@reports_router.post("/convert", response_model=ReportConvertRes)
async def report_convert(request: dict) -> ReportConvertRes:
    """转换生成的 Markdown 报告格式，并可选美化 HTML。

    Args:
        request: 包含报告导出请求的原始 HTTP 请求体。

    Returns:
        ReportConvertRes: 标准化的报告 ZIP Base64 及样式状态。

    Raises:
        HTTPException: 请求校验或报告导出失败时按既有语义抛出。
    """
    try:
        req = validate_request(request, ReportConvertReq)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        return await mgr.report_convert(req)
    except ReportConvertBasicException as exc:
        raise HTTPException(
            status_code=getattr(exc, "STATUS_CODE", status.HTTP_400_BAD_REQUEST),
            detail=str(exc),
        ) from exc
