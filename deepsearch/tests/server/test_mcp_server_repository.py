# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""MCP server repository 单元测试。

import 延迟到 fixture/函数内部，避免收集阶段触发 server.core.database 的模块级
engine 构造（匹配 test_mcp_server_service.py 的既定模式）。
"""
import base64
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 32 字节固定主密钥（base64 编码），用于加解密往返测试
_TEST_MASTER_KEY_B64 = base64.b64encode(b"0" * 32).decode("utf-8")


def _make_model(**overrides):
    from server.deepsearch.core.models.mcp_server_model import McpServerModel

    defaults = {
        "space_id": "space-a",
        "server_name": "example-mcp",
        "server_url": "https://mcp.example.com/sse",
        "transport_type": "streamable_http",
        "timeout": 30.0,
        "type": "search",
    }
    defaults.update(overrides)
    return McpServerModel(**defaults)


@pytest.fixture
def db_session():
    from server.core.database import Base
    from server.deepsearch.core.models.mcp_server_model import McpServerModel

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[McpServerModel.__table__])
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def repo(db_session):
    from server.deepsearch.core.manager.repositories.mcp_server_repository import (
        McpServerRepository,
    )

    return McpServerRepository(db_session)


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """固定测试环境：DB_TYPE=sqlite 避免 database.py 模块级 create_engine(mysql) 失败；
    SERVER_AES_MASTER_KEY_ENV 让 SecurityUtils 加解密可往返。"""
    monkeypatch.setenv("DB_TYPE", "sqlite")
    monkeypatch.setenv("SERVER_AES_MASTER_KEY_ENV", _TEST_MASTER_KEY_B64)


def test_create_and_get_by_id_round_trip(repo):
    model = _make_model()
    repo.create(model)
    assert model.mcp_server_id is not None

    fetched = repo.get_by_id("space-a", model.mcp_server_id)
    assert fetched is not None
    assert fetched.server_name == "example-mcp"
    assert fetched.server_url == "https://mcp.example.com/sse"


def test_get_by_name_returns_match(repo):
    repo.create(_make_model(space_id="space-a", server_name="srv-1"))

    found = repo.get_by_name("space-a", "srv-1")
    assert found is not None
    assert found.server_name == "srv-1"


def test_get_by_name_space_isolation(repo):
    repo.create(_make_model(space_id="space-a", server_name="srv-1"))
    repo.create(_make_model(space_id="space-b", server_name="srv-1"))

    assert repo.get_by_name("space-a", "srv-1").space_id == "space-a"
    assert repo.get_by_name("space-b", "srv-1").space_id == "space-b"
    assert repo.get_by_name("space-c", "srv-1") is None


def test_get_list_by_id_filters_by_space(repo):
    repo.create(_make_model(space_id="space-a", server_name="srv-1"))
    repo.create(_make_model(space_id="space-a", server_name="srv-2"))
    repo.create(_make_model(space_id="space-b", server_name="srv-3"))

    items_a = repo.get_list_by_id("space-a")
    items_b = repo.get_list_by_id("space-b")
    assert len(items_a) == 2
    assert len(items_b) == 1
    assert {i.server_name for i in items_a} == {"srv-1", "srv-2"}


def test_update_modifies_fields(repo):
    model = _make_model()
    repo.create(model)

    patch = _make_model(
        space_id=model.space_id,
        mcp_server_id=model.mcp_server_id,
        server_name="renamed",
        server_url="https://mcp.example.com/v2",
        transport_type="sse",
        timeout=60.0,
        type="report",
    )
    repo.update(patch)

    fetched = repo.get_by_id(model.space_id, model.mcp_server_id)
    assert fetched.server_name == "renamed"
    assert fetched.server_url == "https://mcp.example.com/v2"
    assert fetched.transport_type == "sse"
    assert fetched.timeout == 60.0
    assert fetched.type == "report"


def test_update_not_found_raises(repo):
    from server.deepsearch.common.exception.exceptions import McpServerNotFoundException

    with pytest.raises(McpServerNotFoundException):
        repo.update(_make_model(mcp_server_id=99999))


def test_delete_by_id_removes_record(repo):
    model = _make_model()
    repo.create(model)
    repo.delete_by_id(model.space_id, model.mcp_server_id)
    assert repo.get_by_id(model.space_id, model.mcp_server_id) is None


def test_delete_by_id_not_found_raises(repo):
    from server.deepsearch.common.exception.exceptions import McpServerNotFoundException

    with pytest.raises(McpServerNotFoundException):
        repo.delete_by_id("space-a", 99999)


def test_sensitive_header_keys_covers_common_variants():
    from server.deepsearch.core.manager.repositories.mcp_server_repository import (
        SENSITIVE_HEADER_KEYS,
    )

    expected = {
        "authorization", "proxy-authorization",
        "x-api-key", "api-key", "apikey",
        "x-auth-token", "x-custom-token", "x-auth", "bearer-token",
        "x-secret", "secret",
    }
    assert expected.issubset(SENSITIVE_HEADER_KEYS), (
        f"Missing sensitive keys: {expected - SENSITIVE_HEADER_KEYS}"
    )


def test_get_server_detail_by_id_decrypts_sensitive_headers(repo, db_session):
    from server.core.manager.model_manager.utils import SecurityUtils

    security_utils = SecurityUtils()
    encrypted = security_utils.encrypt_api_key("Bearer secret-token")
    model = _make_model(headers={"Authorization": encrypted, "X-Custom-Header": "plain"})
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)

    detail = repo.get_server_detail_by_id(model.space_id, model.mcp_server_id)
    assert detail is not None
    assert detail.headers["Authorization"] == "Bearer secret-token"
    assert detail.headers["X-Custom-Header"] == "plain"


def test_get_server_detail_by_id_not_found_returns_none(repo):
    assert repo.get_server_detail_by_id("space-a", 99999) is None


def test_decrypt_sensitive_headers_skips_non_sensitive():
    from server.deepsearch.core.manager.repositories.mcp_server_repository import (
        _decrypt_sensitive_headers,
    )

    headers = {"Content-Type": "application/json", "X-Trace-Id": "abc"}
    result = _decrypt_sensitive_headers(headers)
    assert result == headers


def test_decrypt_sensitive_headers_fallback_logs_warning(caplog):
    from server.deepsearch.core.manager.repositories.mcp_server_repository import (
        _decrypt_sensitive_headers,
    )

    # 50 字节数据：base64 解码成功，长度 >= 44 进入 AES 解密，GCM 验证失败
    bad_ciphertext = base64.b64encode(b"x" * 50).decode("utf-8")
    caplog.set_level(
        logging.WARNING,
        logger="server.deepsearch.core.manager.repositories.mcp_server_repository",
    )

    result = _decrypt_sensitive_headers({"Authorization": bad_ciphertext})

    assert result["Authorization"] == bad_ciphertext  # 降级保留原值
    assert any(
        "Failed to decrypt header key 'Authorization'" in record.message
        for record in caplog.records
    )
