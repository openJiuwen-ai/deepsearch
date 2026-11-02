"""Java language parser using tree-sitter."""

import asyncio
import logging
from pathlib import Path

import tree_sitter_java
from tree_sitter import Language, Node, Parser

from ...constants import MAX_AST_DEPTH, NodeType
from ...custom_types import Parameter
from ...models.core import (
    CallNode,
    ClassNode,
    CodeBlockNode,
    FunctionNode,
    ImportNode,
    InterfaceNode,
    LocalVarNode,
    PropertyNode,
)
from ...models.extensions import AnnotationNode, EnumNode, ModuleNode, StructNode
from ...models.structural import FileNode
from .. import BaseLanguageParser
from .._common import children_of_type, complexity, first_child_of_type, span, text

logger = logging.getLogger(__name__)

_JAVA_LANG = Language(tree_sitter_java.language())

_MAX_CODE_BLOCK_NAME = 60


# ---------------------------------------------------------------------------
# Type text helper
# ---------------------------------------------------------------------------


def _type_text(node: Node) -> str:
    """Reconstruct a human-readable type string from a Java type AST node."""
    t = node.type
    if t == "type_identifier":
        return text(node)
    if t == "void_type":
        return "void"
    if t in ("integral_type", "floating_point_type", "boolean_type"):
        return text(node)
    if t == "generic_type":
        name_node = first_child_of_type(node, "type_identifier", "scoped_type_identifier")
        args_node = first_child_of_type(node, "type_arguments")
        name = _type_text(name_node) if name_node else text(node)
        if args_node:
            arg_parts: list[str] = []
            for c in args_node.children:
                if c.type in ("<", ">", ","):
                    continue
                arg_parts.append(_type_text(c))
            return f"{name}<{', '.join(arg_parts)}>"
        return name
    if t == "array_type":
        elem = node.child_by_field_name("element")
        dims = first_child_of_type(node, "dimensions")
        base = _type_text(elem) if elem else text(node)
        dim_text = text(dims) if dims else "[]"
        return f"{base}{dim_text}"
    if t == "scoped_type_identifier":
        return text(node)
    if t == "wildcard":
        children = [c for c in node.children if c.type not in ("?",)]
        if not children:
            return "?"
        parts = ["?"]
        for c in children:
            if c.type in ("extends", "super"):
                parts.append(text(c))
            else:
                parts.append(_type_text(c))
        return " ".join(parts)
    if t == "annotated_type":
        for c in node.children:
            if c.type not in ("annotation", "marker_annotation"):
                return _type_text(c)
        return text(node)
    return text(node)


# ---------------------------------------------------------------------------
# Javadoc extraction
# ---------------------------------------------------------------------------


def _extract_javadoc(node: Node) -> str | None:
    """Extract Javadoc from the preceding sibling if it is a ``/** ... */`` comment."""
    prev = node.prev_named_sibling
    if prev is None or prev.type != "block_comment":
        return None
    raw = text(prev)
    if not raw.startswith("/**"):
        return None
    lines = raw.splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in ("/**", "*/"):
            continue
        if stripped.startswith("* "):
            cleaned.append(stripped[2:])
        elif stripped.startswith("*"):
            cleaned.append(stripped[1:].lstrip())
        else:
            cleaned.append(stripped)
    return "\n".join(cleaned).strip() or None


# ---------------------------------------------------------------------------
# Annotation / decorator extraction
# ---------------------------------------------------------------------------


def _extract_annotations(node: Node) -> tuple[str, ...]:
    """Extract annotations from a node's ``modifiers`` child."""
    mods = first_child_of_type(node, "modifiers")
    if mods is None:
        return ()
    annotations: list[str] = []
    for c in mods.children:
        if c.type in ("annotation", "marker_annotation"):
            annotations.append(text(c))
    return tuple(annotations)


_MODIFIER_DECORATORS = frozenset({"static", "abstract", "final", "synchronized", "native"})


def _append_modifier_decorators(node: Node, decorators: tuple[str, ...]) -> tuple[str, ...]:
    """Promote Java modifiers (static, abstract, etc.) to synthetic ``@``-prefixed decorators.

    This parallels Python's ``@staticmethod`` / ``@abstractmethod`` convention,
    making modifiers visible in the same ``decorators`` tuple used by the
    resolver and the viewer.
    """
    mods = first_child_of_type(node, "modifiers")
    if mods is None:
        return decorators
    extras: list[str] = []
    for word in text(mods).split():
        if word in _MODIFIER_DECORATORS:
            extras.append(f"@{word}")
    if not extras:
        return decorators
    return decorators + tuple(extras)


# ---------------------------------------------------------------------------
# Parameter extraction
# ---------------------------------------------------------------------------


def _parse_params(params_node: Node) -> tuple[Parameter, ...]:
    """Extract parameters from a ``formal_parameters`` node."""
    result: list[Parameter] = []
    for child in params_node.children:
        if child.type == "formal_parameter":
            name_node = child.child_by_field_name("name")
            type_node = child.child_by_field_name("type")
            if name_node:
                result.append(
                    Parameter(
                        name=text(name_node),
                        type_annotation=_type_text(type_node) if type_node else None,
                    )
                )
        elif child.type == "spread_parameter":
            type_node = child.child_by_field_name("type")
            if type_node is None:
                type_node = first_child_of_type(
                    child, "type_identifier", "generic_type", "scoped_type_identifier", "array_type"
                )
            var_decl = first_child_of_type(child, "variable_declarator")
            name_node = var_decl.child_by_field_name("name") if var_decl else None
            if name_node is None:
                name_node = first_child_of_type(child, "identifier")
            if name_node:
                result.append(
                    Parameter(
                        name=f"{text(name_node)}...",
                        type_annotation=f"{_type_text(type_node)}..." if type_node else None,
                    )
                )
        elif child.type == "receiver_parameter":
            continue
    return tuple(result)


# ---------------------------------------------------------------------------
# Package extraction
# ---------------------------------------------------------------------------


def _extract_package(root: Node) -> str | None:
    """Extract the package name from a ``package_declaration``."""
    for child in root.children:
        if child.type == "package_declaration":
            for c in child.children:
                if c.type == "scoped_identifier":
                    return text(c)
                if c.type == "identifier":
                    return text(c)
    return None


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------


def _extract_imports(root: Node) -> list[ImportNode]:
    """Extract import declarations."""
    imports: list[ImportNode] = []
    for child in root.children:
        if child.type != "import_declaration":
            continue

        is_static = any(c.type == "static" for c in child.children)
        is_wildcard = any(c.type == "asterisk" for c in child.children)

        scoped = first_child_of_type(child, "scoped_identifier")
        ident = first_child_of_type(child, "identifier")

        if scoped:
            full_path = text(scoped)
        elif ident:
            full_path = text(ident)
        else:
            continue

        if is_wildcard:
            module = full_path
            names: tuple[str, ...] = ()
            display = f"import {full_path}.*"
            if is_static:
                display = f"import static {full_path}.*"
        else:
            parts = full_path.rsplit(".", 1)
            if len(parts) == 2:
                module = parts[0]
                names = (parts[1],)
            else:
                module = ""
                names = (full_path,)
            display = f"import {full_path}"
            if is_static:
                display = f"import static {full_path}"

        imports.append(
            ImportNode(
                node_type=NodeType.IMPORT,
                name=display,
                span=span(child),
                source=text(child),
                module=module,
                names=names,
                is_wildcard=is_wildcard,
            )
        )
    return imports


# ---------------------------------------------------------------------------
# Class extraction
# ---------------------------------------------------------------------------


def _extract_bases(node: Node) -> tuple[str, ...]:
    """Extract superclass + implemented interfaces into a single bases tuple."""
    bases: list[str] = []
    superclass = first_child_of_type(node, "superclass")
    if superclass:
        for c in superclass.children:
            if c.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                bases.append(_type_text(c))

    super_interfaces = first_child_of_type(node, "super_interfaces")
    if super_interfaces:
        type_list = first_child_of_type(super_interfaces, "type_list")
        if type_list:
            for c in type_list.children:
                if c.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                    bases.append(_type_text(c))
    return tuple(bases)


def _extract_interface_bases(node: Node) -> tuple[str, ...]:
    """Extract extended interfaces from an ``interface_declaration``."""
    bases: list[str] = []
    extends = first_child_of_type(node, "extends_interfaces")
    if extends:
        type_list = first_child_of_type(extends, "type_list")
        if type_list:
            for c in type_list.children:
                if c.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                    bases.append(_type_text(c))
    return tuple(bases)


# ---------------------------------------------------------------------------
# Overload disambiguation
# ---------------------------------------------------------------------------


def _overload_suffix(params: tuple[Parameter, ...]) -> str:
    """Build a type-based suffix for overloaded methods/constructors.

    Produces e.g. ``(int, String)`` from the parameter type annotations,
    appended to the method name to disambiguate overloads.
    """
    parts = [p.type_annotation or "?" for p in params]
    return f"({', '.join(parts)})"


def _needs_overload_suffix(body: Node, name_to_check: str, node_type: str) -> bool:
    """Return True if ``body`` contains multiple declarations with the same name and type."""
    count = 0
    for child in body.children:
        if child.type != node_type:
            continue
        n = child.child_by_field_name("name")
        if n is None:
            n = first_child_of_type(child, "identifier")
        if n and text(n) == name_to_check:
            count += 1
            if count > 1:
                return True
    return False


# ---------------------------------------------------------------------------
# Class member extraction
# ---------------------------------------------------------------------------


def _extract_class_members(
    body: Node,
    class_name: str,
) -> list[
    FunctionNode | PropertyNode | ClassNode | InterfaceNode | EnumNode | AnnotationNode | StructNode | CodeBlockNode
]:
    """Walk a ``class_body`` / ``interface_body`` / ``enum_body_declarations`` and extract members."""
    members: list[
        FunctionNode | PropertyNode | ClassNode | InterfaceNode | EnumNode | AnnotationNode | StructNode | CodeBlockNode
    ] = []
    for child in body.children:
        if child.type == "method_declaration":
            members.append(_build_method(child, class_name, body))
        elif child.type == "constructor_declaration":
            members.append(_build_constructor(child, class_name, body))
        elif child.type == "compact_constructor_declaration":
            members.append(_build_compact_constructor(child, class_name))
        elif child.type == "field_declaration":
            members.extend(_build_fields(child, class_name))
        elif child.type == "constant_declaration":
            members.extend(_build_fields(child, class_name))
        elif child.type == "class_declaration":
            inner = _build_class(child)
            if inner:
                members.append(inner)
        elif child.type == "interface_declaration":
            inner = _build_interface(child)
            if inner:
                members.append(inner)
        elif child.type == "enum_declaration":
            inner = _build_enum(child)
            if inner:
                members.append(inner)
        elif child.type == "record_declaration":
            inner = _build_record(child)
            if inner:
                members.append(inner)
        elif child.type == "annotation_type_declaration":
            inner = _build_annotation_type(child)
            if inner:
                members.append(inner)
        elif child.type == "static_initializer":
            members.append(
                CodeBlockNode(
                    node_type=NodeType.CODE_BLOCK,
                    name=f"{class_name}.<clinit>",
                    span=span(child),
                    source=text(child),
                )
            )
        elif child.type == "block":
            members.append(
                CodeBlockNode(
                    node_type=NodeType.CODE_BLOCK,
                    name=f"{class_name}.<init-block>",
                    span=span(child),
                    source=text(child),
                )
            )
    return members


_JAVA_PRIMITIVE_TYPES = frozenset(
    {
        "int",
        "long",
        "short",
        "byte",
        "char",
        "float",
        "double",
        "boolean",
        "void",
        "String",
    }
)


def _extract_local_vars(block: Node, depth: int = 0) -> list[LocalVarNode]:
    """Recursively extract local variable declarations from a Java block."""
    results: list[LocalVarNode] = []
    if block is None:
        return results

    for child in block.children:
        if child.type == "local_variable_declaration":
            type_node = child.child_by_field_name("type")
            if type_node is None:
                for sub in child.children:
                    if sub.type in ("type_identifier", "generic_type", "array_type", "scoped_type_identifier"):
                        type_node = sub
                        break
            if type_node is None:
                continue
            type_str = _type_text(type_node)
            if not type_str or type_str in _JAVA_PRIMITIVE_TYPES:
                continue

            for sub in child.children:
                if sub.type == "variable_declarator":
                    name_node = sub.child_by_field_name("name")
                    if name_node is None:
                        name_node = first_child_of_type(sub, "identifier")
                    if name_node:
                        name = text(name_node)
                        line = child.start_point[0] + 1
                        results.append(
                            LocalVarNode(
                                node_type=NodeType.LOCAL_VAR,
                                name=f"{name}@L{line}@D{depth}",
                                span=span(child),
                                source=text(child),
                                type_annotation=type_str,
                            )
                        )

        # Recurse into nested blocks
        if child.type == "block":
            results.extend(_extract_local_vars(child, depth + 1))
        elif child.type in ("if_statement", "while_statement", "do_statement", "switch_expression"):
            for sub in child.children:
                if sub.type == "block":
                    results.extend(_extract_local_vars(sub, depth + 1))
        elif child.type == "for_statement":
            for sub in child.children:
                if sub.type == "local_variable_declaration":
                    type_node = sub.child_by_field_name("type")
                    if type_node is None:
                        for inner in sub.children:
                            if inner.type in (
                                "type_identifier",
                                "generic_type",
                                "array_type",
                                "scoped_type_identifier",
                            ):
                                type_node = inner
                                break
                    if type_node:
                        type_str = _type_text(type_node)
                        if type_str and type_str not in _JAVA_PRIMITIVE_TYPES:
                            for inner in sub.children:
                                if inner.type == "variable_declarator":
                                    name_node = inner.child_by_field_name("name")
                                    if name_node is None:
                                        name_node = first_child_of_type(inner, "identifier")
                                    if name_node:
                                        name = text(name_node)
                                        line = sub.start_point[0] + 1
                                        results.append(
                                            LocalVarNode(
                                                node_type=NodeType.LOCAL_VAR,
                                                name=f"{name}@L{line}@D{depth + 1}",
                                                span=span(sub),
                                                source=text(sub),
                                                type_annotation=type_str,
                                            )
                                        )
                elif sub.type == "block":
                    results.extend(_extract_local_vars(sub, depth + 1))
        elif child.type == "enhanced_for_statement":
            for sub in child.children:
                if sub.type == "block":
                    results.extend(_extract_local_vars(sub, depth + 1))
        elif child.type == "try_statement":
            for sub in child.children:
                if sub.type == "block":
                    results.extend(_extract_local_vars(sub, depth + 1))
                elif sub.type == "catch_clause":
                    for inner in sub.children:
                        if inner.type == "block":
                            results.extend(_extract_local_vars(inner, depth + 1))
                elif sub.type == "finally_clause":
                    for inner in sub.children:
                        if inner.type == "block":
                            results.extend(_extract_local_vars(inner, depth + 1))

    return results


def _build_method(node: Node, class_name: str, body: Node) -> FunctionNode:
    """Build a FunctionNode from a ``method_declaration``."""
    name_node = node.child_by_field_name("name")
    raw_name = text(name_node) if name_node else "<unknown>"
    type_node = node.child_by_field_name("type")
    params_node = first_child_of_type(node, "formal_parameters")
    body_node = first_child_of_type(node, "block")

    params = _parse_params(params_node) if params_node else ()
    qualified = f"{class_name}.{raw_name}"
    if _needs_overload_suffix(body, raw_name, "method_declaration"):
        qualified = f"{qualified}{_overload_suffix(params)}"

    decorators = _extract_annotations(node)
    decorators = _append_modifier_decorators(node, decorators)

    local_vars = _extract_local_vars(body_node) if body_node else []

    return FunctionNode(
        node_type=NodeType.FUNCTION,
        name=qualified,
        span=span(node),
        docstring=_extract_javadoc(node),
        source=text(node),
        owner=class_name,
        func_type="method",
        parameters=params,
        return_type=_type_text(type_node) if type_node else None,
        decorators=decorators,
        is_async=False,
        cyclomatic_complexity=complexity(body_node) if body_node else 1,
        children=tuple(local_vars),
    )


def _build_constructor(node: Node, class_name: str, body: Node) -> FunctionNode:
    """Build a FunctionNode from a ``constructor_declaration``."""
    params_node = first_child_of_type(node, "formal_parameters")
    body_node = first_child_of_type(node, "constructor_body")

    params = _parse_params(params_node) if params_node else ()
    qualified = f"{class_name}.<init>"
    if _needs_overload_suffix(body, class_name, "constructor_declaration"):
        qualified = f"{qualified}{_overload_suffix(params)}"

    local_vars = _extract_local_vars(body_node) if body_node else []

    return FunctionNode(
        node_type=NodeType.FUNCTION,
        name=qualified,
        span=span(node),
        docstring=_extract_javadoc(node),
        source=text(node),
        owner=class_name,
        func_type="method",
        parameters=params,
        return_type=None,
        decorators=_extract_annotations(node),
        cyclomatic_complexity=complexity(body_node) if body_node else 1,
        children=tuple(local_vars),
    )


def _build_compact_constructor(node: Node, class_name: str) -> FunctionNode:
    """Build a FunctionNode from a ``compact_constructor_declaration``."""
    body_node = first_child_of_type(node, "block")

    return FunctionNode(
        node_type=NodeType.FUNCTION,
        name=f"{class_name}.<init>",
        span=span(node),
        docstring=_extract_javadoc(node),
        source=text(node),
        owner=class_name,
        func_type="method",
        parameters=(),
        return_type=None,
        decorators=_extract_annotations(node),
        cyclomatic_complexity=complexity(body_node) if body_node else 1,
    )


def _build_fields(node: Node, owner: str) -> list[PropertyNode]:
    """Build PropertyNode(s) from a ``field_declaration`` or ``constant_declaration``.

    A single declaration may declare multiple variables:
    ``int x = 1, y = 2;``
    """
    type_node = node.child_by_field_name("type")
    if type_node is None:
        for c in node.children:
            if c.type not in ("modifiers", "variable_declarator", ";"):
                type_node = c
                break

    type_ann = _type_text(type_node) if type_node else None

    props: list[PropertyNode] = []
    for decl in children_of_type(node, "variable_declarator"):
        name_node = decl.child_by_field_name("name")
        if name_node is None:
            continue
        value_node = decl.child_by_field_name("value")
        props.append(
            PropertyNode(
                node_type=NodeType.PROPERTY,
                name=text(name_node),
                span=span(node),
                docstring=_extract_javadoc(node),
                source=text(node),
                owner=owner,
                type_annotation=type_ann,
                default_value=text(value_node) if value_node else None,
            )
        )
    return props


# ---------------------------------------------------------------------------
# Top-level type declaration builders
# ---------------------------------------------------------------------------


def _build_class(node: Node) -> ClassNode | None:
    """Build a ClassNode from a ``class_declaration``."""
    name_node = first_child_of_type(node, "identifier")
    if name_node is None:
        return None
    name = text(name_node)
    bases = _extract_bases(node)
    body_node = first_child_of_type(node, "class_body")
    members = _extract_class_members(body_node, name) if body_node else []

    return ClassNode(
        node_type=NodeType.CLASS,
        name=name,
        span=span(node),
        docstring=_extract_javadoc(node),
        source=text(node),
        children=tuple(members),
        bases=bases,
        decorators=_extract_annotations(node),
    )


def _build_interface(node: Node) -> InterfaceNode | None:
    """Build an InterfaceNode from an ``interface_declaration``."""
    name_node = first_child_of_type(node, "identifier")
    if name_node is None:
        return None
    name = text(name_node)
    bases = _extract_interface_bases(node)
    body_node = first_child_of_type(node, "interface_body")
    members = _extract_class_members(body_node, name) if body_node else []

    return InterfaceNode(
        node_type=NodeType.INTERFACE,
        name=name,
        span=span(node),
        docstring=_extract_javadoc(node),
        source=text(node),
        children=tuple(members),
        bases=bases,
    )


def _build_enum(node: Node) -> EnumNode | None:
    """Build an EnumNode from an ``enum_declaration``."""
    name_node = first_child_of_type(node, "identifier")
    if name_node is None:
        return None
    name = text(name_node)
    body = first_child_of_type(node, "enum_body")
    member_names: list[str] = []
    nested_members: list[
        FunctionNode | PropertyNode | ClassNode | InterfaceNode | EnumNode | AnnotationNode | StructNode | CodeBlockNode
    ] = []
    if body:
        for c in body.children:
            if c.type == "enum_constant":
                const_name = first_child_of_type(c, "identifier")
                if const_name:
                    member_names.append(text(const_name))
            elif c.type == "enum_body_declarations":
                nested_members.extend(_extract_class_members(c, name))

    children = list(nested_members)
    return EnumNode(
        node_type=NodeType.ENUM,
        name=name,
        span=span(node),
        docstring=_extract_javadoc(node),
        source=text(node),
        children=tuple(children),
        members=tuple(member_names),
    )


def _build_record(node: Node) -> StructNode | None:
    """Build a StructNode from a ``record_declaration``."""
    name_node = first_child_of_type(node, "identifier")
    if name_node is None:
        return None
    name = text(name_node)
    params_node = first_child_of_type(node, "formal_parameters")
    fields: list[PropertyNode] = []
    if params_node:
        for param in params_node.children:
            if param.type == "formal_parameter":
                pname = param.child_by_field_name("name")
                ptype = param.child_by_field_name("type")
                if pname:
                    fields.append(
                        PropertyNode(
                            node_type=NodeType.PROPERTY,
                            name=text(pname),
                            span=span(param),
                            owner=name,
                            type_annotation=_type_text(ptype) if ptype else None,
                        )
                    )

    body_node = first_child_of_type(node, "class_body")
    members = _extract_class_members(body_node, name) if body_node else []

    all_children = list(fields) + members

    return StructNode(
        node_type=NodeType.STRUCT,
        name=name,
        span=span(node),
        docstring=_extract_javadoc(node),
        source=text(node),
        children=tuple(all_children),
        fields=tuple(fields),
    )


def _build_annotation_type(node: Node) -> AnnotationNode | None:
    """Build an AnnotationNode from an ``annotation_type_declaration``."""
    name_node = first_child_of_type(node, "identifier")
    if name_node is None:
        return None
    name = text(name_node)
    body = first_child_of_type(node, "annotation_type_body")
    members: list[FunctionNode | PropertyNode] = []
    if body:
        for child in body.children:
            if child.type == "annotation_type_element_declaration":
                elem_name = child.child_by_field_name("name")
                elem_type = child.child_by_field_name("type")
                if elem_name:
                    members.append(
                        PropertyNode(
                            node_type=NodeType.PROPERTY,
                            name=text(elem_name),
                            span=span(child),
                            owner=name,
                            type_annotation=_type_text(elem_type) if elem_type else None,
                        )
                    )

    return AnnotationNode(
        node_type=NodeType.ANNOTATION,
        name=name,
        span=span(node),
        docstring=_extract_javadoc(node),
        source=text(node),
        children=tuple(members),
    )


# ---------------------------------------------------------------------------
# Module extraction (module-info.java)
# ---------------------------------------------------------------------------


def _build_module(node: Node) -> ModuleNode | None:
    """Build a ModuleNode from a ``module_declaration``."""
    name_node = first_child_of_type(node, "scoped_identifier", "identifier")
    if name_node is None:
        return None
    return ModuleNode(
        node_type=NodeType.MODULE,
        name=text(name_node),
        span=span(node),
        docstring=_extract_javadoc(node),
        source=text(node),
    )


# ---------------------------------------------------------------------------
# Call extraction
# ---------------------------------------------------------------------------


def _extract_calls(root: Node) -> list[CallNode]:
    """Extract method invocations, constructor calls, and method references."""
    calls: list[CallNode] = []

    def _context_name(node: Node) -> str | None:
        curr = node.parent
        while curr:
            if curr.type == "method_declaration":
                n = curr.child_by_field_name("name")
                if n:
                    return text(n)
            elif curr.type in ("constructor_declaration", "compact_constructor_declaration"):
                n = first_child_of_type(curr, "identifier")
                if n:
                    return text(n)
            elif curr.type == "class_declaration":
                n = first_child_of_type(curr, "identifier")
                if n:
                    return text(n)
            curr = curr.parent
        return None

    def _walk(node: Node, depth: int = 0) -> None:
        if depth > MAX_AST_DEPTH:
            return
        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            obj_node = node.child_by_field_name("object")
            callee = text(name_node) if name_node else ""
            receiver = text(obj_node) if obj_node else None
            calls.append(
                CallNode(
                    node_type=NodeType.CALL,
                    name=callee,
                    span=span(node),
                    callee=callee,
                    receiver=receiver,
                    full_expression=text(node),
                    context=_context_name(node),
                )
            )
        elif node.type == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            if type_node is None:
                for c in node.children:
                    if c.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                        type_node = c
                        break
            if type_node:
                callee = _type_text(type_node)
                bare = callee.split("<")[0].rsplit(".", 1)[-1]
                calls.append(
                    CallNode(
                        node_type=NodeType.CALL,
                        name=bare,
                        span=span(node),
                        callee=bare,
                        full_expression=text(node),
                        context=_context_name(node),
                    )
                )
        elif node.type == "method_reference":
            ref_text = text(node)
            parts = ref_text.split("::")
            if len(parts) == 2:
                receiver_part = parts[0].strip()
                method_part = parts[1].strip()
                callee = f"{receiver_part}.{method_part}"
                calls.append(
                    CallNode(
                        node_type=NodeType.CALL,
                        name=callee,
                        span=span(node),
                        callee=callee,
                        receiver=receiver_part,
                        full_expression=ref_text,
                        context=_context_name(node),
                    )
                )

        for child in node.children:
            _walk(child, depth + 1)

    for child in root.children:
        if child.type in ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"):
            body = first_child_of_type(child, "class_body", "interface_body", "enum_body")
            if body:
                _walk(body)

    return calls


# ---------------------------------------------------------------------------
# Top-level extraction dispatcher
# ---------------------------------------------------------------------------


def _parse_sync(parser: Parser, path: Path, source: bytes) -> FileNode:
    """Parse Java source into a FileNode."""
    tree = parser.parse(source)
    root = tree.root_node

    all_children: list = []
    imports = _extract_imports(root)
    all_children.extend(imports)

    calls = _extract_calls(root)
    all_children.extend(calls)

    for child in root.children:
        if child.type == "class_declaration":
            cls = _build_class(child)
            if cls:
                all_children.append(cls)
        elif child.type == "interface_declaration":
            iface = _build_interface(child)
            if iface:
                all_children.append(iface)
        elif child.type == "enum_declaration":
            enum = _build_enum(child)
            if enum:
                all_children.append(enum)
        elif child.type == "record_declaration":
            rec = _build_record(child)
            if rec:
                all_children.append(rec)
        elif child.type == "annotation_type_declaration":
            ann = _build_annotation_type(child)
            if ann:
                all_children.append(ann)
        elif child.type == "module_declaration":
            mod = _build_module(child)
            if mod:
                all_children.append(mod)

    return FileNode(
        node_type=NodeType.FILE,
        name=path.name,
        span=span(root),
        children=tuple(all_children),
        path=str(path),
        language="java",
    )


# ---------------------------------------------------------------------------
# Public parser class
# ---------------------------------------------------------------------------


class JavaParser(BaseLanguageParser):
    """Parse Java source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(_JAVA_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(_parse_sync, self._parser, path, source)
