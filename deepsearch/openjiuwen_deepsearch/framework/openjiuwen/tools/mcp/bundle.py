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

        type 为 search 的工具按 inputSchema 是否仅需 query 二次分流：
        query-兼容归 search（可被 query-gen 直调），不兼容归 search_param
        （需独立 LLM 通道生成参数）。单个 server 连接失败时记录日志跳过。
        """
        for server_config in mcp_servers:
            client = McpClient(server_config)
            try:
                await client.connect()
                tools = await build_mcp_local_functions(client, server_config["server_name"])
                server_type = server_config.get("type", "search")
                if server_type == "search":
                    for tool in tools:
                        schema = getattr(getattr(tool, "card", None), "input_params", None)
                        bucket = "search" if is_query_compatible(schema) else "search_param"
                        self._tools_by_type.setdefault(bucket, []).append(tool)
                else:
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


def is_query_compatible(input_schema: dict | None) -> bool:
    """inputSchema 的必填字段是否仅 ⊆ {query} 且 query 接受字符串（可被 query-gen 直调）。

    无 required 或 required ⊆ {query} 且 query 属性为 string 类型时视为兼容：
    直调用 {query} 即可。存在 query 以外的必填字段或 query 非 string 类型视为不兼容，
    需独立 LLM 通道生成参数。
    """
    if not isinstance(input_schema, dict):
        return True
    required = input_schema.get("required")
    if not isinstance(required, list) or not required:
        return True
    if not all(field == "query" for field in required):
        return False
    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return True
    query_prop = properties.get("query")
    if not isinstance(query_prop, dict):
        return True
    query_type = query_prop.get("type")
    if isinstance(query_type, list):
        return "string" in query_type
    return query_type in (None, "string")
