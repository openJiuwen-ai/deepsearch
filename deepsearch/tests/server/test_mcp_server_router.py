# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""MCP server router 集成测试。

import 延迟到 fixture 内部，避免收集阶段触发 server.core.database 的模块级
engine 构造（匹配 test_mcp_server_service.py 的既定模式）。
"""
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest

from server.deepsearch.common.exception.exceptions import McpServerNotFoundException

_BASE_URL = "https://mcp.example.com/sse"
_PREFIX = "/api/v1/agent/deepsearch/mcp"


class InMemoryFakeRepository:
    """可完整 CRUD 的内存仓储，供 router 集成测试使用。"""

    def __init__(self):
        self._store: dict[tuple[str, int], object] = {}
        self._next_id = 1

    def create(self, model):
        model.mcp_server_id = self._next_id
        self._next_id += 1
        # 模拟 SQLAlchemy commit+refresh 填充 column defaults
        if model.transport_type is None:
            model.transport_type = "streamable_http"
        if model.timeout is None:
            model.timeout = 30.0
        if model.type is None:
            model.type = "search"
        if not model.create_time:
            model.create_time = "2026-09-23T00:00:00+00:00"
        if not model.update_time:
            model.update_time = "2026-09-23T00:00:00+00:00"
        self._store[(model.space_id, model.mcp_server_id)] = model

    def get_by_id(self, space_id, mcp_server_id):
        return self._store.get((space_id, mcp_server_id))

    def get_by_name(self, space_id, server_name):
        for (sid, _), model in self._store.items():
            if sid == space_id and model.server_name == server_name:
                return model
        return None

    def get_list_by_id(self, space_id):
        return [m for (sid, _), m in self._store.items() if sid == space_id]

    def update(self, model):
        record = self.get_by_id(model.space_id, model.mcp_server_id)
        if record is None:
            raise McpServerNotFoundException(
                f"mcp server id {model.mcp_server_id} not found under your space."
            )
        for attr in (
            "server_name", "server_url", "transport_type", "headers",
            "timeout", "type", "extension", "is_active",
        ):
            value = getattr(model, attr, None)
            if value is not None:
                setattr(record, attr, value)

    def delete_by_id(self, space_id, mcp_server_id):
        key = (space_id, mcp_server_id)
        if key not in self._store:
            raise McpServerNotFoundException(
                f"mcp server id {mcp_server_id} not found under your space."
            )
        del self._store[key]

    def get_server_detail_by_id(self, space_id, mcp_server_id):
        return None


@pytest.fixture
def client(monkeypatch):
    import importlib.util
    from pathlib import Path

    # DB_TYPE=sqlite 避免 database.py 模块级 create_engine(mysql) 失败
    monkeypatch.setenv("DB_TYPE", "sqlite")
    from server.deepsearch.core.manager.mcp_server_service import McpServerService

    # 直接加载 mcp_server_router.py，绕过 server/routers/__init__.py
    # （__init__.py 会 import knowledge_base → aioboto3 等未装依赖）
    router_path = (
        Path(__file__).resolve().parent.parent.parent
        / "server" / "routers" / "mcp_server_router.py"
    )
    spec = importlib.util.spec_from_file_location("_mcp_server_router_test", router_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    router = mod.router
    get_mcp_server_service = mod.get_mcp_server_service

    # 跳过真实 SSRF 校验（避免 DNS 解析网络依赖）
    monkeypatch.setattr(
        "server.deepsearch.core.manager.mcp_server_service.validate_search_service_url",
        lambda url: None,
    )
    service = McpServerService(InMemoryFakeRepository())

    app = FastAPI()
    parent = APIRouter(prefix=_PREFIX)
    parent.include_router(router)
    app.include_router(parent)
    app.dependency_overrides[get_mcp_server_service] = lambda: service
    return TestClient(app)


def test_create_mcp_server_returns_201(client):
    response = client.post(
        f"{_PREFIX}/",
        json={
            "space_id": "space-a",
            "server_name": "example-mcp",
            "server_url": _BASE_URL,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 200
    assert body["msg"] == "success"
    assert body["mcp_server_id"] == 1


def test_get_mcp_server_by_id_returns_200(client):
    client.post(
        f"{_PREFIX}/",
        json={
            "space_id": "space-a",
            "server_name": "example-mcp",
            "server_url": _BASE_URL,
        },
    )
    response = client.get(f"{_PREFIX}/space-a/1")
    assert response.status_code == 200
    body = response.json()
    assert body["server_name"] == "example-mcp"
    assert body["server_url"] == _BASE_URL


def test_get_mcp_server_list_returns_200(client):
    for name in ("srv-1", "srv-2"):
        client.post(
            f"{_PREFIX}/",
            json={
                "space_id": "space-a",
                "server_name": name,
                "server_url": _BASE_URL,
            },
        )
    response = client.get(f"{_PREFIX}/space-a")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert len(body["data"]) == 2
    assert {item["server_name"] for item in body["data"]} == {"srv-1", "srv-2"}


def test_update_mcp_server_returns_200(client):
    create_resp = client.post(
        f"{_PREFIX}/",
        json={
            "space_id": "space-a",
            "server_name": "old-name",
            "server_url": _BASE_URL,
        },
    )
    mcp_id = create_resp.json()["mcp_server_id"]

    response = client.put(
        f"{_PREFIX}/",
        json={
            "space_id": "space-a",
            "mcp_server_id": mcp_id,
            "server_name": "new-name",
            "server_url": "https://mcp.example.com/v2",
        },
    )
    assert response.status_code == 200
    assert response.json()["mcp_server_id"] == mcp_id

    # 验证更新已生效
    fetched = client.get(f"{_PREFIX}/space-a/{mcp_id}").json()
    assert fetched["server_name"] == "new-name"
    assert fetched["server_url"] == "https://mcp.example.com/v2"


def test_delete_mcp_server_returns_200(client):
    create_resp = client.post(
        f"{_PREFIX}/",
        json={
            "space_id": "space-a",
            "server_name": "to-delete",
            "server_url": _BASE_URL,
        },
    )
    mcp_id = create_resp.json()["mcp_server_id"]

    response = client.delete(f"{_PREFIX}/space-a/{mcp_id}")
    assert response.status_code == 200
    assert response.json()["code"] == 200


def test_delete_not_found_returns_400(client):
    response = client.delete(f"{_PREFIX}/space-a/99999")
    assert response.status_code == 400
    assert "MCP_SERVER_EX" in response.json()["detail"]


def test_create_duplicate_name_returns_400(client):
    payload = {
        "space_id": "space-a",
        "server_name": "dup",
        "server_url": _BASE_URL,
    }
    client.post(f"{_PREFIX}/", json=payload)
    response = client.post(f"{_PREFIX}/", json=payload)
    assert response.status_code == 400
    assert "MCP_SERVER_EX" in response.json()["detail"]
