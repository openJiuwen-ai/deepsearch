# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp.types import CallToolResult, TextContent

from openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.tool_builder import (
    _sanitize_mcp_tool_name,
    _extract_text_content,
    build_mcp_local_functions,
)


def _make_call_tool_result(text: str, is_error: bool = False) -> CallToolResult:
    """用真实 MCP SDK 的 CallToolResult 构造结果,避免 SimpleNamespace 掩盖字段名 bug。"""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        isError=is_error,
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
    mock_client.call_tool.return_value = _make_call_tool_result(
        '{"results": [{"title": "t", "url": "u", "content": "c"}]}', is_error=False
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
    mock_client.call_tool.return_value = _make_call_tool_result("plain text result", is_error=False)
    tools = await build_mcp_local_functions(mock_client, "srv")
    result = await tools[0].invoke({"query": "test"})
    assert result == {"mcp_raw_output": "plain text result"}


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
    mock_client.call_tool.return_value = _make_call_tool_result("execution failed", is_error=True)
    tools = await build_mcp_local_functions(mock_client, "srv")
    result = await tools[0].invoke({"query": "test"})
    assert result == {"error": "execution failed"}


@pytest.mark.asyncio
async def test_build_mcp_local_functions_disambiguates_name_collisions():
    """两个工具名清洗后碰撞(如 a.b 与 a b 均成 a_b)时,必须去重而非互相覆盖。

    回归问题7：tool_dict 后写覆盖前写,但 tool_list 仍含两项,导致不一致。
    """
    mock_client = AsyncMock()
    mock_client.list_tools.return_value = [
        SimpleNamespace(name="a.b", description="d1", inputSchema={"type": "object", "properties": {}}),
        SimpleNamespace(name="a b", description="d2", inputSchema={"type": "object", "properties": {}}),
    ]
    tools = await build_mcp_local_functions(mock_client, "srv")
    names = [t.card.name for t in tools]
    assert len(names) == 2, "两个碰撞工具必须都保留,不能互相覆盖"
    assert len(set(names)) == 2, "碰撞工具名必须被去重为不同名称"
    assert all(n.startswith("mcp__srv__a_b") for n in names)


@pytest.mark.asyncio
async def test_build_mcp_local_functions_collision_does_not_overwrite_existing_suffix():
    """碰撞修复生成的后缀名不得与已存在的真实工具名冲突。

    回归:原实现用 len(seen_names) 作序号且不二次校验,处理 a_b / a_b_2 / a.b
    时第三项碰撞生成 a_b_2,与第二项真实名 a_b_2 撞名,导致覆盖。
    """
    mock_client = AsyncMock()
    mock_client.list_tools.return_value = [
        SimpleNamespace(name="a_b", description="d1", inputSchema={"type": "object", "properties": {}}),
        SimpleNamespace(name="a_b_2", description="d2", inputSchema={"type": "object", "properties": {}}),
        SimpleNamespace(name="a.b", description="d3", inputSchema={"type": "object", "properties": {}}),
    ]
    tools = await build_mcp_local_functions(mock_client, "srv")
    names = [t.card.name for t in tools]
    assert len(names) == 3, "三个工具必须都保留"
    assert len(set(names)) == 3, f"三个工具名必须互不相同,实际: {names}"
    assert all(n.startswith("mcp__srv__a_b") for n in names)
