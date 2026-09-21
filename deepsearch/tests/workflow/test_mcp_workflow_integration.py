# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen_deepsearch.framework.openjiuwen.tools.mcp.bundle import McpToolBundle
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import mcp_tool_context


@pytest.mark.asyncio
async def test_initialize_mcp_context_returns_none_when_no_servers():
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
        _initialize_mcp_context_from_agent_config,
    )
    mock_config = MagicMock()
    mock_config.mcp_servers = []
    token = await _initialize_mcp_context_from_agent_config(mock_config)
    assert token is None


@pytest.mark.asyncio
async def test_initialize_mcp_context_sets_contextvar():
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
        _initialize_mcp_context_from_agent_config,
    )
    mock_config = MagicMock()
    mock_config.mcp_servers = [
        MagicMock(model_dump=MagicMock(return_value={
            "server_name": "test",
            "server_url": "https://mcp.com/sse",
            "transport_type": "sse",
            "type": "search",
        }))
    ]
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.McpToolBundle"
    ) as mock_bundle_cls:
        mock_bundle = MagicMock()
        mock_bundle.connect_and_build_tools = AsyncMock()
        mock_bundle_cls.return_value = mock_bundle

        token = await _initialize_mcp_context_from_agent_config(mock_config)

    assert token is not None
    assert mcp_tool_context.get() is mock_bundle
    mcp_tool_context.reset(token)


@pytest.mark.asyncio
async def test_initialize_mcp_context_returns_none_on_failure():
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
        _initialize_mcp_context_from_agent_config,
    )
    mock_config = MagicMock()
    mock_config.mcp_servers = [
        MagicMock(model_dump=MagicMock(return_value={
            "server_name": "test",
            "server_url": "https://mcp.com/sse",
            "transport_type": "sse",
            "type": "search",
        }))
    ]
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.McpToolBundle"
    ) as mock_bundle_cls:
        mock_bundle = MagicMock()
        mock_bundle.connect_and_build_tools = AsyncMock(side_effect=Exception("connect failed"))
        mock_bundle.close_all = AsyncMock()
        mock_bundle_cls.return_value = mock_bundle

        token = await _initialize_mcp_context_from_agent_config(mock_config)

    assert token is None
