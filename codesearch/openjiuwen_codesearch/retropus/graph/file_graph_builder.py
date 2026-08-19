# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Build a per-file subgraph (AST for code, chained text chunks for docs)."""

from __future__ import annotations

import bisect
from collections import deque
from pathlib import Path
from typing import Any, List, Sequence, Tuple

from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
)
from openjiuwen_codesearch.retropus.graph.text_splitter import split_text

# Plain-text / markup suffixes handled by the chunking path.
_DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt")

_Subgraph = Tuple[int, Sequence[KnowledgeGraphNode], Sequence[KnowledgeGraphEdge]]


class FileGraphBuilder:
    """Construct knowledge-graph vertices/edges for one repository file."""

    def __init__(self, max_ast_depth: int, chunk_size: int, chunk_overlap: int):
        self.max_ast_depth = max_ast_depth
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    @staticmethod
    def support_code_file(file: Path) -> bool:
        """True when tree-sitter can parse ``file``."""
        from openjiuwen_codesearch.retropus.parser import tree_sitter_parser  # guarded

        return tree_sitter_parser.supports_file(file)

    @staticmethod
    def support_text_file(file: Path) -> bool:
        """True for markdown / rst / plain-text documents."""
        return file.suffix.lower() in _DOC_SUFFIXES

    def supports_file(self, file: Path) -> bool:
        """Whether this builder can index ``file``."""
        return self.support_code_file(file) or self.support_text_file(file)

    def build_file_graph(
        self, parent_node: KnowledgeGraphNode, file: Path, next_node_id: int
    ) -> _Subgraph:
        """Attach AST or text-chunk subgraph under ``parent_node``."""
        if self.support_code_file(file):
            return self._build_ast_subgraph(parent_node, file, next_node_id)
        return self._build_text_subgraph(parent_node, file, next_node_id)

    def _build_ast_subgraph(
        self, parent_node: KnowledgeGraphNode, file: Path, next_node_id: int
    ) -> _Subgraph:
        """DFS-walk a tree-sitter tree into ``PARENT_OF`` / ``HAS_AST`` edges."""
        from openjiuwen_codesearch.retropus.parser import tree_sitter_parser  # guarded

        tree = tree_sitter_parser.parse(file)
        root = tree.root_node
        if root.has_error or root.child_count == 0:
            return next_node_id, (), ()

        nodes: List[KnowledgeGraphNode] = []
        edges: List[KnowledgeGraphEdge] = []

        def _wrap(ts_node: Any) -> KnowledgeGraphNode:
            nonlocal next_node_id
            payload = ASTNode(
                type=ts_node.type,
                start_line=ts_node.start_point[0] + 1,
                end_line=ts_node.end_point[0] + 1,
                text=ts_node.text.decode("utf-8"),
            )
            kg = KnowledgeGraphNode(next_node_id, payload)
            next_node_id += 1
            nodes.append(kg)
            return kg

        kg_root = _wrap(root)
        edges.append(
            KnowledgeGraphEdge(parent_node, kg_root, KnowledgeGraphEdgeType.has_ast)
        )

        # Explicit stack: (ts_node, kg_node, depth). Depth 1 = AST root.
        stack: deque[tuple[Any, KnowledgeGraphNode, int]] = deque([(root, kg_root, 1)])
        while stack:
            ts_node, kg_node, depth = stack.pop()
            # Allow creating children of depth==max; skip expanding those children.
            if depth > self.max_ast_depth:
                continue
            for child in ts_node.children:
                kg_child = _wrap(child)
                edges.append(
                    KnowledgeGraphEdge(kg_node, kg_child, KnowledgeGraphEdgeType.parent_of)
                )
                stack.append((child, kg_child, depth + 1))

        return next_node_id, nodes, edges

    def _build_text_subgraph(
        self, parent_node: KnowledgeGraphNode, file: Path, next_node_id: int
    ) -> _Subgraph:
        """Chunk a text file and wire ``HAS_TEXT`` / ``NEXT_CHUNK`` edges."""
        from openjiuwen_codesearch.retropus.graph.graph_types import TextNode

        body = file.read_text(encoding="utf-8")
        pieces = split_text(
            body, chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )
        spans = _line_spans_for_pieces(body, pieces)
        return _chain_text_nodes(pieces, spans, parent_node, next_node_id, TextNode)


def _line_spans_for_pieces(
    body: str, pieces: Sequence[str]
) -> List[Tuple[int, int]]:
    """Map each piece to (start_line, end_line) using bisect_right convention."""
    line_starts = [0]
    for i, ch in enumerate(body):
        if ch == "\n":
            line_starts.append(i + 1)

    spans: List[Tuple[int, int]] = []
    cursor = 0
    for piece in pieces:
        start = body.find(piece, cursor)
        if start < 0:
            start = body.find(piece)
            if start < 0:
                raise ValueError("Chunk text not found in original text.")
        end = start + len(piece)
        cursor = end
        # Historical Retropus convention: first line-start strictly after offset.
        spans.append(
            (
                bisect.bisect_right(line_starts, start),
                bisect.bisect_right(line_starts, end),
            )
        )
    return spans


def _chain_text_nodes(
    pieces: Sequence[str],
    spans: Sequence[Tuple[int, int]],
    parent_node: KnowledgeGraphNode,
    next_node_id: int,
    text_node_cls: Any,
) -> _Subgraph:
    nodes: List[KnowledgeGraphNode] = []
    edges: List[KnowledgeGraphEdge] = []
    prev: KnowledgeGraphNode | None = None

    for piece, (start_line, end_line) in zip(pieces, spans):
        payload = text_node_cls(text=piece, start_line=start_line, end_line=end_line)
        kg = KnowledgeGraphNode(next_node_id, payload)
        next_node_id += 1
        nodes.append(kg)
        edges.append(
            KnowledgeGraphEdge(parent_node, kg, KnowledgeGraphEdgeType.has_text)
        )
        if prev is not None:
            edges.append(
                KnowledgeGraphEdge(prev, kg, KnowledgeGraphEdgeType.next_chunk)
            )
        prev = kg

    return next_node_id, nodes, edges
