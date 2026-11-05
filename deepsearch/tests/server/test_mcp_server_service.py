# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

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
@patch("server.deepsearch.core.manager.mcp_server_service.SecurityUtils")
def test_create_mcp_server_returns_id(mock_security_cls, mock_validate):
    from server.deepsearch.core.manager.mcp_server_service import McpServerService

    # 模拟已配置主密钥,encrypt 返回密文(与明文不同),通过 fail-closed 校验
    mock_security_cls.return_value.encrypt_api_key.return_value = "ENCRYPTED_Bearer_token"
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


def test_encrypt_sensitive_headers_rejects_plaintext_when_no_master_key(monkeypatch):
    """缺主密钥时,敏感 header 加密会退化为明文,必须显式拒绝。

    回归问题10：SecurityUtils.encrypt_api_key 在无主密钥时原样返回,
    导致明文凭据落库。
    """
    from server.deepsearch.core.manager.mcp_server_service import (
        _encrypt_sensitive_headers,
    )

    # 确保未配置主密钥
    monkeypatch.delenv("SERVER_AES_MASTER_KEY_ENV", raising=False)
    monkeypatch.delenv("SERVER_AES_MASTER_KEY", raising=False)
    monkeypatch.delenv("SERVICE_MODE", raising=False)

    with pytest.raises(ValidationError, match="SERVER_AES_MASTER_KEY_ENV"):
        _encrypt_sensitive_headers({"Authorization": "Bearer secret-token"})


def test_encrypt_sensitive_headers_covers_goog_api_key(monkeypatch):
    """x-goog-api-key 等新增白名单 header 必须被加密处理。

    回归问题10：SENSITIVE_HEADER_KEYS 原先不含 x-goog-api-key。
    """
    from server.deepsearch.core.manager.mcp_server_service import (
        _encrypt_sensitive_headers,
    )
    from server.deepsearch.core.manager.repositories.mcp_server_repository import (
        SENSITIVE_HEADER_KEYS,
    )

    assert "x-goog-api-key" in SENSITIVE_HEADER_KEYS

    monkeypatch.delenv("SERVER_AES_MASTER_KEY_ENV", raising=False)
    monkeypatch.delenv("SERVER_AES_MASTER_KEY", raising=False)
    monkeypatch.delenv("SERVICE_MODE", raising=False)
    with pytest.raises(ValidationError):
        _encrypt_sensitive_headers({"x-goog-api-key": "ya29.test-token"})


def test_encrypt_sensitive_headers_passes_non_sensitive(monkeypatch):
    """非敏感 header 不受加密逻辑影响,原样返回。"""
    from server.deepsearch.core.manager.mcp_server_service import (
        _encrypt_sensitive_headers,
    )

    monkeypatch.delenv("SERVER_AES_MASTER_KEY_ENV", raising=False)
    monkeypatch.delenv("SERVER_AES_MASTER_KEY", raising=False)
    monkeypatch.delenv("SERVICE_MODE", raising=False)
    result = _encrypt_sensitive_headers({"X-Custom": "plain"})
    assert result == {"X-Custom": "plain"}


@patch("server.deepsearch.core.manager.mcp_server_service.SecurityUtils")
@patch("server.deepsearch.core.manager.mcp_server_service.validate_search_service_url")
def test_create_mcp_server_concurrent_duplicate_raises_exists(mock_validate, mock_security_cls):
    """DB 唯一约束触发 IntegrityError 时,service 必须转成 McpServerExistsException。

    回归问题6：并发 create 同名时,应用层查重都通过,只有 DB 约束能拦住。
    """
    from server.deepsearch.core.manager.mcp_server_service import McpServerService

    mock_security_cls.return_value.encrypt_api_key.return_value = "ENCRYPTED"

    class ConcurrentDupRepo(FakeRepository):
        def create(self, model):
            raise IntegrityError("statement", {}, None)

    service = McpServerService(ConcurrentDupRepo())
    request = McpServerCreateRequestDTO(
        space_id="space",
        server_name="concurrent-dup",
        server_url="https://mcp.example.com/sse",
    )
    with pytest.raises(McpServerExistsException):
        service.create_mcp_server(request)
