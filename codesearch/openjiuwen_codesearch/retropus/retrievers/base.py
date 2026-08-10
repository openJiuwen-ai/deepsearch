# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Abstract interface for knowledge-graph text retrieval strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openjiuwen_codesearch.retropus.graph.graph_types import KnowledgeGraphNode
from openjiuwen_codesearch.retropus.graph.knowledge_graph import KnowledgeGraph

MAX_RESULT = 5


def _file_payload(file_node: KnowledgeGraphNode) -> Dict[str, Any]:
    n = file_node.node
    return {
        "node_id": file_node.node_id,
        "basename": n.basename,
        "relative_path": n.relative_path,
    }


def _ast_payload(ast_node: KnowledgeGraphNode) -> Dict[str, Any]:
    n = ast_node.node
    return {
        "node_id": ast_node.node_id,
        "type": n.type,
        "start_line": n.start_line,
        "end_line": n.end_line,
        "text": n.text,
    }


def _text_payload(text_node: KnowledgeGraphNode) -> Dict[str, Any]:
    n = text_node.node
    return {
        "node_id": text_node.node_id,
        "text": n.text,
        "start_line": n.start_line,
        "end_line": n.end_line,
    }


class AbstractBaseRetriever(ABC):
    """Pluggable query strategy over a ``KnowledgeGraph``."""

    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg

    def find_file_node_of_a_text_node(self, text_node: KnowledgeGraphNode) -> KnowledgeGraphNode:
        """Resolve the owning ``FileNode`` for a text chunk."""
        return self.kg.find_file_node_for_text_node(text_node)

    def iter_ast_candidates(
        self, target_file_nodes: List[KnowledgeGraphNode]
    ) -> Sequence[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]]:
        """Yield ``(file, ast)`` pairs for non-root AST nodes under ``target_file_nodes``."""
        wanted = {n.node_id for n in target_file_nodes}
        pairs_fn = getattr(self.kg, "get_ast_file_pairs", None)
        if callable(pairs_fn):
            pairs = pairs_fn()
            if len(wanted) >= len(self.kg.get_file_nodes()):
                return pairs
            return [(f, a) for f, a in pairs if f.node_id in wanted]

        roots = {
            edge.source.node_id: edge.target
            for edge in self.kg.get_has_ast_edges()
            if edge.source.node_id in wanted
        }
        children = self.kg.get_parent_to_children_map()
        by_id = {n.node_id: n for n in target_file_nodes}
        found: List[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]] = []
        for fid, root in roots.items():
            file_kg = by_id[fid]
            stack = list(children.get(root.node_id, ()))
            while stack:
                cur = stack.pop()
                found.append((file_kg, cur))
                kids = children.get(cur.node_id)
                if kids:
                    stack.extend(kids)
        return found

    @staticmethod
    def _format_ast_result(
        file_node: KnowledgeGraphNode, ast_node: KnowledgeGraphNode
    ) -> Dict[str, Any]:
        """Tool payload: ``FileNode`` + ``ASTNode`` property bags."""
        return {"FileNode": _file_payload(file_node), "ASTNode": _ast_payload(ast_node)}

    def _format_text_result(
        self, text_node: KnowledgeGraphNode, file_node: Optional[KnowledgeGraphNode] = None
    ) -> Dict[str, Any]:
        """Tool payload: ``FileNode`` + ``TextNode`` property bags."""
        owner = file_node or self.find_file_node_of_a_text_node(text_node)
        return {"FileNode": _file_payload(owner), "TextNode": _text_payload(text_node)}

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


AbstractRetriever = AbstractBaseRetriever
