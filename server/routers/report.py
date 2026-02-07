# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

import server.deepsearch.core.manager.report as mgr
from server.routers.common import validate_request
from server.schemas.report import ReportConvertRes, ReportConvertReq

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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return mgr.report_convert(req)
