"""INSTANTIATES and TYPE_OF edge resolution pass."""

from collections.abc import Callable

from ...constants import FILTER_BUILTIN_NAMES
from ...languages import LanguageHooks
from ...models.core import BaseNode, CallNode, ClassNode, FunctionNode, PropertyNode
from ...models.structural import FileNode
from ..indexes import ImportIndex, SymbolIndex
from ..types import EdgeType, ResolvedEdge
from ._utils import match_name


def resolve_types(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    hooks_map: dict[str, LanguageHooks],
) -> list[ResolvedEdge]:
    """Resolve type annotations and constructor calls to definitions."""
    edges: list[ResolvedEdge] = []
    edges.extend(_resolve_instantiates(file_nodes, symbol_index, import_index, node_id_fn, hooks_map))
    edges.extend(_resolve_type_of(file_nodes, symbol_index, import_index, node_id_fn, hooks_map))
    return edges


def _resolve_instantiates(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    hooks_map: dict[str, LanguageHooks],
) -> list[ResolvedEdge]:
    """Emit INSTANTIATES edges for constructor calls (uppercase callee convention)."""
    edges: list[ResolvedEdge] = []
    _default = LanguageHooks()

    for fnode in file_nodes:
        fp = fnode.path
        hooks = hooks_map.get(fnode.language, _default)
        for child in fnode.children:
            if not isinstance(child, CallNode):
                continue
            callee = child.callee
            if not callee:
                continue
            if callee in hooks.builtins and not symbol_index.lookup(callee) and FILTER_BUILTIN_NAMES:
                continue
            if not hooks.is_constructor_call(callee):
                continue

            target_id = _resolve_name_via_imports(fp, callee, symbol_index, import_index)
            if target_id is None:
                continue

            target_node = symbol_index.get_by_id(target_id)
            if target_node is None or not isinstance(target_node, ClassNode):
                continue

            source_id = _find_enclosing_id(fnode, child, node_id_fn)
            edges.append(
                ResolvedEdge(
                    source_id=source_id,
                    target_id=target_id,
                    relation=EdgeType.INSTANTIATES,
                    confidence=0.9,
                    resolved_by="constructor_convention",
                )
            )

    return edges


def _resolve_type_of(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    hooks_map: dict[str, LanguageHooks],
) -> list[ResolvedEdge]:
    """Emit TYPE_OF edges from properties and function return types to type definitions."""
    edges: list[ResolvedEdge] = []
    _default = LanguageHooks()

    for fnode in file_nodes:
        fp = fnode.path
        hooks = hooks_map.get(fnode.language, _default)
        for child in fnode.children:
            if isinstance(child, PropertyNode) and child.type_annotation:
                prop_id = node_id_fn(fp, child)
                _emit_type_of_edges(fp, prop_id, child.type_annotation, symbol_index, import_index, edges, hooks)

            elif isinstance(child, FunctionNode):
                _emit_function_type_edges(fp, child, symbol_index, import_index, node_id_fn, edges, hooks)

            if isinstance(child, ClassNode):
                for member in child.children:
                    if isinstance(member, PropertyNode) and member.type_annotation:
                        mem_id = node_id_fn(fp, member)
                        _emit_type_of_edges(
                            fp, mem_id, member.type_annotation, symbol_index, import_index, edges, hooks
                        )
                    elif isinstance(member, FunctionNode):
                        _emit_function_type_edges(fp, member, symbol_index, import_index, node_id_fn, edges, hooks)

    return edges


def _emit_function_type_edges(
    file_path: str,
    func: FunctionNode,
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    edges: list[ResolvedEdge],
    hooks: LanguageHooks,
) -> None:
    """Emit TYPE_OF edges for a function's return type and parameter annotations."""
    func_id = node_id_fn(file_path, func)
    if func.return_type:
        _emit_type_of_edges(file_path, func_id, func.return_type, symbol_index, import_index, edges, hooks)
    for param in func.parameters:
        if param.type_annotation:
            _emit_type_of_edges(file_path, func_id, param.type_annotation, symbol_index, import_index, edges, hooks)


def _emit_type_of_edges(
    file_path: str,
    source_id: str,
    annotation: str,
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    edges: list[ResolvedEdge],
    hooks: LanguageHooks,
) -> None:
    """Extract type names from an annotation and emit TYPE_OF edges."""
    seen: set[str] = set()
    for type_name in hooks.extract_type_names(annotation):
        if type_name in seen:
            continue
        if type_name in hooks.builtins and not symbol_index.lookup(type_name) and FILTER_BUILTIN_NAMES:
            continue
        seen.add(type_name)
        target_id = _resolve_name_via_imports(file_path, type_name, symbol_index, import_index)
        if target_id is not None:
            edges.append(
                ResolvedEdge(
                    source_id=source_id,
                    target_id=target_id,
                    relation=EdgeType.TYPE_OF,
                    confidence=0.8,
                    resolved_by="annotation_match",
                )
            )


def _resolve_name_via_imports(
    file_path: str,
    name: str,
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
) -> str | None:
    """Resolve a name via import index first, then direct symbol lookup."""
    imp = import_index.resolve_name(file_path, name)
    if imp is not None:
        _module, original_name, _imp_id = imp
        candidates = symbol_index.lookup(original_name)
        if candidates:
            return candidates[0][0]

    candidates = symbol_index.lookup(name)
    if candidates:
        return candidates[0][0]

    return None


def _find_enclosing_id(
    fnode: FileNode,
    call_node: CallNode,
    node_id_fn: Callable[[str, BaseNode], str],
) -> str:
    """Find the enclosing function/class for a call node, falling back to the file.

    The context is a short name (e.g. ``SaveImg``) but method names may be
    qualified (e.g. ``ArrayHelper.SaveImg`` or ``ArrayHelper.SaveImg(int[], String)``),
    so both forms are checked.
    """
    fp = fnode.path
    ctx = call_node.context
    if ctx:
        for child in fnode.children:
            if isinstance(child, FunctionNode) and match_name(child.name, ctx):
                return node_id_fn(fp, child)
            if isinstance(child, ClassNode):
                qualified = f"{child.name}.{ctx}"
                for member in child.children:
                    if isinstance(member, FunctionNode) and match_name(member.name, ctx, qualified):
                        return node_id_fn(fp, member)
                if ctx == child.name:
                    init_prefix = f"{child.name}.<init>"
                    for member in child.children:
                        if isinstance(member, FunctionNode) and member.name.startswith(init_prefix):
                            return node_id_fn(fp, member)
                    return node_id_fn(fp, child)
    return node_id_fn(fp, fnode)
