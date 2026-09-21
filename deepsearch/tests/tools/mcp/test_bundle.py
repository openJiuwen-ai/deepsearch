# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.bundle import McpToolBundle


@pytest.mark.asyncio
async def test_bundle_connect_and_build_tools_classifies_by_type():
    bundle = McpToolBundle()
    servers = [
        {"server_name": "srv1", "server_url": "https://mcp1.com/sse", "transport_type": "sse", "type": "search"},
        {"server_name": "srv2", "server_url": "https://mcp2.com/sse", "transport_type": "sse", "type": "search"},
    ]
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.bundle.McpClient"
    ) as mock_client_cls, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.bundle.build_mcp_local_functions"
    ) as mock_build:
        mock_client1 = AsyncMock()
        mock_client1.connect = AsyncMock()
        mock_client1.close = AsyncMock()
        mock_client2 = AsyncMock()
        mock_client2.connect = AsyncMock()
        mock_client2.close = AsyncMock()
        mock_client_cls.side_effect = [mock_client1, mock_client2]

        mock_build.side_effect = [
            [SimpleNamespace(card=SimpleNamespace(name="tool1"))],
            [SimpleNamespace(card=SimpleNamespace(name="tool2"))],
        ]

        await bundle.connect_and_build_tools(servers)

    assert len(bundle.get_tools_by_type("search")) == 2
    assert bundle.get_tools_by_type("other") == []


@pytest.mark.asyncio
async def test_bundle_single_server_failure_does_not_block():
    bundle = McpToolBundle()
    servers = [
        {"server_name": "fail-srv", "server_url": "https://fail.com/sse", "transport_type": "sse", "type": "search"},
        {"server_name": "ok-srv", "server_url": "https://ok.com/sse", "transport_type": "sse", "type": "search"},
    ]
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.bundle.McpClient"
    ) as mock_client_cls, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.bundle.build_mcp_local_functions"
    ) as mock_build:
        mock_client1 = AsyncMock()
        mock_client1.connect = AsyncMock(side_effect=Exception("connection failed"))
        mock_client1.close = AsyncMock()
        mock_client2 = AsyncMock()
        mock_client2.connect = AsyncMock()
        mock_client2.close = AsyncMock()
        mock_client_cls.side_effect = [mock_client1, mock_client2]

        mock_build.return_value = [SimpleNamespace(card=SimpleNamespace(name="tool2"))]

        await bundle.connect_and_build_tools(servers)

    assert len(bundle.get_tools_by_type("search")) == 1


@pytest.mark.asyncio
async def test_bundle_close_all_clears_state():
    bundle = McpToolBundle()
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    bundle._clients = [mock_client]
    bundle._tools_by_type = {"search": ["tool"]}
    await bundle.close_all()
    assert bundle._clients == []
    assert bundle._tools_by_type == {}