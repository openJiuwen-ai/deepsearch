# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging

from openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.client import McpClient
from openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.tool_builder import build_mcp_local_functions

logger = logging.getLogger(__name__)


class McpToolBundle:
    """管理多个 McpClient 的连接生命周期，产出按 type 分类的工具列表。"""

    def __init__(self):
        self._clients: list[McpClient] = []
        self._tools_by_type: dict[str, list] = {}

    async def connect_and_build_tools(self, mcp_servers: list[dict]) -> None:
        """连接所有 MCP server，list_tools，包装 LocalFunction，按 type 分类。
        单个 server 连接失败时记录日志跳过，不阻塞其他 server。
        """
        for server_config in mcp_servers:
            client = McpClient(server_config)
            try:
                await client.connect()
                tools = await build_mcp_local_functions(client, server_config["server_name"])
                server_type = server_config.get("type", "search")
                self._tools_by_type.setdefault(server_type, []).extend(tools)
                self._clients.append(client)
            except Exception as e:
                logger.warning(
                    "MCP server '%s' connect failed: %s",
                    server_config.get("server_name"), e,
                )
                await client.close()

    def get_tools_by_type(self, server_type: str) -> list:
        """获取指定 type 的工具列表。"""
        return self._tools_by_type.get(server_type, [])

    async def close_all(self) -> None:
        """关闭所有 MCP client 连接。"""
        for client in self._clients:
            try:
                await client.close()
            except Exception as e:
                logger.warning("MCP client close error: %s", e)
        self._clients.clear()
        self._tools_by_type.clear()