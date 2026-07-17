# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from openjiuwen_deepsearch.algorithm.report_style.exceptions import (
    ReportStyleExportError,
    ReportStyleValidationError,
)
from openjiuwen_deepsearch.algorithm.report_style.service import stylize_report
from openjiuwen_deepsearch.framework.openjiuwen.llm.report_style_runtime import report_style_llm_context
import server.deepsearch.core.manager.report as mgr
from server.deepsearch.common.exception.exceptions import ReportConvertBasicException
from server.routers.common import validate_request
from server.schemas.report import ReportConvertRes, ReportConvertReq, ReportStylizeReq, ReportStylizeRes

reports_router = APIRouter()


@reports_router.post("/convert", response_model=ReportConvertRes)
async def report_convert(
        request: dict
):
    """
    转换生成的markdown报告的格式

    Args:
        request (dict):  包含用户报告转换需求的请求体数据，需符合ReportConvert模型定义
        current_user (dict): 执行此操作的用户上下文信息

    Returns:
        ReportConvertRes: 标准化响应对象，其中封装了转换成功后的格式报告内容的base64编码二进制
        如果转换失败，则包含相应的提示信息
    """
    try:
        req = validate_request(request, ReportConvertReq)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    try:
        return mgr.report_convert(req)
    except ReportConvertBasicException as e:
        raise HTTPException(
            status_code=getattr(e, "STATUS_CODE", status.HTTP_400_BAD_REQUEST),
            detail=str(e),
        ) from e


@reports_router.post("/stylize", response_model=ReportStylizeRes)
async def report_stylize(request: dict) -> ReportStylizeRes:
    """Generate a report bundle with optional LLM-generated CSS.

    Args:
        request: 包含 final_result 和 llm_config 的请求体。

    Returns:
        ReportStylizeRes: ZIP bundle 及样式应用或回退状态。

    Raises:
        HTTPException: 请求、LLM 配置或基础报告导出失败时抛出对应 HTTP 状态。
    """
    try:
        req = validate_request(request, ReportStylizeReq)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        llm_config = mgr.normalize_report_stylize_llm_config(req.llm_config)
        async with report_style_llm_context(llm_config) as llm:
            result = await stylize_report(req.final_result, llm)
    except ReportStyleValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ReportStyleExportError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return ReportStylizeRes(code=status.HTTP_200_OK, msg="success", **asdict(result))
