# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
# mcp>=1.24,<2.0: streamable_http_client 自 v1.24 引入; v2.0 迁移 httpx→httpx2,
# http_client 形参类型变为 httpx2.AsyncClient,故本文件经 httpx.AsyncClient 注入仅兼容 1.x。
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
        # 仅 streamable_http 分支自建;SDK 不管理外部传入 client 的生命周期
        # (mcp 1.29.0 streamable_http.py: "Only manage client lifecycle if we created it"),
        # 故需在 close()/异常路径显式 aclose,否则长驻服务下持续泄漏连接池与 FD。
        self._http_client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """建立连接并 initialize。失败时清理已进入的上下文,避免泄漏 transport 资源。"""
        stack = AsyncExitStack()
        http_client: httpx.AsyncClient | None = None
        try:
            if self._transport == "sse":
                transport_cm = sse_client(self._url, headers=self._headers, timeout=self._timeout)
                read, write = await stack.enter_async_context(transport_cm)
            else:
                # streamable_http_client 签名 (url, *, http_client=None, terminate_on_close=True)
                # 不接收 headers/timeout;经 httpx.AsyncClient 注入鉴权头与超时。
                http_client = httpx.AsyncClient(
                    headers=self._headers,
                    timeout=self._timeout,
                )
                transport_cm = streamable_http_client(self._url, http_client=http_client)
                read, write, _get_session_id = await stack.enter_async_context(transport_cm)
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._session = session
            self._cm_stack = stack
            self._http_client = http_client
        except BaseException:
            # initialize 或 transport 进入失败时,清理已 acquired 的资源;
            # 否则局部 stack 无人 aclose,后台连接/读循环泄漏,
            # 且 bundle 的 client.close() 因 _cm_stack is None 跳过,兜底无效。
            # http_client 同样需在此路径关闭:SDK 不管理外部 client,
            # stack.aclose() 不会触及它,否则 connect 失败也泄漏连接池。
            await stack.aclose()
            if http_client is not None:
                await http_client.aclose()
            raise

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
        if self._http_client is not None:
            # 显式关闭自建 httpx.AsyncClient:SDK 不管理外部传入 client 的生命周期,
            # _cm_stack.aclose() 不会触及它。try/except 防止单个 client 关闭失败
            # 阻断其余 client 的清理(bundle.close_all 逐个调用)。
            try:
                await self._http_client.aclose()
            except Exception as e:
                logger.warning("Failed to close httpx client: %s", e)
            self._http_client = None
