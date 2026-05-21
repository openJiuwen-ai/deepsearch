import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import BaseModel

from openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api import (
    SearchResultApiWrapper,
    build_runtime_api_search_payload,
    build_runtime_api_tools,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.api_wrapper import build_api_wrapper


class DemoResponse(BaseModel):
    result: str


@pytest.fixture(autouse=True)
def bypass_runtime_url_validation(monkeypatch):
    monkeypatch.setattr(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.validate_runtime_request_url",
        lambda url: None,
    )


async def _make_async_iter(chunks: list):
    """Helper function to create an async iterator from a list of chunks."""
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_runtime_api_tool_splits_request_parts():
    tools = build_runtime_api_tools([
        {
            "tool_id": "tool-1",
            "name": "weather_tool",
            "description": "Weather tool",
            "path": "/weather",
            "http_method": "post",
            "base_url": "https://example.com",
            "headers": [{"name": "x-plugin", "value": "plugin-token"}],
            "request_params": [
                {"name": "authorization", "send_method": "header", "required": True},
                {"name": "city", "send_method": "query", "required": True},
                {"name": "unit", "send_method": "body", "required": False, "default_value": "c"},
            ],
        }
    ])
    tool = tools[0]

    # Mock response for stream mode
    mock_response = Mock()
    mock_response.headers = {}  # No content-length header
    mock_response.encoding = "utf-8"
    mock_response.raise_for_status = Mock()
    # Mock aiter_bytes to return JSON data
    json_data = json.dumps({"code": 0, "message": "ok", "data": {"result": "sunny"}}).encode("utf-8")
    mock_response.aiter_bytes = Mock(return_value=_make_async_iter([json_data]))

    # Mock stream context manager - use Mock() not AsyncMock() since it's the context manager object itself
    mock_stream_cm = Mock()
    mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_cm.__aexit__ = AsyncMock(return_value=None)

    # Mock client - stream() returns context manager directly (no await needed after fix)
    mock_client = Mock()
    mock_client.stream = Mock(return_value=mock_stream_cm)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.httpx.AsyncClient",
               return_value=mock_client):
        result = await tool.invoke({
            "authorization": "Bearer token",
            "city": "Shanghai",
            "unit": "c",
        })

    mock_client.stream.assert_called_once_with(
        method="POST",
        url="https://example.com/weather",
        headers={"x-plugin": "plugin-token", "authorization": "Bearer token"},
        params={"city": "Shanghai"},
        json={"unit": "c"},
    )
    assert result["data"]["result"] == "sunny"


@pytest.mark.asyncio
async def test_runtime_api_tool_parses_response_model():
    tools = build_runtime_api_tools([
        {
            "tool_id": "tool-1",
            "name": "demo_tool",
            "description": "Demo tool",
            "path": "https://example.com/demo",
            "http_method": "get",
            "request_params": [
                {"name": "keyword", "send_method": "query", "required": True},
            ],
        }
    ], response_model=DemoResponse)
    tool = tools[0]

    # Mock response for stream mode
    mock_response = Mock()
    mock_response.headers = {}
    mock_response.encoding = "utf-8"
    mock_response.raise_for_status = Mock()
    json_data = json.dumps({"code": 0, "message": "ok", "data": {"result": "done"}}).encode("utf-8")
    mock_response.aiter_bytes = Mock(return_value=_make_async_iter([json_data]))

    mock_stream_cm = Mock()
    mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_cm.__aexit__ = AsyncMock(return_value=None)

    mock_client = Mock()
    mock_client.stream = Mock(return_value=mock_stream_cm)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.httpx.AsyncClient",
               return_value=mock_client):
        result = await tool.invoke({"keyword": "demo"})

    assert isinstance(result, DemoResponse)
    assert result.result == "done"


def test_search_result_api_wrapper_normalizes_common_payload():
    wrapper = SearchResultApiWrapper()

    result = wrapper.wrap({
        "items": [
            {
                "name": "Demo title",
                "link": "https://example.com/demo",
                "snippet": "Demo content",
            }
        ]
    })

    assert result == {
        "search_engine": "runtime_api",
        "search_results": [
            {
                "title": "Demo title",
                "url": "https://example.com/demo",
                "content": "Demo content",
            }
        ],
    }


def test_build_runtime_api_search_payload_from_list():
    payload = build_runtime_api_search_payload([
        {"title": "Result", "link": "https://example.com", "snippet": "Content"}
    ])

    assert payload == {
        "search_engine": "runtime_api",
        "search_results": [
            {"title": "Result", "url": "https://example.com", "content": "Content"}
        ],
    }


def test_build_runtime_api_search_payload_from_items_key():
    payload = build_runtime_api_search_payload({
        "items": [
            {"title": "Result", "url": "https://example.com", "content": "Content"}
        ]
    })

    assert payload == {
        "search_engine": "runtime_api",
        "search_results": [
            {"title": "Result", "url": "https://example.com", "content": "Content"}
        ],
    }


def test_build_runtime_api_search_payload_from_single_dict():
    payload = build_runtime_api_search_payload({
        "name": "Single Result",
        "link": "https://example.com/single",
        "summary": "Single Content",
    })

    assert payload == {
        "search_engine": "runtime_api",
        "search_results": [
            {"title": "Single Result", "url": "https://example.com/single", "content": "Single Content"}
        ],
    }


def test_build_runtime_api_search_payload_returns_none_for_plain_dict():
    assert build_runtime_api_search_payload({"key": "value"}) is None


def test_build_api_wrapper_unknown_wrapper_logs_warning():
    with patch("openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.api_wrapper.logger") as mock_logger:
        assert build_api_wrapper("unknown_wrapper") is None

    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_runtime_api_tool_applies_search_result_wrapper():
    tools = build_runtime_api_tools([
        {
            "tool_id": "tool-1",
            "name": "wrapped_tool",
            "description": "Wrapped tool",
            "path": "https://example.com/demo",
            "http_method": "get",
            "response_wrapper": "search_result",
        }
    ])
    tool = tools[0]

    # Mock response for stream mode
    mock_response = Mock()
    mock_response.headers = {}
    mock_response.encoding = "utf-8"
    mock_response.raise_for_status = Mock()
    json_data = json.dumps({
        "search_results": [
            {
                "title": "Wrapped title",
                "url": "https://example.com/item",
                "content": "Wrapped content",
            }
        ],
    }).encode("utf-8")
    mock_response.aiter_bytes = Mock(return_value=_make_async_iter([json_data]))

    mock_stream_cm = Mock()
    mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_cm.__aexit__ = AsyncMock(return_value=None)

    mock_client = Mock()
    mock_client.stream = Mock(return_value=mock_stream_cm)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.httpx.AsyncClient",
               return_value=mock_client):
        result = await tool.invoke({})

    assert result == {
        "search_engine": "runtime_api",
        "search_results": [
            {
                "title": "Wrapped title",
                "url": "https://example.com/item",
                "content": "Wrapped content",
            }
        ],
    }


@pytest.mark.asyncio
async def test_runtime_api_tool_ignores_wrapper_when_response_model_present():
    tools = build_runtime_api_tools([
        {
            "tool_id": "tool-1",
            "name": "wrapped_model_tool",
            "description": "Wrapped model tool",
            "path": "https://example.com/demo",
            "http_method": "get",
            "response_wrapper": "search_result",
        }
    ], response_model=DemoResponse)
    tool = tools[0]

    # Mock response for stream mode
    mock_response = Mock()
    mock_response.headers = {}
    mock_response.encoding = "utf-8"
    mock_response.raise_for_status = Mock()
    json_data = json.dumps({
        "code": 0,
        "message": "ok",
        "data": {
            "result": "done",
            "items": [
                {
                    "title": "Wrapped title",
                    "url": "https://example.com/item",
                    "content": "Wrapped content",
                }
            ],
        },
    }).encode("utf-8")
    mock_response.aiter_bytes = Mock(return_value=_make_async_iter([json_data]))

    mock_stream_cm = Mock()
    mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_cm.__aexit__ = AsyncMock(return_value=None)

    mock_client = Mock()
    mock_client.stream = Mock(return_value=mock_stream_cm)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.httpx.AsyncClient",
               return_value=mock_client):
        result = await tool.invoke({})

    assert isinstance(result, DemoResponse)
    assert result.result == "done"
