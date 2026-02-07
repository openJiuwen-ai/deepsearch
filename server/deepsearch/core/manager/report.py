# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import binascii

from fastapi import HTTPException, status

from server.schemas.report import ReportConvertReq, ReportConvertRes


def _raise_http_error(
        status_code: int,
        detail: str,
        cause: Exception | None = None,
) -> None:
    exc = HTTPException(status_code=status_code, detail=detail)
    if cause is not None:
        raise exc from cause
    raise exc


def report_convert(req: ReportConvertReq) -> ReportConvertRes:
    """转换报告格式"""
    processor = req.convert_type.get_processor()
    try:
        b64_convert_content = processor.base64_convert_from_markdown(req.report_content)
    except binascii.Error as exc:
        _raise_http_error(status.HTTP_400_BAD_REQUEST, "invalid Base64 string", exc)
    except UnicodeDecodeError as exc:
        _raise_http_error(status.HTTP_400_BAD_REQUEST, "not valid UTF-8 text", exc)
    except Exception as exc:
        _raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "convert failed", exc)

    if not b64_convert_content:
        _raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "convert failed")

    return ReportConvertRes(
        code=status.HTTP_200_OK,
        msg='success',
        convert_content=b64_convert_content
    )
