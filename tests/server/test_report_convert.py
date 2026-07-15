import base64
import binascii
import io
import logging
import zipfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from server.deepsearch.common.exception.exceptions import (
    ReportConvertExecutionException,
    ReportConvertValidationException,
)
from openjiuwen_deepsearch.algorithm.report_style.exceptions import (
    ReportStyleExportError,
    ReportStyleValidationError,
)
from server.schemas.report import ReportConvertReq, ReportFormat
from server.deepsearch.core.manager.report_manager.report_processor import ReportHtml, ReportWord


@pytest.mark.asyncio
async def test_report_stylize_returns_algorithm_result(monkeypatch):
    """Validate that the thin stylize route maps the algorithm result unchanged.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    from openjiuwen_deepsearch.algorithm.report_style.service import StyledReportResult
    from server.routers import report as report_router

    assert hasattr(report_router, "report_stylize")

    @asynccontextmanager
    async def fake_style_context(_config):
        """Provide a fake LLM runtime for the route contract test.

        Args:
            _config: 路由传入的 LLM 配置。

        Yields:
            dict: 伪造的 LLM 运行时对象。
        """
        yield {"model_name": "style-model", "model": object()}

    monkeypatch.setattr(report_router, "report_style_llm_context", fake_style_context)
    monkeypatch.setattr(
        report_router,
        "stylize_report",
        AsyncMock(return_value=StyledReportResult("UEs=", True, "applied")),
    )

    response = await report_router.report_stylize(
        {
            "final_result": {"response_content": "正文"},
            "llm_config": {"model_name": "style-model", "api_key": "key"},
        }
    )

    assert response.convert_content == "UEs="
    assert response.style_applied is True
    assert response.style_status == "applied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("style_error", "expected_status"),
    [
        (ReportStyleValidationError("invalid input"), 400),
        (ReportStyleExportError("export failed"), 500),
    ],
)
async def test_report_stylize_maps_algorithm_errors(monkeypatch, style_error, expected_status):
    """Validate that stylize errors retain their API status classification.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        style_error: 算法层抛出的样式化异常。
        expected_status: 期望映射的 HTTP 状态码。
    """
    from server.routers import report as report_router

    @asynccontextmanager
    async def fake_style_context(_config):
        """Provide a fake LLM runtime for the route error test.

        Args:
            _config: 路由传入的 LLM 配置。

        Yields:
            dict: 伪造的 LLM 运行时对象。
        """
        yield {"model_name": "style-model", "model": object()}

    monkeypatch.setattr(report_router, "report_style_llm_context", fake_style_context)
    monkeypatch.setattr(report_router, "stylize_report", AsyncMock(side_effect=style_error))

    with pytest.raises(HTTPException) as exc_info:
        await report_router.report_stylize(
            {
                "final_result": {"response_content": "正文"},
                "llm_config": {"model_name": "style-model", "api_key": "key"},
            }
        )

    assert exc_info.value.status_code == expected_status


def test_report_convert_returns_zip_base64(monkeypatch):
    """Validate that report_convert returns a ZIP bundle encoded as base64.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        None.
    """
    from server.deepsearch.core.manager import report as report_mgr
    from server.schemas import report as report_schema

    class _DummyProcessor:
        """Provide a minimal processor stub for manager tests."""

        def convert_from_final_result_to_bundle_base64(self, final_result):
            """Return a dummy ZIP payload for manager contract testing.

            Args:
                final_result: 输入的 final_result 字典。

            Returns:
                base64 编码后的伪 ZIP 二进制内容。
            """
            assert final_result["response_content"] == "正文"
            return base64.b64encode(b"PK\x03\x04dummy").decode("utf-8")

    req = ReportConvertReq(
        final_result={
            "response_content": "正文",
            "infer_messages": [],
            "chart_messages": [],
            "warning_info": "",
            "exception_info": "",
        },
        convert_type=ReportFormat.HTML,
    )
    monkeypatch.setattr(
        report_schema.ReportFormat,
        "get_processor",
        lambda self: _DummyProcessor(),
    )

    res = report_mgr.report_convert(req)

    assert base64.b64decode(res.convert_content).startswith(b"PK")


def test_report_convert_logs_start_and_success(monkeypatch, caplog):
    """验证报告转换 manager 成功路径会记录入口和结果日志。

    Args:
        monkeypatch: pytest monkeypatch fixture.
        caplog: pytest 日志捕获工具。

    Returns:
        None.
    """
    from server.deepsearch.core.manager import report as report_mgr
    from server.schemas import report as report_schema

    class _DummyProcessor:
        """Provide a minimal processor stub for log contract testing."""

        def convert_from_final_result_to_bundle_base64(self, final_result):
            """Return a dummy ZIP payload.

            Args:
                final_result: 输入的 final_result 字典。

            Returns:
                base64 编码后的伪 ZIP 二进制内容。
            """
            return base64.b64encode(b"PK\x03\x04dummy").decode("utf-8")

    req = ReportConvertReq(
        final_result={
            "response_content": "正文",
            "infer_messages": [],
            "chart_messages": [],
            "warning_info": "",
            "exception_info": "",
        },
        convert_type=ReportFormat.HTML,
    )
    monkeypatch.setattr(
        report_schema.ReportFormat,
        "get_processor",
        lambda self: _DummyProcessor(),
    )
    caplog.set_level(logging.INFO, logger="server.deepsearch.core.manager.report")

    report_mgr.report_convert(req)

    assert any("Starting report convert convert_type=html" in record.message for record in caplog.records)
    assert any("Completed report convert convert_type=html" in record.message for record in caplog.records)


def test_report_convert_raises_validation_exception(monkeypatch):
    """Validate that manager raises report convert validation exceptions.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        None.
    """
    from server.deepsearch.core.manager import report as report_mgr
    from server.schemas import report as report_schema

    class _DummyProcessor:
        """Provide a processor stub that triggers base64 validation failures."""

        def convert_from_final_result_to_bundle_base64(self, final_result):
            """Raise the same exception as an invalid base64 decode path.

            Args:
                final_result: 输入的 final_result 字典。

            Raises:
                binascii.Error: 用于模拟非法 base64 内容。
            """
            raise binascii.Error("bad base64")

    req = ReportConvertReq(
        final_result={
            "response_content": "正文",
            "infer_messages": [],
            "chart_messages": [],
            "warning_info": "",
            "exception_info": "",
        },
        convert_type=ReportFormat.HTML,
    )
    monkeypatch.setattr(
        report_schema.ReportFormat,
        "get_processor",
        lambda self: _DummyProcessor(),
    )

    with pytest.raises(ReportConvertValidationException):
        report_mgr.report_convert(req)


@pytest.mark.asyncio
async def test_report_router_maps_convert_exception_to_http(monkeypatch):
    """Validate that the router maps report convert exceptions to HTTP errors.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        None.
    """
    from server.routers import report as report_router

    monkeypatch.setattr(
        report_router.mgr,
        "report_convert",
        lambda req: (_ for _ in ()).throw(ReportConvertExecutionException("convert failed")),
    )

    request = {
        "final_result": {
            "response_content": "正文",
            "infer_messages": [],
            "chart_messages": [],
            "warning_info": "",
            "exception_info": "",
        },
        "convert_type": "html",
    }

    with pytest.raises(HTTPException) as exc_info:
        await report_router.report_convert(request)

    assert exc_info.value.status_code == 500


def test_report_html_processor_returns_bundle_zip_base64():
    """Validate that ReportHtml packages the converted artifact as a ZIP bundle.

    Returns:
        None.
    """
    final_result = {
        "response_content": "正文[结论](#inference:0)",
        "infer_messages": [
            {
                "id": 0,
                "html_base64": base64.b64encode(b"<html>infer</html>").decode("utf-8"),
            }
        ],
        "chart_messages": [],
        "warning_info": "",
        "exception_info": "",
    }

    bundle_b64 = ReportHtml().convert_from_final_result_to_bundle_base64(final_result)
    data = base64.b64decode(bundle_b64)
    with zipfile.ZipFile(io.BytesIO(data)) as zip_file:
        names = set(zip_file.namelist())

    assert "report_bundle/report.md" in names
    assert "report_bundle/report.html" in names
    assert "report_bundle/infer/inference_0.html" in names

def test_report_docx_processor_returns_bundle_zip_base64():
    """Validate that ReportWord packages DOCX output inside the ZIP bundle.

    Returns:
        None.
    """
    tiny_png_base64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO"
        "+/p9sAAAAASUVORK5CYII="
    )
    final_result = {
        "response_content": "(#insertChart:chart_0)",
        "infer_messages": [],
        "chart_messages": [
            {
                "chart_id": "chart_0",
                "chart_title": "图表标题",
                "base64": tiny_png_base64,
            }
        ],
        "warning_info": "",
        "exception_info": "",
    }

    bundle_b64 = ReportWord().convert_from_final_result_to_bundle_base64(final_result)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(bundle_b64))) as zip_file:
        names = set(zip_file.namelist())
        docx_bytes = zip_file.read("report_bundle/report.docx")

    assert "report_bundle/report.docx" in names
    assert "report_bundle/charts/chart_0.png" in names
    assert docx_bytes.startswith(b"PK")
