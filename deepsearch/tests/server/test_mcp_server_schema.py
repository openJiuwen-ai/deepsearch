# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from server.schemas.mcp_server import (
    McpServerBasicRequestDTO,
    McpServerCreateRequestDTO,
    McpServerUpdateRequestDTO,
    McpServerDetail,
    McpServerItem,
    BasicResponseDTO,
    McpServerCreateRes,
    McpServerGetRes,
    McpServerListRes,
    McpServerDeleteRes,
    McpServerUpdateRes,
)


def test_mcp_server_create_request_dto_required_fields():
    dto = McpServerCreateRequestDTO(
        space_id="space-a",
        server_name="example-mcp",
        server_url="https://mcp.example.com/sse",
    )
    assert dto.space_id == "space-a"
    assert dto.server_name == "example-mcp"
    assert dto.server_url == "https://mcp.example.com/sse"
    assert dto.transport_type == "streamable_http"
    assert dto.type == "search"
    assert dto.timeout == 30.0
    assert dto.headers is None


def test_mcp_server_create_request_dto_accepts_headers():
    dto = McpServerCreateRequestDTO(
        space_id="space-a",
        server_name="example-mcp",
        server_url="https://mcp.example.com/sse",
        headers={"Authorization": "Bearer token"},
    )
    assert dto.headers == {"Authorization": "Bearer token"}


def test_mcp_server_update_request_dto_all_optional_except_space_and_id():
    dto = McpServerUpdateRequestDTO(
        space_id="space-a",
        mcp_server_id=1,
    )
    assert dto.server_name is None
    assert dto.server_url is None
    assert dto.headers is None


def test_mcp_server_detail_has_decrypted_fields():
    detail = McpServerDetail(
        server_name="example-mcp",
        server_url="https://mcp.example.com/sse",
        headers={"Authorization": "Bearer token"},
    )
    assert detail.server_name == "example-mcp"
    assert detail.headers == {"Authorization": "Bearer token"}


def test_mcp_server_item_has_list_fields():
    item = McpServerItem(
        server_name="example-mcp",
        server_url="https://mcp.example.com/sse",
        mcp_server_id=1,
        create_time="2026-09-21T00:00:00",
        type="search",
    )
    assert item.mcp_server_id == 1
    assert item.type == "search"


def test_basic_response_dto_defaults():
    res = BasicResponseDTO()
    assert res.code == 200
    assert res.msg == "success"


def test_mcp_server_list_res_has_data_field():
    res = McpServerListRes()
    assert res.data == []
