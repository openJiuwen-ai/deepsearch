# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client import McpClient


@pytest.mark.asyncio
async def test_mcp_client_connect_initializes_session():
    client = McpClient({
        "server_url": "https://mcp.example.com/sse",
        "transport_type": "sse",
        "headers": None,
        "timeout": 30.0,
    })
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.sse_client"
    ) as mock_sse, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client.ClientSession"
    ) as mock_session_cls:
        mock_transport_cm = AsyncMock()
        mock_transport_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_transport_cm.__aexit__ = AsyncMock(return_value=None)
        mock_sse.return_value = mock_transport_cm

        mock_session_cm = AsyncMock()
        mock_session_cls.return_value = mock_session_cm
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session_cls.return_value)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)

        await client.connect()
        assert client._session is not None


@pytest.mark.asyncio
async def test_mcp_client_close_clears_session():
    client = McpClient({"server_url": "https://mcp.example.com/sse", "transport_type": "sse"})
    client._session = MagicMock()
    client._cm_stack = MagicMock()
    client._cm_stack.aclose = AsyncMock()
    await client.close()
    assert client._session is None
    assert client._cm_stack is None


@pytest.mark.asyncio
async def test_mcp_client_list_tools_raises_when_not_connected():
    client = McpClient({"server_url": "https://mcp.example.com/sse", "transport_type": "sse"})
    with pytest.raises(RuntimeError, match="not connected"):
        await client.list_tools()


@pytest.mark.asyncio
async def test_mcp_client_call_tool_raises_when_not_connected():
    client = McpClient({"server_url": "https://mcp.example.com/sse", "transport_type": "sse"})
    with pytest.raises(RuntimeError, match="not connected"):
        await client.call_tool("test", {})
