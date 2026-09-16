# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure helpers for KnowledgeGraph presentation / comparison (no graph mutation)."""

from __future__ import annotations

from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

from openjiuwen_codesearch.retropus.graph.graph_types import (
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
)

_ChildrenFn = Callable[[KnowledgeGraphNode], Sequence[KnowledgeGraphNode]]


def render_directory_tree(
    root: KnowledgeGraphNode,
    children_of: _ChildrenFn,
    *,
    max_depth: int = 5,
    max_lines: int = 5000,
) -> str:
    """Depth-first box-drawing tree; stops at ``max_depth`` / ``max_lines``."""
    if max_lines <= 0:
        return ""

    out: List[str] = []

    def walk(
        node: KnowledgeGraphNode,
        depth: int,
        prefix: str,
        last_sibling: bool,
    ) -> None:
        if len(out) >= max_lines or depth > max_depth:
            return
        if depth == 0:
            out.append(node.node.basename)
        else:
            branch = "└── " if last_sibling else "├── "
            out.append(f"{prefix}{branch}{node.node.basename}")

        kids = sorted(children_of(node), key=lambda n: n.node.basename)
        if not kids or depth >= max_depth:
            return
        child_prefix = ""
        if depth > 0:
            child_prefix = prefix + ("    " if last_sibling else "|   ")
        last_i = len(kids) - 1
        for i, kid in enumerate(kids):
            walk(kid, depth + 1, child_prefix, i == last_i)

    walk(root, 0, "", True)
    return "\n".join(out)


def unique_ast_types(nodes: Iterable[KnowledgeGraphNode]) -> List[str]:
    return sorted({n.node.type for n in nodes})


def merge_undirected(
    node_id: int,
    outbound: Mapping[int, Sequence[KnowledgeGraphNode]],
    inbound: Mapping[int, Sequence[KnowledgeGraphNode]],
) -> List[KnowledgeGraphNode]:
    """Union of out- and in-neighbors, excluding ``node_id``, order-stable."""
    merged: List[KnowledgeGraphNode] = []
    seen = {node_id}
    for neighbor in (*outbound.get(node_id, ()), *inbound.get(node_id, ())):
        if neighbor.node_id in seen:
            continue
        seen.add(neighbor.node_id)
        merged.append(neighbor)
    return merged


def resolve_text_owner(
    text_node_id: int,
    prev_chunk: Mapping[int, KnowledgeGraphNode],
    first_chunk_file: Mapping[int, KnowledgeGraphNode],
) -> KnowledgeGraphNode:
    """Follow ``NEXT_CHUNK`` backward until ``HAS_TEXT`` owner is found."""
    nid = text_node_id
    while nid in prev_chunk:
        nid = prev_chunk[nid].node_id
    owner = first_chunk_file.get(nid)
    if owner is None:
        raise KeyError(f"no file node for text node id {nid}")
    return owner


def same_graph(
    left_nodes: Sequence[KnowledgeGraphNode],
    left_edges: Sequence[KnowledgeGraphEdge],
    right_nodes: Sequence[KnowledgeGraphNode],
    right_edges: Sequence[KnowledgeGraphEdge],
) -> bool:
    """Structural equality of node/edge multisets (ids + payloads + edge types)."""
    ln = sorted(left_nodes, key=lambda n: n.node_id)
    rn = sorted(right_nodes, key=lambda n: n.node_id)
    if len(ln) != len(rn):
        return False
    for a, b in zip(ln, rn):
        if a != b:
            return False

    def edge_key(e: KnowledgeGraphEdge) -> Tuple[int, int, object]:
        return (e.source.node_id, e.target.node_id, e.type)

    le = sorted(left_edges, key=edge_key)
    re = sorted(right_edges, key=edge_key)
    if len(le) != len(re):
        return False
    for a, b in zip(le, re):
        if a != b:
            return False
    return True


def require_index(value: Optional[object], message: str) -> object:
    if value is None:
        raise RuntimeError(message)
    return value
