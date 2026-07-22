# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Manage HTTP-facing report conversion orchestration."""

from __future__ import annotations

import binascii
import logging
import time
from dataclasses import asdict

from fastapi import status

from openjiuwen_deepsearch.algorithm.report_export.service import export_report
from openjiuwen_deepsearch.common.exception import CustomRuntimeException, CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.llm.report_style_runtime import report_style_llm_context
from server.deepsearch.common.exception.exceptions import (
    ReportConvertBasicException,
    ReportConvertExecutionException,
    ReportConvertValidationException,
)
from server.schemas.report import ReportConvertReq, ReportConvertRes, ReportFormat


logger = logging.getLogger(__name__)


def normalize_report_html_style_llm_config(llm_config: dict) -> dict:
    """将报告美化请求中的 LLM 密钥转换为可清零的字节数组。

    Args:
        llm_config: 顶层直接模型配置，或以模型类别为键的 LLM 配置。

    Returns:
        dict: 保持原有配置结构、且字符串 API 密钥已转换的配置副本。
    """
    normalized_config = llm_config.copy()
    if "model_name" in normalized_config:
        api_key = normalized_config.get("api_key")
        if isinstance(api_key, str):
            normalized_config["api_key"] = bytearray(api_key, encoding="utf-8")
        return normalized_config

    for config_name, config in normalized_config.items():
        if not isinstance(config, dict):
            continue
        normalized_model_config = config.copy()
        api_key = normalized_model_config.get("api_key")
        if isinstance(api_key, str):
            normalized_model_config["api_key"] = bytearray(api_key, encoding="utf-8")
        normalized_config[config_name] = normalized_model_config
    return normalized_config


def _raise_report_convert_error(
    exc_cls: type[ReportConvertBasicException],
    detail: str,
    cause: Exception | None = None,
) -> None:
    """抛出报告转换业务异常并保留原始原因。

    Args:
        exc_cls: 需要抛出的报告导出异常类型。
        detail: 暴露给上层的错误信息。
        cause: 原始异常，可选。

    Raises:
        ReportConvertBasicException: 指定类型的业务异常。
    """
    exc = exc_cls(detail)
    if cause is not None:
        raise exc from cause
    raise exc


async def report_convert(req: ReportConvertReq) -> ReportConvertRes:
    """将 HTTP 转换请求编排为统一报告导出调用。

    Args:
        req: 报告导出请求，包含最终结果、格式和可选 HTML 样式配置。

    Returns:
        ReportConvertRes: 包含 ZIP Base64 和样式处理状态的响应对象。

    Raises:
        ReportConvertValidationException: 输入、LLM 配置或资源内容非法时抛出。
        ReportConvertExecutionException: 导出流程执行失败时抛出。
    """
    start_time = time.perf_counter()
    final_result = req.final_result if isinstance(req.final_result, dict) else {}
    logger.info(
        "Starting report convert convert_type=%s html_styling=%s infer_messages=%s chart_messages=%s",
        req.convert_type.value,
        req.enable_html_styling,
        len(final_result.get("infer_messages") or []),
        len(final_result.get("chart_messages") or []),
    )
    try:
        is_styled_html = req.convert_type is ReportFormat.HTML and req.enable_html_styling
        if is_styled_html:
            if req.llm_config is None:
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                    "llm_config is required when enable_html_styling is true",
                )
            llm_config = normalize_report_html_style_llm_config(req.llm_config)
            async with report_style_llm_context(llm_config) as llm:
                result = await export_report(
                    req.final_result,
                    req.convert_type.value,
                    enable_html_styling=True,
                    llm=llm,
                )
        else:
            result = await export_report(
                req.final_result,
                req.convert_type.value,
                enable_html_styling=False,
            )
    except ReportConvertBasicException:
        raise
    except CustomValueException as exc:
        logger.warning(
            "Report convert validation failed convert_type=%s duration_ms=%.2f",
            req.convert_type.value,
            (time.perf_counter() - start_time) * 1000,
        )
        _raise_report_convert_error(ReportConvertValidationException, exc.message, exc)
    except CustomRuntimeException as exc:
        logger.warning(
            "Report convert execution failed convert_type=%s duration_ms=%.2f",
            req.convert_type.value,
            (time.perf_counter() - start_time) * 1000,
        )
        _raise_report_convert_error(ReportConvertExecutionException, "convert failed", exc)
    except binascii.Error as exc:
        logger.warning(
            "Report convert failed on base64 validation convert_type=%s duration_ms=%.2f",
            req.convert_type.value,
            (time.perf_counter() - start_time) * 1000,
        )
        _raise_report_convert_error(ReportConvertValidationException, "invalid Base64 string", exc)
    except UnicodeDecodeError as exc:
        logger.warning(
            "Report convert failed on UTF-8 validation convert_type=%s duration_ms=%.2f",
            req.convert_type.value,
            (time.perf_counter() - start_time) * 1000,
        )
        _raise_report_convert_error(ReportConvertValidationException, "not valid UTF-8 text", exc)
    except Exception as exc:
        logger.exception(
            "Report convert execution failed convert_type=%s duration_ms=%.2f",
            req.convert_type.value,
            (time.perf_counter() - start_time) * 1000,
        )
        _raise_report_convert_error(ReportConvertExecutionException, "convert failed", exc)

    if not result.convert_content:
        logger.warning(
            "Report convert produced empty content convert_type=%s duration_ms=%.2f",
            req.convert_type.value,
            (time.perf_counter() - start_time) * 1000,
        )
        _raise_report_convert_error(ReportConvertExecutionException, "convert failed")

    logger.info(
        "Completed report convert convert_type=%s style_status=%s bundle_base64_length=%s duration_ms=%.2f",
        req.convert_type.value,
        result.style_status,
        len(result.convert_content),
        (time.perf_counter() - start_time) * 1000,
    )
    return ReportConvertRes(code=status.HTTP_200_OK, msg="success", **asdict(result))
