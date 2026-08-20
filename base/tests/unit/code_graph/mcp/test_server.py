"""Smoke test for FastMCP server construction (skipped without fastmcp)."""

from typing import Any, Coroutine
from unittest.mock import AsyncMock

import pytest
from mcp.types import Resource

pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402

from openjiuwen_search_base.codegraph.mcp import GraphSession, create_mcp_server  # noqa: E402
from openjiuwen_search_base.codegraph.mcp.server import JiuwenMCP  # noqa: E402
from openjiuwen_search_base.codegraph.mcp.type_docs import EDGE_TYPE_DOCS, NODE_TYPE_DOCS  # noqa: E402
from openjiuwen_search_base.codegraph.parser.constants import EdgeType, NodeType  # noqa: E402


def test_create_mcp_server() -> None:
    mcp = create_mcp_server(session=GraphSession())
    assert mcp is not None


def test_jiuwen_mcp_delegates_name() -> None:
    server = JiuwenMCP(name="Test Jiuwen")
    assert server.mcp is not None


@pytest.mark.asyncio
async def test_type_resources_are_static_and_readable() -> None:
    mcp = create_mcp_server(session=GraphSession())
    # FastMCP.list_resources for fastmcp>=3; FastMCP._list_resources_mcp for fastmcp==2.*
    list_resouces_fn: Coroutine[Any, list[Resource]] = getattr(mcp, "list_resources", None) or getattr(
        mcp, "_list_resources_mcp"
    )
    resources = await list_resouces_fn()
    uris = {str(r.uri) for r in resources}

    assert "jiuwen-code-graph://types" in uris
    for nt in NodeType:
        assert f"jiuwen-code-graph://types/nodes/{nt.value}" in uris
    for et in EdgeType:
        assert f"jiuwen-code-graph://types/edges/{et.value}" in uris
    assert len(uris) == 1 + len(NODE_TYPE_DOCS) + len(EDGE_TYPE_DOCS)

    async with Client(mcp) as client:
        node_body = await client.read_resource("jiuwen-code-graph://types/nodes/function")
        assert "node_type: function" in node_body[0].text
        edge_body = await client.read_resource("jiuwen-code-graph://types/edges/CALLS")
        assert "edge_type: CALLS" in edge_body[0].text


@pytest.mark.asyncio
async def test_tools_return_error_strings_instead_of_raising() -> None:
    session = GraphSession()
    session.index = AsyncMock(side_effect=RuntimeError("boom index"))  # type: ignore[method-assign]
    session.search_nodes = lambda *a, **k: (_ for _ in ()).throw(ValueError("boom nodes"))  # type: ignore[method-assign]
    session.search_edges = lambda *a, **k: (_ for _ in ()).throw(TypeError("boom edges"))  # type: ignore[method-assign]

    mcp = create_mcp_server(session=session)
    async with Client(mcp) as client:
        index_result = await client.call_tool("index", {"path": "/tmp"})
        nodes_result = await client.call_tool("search_nodes", {"query": "x"})
        edges_result = await client.call_tool("search_edges", {"query": "{relation:CALLS}"})

    assert "Error: boom index" in str(index_result.data if hasattr(index_result, "data") else index_result)
    assert "Error: boom nodes" in str(nodes_result.data if hasattr(nodes_result, "data") else nodes_result)
    assert "Error: boom edges" in str(edges_result.data if hasattr(edges_result, "data") else edges_result)
