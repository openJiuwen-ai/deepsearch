"""Test end-to-end report styling orchestration."""

import base64
import asyncio
import io
import logging
import zipfile
from unittest.mock import AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.report_style import service


FINAL_RESULT = {
    "response_content": "# 标题\n\n# 摘要\n\n这是完整摘要。\n\n# 正文\n\n报告正文。",
    "infer_messages": [],
    "chart_messages": [],
}
LLM = {"model_name": "style-model", "model": object()}


def _read_report_html(convert_content: str) -> str:
    """Read the styled HTML document from an exported report ZIP.

    Args:
        convert_content: base64 编码的报告 ZIP bundle。

    Returns:
        str: bundle 内的 HTML 报告文本。
    """
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(convert_content))) as archive:
        return archive.read("report_bundle/report.html").decode()


@pytest.mark.asyncio
async def test_stylize_report_injects_arbitrary_css_without_changing_markdown(monkeypatch):
    """原样注入模型 CSS，同时保持打包 Markdown 原文不变。

    Args:
        monkeypatch: pytest 的动态替换夹具。
    """
    generated_css = (
        "@media screen { .report-cover { display: none; width: 960px; } }\n"
        ".report-shell { width: 1440px; }\n"
        ".report-cover::before { content: 'replacement'; }"
    )
    monkeypatch.setattr(
        service,
        "ainvoke_llm_with_stats",
        AsyncMock(return_value={"content": generated_css}),
    )

    result = await service.stylize_report(FINAL_RESULT, LLM)

    with zipfile.ZipFile(io.BytesIO(base64.b64decode(result.convert_content))) as archive:
        assert archive.read("report_bundle/report.md").decode() == FINAL_RESULT["response_content"]
        html = archive.read("report_bundle/report.html").decode()
    assert 'id="report-style-generated"' in html
    assert generated_css in html
    assert result.style_applied is True
    assert result.style_status == "applied"


@pytest.mark.asyncio
async def test_stylize_report_logs_css_lengths_without_css_content(monkeypatch, caplog):
    """成功样式化仅记录 CSS 长度，不记录完整模型响应。

    Args:
        monkeypatch: pytest 的动态替换夹具。
        caplog: pytest 日志捕获夹具。
    """
    css_marker = "UNIQUE_CSS_CONTENT_MUST_NOT_BE_LOGGED"
    generated_css = f".report-cover {{ color: #123456; }} /* {css_marker} */"
    monkeypatch.setattr(
        service,
        "ainvoke_llm_with_stats",
        AsyncMock(return_value={"content": generated_css}),
    )

    with caplog.at_level(logging.INFO, logger=service.__name__):
        result = await service.stylize_report(FINAL_RESULT, LLM)

    assert result.style_status == "applied"
    assert f"raw_css_length={len(generated_css)}" in caplog.text
    assert f"normalized_css_length={len(generated_css)}" in caplog.text
    assert css_marker not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "llm_result",
    [RuntimeError("model unavailable"), asyncio.TimeoutError(), {"content": ""}, {"content": None}],
)
async def test_stylize_report_falls_back_when_style_generation_is_unusable(monkeypatch, llm_result):
    """模型失败、空 CSS 或非字符串 CSS 时返回基础报告。

    Args:
        monkeypatch: pytest 的动态替换夹具。
        llm_result: 模型异常或不可注入的 CSS 返回值。
    """
    llm_call = AsyncMock()
    if isinstance(llm_result, Exception):
        llm_call.side_effect = llm_result
    else:
        llm_call.return_value = llm_result
    monkeypatch.setattr(service, "ainvoke_llm_with_stats", llm_call)

    result = await service.stylize_report(FINAL_RESULT, LLM)

    html = _read_report_html(result.convert_content)
    assert "报告正文。" in html
    assert 'id="report-style-generated"' not in html
    assert result.style_applied is False
    assert result.style_status == "fallback"


@pytest.mark.asyncio
async def test_stylize_report_does_not_apply_service_level_timeout(monkeypatch):
    """样式服务不额外限制 LLM 调用的墙钟时间。

    Args:
        monkeypatch: pytest 的动态替换夹具。
    """
    monkeypatch.setattr(
        service,
        "ainvoke_llm_with_stats",
        AsyncMock(return_value={"content": "h1 { color: #123456; }"}),
    )
    async def fail_if_service_uses_wait_for(awaitable, timeout):
        """在样式服务使用额外超时时间时使测试失败。

        Args:
            awaitable: 需要等待的模型协程。
            timeout: 算法传入的超时时间。

        Raises:
            AssertionError: 样式服务调用 `asyncio.wait_for` 时抛出。
        """
        awaitable.close()
        raise AssertionError(f"unexpected service timeout: {timeout}")

    monkeypatch.setattr(asyncio, "wait_for", fail_if_service_uses_wait_for)

    result = await service.stylize_report(FINAL_RESULT, LLM)

    assert result.style_applied is True


@pytest.mark.asyncio
async def test_stylize_report_logs_raw_and_normalized_lengths_when_css_injection_fails(monkeypatch, caplog):
    """注入失败时分别记录原始和规整后的 CSS 长度。

    Args:
        monkeypatch: pytest 的动态替换夹具。
        caplog: pytest 日志捕获夹具。
    """
    normalized_css = "h1 { color: #123456; }"
    raw_css = f"```css\n{normalized_css}\n```"
    monkeypatch.setattr(service, "ainvoke_llm_with_stats", AsyncMock(return_value={"content": raw_css}))
    monkeypatch.setattr(service, "inject_css", lambda _html, _css: (_ for _ in ()).throw(ValueError("invalid HTML")))

    with caplog.at_level(logging.INFO, logger=service.__name__):
        result = await service.stylize_report(FINAL_RESULT, LLM)

    assert 'id="report-style-generated"' not in _read_report_html(result.convert_content)
    assert result.style_applied is False
    assert result.style_status == "fallback"
    warning_records = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warning_records) == 1
    assert f"raw_css_length={len(raw_css)}" in warning_records[0].getMessage()
    assert f"normalized_css_length={len(normalized_css)}" in warning_records[0].getMessage()


@pytest.mark.asyncio
async def test_stylize_report_logs_compact_fallback_diagnostics(monkeypatch, caplog):
    """回退 WARNING 仅记录阶段、异常类型和 CSS 长度。

    Args:
        monkeypatch: pytest 的动态替换夹具。
        caplog: pytest 日志捕获夹具。
    """
    css_marker = "UNIQUE_FALLBACK_CSS_CONTENT_MUST_NOT_BE_LOGGED"
    generated_css = f".report-cover {{ color: #123456; }} /* {css_marker} */"
    monkeypatch.setattr(service, "ainvoke_llm_with_stats", AsyncMock(return_value={"content": generated_css}))
    monkeypatch.setattr(
        service,
        "normalize_css_output",
        lambda _css: (_ for _ in ()).throw(ValueError("invalid CSS")),
    )

    with caplog.at_level(logging.INFO, logger=service.__name__):
        result = await service.stylize_report(FINAL_RESULT, LLM)

    assert result.style_status == "fallback"
    warning_records = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warning_records) == 1
    assert "phase=normalize_css_output" in warning_records[0].getMessage()
    assert "reason=ValueError" in warning_records[0].getMessage()
    assert f"raw_css_length={len(generated_css)}" in warning_records[0].getMessage()
    assert "normalized_css_length=None" in warning_records[0].getMessage()
    assert warning_records[0].exc_info is None
    assert css_marker not in caplog.text
