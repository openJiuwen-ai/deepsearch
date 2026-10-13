# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from server.deepsearch.core.manager.agent import DeepSearchAgentManager
from server.schemas.deepsearch_run import McpServerConfig


def test_load_mcp_config_returns_server_dicts():
    """_load_mcp_config 应返回含 type 的 server 配置 dict 列表"""
    detail = SimpleNamespace(
        server_name="example-mcp",
        server_url="https://mcp.example.com/sse",
        transport_type="sse",
        headers={"Authorization": "Bearer token"},
        timeout=30.0,
        is_active=True,
    )
    with patch(
        "server.deepsearch.core.manager.agent.McpServerRepository"
    ) as repo_cls:
        repo = MagicMock()
        repo.get_server_detail_by_id.return_value = detail
        repo_cls.return_value = repo

        result = DeepSearchAgentManager._load_mcp_config(
            space_id="space",
            mcp_configs=[McpServerConfig(mcp_server_id=1, type="search")],
            db=MagicMock(),
        )
    assert len(result) == 1
    assert result[0]["server_name"] == "example-mcp"
    assert result[0]["server_url"] == "https://mcp.example.com/sse"
    assert result[0]["transport_type"] == "sse"
    assert result[0]["type"] == "search"


def test_load_mcp_config_skips_inactive():
    detail = SimpleNamespace(
        server_name="inactive-mcp",
        server_url="https://mcp.example.com/sse",
        transport_type="sse",
        headers={},
        timeout=30.0,
        is_active=False,
    )
    with patch(
        "server.deepsearch.core.manager.agent.McpServerRepository"
    ) as repo_cls:
        repo = MagicMock()
        repo.get_server_detail_by_id.return_value = detail
        repo_cls.return_value = repo

        result = DeepSearchAgentManager._load_mcp_config(
            space_id="space",
            mcp_configs=[McpServerConfig(mcp_server_id=1, type="search")],
            db=MagicMock(),
        )
    assert result == []


def test_load_mcp_config_raises_on_not_found():
    from server.deepsearch.common.exception.exceptions import McpServerNotFoundException

    with patch(
        "server.deepsearch.core.manager.agent.McpServerRepository"
    ) as repo_cls:
        repo = MagicMock()
        repo.get_server_detail_by_id.return_value = None
        repo_cls.return_value = repo

        with pytest.raises(McpServerNotFoundException):
            DeepSearchAgentManager._load_mcp_config(
                space_id="space",
                mcp_configs=[McpServerConfig(mcp_server_id=999, type="search")],
                db=MagicMock(),
            )
