"""Test the unified report conversion HTTP orchestration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openjiuwen_deepsearch.algorithm.report_export.models import ReportExportResult
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from server.deepsearch.common.exception.exceptions import (
    ReportConvertExecutionException,
    ReportConvertValidationException,
)
from server.schemas.report import ReportConvertReq, ReportFormat


def _request(*, export_format: ReportFormat = ReportFormat.HTML, styling: bool = False) -> ReportConvertReq:
    """构造通用报告转换请求。

    Args:
        export_format: 目标报告格式。
        styling: 是否启用 HTML 样式美化。

    Returns:
        ReportConvertReq: 可用于 manager 测试的请求对象。
    """
    return ReportConvertReq(
        final_result={"response_content": "正文", "infer_messages": [], "chart_messages": []},
        convert_type=export_format,
        enable_html_styling=styling,
    )


@pytest.mark.asyncio
async def test_report_convert_returns_not_requested_for_default_html(monkeypatch) -> None:
    """默认 HTML 转换不初始化 LLM，且返回 not_requested。"""
    from server.deepsearch.core.manager import report as report_mgr

    export_call = AsyncMock(return_value=ReportExportResult("UEs=", False, "not_requested"))
    monkeypatch.setattr(report_mgr, "export_report", export_call)
    context_called = False

    @asynccontextmanager
    async def unexpected_context(_config):
        """确保默认 HTML 不创建 LLM 上下文。"""
        nonlocal context_called
        context_called = True
        yield {}

    monkeypatch.setattr(report_mgr, "report_style_llm_context", unexpected_context)
    req = _request()

    response = await report_mgr.report_convert(req)

    assert response.convert_content == "UEs="
    assert response.style_applied is False
    assert response.style_status == "not_requested"
    assert export_call.await_args.args == (req.final_result, "html")
    assert export_call.await_args.kwargs == {"enable_html_styling": False}
    assert context_called is False


@pytest.mark.asyncio
async def test_report_convert_uses_general_llm_for_styled_html(monkeypatch) -> None:
    """开启 HTML 美化时只使用 general 配置并传入运行时 LLM。"""
    from server.deepsearch.core.manager import report as report_mgr

    runtime_llm = {"model_name": "general-model", "model": object()}
    received_configs = []

    @asynccontextmanager
    async def fake_context(config):
        """提供可断言配置的 LLM 上下文。"""
        received_configs.append(config)
        yield runtime_llm

    export_call = AsyncMock(return_value=ReportExportResult("UEs=", True, "applied"))
    monkeypatch.setattr(report_mgr, "report_style_llm_context", fake_context)
    monkeypatch.setattr(report_mgr, "export_report", export_call)
    req = _request(styling=True)
    req.llm_config = {
        "general": {"model_name": "general-model", "api_key": "secret"},
        "writing_checking": {"model_name": "ignored", "api_key": "ignored"},
    }

    response = await report_mgr.report_convert(req)

    assert response.style_status == "applied"
    assert received_configs[0]["general"]["api_key"] == bytearray(b"secret")
    assert export_call.await_args.args == (req.final_result, "html")
    assert export_call.await_args.kwargs == {"enable_html_styling": True, "llm": runtime_llm}


@pytest.mark.asyncio
async def test_report_convert_accepts_direct_llm_config_for_styled_html(monkeypatch) -> None:
    """HTML 美化保持对顶层直接模型配置的兼容。"""
    from server.deepsearch.core.manager import report as report_mgr

    received_configs = []

    @asynccontextmanager
    async def fake_context(config):
        """记录标准化后的直接模型配置。"""
        received_configs.append(config)
        yield {"model_name": "direct-model", "model": object()}

    monkeypatch.setattr(report_mgr, "report_style_llm_context", fake_context)
    monkeypatch.setattr(
        report_mgr,
        "export_report",
        AsyncMock(return_value=ReportExportResult("UEs=", True, "applied")),
    )
    req = _request(styling=True)
    req.llm_config = {"model_name": "direct-model", "api_key": "secret"}

    await report_mgr.report_convert(req)

    assert received_configs == [
        {"model_name": "direct-model", "api_key": bytearray(b"secret")}
    ]


@pytest.mark.asyncio
async def test_report_convert_rejects_missing_general_config() -> None:
    """开启 HTML 美化但只有 writing_checking 时保留 400 语义。"""
    from server.deepsearch.core.manager import report as report_mgr

    req = _request(styling=True)
    req.llm_config = {"writing_checking": {"model_name": "old-model", "api_key": "key"}}

    with pytest.raises(ReportConvertValidationException):
        await report_mgr.report_convert(req)


@pytest.mark.asyncio
async def test_report_convert_docx_does_not_initialize_llm(monkeypatch) -> None:
    """DOCX 忽略美化开关和配置，并返回 not_supported。"""
    from server.deepsearch.core.manager import report as report_mgr

    export_call = AsyncMock(return_value=ReportExportResult("UEs=", False, "not_supported"))
    monkeypatch.setattr(report_mgr, "export_report", export_call)

    @asynccontextmanager
    async def unexpected_context(_config):
        """DOCX 不应进入样式 LLM 上下文。"""
        raise AssertionError("DOCX must not initialize an LLM")
        yield {}

    monkeypatch.setattr(report_mgr, "report_style_llm_context", unexpected_context)
    req = _request(export_format=ReportFormat.DOCX, styling=True)
    req.llm_config = {"invalid": "ignored"}

    response = await report_mgr.report_convert(req)

    assert response.style_status == "not_supported"
    assert export_call.await_args.kwargs == {"enable_html_styling": False}


@pytest.mark.asyncio
async def test_report_convert_maps_validation_errors(monkeypatch) -> None:
    """算法输入错误仍映射为报告转换校验异常。"""
    from server.deepsearch.core.manager import report as report_mgr

    monkeypatch.setattr(
        report_mgr,
        "export_report",
        AsyncMock(
            side_effect=CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                "invalid report",
            )
        ),
    )

    with pytest.raises(ReportConvertValidationException):
        await report_mgr.report_convert(_request())


@pytest.mark.asyncio
async def test_report_router_only_exposes_convert_and_awaits_manager(monkeypatch) -> None:
    """路由删除 stylize，并将统一结果原样返回。"""
    from server.routers import report as report_router

    assert not hasattr(report_router, "report_stylize")
    manager_call = AsyncMock(return_value=type("Response", (), {
        "code": 200,
        "msg": "success",
        "convert_content": "UEs=",
        "style_applied": False,
        "style_status": "not_requested",
    })())
    monkeypatch.setattr(report_router.mgr, "report_convert", manager_call)

    response = await report_router.report_convert(
        {"final_result": {"response_content": "正文"}, "convert_type": "html"}
    )

    assert response.style_status == "not_requested"
    manager_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_router_maps_convert_exception_to_http(monkeypatch) -> None:
    """统一路由继续将转换执行异常映射为 HTTP 500。"""
    from server.routers import report as report_router

    monkeypatch.setattr(
        report_router.mgr,
        "report_convert",
        AsyncMock(side_effect=ReportConvertExecutionException("convert failed")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await report_router.report_convert(
            {"final_result": {"response_content": "正文"}, "convert_type": "html"}
        )

    assert exc_info.value.status_code == 500
