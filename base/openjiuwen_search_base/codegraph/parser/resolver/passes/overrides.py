"""OVERRIDES edge resolution: method redefines nearest ancestor method."""

from collections import defaultdict, deque
from collections.abc import Callable

from ...models.core import BaseNode, ClassNode, FunctionNode, InterfaceNode
from ...models.extensions import EnumNode, StructNode
from ...models.structural import FileNode
from ..indexes import SymbolIndex
from ..types import EdgeType, ResolvedEdge
from ._utils import _strip_scope, match_name

_OVERRIDE_MARKERS = frozenset({"Override", "override"})
_TYPE_WITH_METHODS = (ClassNode, InterfaceNode, StructNode, EnumNode)


def resolve_overrides(
    file_nodes: list[FileNode],
    inheritance_edges: list[ResolvedEdge],
    symbol_index: SymbolIndex,
    node_id_fn: Callable[[str, BaseNode], str],
) -> list[ResolvedEdge]:
    """Emit OVERRIDES edges from methods to nearest same-name, same-arity ancestors."""
    bases = _build_base_map(inheritance_edges)
    class_methods = _index_class_methods(file_nodes, symbol_index, node_id_fn)

    edges: list[ResolvedEdge] = []
    for class_id, methods in class_methods.items():
        if class_id not in bases:
            continue
        for method_id, method in methods:
            target = _nearest_override_target(class_id, method, bases, class_methods)
            if target is None:
                continue
            target_id, _target_fn = target
            edges.append(
                ResolvedEdge(
                    source_id=method_id,
                    target_id=target_id,
                    relation=EdgeType.OVERRIDES,
                    confidence=1.0,
                    resolved_by=("override_annotation" if _has_override_annotation(method) else "override_match"),
                )
            )
    return edges


def _build_base_map(inheritance_edges: list[ResolvedEdge]) -> dict[str, list[str]]:
    bases: dict[str, list[str]] = defaultdict(list)
    for edge in inheritance_edges:
        if edge.relation in (EdgeType.INHERITS, EdgeType.IMPLEMENTS):
            bases[edge.source_id].append(edge.target_id)
    return bases


def _index_class_methods(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    node_id_fn: Callable[[str, BaseNode], str],
) -> dict[str, list[tuple[str, FunctionNode]]]:
    """Map class_id → [(method_id, FunctionNode), ...]."""
    class_methods: dict[str, list[tuple[str, FunctionNode]]] = defaultdict(list)
    class_ids: dict[str, str] = {}

    for fnode in file_nodes:
        fp = fnode.path
        for child in fnode.children:
            if isinstance(child, _TYPE_WITH_METHODS):
                cid = node_id_fn(fp, child)
                class_ids[child.name] = cid
                for member in child.children:
                    if not isinstance(member, FunctionNode):
                        continue
                    if member.func_type == "method-guessed":
                        continue
                    class_methods[cid].append((node_id_fn(fp, member), member))

    for fnode in file_nodes:
        fp = fnode.path
        for child in fnode.children:
            if not isinstance(child, FunctionNode) or child.owner is None:
                continue
            if child.func_type == "method-guessed":
                continue
            owner_id = class_ids.get(child.owner)
            if owner_id is None:
                candidates = symbol_index.lookup(child.owner)
                if candidates:
                    owner_id = candidates[0][0]
                    class_ids[child.owner] = owner_id
            if owner_id is not None:
                class_methods[owner_id].append((node_id_fn(fp, child), child))

    return class_methods


def _nearest_override_target(
    class_id: str,
    method: FunctionNode,
    bases: dict[str, list[str]],
    class_methods: dict[str, list[tuple[str, FunctionNode]]],
) -> tuple[str, FunctionNode] | None:
    """BFS ancestors; return first same-name, same-arity method."""
    queue: deque[str] = deque(bases.get(class_id, ()))
    seen: set[str] = set()
    arity = len(method.parameters)
    short = _method_basename(method.name)

    while queue:
        base_id = queue.popleft()
        if base_id in seen:
            continue
        seen.add(base_id)

        for mid, ancestor in class_methods.get(base_id, ()):
            if len(ancestor.parameters) != arity:
                continue
            if not _names_match(ancestor.name, short):
                continue
            return mid, ancestor

        queue.extend(bases.get(base_id, ()))

    return None


def _method_basename(name: str) -> str:
    """Unqualified method name: ``Shape.area`` → ``area``, strips scope/overload suffixes."""
    stripped = _strip_scope(name)
    before_overload = stripped.split("(", 1)[0]
    return before_overload.rsplit(".", 1)[-1]


def _names_match(node_name: str, short: str) -> bool:
    """True if *node_name* is *short* or ``Owner.short`` (plus overload/scope suffixes)."""
    return match_name(node_name, short) or _method_basename(node_name) == short


def _has_override_annotation(method: FunctionNode) -> bool:
    for deco in method.decorators:
        marker = deco.lstrip("@")
        if marker in _OVERRIDE_MARKERS or deco in {"@Override", "@override"}:
            return True
    return False
