#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import json
import logging
import re
from typing import Any, Iterable
from urllib.parse import urljoin

import httpx
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from pydantic import BaseModel

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.api_wrapper import (
    SearchResultApiWrapper,
    build_api_wrapper,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.config import RuntimeApiToolConfig
from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_runtime_request_url

logger = logging.getLogger(__name__)

_PARAM_TYPE_JSON_SCHEMA: dict[int, dict[str, Any]] = {
    1: {"type": "string"},
    2: {"type": "integer"},
    3: {"type": "number"},
    4: {"type": "boolean"},
}

MAX_RUNTIME_API_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RUNTIME_API_JSON_DEPTH = 20
MAX_RUNTIME_API_JSON_CONTAINER_ITEMS = 1000


def _json_schema_property_for_param(param: Any) -> dict[str, Any]:
    param_schema = dict(_PARAM_TYPE_JSON_SCHEMA.get(param.param_type, {"type": "string"}))
    if param.description:
        param_schema["description"] = param.description
    return param_schema


def sanitize_tool_name(name: str, fallback: str = "runtime_api_tool") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", (name or "").strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"tool_{normalized}"
    return normalized


def _resolve_request_url(tool_config: RuntimeApiToolConfig) -> str:
    if tool_config.path.startswith("http://") or tool_config.path.startswith("https://"):
        resolved = tool_config.path
    elif not tool_config.base_url:
        resolved = tool_config.path
    else:
        base_url = tool_config.base_url.rstrip("/") + "/"
        path = tool_config.path.lstrip("/")
        resolved = urljoin(base_url, path)
    validate_runtime_request_url(resolved)
    return resolved


def _extract_response_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _runtime_api_response_error(message: str) -> CustomValueException:
    return CustomValueException(
        StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
        StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(e=message),
    )


def _check_runtime_api_content_length(response: httpx.Response) -> None:
    content_length = response.headers.get("content-length")
    if not content_length:
        return
    try:
        declared_size = int(content_length)
    except ValueError:
        return
    if declared_size > MAX_RUNTIME_API_RESPONSE_BYTES:
        raise _runtime_api_response_error(
            "runtime api response exceeds max size "
            f"{MAX_RUNTIME_API_RESPONSE_BYTES} bytes"
        )


async def _read_limited_json_response(response: httpx.Response) -> Any:
    # 先调用raise_for_status检查HTTP状态码
    response.raise_for_status()
    _check_runtime_api_content_length(response)

    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > MAX_RUNTIME_API_RESPONSE_BYTES:
            raise _runtime_api_response_error(
                "runtime api response exceeds max size "
                f"{MAX_RUNTIME_API_RESPONSE_BYTES} bytes"
            )
        body.extend(chunk)

    encoding = response.encoding or "utf-8"
    payload = json.loads(bytes(body).decode(encoding))
    _validate_runtime_api_json_limits(payload)
    return payload


def _validate_runtime_api_json_limits(payload: Any, depth: int = 0) -> None:
    if depth > MAX_RUNTIME_API_JSON_DEPTH:
        raise _runtime_api_response_error(
            "runtime api response JSON exceeds max depth "
            f"{MAX_RUNTIME_API_JSON_DEPTH}"
        )

    if isinstance(payload, dict):
        if len(payload) > MAX_RUNTIME_API_JSON_CONTAINER_ITEMS:
            raise _runtime_api_response_error(
                "runtime api response JSON object exceeds max item count "
                f"{MAX_RUNTIME_API_JSON_CONTAINER_ITEMS}"
            )
        for value in payload.values():
            _validate_runtime_api_json_limits(value, depth + 1)
    elif isinstance(payload, list):
        if len(payload) > MAX_RUNTIME_API_JSON_CONTAINER_ITEMS:
            raise _runtime_api_response_error(
                "runtime api response JSON array exceeds max item count "
                f"{MAX_RUNTIME_API_JSON_CONTAINER_ITEMS}"
            )
        for value in payload:
            _validate_runtime_api_json_limits(value, depth + 1)


def _collect_request_parts(tool_config: RuntimeApiToolConfig, args: dict[str, Any]) -> tuple[dict, dict, dict]:
    headers = {header.name: header.value for header in tool_config.headers if header.name}
    query_params: dict[str, Any] = {}
    body_params: dict[str, Any] = {}

    for param in tool_config.request_params:
        value = args.get(param.name)
        if value in (None, ""):
            value = param.default_value
        if value in (None, "") and param.required and param.send_method != "none":
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(
                    e=f"runtime api tool param '{param.name}' is required"
                ),
            )
        if value in (None, ""):
            continue

        if param.send_method == "header":
            headers[param.name] = value
        elif param.send_method == "query":
            query_params[param.name] = value
        elif param.send_method == "body":
            body_params[param.name] = value
        elif param.send_method == "none":
            body_params[param.name] = value

    return headers, query_params, body_params


def create_runtime_api_tool(
        tool_config: RuntimeApiToolConfig | dict,
        response_model: type[BaseModel] | None = None,
) -> LocalFunction:
    if isinstance(tool_config, dict):
        tool_config = RuntimeApiToolConfig.model_validate(tool_config)
    response_wrapper = build_api_wrapper(tool_config.response_wrapper)

    card = ToolCard(
        id=tool_config.tool_id or tool_config.name,
        name=tool_config.name,
        description=tool_config.description,
        input_params={
            "type": "object",
            "properties": {
                param.name: _json_schema_property_for_param(param)
                for param in tool_config.request_params
                if param.name
            },
            "required": [
                param.name
                for param in tool_config.request_params
                if param.name and param.required
            ],
        },
    )

    async def _invoke(**kwargs):
        url = _resolve_request_url(tool_config)
        headers, query_params, body_params = _collect_request_parts(tool_config, kwargs)
        request_kwargs = {
            "method": tool_config.http_method.upper(),
            "url": url,
            "headers": headers,
        }
        if query_params:
            request_kwargs["params"] = query_params
        if body_params:
            request_kwargs["json"] = body_params

        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(**request_kwargs) as response:
                payload = await _read_limited_json_response(response)

        # Collector-like paths do not use response_model, so only those paths
        # should receive wrapper-normalized search payloads.
        if response_wrapper is not None and response_model is None:
            payload = response_wrapper.wrap(_extract_response_data(payload))

        if response_model is not None:
            return response_model.model_validate(_extract_response_data(payload))
        return payload

    return LocalFunction(card=card, func=_invoke)


def build_runtime_api_tools(
        tool_configs: Iterable[RuntimeApiToolConfig | dict] | None,
        response_model: type[BaseModel] | None = None,
) -> list[LocalFunction]:
    if not tool_configs:
        return []
    return [
        create_runtime_api_tool(tool_config, response_model=response_model)
        for tool_config in tool_configs
    ]


def merge_runtime_api_tools(default_tools: Iterable[LocalFunction], runtime_tools: Iterable[LocalFunction]) -> list[
    LocalFunction]:
    merged_tools: dict[str, LocalFunction] = {
        sanitize_tool_name(tool.card.name): tool for tool in default_tools
    }
    for tool in runtime_tools:
        tool_name = sanitize_tool_name(tool.card.name)
        if tool_name in merged_tools:
            logger.warning("[merge_runtime_api_tools] Duplicate tool name found, keep existing tool: %s", tool_name)
            continue
        merged_tools[tool_name] = tool
    return list(merged_tools.values())


def is_runtime_api_search_items(tool_result: Any) -> bool:
    """Check whether runtime api result can be treated as search results."""
    return isinstance(tool_result, list) and all(isinstance(item, dict) for item in tool_result)


def build_runtime_api_search_payload(tool_result: Any) -> dict | None:
    """Build collector-compatible search payload from runtime api result."""
    normalized_payload = SearchResultApiWrapper().wrap(tool_result)
    if isinstance(normalized_payload, dict) and is_runtime_api_search_items(normalized_payload.get("search_results")):
        return normalized_payload
    return None
