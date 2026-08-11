# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""测试通用报告导出服务。"""

from __future__ import annotations

import base64
import io
import zipfile

from PIL import Image
import pytest


def _png_base64() -> str:
    """生成测试使用的最小 PNG Base64。

    Returns:
        有效 PNG 文件的 Base64 文本。
    """
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_export_report_html_inlines_mermaid_and_vlm_png() -> None:
    """HTML bundle 应内嵌两类图表并保留原 VLM 资源文件。"""
    from openjiuwen_deepsearch.algorithm.report_export.service import export_report

    final_result = {
        "response_content": (
            "# 报告\n\n"
            "```mermaid\n"
            "xychart-beta\n"
            '    x-axis ["收入", "利润"]\n'
            '    y-axis "亿元" 0 --> 60\n'
            "    bar [48, 21]\n"
            "```\n\n"
            "![VLM 图](#insertChart:chart_1)"
        ),
        "infer_messages": [],
        "chart_messages": [
            {"chart_id": "chart_1", "chart_title": "VLM 图", "base64": _png_base64()}
        ],
    }

    result = await export_report(final_result, "html")
    assert result.style_applied is False
    assert result.style_status == "not_requested"
    bundle_bytes = base64.b64decode(result.convert_content)
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as archive:
        html_text = archive.read("report_bundle/report.html").decode("utf-8")
        chart_bytes = archive.read("report_bundle/charts/chart_1.png")

    assert "<svg" in html_text
    assert 'src="data:image/png;base64,' in html_text
    assert chart_bytes == base64.b64decode(final_result["chart_messages"][0]["base64"])


@pytest.mark.asyncio
async def test_export_report_docx_embeds_mermaid_png_without_debug_assets() -> None:
    """DOCX 应嵌入内存 Mermaid PNG，bundle 不应包含 CLI 调试产物。"""
    from openjiuwen_deepsearch.algorithm.report_export.service import export_report

    final_result = {
        "response_content": (
            "# 报告\n\n"
            "```mermaid\n"
            "---\n"
            "config:\n"
            "    showDataLabel: true\n"
            "---\n"
            "xychart-beta\n"
            '    x-axis ["收入", "利润"]\n'
            '    y-axis "亿元" 0 --> 60\n'
            "    line [48, 21]\n"
            "```"
        ),
        "infer_messages": [],
        "chart_messages": [],
    }

    result = await export_report(final_result, "docx", enable_html_styling=True)
    assert result.style_applied is False
    assert result.style_status == "not_supported"
    bundle_bytes = base64.b64decode(result.convert_content)
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as bundle:
        names = bundle.namelist()
        docx_bytes = bundle.read("report_bundle/report.docx")

    assert not any(name.endswith((".mmd", ".error.txt")) for name in names)
    assert not any("mermaid" in name and name.endswith(".png") for name in names)
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as document:
        media_names = [name for name in document.namelist() if name.startswith("word/media/")]
        assert media_names
        image_bytes = document.read(media_names[0])
    with Image.open(io.BytesIO(image_bytes)) as image:
        assert image.format == "PNG"
        assert image.width > 0
