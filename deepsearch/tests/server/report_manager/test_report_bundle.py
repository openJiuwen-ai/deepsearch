import base64

import pytest

from openjiuwen_deepsearch.algorithm.report_export.report_bundle import build_report_bundle
from openjiuwen_deepsearch.common.exception import CustomValueException


def test_build_report_bundle_writes_infer_and_chart_assets(tmp_path):
    """Validate that report bundle assembly writes inference and chart files.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": "正文[结论](#inference:0)\n\n![图表](chart_0.png)",
        "infer_messages": [
            {
                "id": 0,
                "html_base64": base64.b64encode(b"<html>infer</html>").decode("utf-8"),
            }
        ],
        "chart_messages": [
            {
                "chart_id": "chart_0",
                "base64": base64.b64encode(b"fakepng").decode("utf-8"),
            }
        ],
        "warning_info": "",
        "exception_info": "",
    }

    bundle = build_report_bundle(final_result, tmp_path)

    assert bundle.markdown_path.exists()
    assert (bundle.root_dir / "infer" / "inference_0.html").exists()
    assert (bundle.root_dir / "charts" / "chart_0.png").exists()
    assert "infer/inference_0.html" in bundle.markdown_path.read_text(encoding="utf-8")


def test_build_report_bundle_rewrites_chart_placeholders(tmp_path):
    """Validate that VLM chart placeholders are rewritten to bundled image paths.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": "(#insertChart:chart_0)\n<font size=2>**图表标题**: 图表说明</font>",
        "infer_messages": [],
        "chart_messages": [
            {
                "chart_id": "chart_0",
                "chart_title": "图表标题",
                "base64": base64.b64encode(b"fakepng").decode("utf-8"),
            }
        ],
        "warning_info": "",
        "exception_info": "",
    }

    bundle = build_report_bundle(final_result, tmp_path)
    markdown_text = bundle.markdown_path.read_text(encoding="utf-8")

    assert "![图表标题](charts/chart_0.png)" in markdown_text
    assert "(#insertChart:chart_0)" not in markdown_text


def test_build_report_bundle_strips_internal_citation_markers(tmp_path):
    """Validate bundled report markdown hides internal citation control markers.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": (
            "正文[checked_citation:4][[5]](https://example.com/source)"
            "以及旧格式[citation:2]"
        ),
        "infer_messages": [],
        "chart_messages": [],
        "warning_info": "",
        "exception_info": "",
    }

    bundle = build_report_bundle(final_result, tmp_path)
    markdown_text = bundle.markdown_path.read_text(encoding="utf-8")

    assert "checked_citation" not in markdown_text
    assert "[citation:2]" not in markdown_text
    assert "[[5]](https://example.com/source)" in markdown_text


def test_build_report_bundle_raises_validation_exception_when_response_content_empty(tmp_path):
    """Validate empty response_content is treated as a validation error.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": "",
        "infer_messages": [],
        "chart_messages": [],
        "warning_info": "",
        "exception_info": "",
    }

    with pytest.raises(CustomValueException):
        build_report_bundle(final_result, tmp_path)


def test_build_report_bundle_rejects_html_response_content(tmp_path):
    """导出不能将 HTML 响应内容写成 Markdown 中间文件。"""
    final_result = {
        "response_content": "<!DOCTYPE html><html><body>Brief</body></html>",
        "response_content_type": "text/html",
        "infer_messages": [],
        "chart_messages": [],
    }

    with pytest.raises(CustomValueException, match="text/markdown"):
        build_report_bundle(final_result, tmp_path)


def test_build_report_bundle_raises_validation_exception_when_chart_base64_invalid(tmp_path):
    """Validate invalid chart payloads are surfaced as validation errors.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": "(#insertChart:chart_0)",
        "infer_messages": [],
        "chart_messages": [
            {
                "chart_id": "chart_0",
                "chart_title": "图表标题",
                "base64": "not-a-valid-base64$$$",
            }
        ],
        "warning_info": "",
        "exception_info": "",
    }

    with pytest.raises(CustomValueException):
        build_report_bundle(final_result, tmp_path)


def test_build_report_bundle_raises_validation_exception_when_chart_id_is_unsafe(tmp_path):
    """Validate chart asset identifiers reject path traversal characters.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": "(#insertChart:../../../tmp/escape)",
        "infer_messages": [],
        "chart_messages": [
            {
                "chart_id": "../../../tmp/escape",
                "chart_title": "图表标题",
                "base64": base64.b64encode(b"fakepng").decode("utf-8"),
            }
        ],
        "warning_info": "",
        "exception_info": "",
    }

    with pytest.raises(CustomValueException):
        build_report_bundle(final_result, tmp_path)


def test_build_report_bundle_raises_validation_exception_when_chart_placeholder_is_unsafe(tmp_path):
    """Validate chart placeholders reject unsafe path-like identifiers.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": "(#insertChart:../../../tmp/escape)",
        "infer_messages": [],
        "chart_messages": [],
        "warning_info": "",
        "exception_info": "",
    }

    with pytest.raises(CustomValueException):
        build_report_bundle(final_result, tmp_path)


def test_build_report_bundle_raises_validation_exception_when_chart_messages_is_not_a_list(tmp_path):
    """Validate malformed chart_messages containers are treated as validation errors.

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None.
    """
    final_result = {
        "response_content": "正文",
        "infer_messages": [],
        "chart_messages": "not-a-list",
        "warning_info": "",
        "exception_info": "",
    }

    with pytest.raises(CustomValueException):
        build_report_bundle(final_result, tmp_path)
