# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Build export workspaces and ZIP bundles for report conversion."""

from __future__ import annotations

import base64
import logging
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from openjiuwen_deepsearch.algorithm.report_export.conversion_utils import (
    strip_internal_citation_markers,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode

logger = logging.getLogger(__name__)


INFERENCE_LINK_RE = re.compile(r"\[([^\]]+)\]\(#inference:(\d+)\)")
CHART_PLACEHOLDER_RE = re.compile(r"\(#insertChart:([^)]+)\)")
SAFE_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(slots=True)
class ReportBundle:
    """Describe the assembled on-disk report export workspace.

    Attributes:
        root_dir (Path): 导出工作目录根路径。
        markdown_path (Path): 中间 Markdown 文件路径。
    """

    root_dir: Path
    markdown_path: Path


def _decode_base64_payload(payload: str, field_name: str) -> bytes:
    """Decode a base64 payload and raise a field-specific error on failure.

    Args:
        payload: base64 编码的字符串内容。
        field_name: 用于错误提示的字段名。

    Returns:
        bytes: 解码后的二进制内容。

    Raises:
        CustomValueException: base64 内容非法时抛出。
    """
    try:
        return base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            f"invalid base64 payload for {field_name}",
        ) from exc


def _validate_bundle_id(raw_value: object, field_name: str) -> str:
    """Validate a bundle asset identifier before using it in file paths.

    Args:
        raw_value: 原始 ID 值。
        field_name: 用于报错的字段名。

    Returns:
        str: 清理后的安全 ID。

    Raises:
        CustomValueException: ID 包含路径分隔符或其他不安全字符时抛出。
    """
    value = "" if raw_value is None else str(raw_value).strip()
    if not value:
        return ""
    if not SAFE_BUNDLE_ID_RE.fullmatch(value):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            f"invalid {field_name}: only letters, numbers, underscores, and hyphens are allowed",
        )
    return value


def _validate_message_list(final_result: dict, field_name: str) -> list[dict]:
    """Validate that a final_result message field is a list of dictionaries.

    Args:
        final_result: 工作流输出的 final_result 字典。
        field_name: 需要校验的字段名。

    Returns:
        list[dict]: 校验通过后的消息列表。

    Raises:
        CustomValueException: 字段不是列表或列表成员不是字典时抛出。
    """
    messages = final_result.get(field_name, [])
    if messages is None:
        return []
    if not isinstance(messages, list):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            f"{field_name} must be a list",
        )
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                f"{field_name}[{index}] must be a dictionary",
            )
    return messages


def _validate_final_result(final_result: dict) -> tuple[str, list[dict], list[dict]]:
    """Validate the final_result structure required by bundle export.

    Args:
        final_result: 工作流输出的 final_result 字典。

    Returns:
        tuple[str, list[dict], list[dict]]: 报告正文、推理资源列表和图表资源列表。

    Raises:
        CustomValueException: final_result 结构不合法时抛出。
    """
    if not isinstance(final_result, dict):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            "final_result must be a dictionary",
        )

    response_content = final_result.get("response_content", "") or ""
    if not isinstance(response_content, str):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            "response_content must be a string",
        )
    if not response_content:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
            "response_content is empty",
        )

    infer_messages = _validate_message_list(final_result, "infer_messages")
    chart_messages = _validate_message_list(final_result, "chart_messages")
    return response_content, infer_messages, chart_messages


def _rewrite_inference_links(report_content: str) -> str:
    """Rewrite internal inference anchors to bundle-local HTML resources.

    Args:
        report_content: 原始 Markdown 报告正文。

    Returns:
        str: 重写推理图链接后的 Markdown 内容。
    """

    def _replace(match: re.Match[str]) -> str:
        label = match.group(1)
        infer_id = match.group(2)
        return f"[{label}](infer/inference_{infer_id}.html)"

    return INFERENCE_LINK_RE.sub(_replace, report_content)


def _rewrite_chart_placeholders(report_content: str, chart_messages: list[dict]) -> str:
    """Rewrite VLM chart placeholders into Markdown image references.

    Args:
        report_content: 原始 Markdown 报告正文。
        chart_messages: VLM 图资源描述列表。

    Returns:
        str: 替换图表占位符后的 Markdown 文本。
    """
    chart_index: dict[str, dict] = {}
    for item in chart_messages:
        chart_id = _validate_bundle_id(item.get("chart_id", ""), "chart_id")
        if chart_id:
            chart_index[chart_id] = item

    def _replace(match: re.Match[str]) -> str:
        chart_id = _validate_bundle_id(match.group(1), "chart placeholder")
        chart_info = chart_index.get(chart_id, {})
        label = (chart_info.get("chart_title", "") or "").strip() or chart_id
        return f"![{label}](charts/{chart_id}.png)"

    return CHART_PLACEHOLDER_RE.sub(_replace, report_content)


def build_report_bundle(final_result: dict, workspace: Path) -> ReportBundle:
    """Assemble report markdown and companion assets under a workspace.

    Args:
        final_result: 工作流输出的 final_result 字典。
        workspace: 当前导出任务的临时工作目录。

    Returns:
        ReportBundle: 已组装完成的导出工作目录信息。

    Raises:
        CustomValueException: `response_content` 为空或资源内容非法时抛出。
    """
    start_time = time.perf_counter()
    response_content, infer_messages, chart_messages = _validate_final_result(final_result)
    logger.debug(
        "Building report bundle workspace=%s infer_messages=%s chart_messages=%s response_length=%s",
        workspace,
        len(infer_messages),
        len(chart_messages),
        len(response_content),
    )

    root_dir = workspace / "report_bundle"
    infer_dir = root_dir / "infer"
    charts_dir = root_dir / "charts"
    for path in (root_dir, infer_dir, charts_dir):
        path.mkdir(parents=True, exist_ok=True)

    for item in infer_messages:
        infer_id = _validate_bundle_id(item.get("id", ""), "infer_messages[].id")
        html_base64 = item.get("html_base64", "")
        if not infer_id or not html_base64:
            continue
        html_bytes = _decode_base64_payload(html_base64, f"infer_messages[{infer_id}].html_base64")
        (infer_dir / f"inference_{infer_id}.html").write_bytes(html_bytes)

    for item in chart_messages:
        chart_id = _validate_bundle_id(item.get("chart_id", ""), "chart_messages[].chart_id")
        chart_base64 = item.get("base64", "")
        if not chart_id or not chart_base64:
            continue
        chart_bytes = _decode_base64_payload(chart_base64, f"chart_messages[{chart_id}].base64")
        (charts_dir / f"{chart_id}.png").write_bytes(chart_bytes)

    markdown_text = strip_internal_citation_markers(response_content)
    markdown_text = _rewrite_inference_links(markdown_text)
    markdown_text = _rewrite_chart_placeholders(markdown_text, chart_messages)
    markdown_path = root_dir / "report.md"
    markdown_path.write_bytes(markdown_text.encode("utf-8"))
    logger.info(
        "Built report bundle infer_assets=%s chart_assets=%s duration_ms=%.2f",
        len(list(infer_dir.glob("*.html"))),
        len(list(charts_dir.glob("*.png"))),
        (time.perf_counter() - start_time) * 1000,
    )
    return ReportBundle(root_dir=root_dir, markdown_path=markdown_path)


def pack_bundle_to_base64(bundle_root: Path) -> str:
    """Pack a prepared bundle directory into a base64-encoded ZIP archive.

    Args:
        bundle_root: 需要打包的 `report_bundle` 根目录。

    Returns:
        str: base64 编码后的 ZIP 文件内容。
    """
    start_time = time.perf_counter()
    zip_path = bundle_root.parent / "report_bundle.zip"
    file_count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in bundle_root.rglob("*"):
            if file_path.is_file():
                zip_file.write(file_path, file_path.relative_to(bundle_root.parent))
                file_count += 1

    encoded = base64.b64encode(zip_path.read_bytes()).decode("utf-8")
    logger.info(
        "Packed report bundle file_count=%s zip_bytes=%s duration_ms=%.2f",
        file_count,
        zip_path.stat().st_size,
        (time.perf_counter() - start_time) * 1000,
    )
    return encoded
