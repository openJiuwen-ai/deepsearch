# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.tool_builder import (
    _sanitize_mcp_tool_name,
    _extract_text_content,
    build_mcp_local_functions,
)


def test_sanitize_mcp_tool_name_basic():
    name = _sanitize_mcp_tool_name("my-server", "search_tool")
    assert name == "mcp__my-server__search_tool"


def test_sanitize_mcp_tool_name_special_chars():
    name = _sanitize_mcp_tool_name("my server!", "tool.name")
    # Special chars replaced with _, collapsed
    assert "mcp__" in name
    assert " " not in name


def test_extract_text_content_concatenates_text_blocks():
    result = SimpleNamespace(
        content=[
            SimpleNamespace(text="hello "),
            SimpleNamespace(text="world"),
        ]
    )
    assert _extract_text_content(result) == "hello \nworld"


def test_extract_text_content_empty():
    result = SimpleNamespace(content=[])
    assert _extract_text_content(result) == ""


@pytest.mark.asyncio
async def test_build_mcp_local_functions_wraps_tools():
    mock_client = AsyncMock()
    mock_client.list_tools.return_value = [
        SimpleNamespace(
            name="search",
            description="Search documents",
            inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
        ),
    ]
    tools = await build_mcp_local_functions(mock_client, "test-server")
    assert len(tools) == 1
    assert tools[0].card.name == "mcp__test-server__search"
    assert "Search documents" in tools[0].card.description


@pytest.mark.asyncio
async def test_build_mcp_local_functions_invoke_parses_json():
    mock_client = AsyncMock()
    mock_client.list_tools.return_value = [
        SimpleNamespace(
            name="search",
            description="d",
            inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
        ),
    ]
    mock_client.call_tool.return_value = SimpleNamespace(
        is_error=False,
        content=[SimpleNamespace(text='{"results": [{"title": "t", "url": "u", "content": "c"}]}')],
    )
    tools = await build_mcp_local_functions(mock_client, "srv")
    result = await tools[0].invoke({"query": "test"})
    assert isinstance(result, dict)
    assert "results" in result


@pytest.mark.asyncio
async def test_build_mcp_local_functions_invoke_returns_content_on_non_json():
    mock_client = AsyncMock()
    mock_client.list_tools.return_value = [
        SimpleNamespace(
            name="search",
            description="d",
            inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
        ),
    ]
    mock_client.call_tool.return_value = SimpleNamespace(
        is_error=False,
        content=[SimpleNamespace(text="plain text result")],
    )
    tools = await build_mcp_local_functions(mock_client, "srv")
    result = await tools[0].invoke({"query": "test"})
    assert result == {"content": "plain text result"}


@pytest.mark.asyncio
async def test_build_mcp_local_functions_invoke_returns_error_dict():
    mock_client = AsyncMock()
    mock_client.list_tools.return_value = [
        SimpleNamespace(
            name="search",
            description="d",
            inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
        ),
    ]
    mock_client.call_tool.return_value = SimpleNamespace(
        is_error=True,
        content=[SimpleNamespace(text="execution failed")],
    )
    tools = await build_mcp_local_functions(mock_client, "srv")
    result = await tools[0].invoke({"query": "test"})
    assert result == {"error": "execution failed"}