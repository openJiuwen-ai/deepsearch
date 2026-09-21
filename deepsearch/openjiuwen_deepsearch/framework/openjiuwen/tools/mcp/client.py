# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)


class McpClient:
    """单个 MCP server 的连接封装。生命周期由调用方管理。"""

    def __init__(self, server_config: dict):
        self._url = server_config["server_url"]
        self._transport = server_config.get("transport_type", "streamable_http")
        self._headers = server_config.get("headers") or None
        self._timeout = server_config.get("timeout", 30.0)
        self._session: ClientSession | None = None
        self._cm_stack: AsyncExitStack | None = None

    async def connect(self) -> None:
        """建立连接并 initialize。"""
        stack = AsyncExitStack()
        if self._transport == "sse":
            transport_cm = sse_client(self._url, headers=self._headers, timeout=self._timeout)
        else:
            transport_cm = streamable_http_client(self._url, timeout=self._timeout)
        read, write = await stack.enter_async_context(transport_cm)
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session
        self._cm_stack = stack

    async def list_tools(self) -> list:
        """返回 MCP server 暴露的 Tool 列表。"""
        if not self._session:
            raise RuntimeError("McpClient not connected")
        result = await self._session.list_tools()
        return result.tools

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """调用指定 tool。"""
        if not self._session:
            raise RuntimeError("McpClient not connected")
        return await self._session.call_tool(name, arguments)

    async def close(self) -> None:
        """关闭连接。"""
        if self._cm_stack:
            await self._cm_stack.aclose()
        self._session = None
        self._cm_stack = None