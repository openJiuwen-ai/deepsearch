"""In-memory graph session for MCP tools (no FastMCP dependency)."""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..export import index_directory
from ..search import SearchResult
from ..search import search_edges as search_edges_impl
from ..search import search_nodes as search_nodes_impl
from ..search import search_regex as search_regex_impl


@dataclass
class GraphSession:
    """Holds the last indexed graph for MCP search tools.

    :ivar root: Indexed project root, or ``None`` before the first index.
    :ivar output_dir: Directory where JSONL/JCP were written.
    :ivar nodes: Exported node dicts.
    :ivar edges: Exported edge dicts.
    :ivar file_count: Number of source files included in the last index.
    """

    root: Path | None = None
    output_dir: Path | None = None
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    file_count: int = 0

    @property
    def is_indexed(self) -> bool:
        """Return whether a graph has been indexed into this session."""
        return self.root is not None

    async def index(self, path: str | Path) -> dict[str, Any]:
        """Parse *path*, write ``.jiuwen_graph``, and replace session state.

        :param path: Project directory to index.
        :returns: Compact summary with counts and output paths.
        :raises NotADirectoryError: If *path* is not a directory.
        :raises FileNotFoundError: If no supported source files are found.
        """
        root = Path(path).expanduser().resolve()
        artifacts, nodes, edges, files = await index_directory(root, quiet=True)
        output_dir = root / ".jiuwen_graph"
        if artifacts.nodes_path is not None:
            output_dir = artifacts.nodes_path.parent

        self.root = root
        self.output_dir = output_dir
        self.nodes = nodes
        self.edges = edges
        self.file_count = len(files)

        return {
            "root": str(root),
            "output_dir": str(output_dir),
            "file_count": self.file_count,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_counts": dict(Counter(n["type"] for n in nodes)),
            "edge_counts": dict(Counter(e["relation"] for e in edges)),
            "nodes_path": str(artifacts.nodes_path) if artifacts.nodes_path else None,
            "edges_path": str(artifacts.edges_path) if artifacts.edges_path else None,
            "jcp_path": str(artifacts.jcp_path) if artifacts.jcp_path else None,
        }

    def search_nodes(self, query: str, *, limit: int = 50) -> SearchResult:
        """Search indexed nodes with viewer query syntax.

        :param query: Viewer search string (free text and ``{field:glob}``).
        :param limit: Maximum matches to return in ``matches``.
        :returns: Sorted :class:`~openjiuwen_search_base.codegraph.search.SearchResult`.
        :raises RuntimeError: If no graph has been indexed yet.
        """
        self._require_indexed()
        return search_nodes_impl(self.nodes, query, limit=limit)

    def search_edges(self, query: str, *, limit: int = 50) -> SearchResult:
        """Search indexed edges with viewer query syntax.

        :param query: Viewer search string (free text and ``{field:glob}``).
        :param limit: Maximum matches to return in ``matches``.
        :returns: Sorted :class:`~openjiuwen_search_base.codegraph.search.SearchResult`
            with endpoint tag stats from the indexed nodes.
        :raises RuntimeError: If no graph has been indexed yet.
        """
        self._require_indexed()
        return search_edges_impl(self.edges, query, limit=limit, nodes=self.nodes)

    def search_regex(
        self,
        pattern: str,
        *,
        target: Literal["nodes", "edges"] = "nodes",
        limit: int = 50,
        ignore_case: bool = True,
    ) -> SearchResult:
        """Search indexed nodes or edges with a regular expression.

        :param pattern: Python regex (not viewer search syntax).
        :param target: ``"nodes"`` or ``"edges"``.
        :param limit: Maximum matches to return in ``matches``.
        :param ignore_case: Case-insensitive match when true.
        :returns: Sorted :class:`~openjiuwen_search_base.codegraph.search.SearchResult`.
        :raises RuntimeError: If no graph has been indexed yet.
        :raises ValueError: If *pattern* or *target* is invalid.
        """
        self._require_indexed()
        return search_regex_impl(
            pattern,
            target=target,
            nodes=self.nodes,
            edges=self.edges,
            limit=limit,
            ignore_case=ignore_case,
        )

    def _require_indexed(self) -> None:
        if not self.is_indexed:
            raise RuntimeError("No graph indexed. Call index(path) first.")
