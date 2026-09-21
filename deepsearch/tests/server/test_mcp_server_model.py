# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import pytest

from server.deepsearch.common.exception.exceptions import (
    McpServerBasicException,
    McpServerExistsException,
    McpServerHeaderDecryptError,
    McpServerNotFoundException,
    McpServerValidationError,
)


def test_mcp_server_basic_exception_has_code():
    exc = McpServerBasicException("test error")
    assert McpServerBasicException.CODE == "MCP_SERVER_EX"
    assert "[MCP_SERVER_EX]" in str(exc)


def test_mcp_server_not_found_exception_inherits_basic():
    exc = McpServerNotFoundException("not found")
    assert isinstance(exc, McpServerBasicException)


def test_mcp_server_exists_exception_inherits_basic():
    exc = McpServerExistsException("exists")
    assert isinstance(exc, McpServerBasicException)


def test_mcp_server_header_decrypt_error_is_standalone():
    exc = McpServerHeaderDecryptError("decrypt failed")
    assert not isinstance(exc, McpServerBasicException)


def test_mcp_server_validation_error_is_standalone():
    exc = McpServerValidationError("validation failed")
    assert not isinstance(exc, McpServerBasicException)


def test_mcp_server_model_table_name():
    from server.deepsearch.core.models.mcp_server_model import McpServerModel
    assert McpServerModel.__tablename__ == "mcp_server"


def test_mcp_server_model_has_required_columns():
    from server.deepsearch.core.models.mcp_server_model import McpServerModel
    columns = {c.name for c in McpServerModel.__table__.columns}
    expected = {
        "mcp_server_id", "space_id", "server_name", "server_url",
        "transport_type", "headers", "timeout", "type",
        "extension", "is_active", "create_time", "update_time",
    }
    assert expected.issubset(columns), f"Missing columns: {expected - columns}"


def test_mcp_server_model_type_default_is_search():
    from server.deepsearch.core.models.mcp_server_model import McpServerModel
    type_col = McpServerModel.__table__.columns["type"]
    assert type_col.default is not None
    assert type_col.default.arg == "search"


def test_mcp_server_model_transport_type_default_is_streamable_http():
    from server.deepsearch.core.models.mcp_server_model import McpServerModel
    transport_col = McpServerModel.__table__.columns["transport_type"]
    assert transport_col.default is not None
    assert transport_col.default.arg == "streamable_http"


def test_mcp_server_model_timeout_default_is_30():
    from server.deepsearch.core.models.mcp_server_model import McpServerModel
    timeout_col = McpServerModel.__table__.columns["timeout"]
    assert timeout_col.default is not None
    assert timeout_col.default.arg == 30.0
