"""INHERITS and IMPLEMENTS edge resolution pass."""

from collections.abc import Callable

from ...languages import LanguageHooks
from ...models.core import BaseNode, ClassNode, InterfaceNode
from ...models.extensions import EnumNode, StructNode
from ...models.structural import FileNode
from ..indexes import ImportIndex, SymbolIndex
from ..types import EdgeType, ResolvedEdge


def resolve_inheritance(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    hooks_map: dict[str, LanguageHooks],
) -> list[ResolvedEdge]:
    """Resolve base classes and interfaces to their definitions."""
    edges: list[ResolvedEdge] = []
    _default = LanguageHooks()

    for fnode in file_nodes:
        fp = fnode.path
        hooks = hooks_map.get(fnode.language, _default)
        for child in fnode.children:
            if not isinstance(child, (ClassNode, InterfaceNode, StructNode, EnumNode)):
                continue
            if not child.bases:
                continue

            child_id = node_id_fn(fp, child)

            for base_name in child.bases:
                target_id, target_node, strategy = _resolve_base(
                    fp,
                    base_name,
                    symbol_index,
                    import_index,
                )
                if target_id is None:
                    continue

                if isinstance(target_node, InterfaceNode) or hooks.is_protocol_base(base_name):
                    relation = EdgeType.IMPLEMENTS
                else:
                    relation = EdgeType.INHERITS

                edges.append(
                    ResolvedEdge(
                        source_id=child_id,
                        target_id=target_id,
                        relation=relation,
                        confidence=1.0,
                        resolved_by=strategy,
                    )
                )

    return edges


def _resolve_base(
    file_path: str,
    base_name: str,
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
) -> tuple[str | None, BaseNode | None, str]:
    """Try import-based resolution first, then direct symbol lookup."""
    imp = import_index.resolve_name(file_path, base_name)
    if imp is not None:
        _module, original_name, _imp_id = imp
        candidates = symbol_index.lookup(original_name)
        if candidates:
            return candidates[0][0], candidates[0][1], "import_then_match"

    candidates = symbol_index.lookup(base_name)
    if candidates:
        return candidates[0][0], candidates[0][1], "base_class_match"

    return None, None, ""
