"""IMPORTS edge resolution pass."""

from collections.abc import Callable

from ...models.core import BaseNode, ClassNode, FunctionNode, ImportNode
from ...models.structural import FileNode
from ..indexes import SymbolIndex
from ..types import EdgeType, ResolvedEdge


def _find_enclosing_scope(
    fnode: FileNode,
    target: BaseNode,
    node_id_fn: Callable[[str, BaseNode], str],
) -> str:
    """Return the ID of the innermost function/class whose span contains *target*, or the file ID."""
    fp = fnode.path
    line = target.span.line_start
    best: BaseNode | None = None
    best_size = float("inf")

    for child in fnode.children:
        if not isinstance(child, (FunctionNode, ClassNode)):
            continue
        s = child.span
        if s.line_start <= line <= s.line_end:
            size = s.line_end - s.line_start
            if size < best_size:
                best, best_size = child, size
        if isinstance(child, ClassNode):
            for member in child.children:
                if not isinstance(member, FunctionNode):
                    continue
                ms = member.span
                if ms.line_start <= line <= ms.line_end:
                    msize = ms.line_end - ms.line_start
                    if msize < best_size:
                        best, best_size = member, msize

    if best is not None:
        return node_id_fn(fp, best)
    return node_id_fn(fp, fnode)


def resolve_imports(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    node_id_fn: Callable[[str, BaseNode], str],
) -> list[ResolvedEdge]:
    """Resolve import statements to their target definitions."""
    edges: list[ResolvedEdge] = []

    for fnode in file_nodes:
        for child in fnode.children:
            if not isinstance(child, ImportNode):
                continue
            if child.is_wildcard:
                continue

            source_id = _find_enclosing_scope(fnode, child, node_id_fn)
            names_to_resolve = list(child.names) if child.names else [child.module.rsplit(".", maxsplit=1)[-1]]

            for name in names_to_resolve:
                candidates = symbol_index.lookup(name)
                if not candidates:
                    # Try qualified: module.name
                    candidates = symbol_index.lookup(f"{child.module}.{name}")
                for target_id, _target_node in candidates:
                    edges.append(
                        ResolvedEdge(
                            source_id=source_id,
                            target_id=target_id,
                            relation=EdgeType.IMPORTS,
                            confidence=1.0,
                            resolved_by="import_match",
                        )
                    )

    return edges
