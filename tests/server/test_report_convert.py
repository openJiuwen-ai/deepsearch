import base64
import binascii
import io
import logging
import zipfile

import pytest
from fastapi import HTTPException

from server.deepsearch.common.exception.exceptions import (
    ReportConvertExecutionException,
    ReportConvertValidationException,
)
from server.schemas.report import ReportConvertReq, ReportFormat
from server.deepsearch.core.manager.report_manager.report_processor import ReportHtml, ReportWord

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
