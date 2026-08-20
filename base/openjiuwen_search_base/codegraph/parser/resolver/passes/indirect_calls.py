"""Indirect CALLS resolution for complex receiver expressions (subscripts, dereferences)."""

import re
from collections.abc import Callable

from ...languages import LanguageHooks
from ...models.core import BaseNode, CallNode, ClassNode, FunctionNode, InterfaceNode, LocalVarNode, PropertyNode
from ...models.structural import FileNode
from ..indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from ..types import EdgeType, ResolvedEdge
from ._utils import _strip_scope, contain_name, match_name

_SUBSCRIPT_RE = re.compile(r"\[[^\[\]]*\]")


def resolve_indirect_calls(
    file_nodes: list[FileNode],
    symbol_idx: SymbolIndex,
    import_idx: ImportIndex,
    class_method_idx: ClassMethodIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    hooks_map: dict[str, LanguageHooks],
    already_resolved: set[tuple[str, str, str]],
) -> list[ResolvedEdge]:
    """Resolve method calls on subscripted/dereferenced receivers.

    Handles patterns like ``objects[i].method()`` or ``self.items[0].update()``
    where the receiver requires type unwrapping to determine the element type.
    """
    edges: list[ResolvedEdge] = []
    _default = LanguageHooks()

    for fnode in file_nodes:
        fp = fnode.path
        hooks = hooks_map.get(fnode.language, _default)

        for child in fnode.children:
            if not isinstance(child, CallNode):
                continue
            if not child.receiver or not child.callee:
                continue

            source_id = _find_enclosing(fnode, child, node_id_fn)
            if source_id is None:
                source_id = node_id_fn(fp, fnode)

            # Skip if already resolved by the main calls pass
            key = (source_id, child.callee, child.receiver)
            if key in already_resolved:
                continue

            base_name, subscript_depth, has_deref = _strip_receiver(child.receiver)
            if subscript_depth == 0 and not has_deref:
                continue

            annotation = _lookup_type_annotation(fnode, base_name, child.context)
            if annotation is None:
                # Cross-file fallback: if context is in an out-of-class method,
                # look up the owning class from the symbol index
                annotation = _lookup_type_cross_file(fnode, base_name, child.context, symbol_idx, class_method_idx)
            if annotation is None:
                continue

            depth = subscript_depth
            if has_deref:
                depth += 1

            element_type = hooks.unwrap_receiver_type(annotation, depth)
            if element_type is None:
                continue

            # Verify the callee is a known method on the unwrapped type
            callee = child.callee
            methods = class_method_idx.get_methods(element_type)
            qualified_callee = f"{element_type}.{callee}"
            if not contain_name(callee, qualified_callee, methods):
                continue

            # Find the target FunctionNode
            target_id = _find_target_method(element_type, callee, class_method_idx, symbol_idx, node_id_fn, file_nodes)
            if target_id is None:
                continue

            edge_key = (source_id, target_id, EdgeType.CALLS)
            if edge_key in already_resolved:
                continue

            edges.append(
                ResolvedEdge(
                    source_id=source_id,
                    target_id=target_id,
                    relation=EdgeType.CALLS,
                    confidence=0.6,
                    resolved_by="indirect_receiver",
                )
            )

    return edges


def _strip_receiver(receiver: str) -> tuple[str, int, bool]:
    """Strip subscripts and detect dereference from a receiver expression.

    Returns (base_name, subscript_depth, has_deref).
    """
    r = receiver.strip()

    # Detect leading dereference
    has_deref = False
    if r.startswith("*"):
        has_deref = True
        r = r.lstrip("*").strip()

    # Count and strip trailing subscript expressions [...]
    depth = 0
    while True:
        stripped = _SUBSCRIPT_RE.sub("", r, count=0)
        # Count how many subscripts were in the original
        new_depth = len(_SUBSCRIPT_RE.findall(r))
        if new_depth == 0:
            break
        depth += new_depth
        r = stripped.strip()

    return (r, depth, has_deref)


def _lookup_type_annotation(
    fnode: FileNode,
    base_name: str,
    context: str | None,
) -> str | None:
    """Look up the type annotation for a base variable name.

    Handles self.X / this->X / this.X by searching class member fields.
    """
    # Strip self./this->/this. prefix to get the member name
    member_name: str | None = None
    if base_name.startswith("self."):
        member_name = base_name[5:]
    elif base_name.startswith("this->"):
        member_name = base_name[6:]
    elif base_name.startswith("this."):
        member_name = base_name[5:]

    for child in fnode.children:
        # Search class members for self.X / this->X pattern
        if member_name and isinstance(child, ClassNode):
            # Only search in the class that contains the context method
            if context:
                has_context = any(isinstance(m, FunctionNode) and match_name(m.name, context) for m in child.children)
                if not has_context:
                    continue
            for member in child.children:
                if (
                    isinstance(member, PropertyNode)
                    and _strip_scope(member.name) == member_name
                    and member.type_annotation
                ):
                    return member.type_annotation

        # Search class constructor parameters (e.g. self.particles from __init__)
        if member_name and isinstance(child, ClassNode):
            for member in child.children:
                if isinstance(member, FunctionNode):
                    for param in member.parameters:
                        if param.name == member_name and param.type_annotation:
                            return param.type_annotation

        # Also check out-of-class methods that own this class
        if member_name and isinstance(child, FunctionNode) and child.owner:
            qualified_context = f"{child.owner}.{context}" if context else None
            if context and match_name(child.name, context, qualified_context):
                # Search local variables in this method
                for sub in child.children:
                    if isinstance(sub, PropertyNode) and sub.name == member_name and sub.type_annotation:
                        return sub.type_annotation

        # Direct property at file level
        if not member_name and isinstance(child, (PropertyNode, LocalVarNode)):
            if _strip_scope(child.name) == base_name and child.type_annotation:
                return child.type_annotation

        # Function parameter or local variable
        if not member_name and isinstance(child, FunctionNode) and context:
            qualified_context = f"{child.owner}.{context}" if child.owner else None
            if match_name(child.name, context, qualified_context):
                for param in child.parameters:
                    if param.name == base_name and param.type_annotation:
                        return param.type_annotation
                for sub in child.children:
                    if (
                        isinstance(sub, (PropertyNode, LocalVarNode))
                        and _strip_scope(sub.name) == base_name
                        and sub.type_annotation
                    ):
                        return sub.type_annotation
                # For out-of-class methods, also search owning class members
                if child.owner:
                    for sibling in fnode.children:
                        if isinstance(sibling, ClassNode) and sibling.name == child.owner:
                            for member in sibling.children:
                                if (
                                    isinstance(member, PropertyNode)
                                    and _strip_scope(member.name) == base_name
                                    and member.type_annotation
                                ):
                                    return member.type_annotation

        # For in-class methods: unqualified names may refer to class members
        if not member_name and isinstance(child, ClassNode) and context:
            qualified_context = f"{child.name}.{context}"
            has_method = any(
                isinstance(m, FunctionNode) and match_name(m.name, context, qualified_context) for m in child.children
            )
            if has_method:
                for member in child.children:
                    if (
                        isinstance(member, PropertyNode)
                        and _strip_scope(member.name) == base_name
                        and member.type_annotation
                    ):
                        return member.type_annotation
        return None


def _lookup_type_cross_file(
    fnode: FileNode,
    base_name: str,
    context: str | None,
    symbol_idx: SymbolIndex,
    class_method_idx: ClassMethodIndex,
) -> str | None:
    """Cross-file fallback: find the type annotation from the owning class in another file."""
    if not context:
        return None

    # Find the out-of-class method that matches the context
    owner: str | None = None
    for child in fnode.children:
        if isinstance(child, FunctionNode) and child.owner:
            qualified_context = f"{child.owner}.{context}"
            if match_name(child.name, context, qualified_context):
                owner = child.owner
                break

    if not owner:
        return None

    # Look up the owning class from the symbol index
    class_ids = class_method_idx.get_class_ids(owner)
    if not class_ids:
        return None

    for class_id in class_ids:
        class_node = symbol_idx.get_by_id(class_id)
        if class_node is None or not isinstance(class_node, (ClassNode, InterfaceNode)):
            continue
        for member in class_node.children:
            if isinstance(member, PropertyNode) and _strip_scope(member.name) == base_name and member.type_annotation:
                return member.type_annotation

    return None


def _find_enclosing(
    fnode: FileNode,
    call_node: CallNode,
    node_id_fn: Callable[[str, BaseNode], str],
) -> str | None:
    """Find the enclosing function/method that contains this call."""
    context = call_node.context
    if not context:
        return None
    fp = fnode.path

    for child in fnode.children:
        if isinstance(child, FunctionNode):
            if child.owner:
                qualified_context = f"{child.owner}.{context}"
            else:
                qualified_context = None
            if match_name(child.name, context, qualified_context):
                return node_id_fn(fp, child)

        if isinstance(child, ClassNode):
            qualified_context = f"{child.name}.{context}"
            for member in child.children:
                if isinstance(member, FunctionNode) and match_name(member.name, context, qualified_context):
                    return node_id_fn(fp, member)

    return None


def _find_target_method(
    class_name: str,
    callee: str,
    class_method_idx: ClassMethodIndex,
    symbol_idx: SymbolIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    file_nodes: list[FileNode] | None = None,
) -> str | None:
    """Find the FunctionNode ID for a method on a given class."""
    class_ids = class_method_idx.get_class_ids(class_name)
    if not class_ids:
        return None

    qualified_callee = f"{class_name}.{callee}"
    target_class_id = class_ids[0]
    target_class = symbol_idx.get_by_id(target_class_id)

    # Search in-class method definitions
    if target_class is not None:
        for member in target_class.children:
            if isinstance(member, FunctionNode) and match_name(member.name, callee, qualified_callee):
                fp = target_class_id.split("::")[0]
                return node_id_fn(fp, member)

    # Search out-of-class method definitions (C++ pattern: top-level FunctionNode with owner)
    if file_nodes:
        for fnode in file_nodes:
            for child in fnode.children:
                if isinstance(child, FunctionNode) and child.owner == class_name:
                    if match_name(child.name, callee, qualified_callee):
                        return node_id_fn(fnode.path, child)

    # Fallback: synthesized nodes
    for candidate_name in (qualified_callee, callee):
        candidates = symbol_idx.lookup(candidate_name)
        if candidates:
            return candidates[0][0]

    return None
