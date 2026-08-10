# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Build a per-file subgraph (AST for code, chained text chunks for docs)."""

from __future__ import annotations

import bisect
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
    TextNode,
)
from openjiuwen_codesearch.retropus.graph.text_splitter import split_text

_TEXT_SUFFIXES = frozenset({".markdown", ".md", ".txt", ".rst"})

GraphBuildResult = Tuple[int, Sequence[KnowledgeGraphNode], Sequence[KnowledgeGraphEdge]]


@dataclass
class _Chunk:
    """Internal text piece plus optional line-span metadata."""

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


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
        return file.suffix.lower() in _TEXT_SUFFIXES

    def supports_file(self, file: Path) -> bool:
        """Whether this builder can index ``file``."""
        return self.support_code_file(file) or self.support_text_file(file)

    def build_file_graph(
        self, parent_node: KnowledgeGraphNode, file: Path, next_node_id: int
    ) -> GraphBuildResult:
        """Attach AST or text-chunk subgraph under ``parent_node``."""
        if self.support_code_file(file):
            return self._build_ast_subgraph(parent_node, file, next_node_id)
        return self._build_text_subgraph(parent_node, file, next_node_id)

    def _build_ast_subgraph(
        self, parent_node: KnowledgeGraphNode, file: Path, next_node_id: int
    ) -> GraphBuildResult:
        """DFS-walk a tree-sitter tree into ``PARENT_OF`` / ``HAS_AST`` edges."""
        from openjiuwen_codesearch.retropus.parser import tree_sitter_parser  # guarded

        tree = tree_sitter_parser.parse(file)
        root = tree.root_node
        if root.has_error or root.child_count == 0:
            return next_node_id, [], []

        nodes: List[KnowledgeGraphNode] = []
        edges: List[KnowledgeGraphEdge] = []

        def _wrap(ts_node) -> KnowledgeGraphNode:
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
    ) -> GraphBuildResult:
        """Chunk a text file and wire ``HAS_TEXT`` / ``NEXT_CHUNK`` edges."""
        body = file.read_text(encoding="utf-8")
        pieces = split_text(
            body, chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )
        chunks = [_Chunk(text=p) for p in pieces]
        self._annotate_line_spans(body, chunks)
        return self._link_text_chunks(chunks, parent_node, next_node_id)

    @staticmethod
    def _annotate_line_spans(body: str, chunks: Sequence[_Chunk]) -> None:
        """Attach 0-based start/end line metadata by locating each chunk in ``body``."""
        # Byte offset of each line start (line 0 → 0).
        line_starts = [0]
        for i, ch in enumerate(body):
            if ch == "\n":
                line_starts.append(i + 1)

        cursor = 0
        for chunk in chunks:
            start = body.find(chunk.text, cursor)
            if start < 0:
                start = body.find(chunk.text)
                if start < 0:
                    raise ValueError("Chunk text not found in original text.")
            end = start + len(chunk.text)
            cursor = end
            # Same convention as historical Retropus text spans: first line-start
            # strictly after the offset (bisect_right), not the containing line.
            chunk.metadata["start_line"] = bisect.bisect_right(line_starts, start)
            chunk.metadata["end_line"] = bisect.bisect_right(line_starts, end)

    @staticmethod
    def _link_text_chunks(
        chunks: Sequence[_Chunk],
        parent_node: KnowledgeGraphNode,
        next_node_id: int,
    ) -> GraphBuildResult:
        nodes: List[KnowledgeGraphNode] = []
        edges: List[KnowledgeGraphEdge] = []
        prev: KnowledgeGraphNode | None = None

        for chunk in chunks:
            payload = TextNode(
                text=chunk.text,
                start_line=int(chunk.metadata.get("start_line", 0)),
                end_line=int(chunk.metadata.get("end_line", 0)),
            )
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
