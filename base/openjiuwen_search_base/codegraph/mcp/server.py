"""FastMCP server exposing index and viewer-syntax search tools."""

import sys
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, Iterator

from fastmcp import FastMCP

from .session import GraphSession
from .type_docs import (
    EDGE_TYPE_DOCS,
    NODE_TYPE_DOCS,
    edge_type_resource_body,
    node_type_resource_body,
    types_index_body,
)

MCP_SERVER_INSTRUCTIONS = """\
Jiuwen Code Parser MCP server.

Workflow:
1. Call index(path) once on a project directory to parse source and build the code graph.
2. Call search_nodes(query) / search_edges(query) using viewer search syntax,
   or search_regex(pattern, target) for Python regex over node/edge fields.
3. Read jiuwen-code-graph://types (and nested URIs) for node and edge type docs.

Viewer search syntax:
- Free text matches node name/signature or edge relation/ids (case-insensitive substring).
- Predicates use {field:glob}; * is a wildcard. Examples: {type:function}, {relation:CALLS}.
"""


def _error_message(exc: BaseException) -> str:
    """Format an exception as a stable MCP tool/resource error string."""
    detail = str(exc).strip() or type(exc).__name__
    return f"Error: {detail}"


@contextmanager
def _stdout_to_stderr() -> Iterator[None]:
    """Redirect stdout to stderr so incidental prints cannot break MCP stdio JSON."""
    old = sys.stdout
    try:
        sys.stdout = sys.stderr
        yield
    finally:
        sys.stdout = old


class JiuwenMCP:
    """FastMCP server wrapping a :class:`GraphSession`.

    :param name: MCP server display name.
    :param session: Optional shared session (a new one is created when omitted).
    :param kwargs: Forwarded to ``fastmcp.FastMCP``. If ``instructions`` is omitted,
        a default overview of tools and type resources is supplied.
    """

    def __init__(
        self,
        name: str = "Jiuwen Code Parser",
        session: GraphSession | None = None,
        **kwargs: Any,
    ) -> None:
        """Create a FastMCP server and register tools and type resources."""
        if "instructions" not in kwargs:
            kwargs["instructions"] = MCP_SERVER_INSTRUCTIONS
        self.session = session or GraphSession()
        self.mcp = FastMCP(name, **kwargs)
        register_mcp_tools(self.mcp, self.session)
        register_mcp_type_resources(self.mcp)

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the wrapped FastMCP server."""
        return getattr(self.mcp, name)


def create_mcp_server(
    name: str = "Jiuwen Code Parser",
    session: GraphSession | None = None,
    **kwargs: Any,
) -> FastMCP:
    """Create a FastMCP server with Jiuwen tools and type resources registered.

    :param name: MCP server display name.
    :param session: Optional shared :class:`GraphSession`.
    :param kwargs: Forwarded to :class:`JiuwenMCP` / ``FastMCP``.
    :returns: Configured ``FastMCP`` instance.
    """
    return JiuwenMCP(name=name, session=session, **kwargs).mcp


def register_mcp_tools(mcp: FastMCP, session: GraphSession) -> None:
    """Register ``index``, ``search_nodes``, ``search_edges``, and ``search_regex`` on *mcp*.

    :param mcp: FastMCP server instance.
    :param session: Session holding the indexed graph.
    """

    @mcp.tool
    async def index(path: str) -> dict[str, Any] | str:
        """Parse a project directory and build an in-memory code graph.

        Writes nodes.jsonl / edges.jsonl / graph.jcp under ``<path>/.jiuwen_graph``
        and stores the graph for later search_nodes / search_edges calls.

        :param path: Absolute or relative path to a source directory.
        :returns: Summary with file/node/edge counts and output paths, or an error string.
        """
        try:
            # Progress / prints must not touch stdout (MCP JSON-RPC uses stdio).
            with _stdout_to_stderr():
                return await session.index(path)
        except Exception as exc:
            return _error_message(exc)

    @mcp.tool
    async def search_nodes(query: str, limit: int = 50) -> dict[str, Any] | str:
        """Search indexed graph nodes with viewer search syntax.

        Free text matches ``name`` / ``signature``. Use ``{type:function}`` (or other
        fields) as glob predicates. See resources under jiuwen-code-graph://types/nodes/.

        Returns ``{matches, total, tag_counts, tag_combo_counts}``. ``total`` is
        always the full hit count before ``limit``; ``limit`` only truncates
        ``matches`` (use ``limit=-1`` for all matches). Tag stats cover the full
        hit set; combos are top-10 full tag-sets (longer first). Sorted by
        free-text relevance, then node_type / name / path / id.

        Requires a prior successful index(path). Empty or edge-only queries return
        an empty result. Failures return an ``Error: …`` string instead of raising.

        :param query: Viewer search string.
        :param limit: Max matches in ``matches`` (default 50; ``-1`` = all).
        :returns: Search result dict, or an error string.
        """
        try:
            return asdict(session.search_nodes(query, limit=limit))
        except Exception as exc:
            return _error_message(exc)

    @mcp.tool
    async def search_edges(query: str, limit: int = 50) -> dict[str, Any] | str:
        """Search indexed graph edges with viewer search syntax.

        Free text matches ``relation`` / ``resolved_by`` / ``source`` / ``target``.
        Use ``{relation:CALLS}`` (or other edge fields) as glob predicates.
        See resources under jiuwen-code-graph://types/edges/.

        Returns ``{matches, total, tag_counts, tag_combo_counts}``. ``total`` is
        always the full hit count before ``limit`` (use ``limit=-1`` for all
        matches). Tag stats come from endpoint nodes (top-10 full tag-sets).
        Sorted by confidence desc, then relation / source / target.

        Requires a prior successful index(path). Empty or node-only queries return
        an empty result. Failures return an ``Error: …`` string instead of raising.

        :param query: Viewer search string.
        :param limit: Max matches in ``matches`` (default 50; ``-1`` = all).
        :returns: Search result dict, or an error string.
        """
        try:
            return asdict(session.search_edges(query, limit=limit))
        except Exception as exc:
            return _error_message(exc)

    @mcp.tool
    async def search_regex(
        pattern: str,
        target: str = "nodes",
        limit: int = 50,
        ignore_case: bool = True,
    ) -> dict[str, Any] | str:
        """Search indexed nodes or edges with a Python regular expression.

        Not viewer search syntax — pass a real regex. Node fields: name, signature,
        id, path, type, node_type. Edge fields: relation, resolved_by, source, target.

        Returns the same ``{matches, total, tag_counts, tag_combo_counts}`` shape as
        search_nodes / search_edges. Use ``limit=-1`` for all matches.

        :param pattern: Regular expression.
        :param target: ``"nodes"`` (default) or ``"edges"``.
        :param limit: Max matches in ``matches`` (default 50; ``-1`` = all).
        :param ignore_case: Case-insensitive when true (default).
        :returns: Search result dict, or an error string.
        """
        try:
            if target not in {"nodes", "edges"}:
                return "Error: target must be 'nodes' or 'edges'"
            return asdict(
                session.search_regex(
                    pattern,
                    target=target,  # type: ignore[arg-type]
                    limit=limit,
                    ignore_case=ignore_case,
                )
            )
        except Exception as exc:
            return _error_message(exc)

    _ = (index, search_nodes, search_edges, search_regex)  # satisfy linters


def register_mcp_type_resources(mcp: FastMCP) -> None:
    """Register MCP resources documenting node and edge types.

    Registers concrete URIs (not templates) so clients that poorly handle
    custom-scheme resource templates can still list and read every type page:

    * ``jiuwen-code-graph://types`` — markdown index of type URIs.
    * ``jiuwen-code-graph://types/nodes/<node_type>`` — one node type each.
    * ``jiuwen-code-graph://types/edges/<edge_type>`` — one edge relation each.

    :param mcp: FastMCP server instance.
    """

    @mcp.resource(
        "jiuwen-code-graph://types",
        name="jiuwen_types_index",
        description="Markdown index of node and edge type documentation URIs.",
        mime_type="text/markdown",
    )
    async def _types_index() -> str:
        try:
            return types_index_body()
        except Exception as exc:
            return _error_message(exc)

    for node_type in NODE_TYPE_DOCS:
        uri = f"jiuwen-code-graph://types/nodes/{node_type}"
        mcp.resource(
            uri,
            name=f"jiuwen_node_{node_type}",
            description=f"Documentation for graph node type {node_type!r}.",
            mime_type="text/plain",
        )(_make_node_type_resource(node_type))

    for edge_type in EDGE_TYPE_DOCS:
        uri = f"jiuwen-code-graph://types/edges/{edge_type}"
        mcp.resource(
            uri,
            name=f"jiuwen_edge_{edge_type}",
            description=f"Documentation for graph edge relation {edge_type!r}.",
            mime_type="text/plain",
        )(_make_edge_type_resource(edge_type))

    _ = _types_index  # satisfy linters


def _make_node_type_resource(node_type: str):
    """Return a zero-arg resource callable bound to *node_type*."""

    async def _resource() -> str:
        try:
            return node_type_resource_body(node_type)
        except Exception as exc:
            return _error_message(exc)

    _resource.__name__ = f"node_type_{node_type}"
    _resource.__doc__ = f"Documentation for graph node type {node_type}."
    return _resource


def _make_edge_type_resource(edge_type: str):
    """Return a zero-arg resource callable bound to *edge_type*."""

    async def _resource() -> str:
        try:
            return edge_type_resource_body(edge_type)
        except Exception as exc:
            return _error_message(exc)

    _resource.__name__ = f"edge_type_{edge_type}"
    _resource.__doc__ = f"Documentation for graph edge relation {edge_type}."
    return _resource
