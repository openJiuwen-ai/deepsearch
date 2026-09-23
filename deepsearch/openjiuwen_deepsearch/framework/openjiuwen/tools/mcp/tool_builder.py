# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import json
import logging
import re

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction

logger = logging.getLogger(__name__)

MCP_TOOL_NAME_PREFIX = "mcp"


def _clean_name_segment(segment: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", segment)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "mcp_tool"


def _sanitize_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """生成不冲突的 tool 名：mcp__{server}__{tool}"""
    return f"{MCP_TOOL_NAME_PREFIX}__{_clean_name_segment(server_name)}__{_clean_name_segment(tool_name)}"


def _extract_text_content(result) -> str:
    """从 CallToolResult.content 提取所有 TextContent.text 拼接。"""
    parts = []
    for block in (result.content or []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


async def build_mcp_local_functions(client, server_name: str) -> list:
    """连接后的 McpClient → list_tools → 包装成 LocalFunction 列表。"""
    tools = await client.list_tools()
    local_functions = []
    for tool in tools:
        sanitized_name = _sanitize_mcp_tool_name(server_name, tool.name)
        card = ToolCard(
            id=sanitized_name,
            name=sanitized_name,
            description=tool.description or f"MCP tool {tool.name} from server {server_name}",
            input_params=tool.inputSchema if isinstance(tool.inputSchema, dict) else {
                "type": "object",
                "properties": {},
            },
        )

        async def _invoke(_client=client, _tool_name=tool.name, **kwargs):
            result = await _client.call_tool(_tool_name, kwargs)
            if result.is_error:
                error_text = _extract_text_content(result) or "MCP tool execution failed"
                logger.warning(
                    "MCP tool '%s' on server '%s' returned error: %s",
                    _tool_name, server_name, error_text,
                )
                return {"error": error_text}
            text = _extract_text_content(result)
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"mcp_raw_output": text}

        local_functions.append(LocalFunction(card=card, func=_invoke))
    return local_functions
