"""Abstract interface for knowledge-graph text retrieval strategies (copied from Prometheus)."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openjiuwen_codesearch.retropus.graph.graph_types import KnowledgeGraphNode
from openjiuwen_codesearch.retropus.graph.knowledge_graph import KnowledgeGraph

MAX_RESULT = 5


class AbstractBaseRetriever(ABC):
    """Pluggable text-search strategy over a KnowledgeGraph.

    Implementations own AST/text query matching so strategies (substring, BM25, ...)
    can be swapped without changing the tool wiring.
    """

    def __init__(self, kg: KnowledgeGraph):
        """Bind this retriever to an in-memory ``KnowledgeGraph``."""
        self.kg = kg

    def find_file_node_of_a_text_node(self, text_node: KnowledgeGraphNode) -> KnowledgeGraphNode:
        """Find the FileNode that owns the given TextNode (following NEXT_CHUNK to root)."""
        return self.kg.find_file_node_for_text_node(text_node)

    def _iter_ast_candidates(
        self, target_file_nodes: List[KnowledgeGraphNode]
    ) -> Sequence[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]]:
        """Return (file_node, ast_node) pairs for non-root AST nodes under the given files."""
        target_ids = {n.node_id for n in target_file_nodes}
        # Prefer the precomputed pairs index (built once after KG parse).
        if hasattr(self.kg, "get_ast_file_pairs"):
            pairs = self.kg.get_ast_file_pairs()
            if len(target_ids) >= len(self.kg.get_file_nodes()):
                return pairs
            return [
                (file_node, ast_node)
                for file_node, ast_node in pairs
                if file_node.node_id in target_ids
            ]

        has_ast_edges = self.kg.get_has_ast_edges()
        file_to_ast_map = {
            edge.source.node_id: edge.target
            for edge in has_ast_edges
            if edge.source.node_id in target_ids
        }
        parent_to_children = self.kg.get_parent_to_children_map()
        file_by_id = {n.node_id: n for n in target_file_nodes}

        candidates: List[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]] = []
        for file_node_id, root_ast in file_to_ast_map.items():
            file_node = file_by_id[file_node_id]
            # Skip the file-level root AST node; index only its descendants.
            stack = list(parent_to_children.get(root_ast.node_id, ()))
            while stack:
                current_node = stack.pop()
                candidates.append((file_node, current_node))
                children = parent_to_children.get(current_node.node_id)
                if children:
                    stack.extend(children)
        return candidates

    @staticmethod
    def _format_ast_result(
        file_node: KnowledgeGraphNode, ast_node: KnowledgeGraphNode
    ) -> Dict[str, Any]:
        """Serialize a hit as the ``FileNode`` + ``ASTNode`` tool payload dict."""
        return {
            "FileNode": {
                "node_id": file_node.node_id,
                "basename": file_node.node.basename,
                "relative_path": file_node.node.relative_path,
            },
            "ASTNode": {
                "node_id": ast_node.node_id,
                "type": ast_node.node.type,
                "start_line": ast_node.node.start_line,
                "end_line": ast_node.node.end_line,
                "text": ast_node.node.text,
            },
        }

    def _format_text_result(
        self, text_node: KnowledgeGraphNode, file_node: Optional[KnowledgeGraphNode] = None
    ) -> Dict[str, Any]:
        """Serialize a hit as the ``FileNode`` + ``TextNode`` tool payload dict."""
        if file_node is None:
            file_node = self.find_file_node_of_a_text_node(text_node)
        return {
            "FileNode": {
                "node_id": file_node.node_id,
                "basename": file_node.node.basename,
                "relative_path": file_node.node.relative_path,
            },
            "TextNode": {
                "node_id": text_node.node_id,
                "text": text_node.node.text,
                "start_line": text_node.node.start_line,
                "end_line": text_node.node.end_line,
            },
        }

    @abstractmethod
    def search_ast_nodes(
        self, query: str, target_file_nodes: List[KnowledgeGraphNode]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Search AST nodes under the given files for ``query``."""

    @abstractmethod
    def search_text_nodes(
        self, query: str, basename: Optional[str] = None
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Search TextNodes for ``query``, optionally scoped to a file basename."""


# Backward-compatible alias
AbstractRetriever = AbstractBaseRetriever
