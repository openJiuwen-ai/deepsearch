# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
__all__ = [
    "apply_web_search_domain_constraints",
    "update_web_search_mapping",
    "update_local_search_mapping",
    "create_web_search_tool",
    "create_local_search_tool",
    "build_runtime_api_tools",
    "build_runtime_api_search_payload",
    "merge_runtime_api_tools",
    "sanitize_tool_name",
    "McpClient",
    "McpToolBundle",
    "build_mcp_local_functions",
]

from openjiuwen_deepsearch.framework.openjiuwen.tools.local_search import create_local_search_tool, \
    update_local_search_mapping
try:
    from openjiuwen_deepsearch.framework.openjiuwen.tools.mcp import McpClient, McpToolBundle, \
        build_mcp_local_functions
except ImportError:
    # MCP 为可选依赖（pyproject [mcp]）；最小安装时 mcp 包缺失，这里置 None 而非阻断导入。
    McpClient = None  # type: ignore[assignment]
    McpToolBundle = None  # type: ignore[assignment]
    build_mcp_local_functions = None  # type: ignore[assignment]
from openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api import build_runtime_api_tools, \
    build_runtime_api_search_payload, merge_runtime_api_tools, sanitize_tool_name
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import apply_web_search_domain_constraints, \
    create_web_search_tool, \
    update_web_search_mapping
