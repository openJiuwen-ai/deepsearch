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
    """连接后的 McpClient → list_tools → 包装成 LocalFunction 列表。

    不同原始工具名可能清洗成同一 sanitized_name（如 a.b 与 a b 均为 a_b），
    直接写入 tool_dict 会互相覆盖。这里对碰撞项追加序号后缀去重。
    """
    tools = await client.list_tools()
    local_functions = []
    seen_names: set[str] = set()
    for tool in tools:
        sanitized_name = _sanitize_mcp_tool_name(server_name, tool.name)
        if sanitized_name in seen_names:
            base = sanitized_name
            idx = 2
            while f"{base}_{idx}" in seen_names:
                idx += 1
            sanitized_name = f"{base}_{idx}"
            logger.warning(
                "MCP tool name collision on server '%s' for tool '%s'; renamed to '%s'",
                server_name, tool.name, sanitized_name,
            )
        seen_names.add(sanitized_name)
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
            if result.isError:
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
