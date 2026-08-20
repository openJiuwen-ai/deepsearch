"""Tests for MCP type documentation resources."""

from openjiuwen_search_base.codegraph.mcp.type_docs import (
    EDGE_TYPE_DOCS,
    NODE_TYPE_DOCS,
    edge_type_resource_body,
    node_type_resource_body,
    types_index_body,
)
from openjiuwen_search_base.codegraph.parser.constants import EdgeType, NodeType


def test_types_index_lists_all_node_and_edge_uris() -> None:
    body = types_index_body()
    assert body.startswith("# Jiuwen Code Parser")
    assert "## Common node fields" in body
    assert "- id: string" in body
    assert "## Common edge fields" in body
    assert "- source: string" in body
    for nt in NodeType:
        assert f"jiuwen-code-parser://types/nodes/{nt.value}" in body
    for et in EdgeType:
        assert f"jiuwen-code-parser://types/edges/{et.value}" in body


def test_node_type_resource_known() -> None:
    body = node_type_resource_body("function")
    assert "node_type: function" in body
    assert "category: Core" in body
    assert NODE_TYPE_DOCS["function"].description in body
    assert "common fields:" in body
    assert "- id: string" in body
    assert "type fields:" in body
    assert "- func_type:" in body
    assert "- parameters:" in body


def test_node_type_resource_internal() -> None:
    body = node_type_resource_body("call")
    assert "internal: true" in body
    assert "- callee: string" in body


def test_node_type_resource_unknown() -> None:
    body = node_type_resource_body("not_a_type")
    assert "Unknown node type" in body
    assert "function" in body


def test_edge_type_resource_known() -> None:
    body = edge_type_resource_body("CALLS")
    assert "edge_type: CALLS" in body
    assert EDGE_TYPE_DOCS["CALLS"].confidence in body
    assert "fields:" in body
    assert "- source: string" in body
    assert "- relation: string" in body
    assert "- confidence: number (optional)" in body


def test_edge_type_resource_unknown() -> None:
    body = edge_type_resource_body("NOT_A_RELATION")
    assert "Unknown edge type" in body
    assert "CONTAINS" in body


def test_catalogs_cover_all_enums() -> None:
    assert set(NODE_TYPE_DOCS) == {nt.value for nt in NodeType}
    assert set(EDGE_TYPE_DOCS) == {et.value for et in EdgeType}
