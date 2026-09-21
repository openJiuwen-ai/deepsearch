# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from server.deepsearch.common.exception.exceptions import (
    McpServerExistsException,
    McpServerNotFoundException,
    ValidationError,
)
from server.schemas.mcp_server import (
    McpServerCreateRequestDTO,
    McpServerListRequestDTO,
    McpServerUpdateRequestDTO,
)


class FakeRepository:
    """镜像 test_web_search_engine_schema.py 的 FakeRepository 模式"""

    def __init__(self):
        self.created = None
        self.updated = None

    def get_by_name(self, space_id, server_name):
        return None

    def get_by_id(self, space_id, mcp_server_id):
        return None

    def get_list_by_id(self, space_id):
        return []

    def create(self, model):
        model.mcp_server_id = 1
        self.created = model

    def update(self, model):
        self.updated = model

    def delete_by_id(self, space_id, mcp_server_id):
        raise McpServerNotFoundException(
            f"mcp server id {mcp_server_id} not found under your space."
        )

    def get_server_detail_by_id(self, space_id, mcp_server_id):
        return None


@patch("server.deepsearch.core.manager.mcp_server_service.validate_search_service_url")
def test_create_mcp_server_returns_id(mock_validate):
    from server.deepsearch.core.manager.mcp_server_service import McpServerService

    repo = FakeRepository()
    service = McpServerService(repo)
    request = McpServerCreateRequestDTO(
        space_id="space",
        server_name="example-mcp",
        server_url="https://mcp.example.com/sse",
        headers={"Authorization": "Bearer token"},
    )
    response = service.create_mcp_server(request)
    assert response.mcp_server_id == 1
    assert repo.created is not None
    assert repo.created.server_name == "example-mcp"


def test_create_mcp_server_rejects_duplicate_name():
    from server.deepsearch.core.manager.mcp_server_service import McpServerService

    class DupRepo(FakeRepository):
        def get_by_name(self, space_id, server_name):
            return SimpleNamespace(server_name=server_name)

    service = McpServerService(DupRepo())
    request = McpServerCreateRequestDTO(
        space_id="space",
        server_name="dup",
        server_url="https://mcp.example.com/sse",
    )
    with pytest.raises(McpServerExistsException):
        service.create_mcp_server(request)


def test_create_mcp_server_rejects_ssrf_url(monkeypatch):
    from server.deepsearch.core.manager.mcp_server_service import McpServerService

    monkeypatch.delenv("SEARCH_SERVICE_ALLOW_UNSAFE_URL", raising=False)
    service = McpServerService(FakeRepository())
    request = McpServerCreateRequestDTO(
        space_id="space",
        server_name="bad",
        server_url="http://169.254.169.254/",
    )
    with pytest.raises(ValidationError):
        service.create_mcp_server(request)


def test_get_mcp_server_by_id_not_found():
    from server.deepsearch.core.manager.mcp_server_service import McpServerService

    service = McpServerService(FakeRepository())
    from server.schemas.mcp_server import McpServerGetRequestDTO
    request = McpServerGetRequestDTO(space_id="space", mcp_server_id=999)
    with pytest.raises(McpServerNotFoundException):
        service.get_mcp_server_by_id(request)


def test_list_mcp_servers_returns_items(caplog):
    from server.deepsearch.core.manager.mcp_server_service import McpServerService

    class ListRepo(FakeRepository):
        def get_list_by_id(self, space_id):
            return [
                SimpleNamespace(
                    space_id=space_id,
                    mcp_server_id=1,
                    server_name="example",
                    server_url="https://mcp.example.com",
                    transport_type="sse",
                    timeout=30.0,
                    create_time="2026-09-21",
                    type="search",
                    extension=None,
                    is_active=True,
                )
            ]

    service = McpServerService(ListRepo())
    caplog.set_level(logging.INFO, logger="server.deepsearch.core.manager.mcp_server_service")
    response = service.get_mcp_server_list(McpServerListRequestDTO(space_id="space-a"))
    assert len(response.data) == 1
    assert response.data[0].server_name == "example"


def test_delete_mcp_server_not_found():
    from server.deepsearch.core.manager.mcp_server_service import McpServerService
    from server.schemas.mcp_server import McpServerDeleteRequestDTO

    service = McpServerService(FakeRepository())
    request = McpServerDeleteRequestDTO(space_id="space", mcp_server_id=999)
    with pytest.raises(McpServerNotFoundException):
        service.delete_mcp_server_by_id(request)


def test_update_mcp_server_not_found():
    from server.deepsearch.core.manager.mcp_server_service import McpServerService

    service = McpServerService(FakeRepository())
    request = McpServerUpdateRequestDTO(space_id="space", mcp_server_id=999)
    with pytest.raises(McpServerNotFoundException):
        service.update_mcp_server(request)