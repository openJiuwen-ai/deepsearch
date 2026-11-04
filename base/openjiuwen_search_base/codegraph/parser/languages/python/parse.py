"""Python language parser using tree-sitter."""

import ast
import asyncio
import logging
from pathlib import Path

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from ...constants import MAX_AST_DEPTH, NodeType
from ...custom_types import Parameter, SourceSpan
from ...models.core import (
    CallNode,
    ClassNode,
    CodeBlockNode,
    DuckTypeNode,
    FunctionNode,
    ImportNode,
    InterfaceNode,
    LocalVarNode,
    PropertyNode,
)
from ...models.extensions import EnumNode, TypeAliasNode
from ...models.structural import FileNode
from .. import BaseLanguageParser

logger = logging.getLogger(__name__)

_LANG = Language(tree_sitter_python.language())

# ---------------------------------------------------------------------------
# Tree-sitter queries
# ---------------------------------------------------------------------------

PY_QUERIES: dict[str, str] = {
    "classes": """
        (class_definition
            name: (identifier) @name
            superclasses: (argument_list)? @superclasses
            body: (block) @body)
    """,
    "functions": """
        (function_definition
            name: (identifier) @name
            parameters: (parameters) @parameters
            body: (block) @body
            return_type: (_)? @return_type)
    """,
    "assignments": """
        (assignment
            left: (identifier) @lhs
            type: (_)? @type
            right: (_)? @rhs)
    """,
    "type_alias_stmt": """
        (type_alias_statement
            left: (type) @alias_name
            right: (_) @alias_value)
    """,
}

_ENUM_BASES = frozenset(
    {
        "Enum",
        "IntEnum",
        "StrEnum",
        "Flag",
        "IntFlag",
        "enum.Enum",
        "enum.IntEnum",
        "enum.StrEnum",
        "enum.Flag",
        "enum.IntFlag",
    }
)

_PROTOCOL_BASES = frozenset({"Protocol", "typing.Protocol", "typing_extensions.Protocol"})

_COMPILED: dict[str, Query] = {}


def _query(name: str) -> Query:
    """Return a compiled query, cached."""
    if name not in _COMPILED:
        _COMPILED[name] = Query(_LANG, PY_QUERIES[name])
    return _COMPILED[name]


def _captures(name: str, node: Node) -> dict[str, list[Node]]:
    """Run a named query and return its captures dict."""
    return QueryCursor(_query(name)).captures(node)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text else ""


def _span(node: Node) -> SourceSpan:
    return SourceSpan(
        line_start=node.start_point.row + 1,
        line_end=node.end_point.row + 1,
        col_start=node.start_point.column,
        col_end=node.end_point.column,
    )


def _docstring(body_node: Node | None) -> str | None:
    if body_node and body_node.child_count > 0:
        first = body_node.children[0]
        if first.type == "expression_statement" and first.child_count > 0 and first.children[0].type == "string":
            try:
                return ast.literal_eval(_text(first.children[0]))
            except (ValueError, SyntaxError):
                return _text(first.children[0])
    return None


def _decorators(node: Node) -> tuple[str, ...]:
    target = node.parent if node.parent and node.parent.type == "decorated_definition" else node
    return tuple(_text(c) for c in target.children if c.type == "decorator")


def _complexity(node: Node) -> int:
    _branch = frozenset(
        {
            "if_statement",
            "for_statement",
            "while_statement",
            "except_clause",
            "with_statement",
            "boolean_operator",
            "list_comprehension",
            "generator_expression",
            "case_clause",
        }
    )
    count = 1

    def _walk(n: Node, depth: int = 0) -> None:
        nonlocal count
        if depth > MAX_AST_DEPTH:
            return
        if n.type in _branch:
            count += 1
        for child in n.children:
            _walk(child, depth + 1)

    _walk(node)
    return count


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_classes(root: Node) -> tuple[list[ClassNode], list[InterfaceNode], list[EnumNode]]:
    classes: list[ClassNode] = []
    interfaces: list[InterfaceNode] = []
    enums: list[EnumNode] = []

    caps = _captures("classes", root)
    name_nodes = caps.get("name", [])

    for name_node in name_nodes:
        class_node = name_node.parent
        if class_node is None:
            raise RuntimeError("Assertion error: class_node should not be None")
        if _is_inside_class(class_node) or _is_inside_function(class_node):
            continue
        name = _text(name_node)
        body_node = class_node.child_by_field_name("body")
        superclasses_node = class_node.child_by_field_name("superclasses")

        bases: list[str] = []
        metaclass: str | None = None
        if superclasses_node:
            for child in superclasses_node.children:
                if child.type == "keyword_argument":
                    kw_name = child.child_by_field_name("name")
                    kw_value = child.child_by_field_name("value")
                    if kw_name and kw_value and _text(kw_name) == "metaclass":
                        metaclass = _text(kw_value)
                elif child.type in ("identifier", "attribute", "subscript"):
                    bases.append(_text(child))

        decs = _decorators(class_node)
        sp = _span(class_node)
        doc = _docstring(body_node)
        src = _text(class_node)
        base_set = frozenset(bases)

        if base_set & _ENUM_BASES:
            members = _enum_members(body_node)
            children = _nested_members(class_node, name)
            enums.append(
                EnumNode(
                    node_type=NodeType.ENUM,
                    name=name,
                    span=sp,
                    docstring=doc,
                    source=src,
                    children=tuple(children),
                    members=members,
                )
            )
        elif base_set & _PROTOCOL_BASES:
            children = _nested_members(class_node, name)
            interfaces.append(
                InterfaceNode(
                    node_type=NodeType.INTERFACE,
                    name=name,
                    span=sp,
                    docstring=doc,
                    source=src,
                    children=tuple(children),
                    bases=tuple(bases),
                )
            )
        else:
            children = _nested_members(class_node, name)
            classes.append(
                ClassNode(
                    node_type=NodeType.CLASS,
                    name=name,
                    span=sp,
                    docstring=doc,
                    source=src,
                    children=tuple(children),
                    bases=tuple(bases),
                    metaclass=metaclass,
                    decorators=decs,
                )
            )

    return classes, interfaces, enums


def _enum_members(body_node: Node | None) -> tuple[str, ...]:
    if body_node is None:
        return ()
    members: list[str] = []
    for child in body_node.children:
        if child.type == "expression_statement":
            expr = child.children[0] if child.child_count else None
            if expr and expr.type == "assignment":
                left = expr.child_by_field_name("left")
                if left and left.type == "identifier":
                    members.append(_text(left))
    return tuple(members)


def _nested_members(
    class_node: Node,
    class_name: str,
) -> list[FunctionNode | PropertyNode | ClassNode | InterfaceNode | EnumNode]:
    body = class_node.child_by_field_name("body")
    if body is None:
        return []

    nodes: list[FunctionNode | PropertyNode | ClassNode | InterfaceNode | EnumNode] = []
    for child in body.children:
        actual = child
        if child.type == "decorated_definition":
            for sub in child.children:
                if sub.type in ("function_definition", "class_definition"):
                    actual = sub
                    break

        if actual.type == "function_definition":
            if actual.child_by_field_name("name") is None:
                continue
            nodes.append(_build_function(actual, class_name=class_name))

        elif actual.type == "class_definition":
            nested_cls = _build_nested_class(actual)
            if nested_cls:
                nodes.append(nested_cls)

        elif child.type == "expression_statement":
            expr = child.children[0] if child.child_count else None
            if expr and expr.type == "assignment":
                prop = _property_from_assignment(expr, owner=class_name)
                if prop:
                    nodes.append(prop)

    return nodes


def _build_nested_class(cls_node: Node) -> ClassNode | InterfaceNode | EnumNode | None:
    """Build a class/interface/enum node from a nested class_definition."""
    name_node = cls_node.child_by_field_name("name")
    if name_node is None:
        return None
    name = _text(name_node)
    body_node = cls_node.child_by_field_name("body")
    superclasses_node = cls_node.child_by_field_name("superclasses")

    bases: list[str] = []
    metaclass: str | None = None
    if superclasses_node:
        for child in superclasses_node.children:
            if child.type == "keyword_argument":
                kw_name = child.child_by_field_name("name")
                kw_value = child.child_by_field_name("value")
                if kw_name and kw_value and _text(kw_name) == "metaclass":
                    metaclass = _text(kw_value)
            elif child.type in ("identifier", "attribute", "subscript"):
                bases.append(_text(child))

    decs = _decorators(cls_node)
    sp = _span(cls_node)
    doc = _docstring(body_node)
    src = _text(cls_node)
    base_set = frozenset(bases)

    if base_set & _ENUM_BASES:
        members = _enum_members(body_node)
        children = _nested_members(cls_node, name)
        return EnumNode(
            node_type=NodeType.ENUM,
            name=name,
            span=sp,
            docstring=doc,
            source=src,
            children=tuple(children),
            members=members,
        )
    elif base_set & _PROTOCOL_BASES:
        children = _nested_members(cls_node, name)
        return InterfaceNode(
            node_type=NodeType.INTERFACE,
            name=name,
            span=sp,
            docstring=doc,
            source=src,
            children=tuple(children),
            bases=tuple(bases),
        )
    else:
        children = _nested_members(cls_node, name)
        return ClassNode(
            node_type=NodeType.CLASS,
            name=name,
            span=sp,
            docstring=doc,
            source=src,
            children=tuple(children),
            bases=tuple(bases),
            metaclass=metaclass,
            decorators=decs,
        )


def _extract_functions(root: Node) -> list[FunctionNode]:
    """Extract only top-level functions (not methods, not nested)."""
    functions: list[FunctionNode] = []
    for name_node in _captures("functions", root).get("name", []):
        func_node = name_node.parent
        if func_node is None:
            raise RuntimeError("Assertion error: func_node should not be None")
        if _is_inside_class(func_node) or _is_inside_function(func_node):
            continue
        functions.append(_build_function(func_node))
    return functions


def _is_inside_class(node: Node) -> bool:
    curr = node.parent
    while curr:
        if curr.type == "class_definition":
            return True
        if curr.type == "function_definition":
            return False
        curr = curr.parent
    return False


def _is_inside_function(node: Node) -> bool:
    curr = node.parent
    while curr:
        if curr.type == "function_definition":
            return True
        curr = curr.parent
    return False


def _build_function(
    func_node: Node,
    *,
    class_name: str | None = None,
    enclosing_func: str | None = None,
) -> FunctionNode:
    name_node = func_node.child_by_field_name("name")
    if name_node is None:
        raise RuntimeError("Assertion error: name_node should not be None")
    params_node = func_node.child_by_field_name("parameters")
    body_node = func_node.child_by_field_name("body")
    return_type_node = func_node.child_by_field_name("return_type")

    is_async = any(c.type == "async" for c in func_node.children)

    raw_name = _text(name_node)

    decs = _decorators(func_node)

    if class_name:
        suffix = ""
        for d in decs:
            if d.endswith(".setter"):
                suffix = ".setter"
                break
            elif d.endswith(".deleter"):
                suffix = ".deleter"
                break
        qualified_name = f"{class_name}.{raw_name}{suffix}"
        owner = class_name
        func_type = "method"
    elif enclosing_func:
        qualified_name = f"{enclosing_func}.{raw_name}"
        owner = enclosing_func
        func_type = "nested"
    else:
        qualified_name = raw_name
        owner = None
        func_type = "function"

    if any(d in ("@overload", "@typing.overload") for d in decs):
        overload_sig = _overload_suffix(params_node, return_type_node)
        qualified_name = f"{qualified_name}[{overload_sig}]"

    nested = _extract_nested_functions(func_node, qualified_name)
    local_annotations = _extract_local_annotations(body_node, owner=qualified_name)
    lambdas = _extract_lambdas(body_node, owner=qualified_name)

    children: list[FunctionNode | PropertyNode] = []
    children.extend(nested)
    children.extend(local_annotations)
    children.extend(lambdas)

    return FunctionNode(
        node_type=NodeType.FUNCTION,
        name=qualified_name,
        span=_span(func_node),
        docstring=_docstring(body_node),
        source=_text(func_node),
        children=tuple(children),
        owner=owner,
        func_type=func_type,  # type: ignore [arg-type]
        parameters=_parse_params(params_node),
        return_type=_text(return_type_node) if return_type_node else None,
        decorators=_decorators(func_node),
        is_async=is_async,
        cyclomatic_complexity=_complexity(func_node),
    )


def _extract_nested_functions(func_node: Node, enclosing_name: str) -> list[FunctionNode]:
    """Extract functions defined directly inside a function body."""
    body = func_node.child_by_field_name("body")
    if body is None:
        return []

    nested: list[FunctionNode] = []
    for child in body.children:
        actual = child
        if child.type == "decorated_definition":
            for sub in child.children:
                if sub.type == "function_definition":
                    actual = sub
                    break
        if actual.type == "function_definition":
            if actual.child_by_field_name("name") is None:
                continue
            nested.append(_build_function(actual, enclosing_func=enclosing_name))
    return nested


def _parse_lambda_params(lambda_node: Node) -> tuple[Parameter, ...]:
    """Parse parameters from a ``lambda`` node's ``lambda_parameters`` child."""
    params_node = lambda_node.child_by_field_name("parameters")
    return _parse_params(params_node)


def _lambda_name(lambda_node: Node) -> str:
    """Build synthetic name ``lambda(args)@L{line}@C{col}`` (1-based line/col)."""
    params = _parse_lambda_params(lambda_node)
    args = ", ".join(p.name for p in params)
    line = lambda_node.start_point.row + 1
    col = lambda_node.start_point.column + 1
    return f"lambda({args})@L{line}@C{col}"


def _build_lambda(lambda_node: Node, *, owner: str | None = None) -> FunctionNode:
    """Build a ``FunctionNode`` for a Python ``lambda`` expression."""
    params = _parse_lambda_params(lambda_node)
    body = lambda_node.child_by_field_name("body")
    return FunctionNode(
        node_type=NodeType.FUNCTION,
        name=_lambda_name(lambda_node),
        span=_span(lambda_node),
        source=_text(lambda_node),
        owner=owner,
        func_type="lambda",
        parameters=params,
        cyclomatic_complexity=_complexity(body) if body is not None else 1,
    )


def _extract_lambdas(ast_root: Node | None, *, owner: str | None = None) -> list[FunctionNode]:
    """Collect every named ``lambda`` under *ast_root*, skipping nested ``def`` bodies."""
    if ast_root is None:
        return []

    results: list[FunctionNode] = []

    def _walk(node: Node, depth: int = 0) -> None:
        if depth > MAX_AST_DEPTH:
            return
        if node.type in ("function_definition", "decorated_definition"):
            return
        if node.type == "lambda" and node.is_named:
            results.append(_build_lambda(node, owner=owner))
        for child in node.children:
            _walk(child, depth + 1)

    _walk(ast_root)
    return results


def _extract_module_lambdas(root: Node) -> list[FunctionNode]:
    """Extract lambdas at module scope (not inside functions or classes)."""
    results: list[FunctionNode] = []

    def _walk(node: Node, depth: int = 0) -> None:
        if depth > MAX_AST_DEPTH:
            return
        if node.type in ("function_definition", "decorated_definition", "class_definition"):
            return
        if node.type == "lambda" and node.is_named:
            results.append(_build_lambda(node, owner=None))
        for child in node.children:
            _walk(child, depth + 1)

    for child in root.children:
        _walk(child)
    return results


def _extract_local_annotations(body_node: Node | None, *, owner: str) -> list[LocalVarNode]:
    """Extract annotated local variables from a function body.

    Captures ``x: T`` and ``x: T = val`` statements inside the function,
    producing :class:`LocalVarNode` children so the resolver can infer
    receiver types from local annotations.  Recurses into nested blocks
    (if, for, while, try, with) since Python uses function-level scoping.
    """
    if body_node is None:
        return []
    props: list[LocalVarNode] = []
    _collect_annotations(body_node, owner, props)
    return props


_PYTHON_BLOCK_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "try_statement",
        "with_statement",
        "elif_clause",
        "else_clause",
        "except_clause",
        "finally_clause",
        "case_clause",
        "match_statement",
    }
)


def _collect_annotations(node: Node, owner: str, out: list[LocalVarNode]) -> None:
    """Walk *node* children, extracting annotated assignments and recursing into blocks."""
    for child in node.children:
        if child.type == "expression_statement":
            expr = child.children[0] if child.child_count else None
            if expr is not None and expr.type == "assignment":
                type_node = expr.child_by_field_name("type")
                if type_node is not None:
                    prop = _property_from_assignment(expr, owner=owner)
                    if prop:
                        out.append(
                            LocalVarNode(
                                node_type=NodeType.LOCAL_VAR,
                                name=prop.name,
                                span=prop.span,
                                type_annotation=prop.type_annotation,
                                default_value=prop.default_value,
                            )
                        )
        elif child.type == "block":
            _collect_annotations(child, owner, out)
        elif child.type in _PYTHON_BLOCK_TYPES:
            _collect_annotations(child, owner, out)


def _overload_suffix(params_node: Node | None, return_type: Node | None) -> str:
    """Build a type-based disambiguation suffix for @overload functions."""
    parts: list[str] = []
    if params_node:
        for p in params_node.children:
            if p.type == "typed_parameter":
                type_n = p.child_by_field_name("type")
                if type_n:
                    parts.append(_text(type_n))
            elif p.type == "typed_default_parameter":
                type_n = p.child_by_field_name("type")
                if type_n:
                    parts.append(_text(type_n))
    sig = ", ".join(parts)
    if return_type:
        sig += f" -> {_text(return_type)}"
    return sig


def _parse_params(params_node: Node | None) -> tuple[Parameter, ...]:
    if params_node is None:
        return ()
    result: list[Parameter] = []
    for p in params_node.children:
        name: str | None = None
        type_ann: str | None = None
        default: str | None = None
        if p.type == "identifier":
            name = _text(p)
        elif p.type == "typed_parameter":
            n = p.child_by_field_name("name")
            if n is None:
                for c in p.children:
                    if c.type == "identifier":
                        n = c
                        break
                    elif c.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                        n = c
                        break
            if n:
                name = _text(n)
            t = p.child_by_field_name("type")
            if t:
                type_ann = _text(t)
        elif p.type == "default_parameter":
            n = p.child_by_field_name("name")
            if n:
                name = _text(n)
            v = p.child_by_field_name("value")
            if v:
                default = _text(v)
        elif p.type == "typed_default_parameter":
            n = p.child_by_field_name("name")
            if n:
                name = _text(n)
            t = p.child_by_field_name("type")
            if t:
                type_ann = _text(t)
            v = p.child_by_field_name("value")
            if v:
                default = _text(v)
        elif p.type in ("list_splat_pattern", "dictionary_splat_pattern"):
            name = _text(p)
        if name:
            result.append(Parameter(name=name, type_annotation=type_ann, default=default))
    return tuple(result)


def _extract_properties(root: Node) -> list[PropertyNode]:
    props: list[PropertyNode] = []
    for lhs_node in _captures("assignments", root).get("lhs", []):
        assignment = lhs_node.parent
        if assignment is None:
            continue
        if _is_inside_class(assignment) or _is_inside_function(assignment):
            continue
        prop = _property_from_assignment(assignment)
        if prop:
            props.append(prop)
    return props


def _property_from_assignment(assignment: Node, *, owner: str | None = None) -> PropertyNode | None:
    left = assignment.child_by_field_name("left")
    if left is None or left.type != "identifier":
        return None
    type_node = assignment.child_by_field_name("type")
    right_node = assignment.child_by_field_name("right")
    return PropertyNode(
        node_type=NodeType.PROPERTY,
        name=_text(left),
        span=_span(assignment),
        owner=owner,
        type_annotation=_text(type_node) if type_node else None,
        default_value=_text(right_node) if right_node else None,
    )


def _extract_type_aliases(root: Node) -> list[TypeAliasNode]:
    aliases: list[TypeAliasNode] = []

    # ``type X = Y`` (Python 3.12+)
    caps = _captures("type_alias_stmt", root)
    for name_node in caps.get("alias_name", []):
        stmt = name_node.parent
        if stmt is None:
            continue
        value_node = stmt.child_by_field_name("right")
        aliases.append(
            TypeAliasNode(
                node_type=NodeType.TYPE_ALIAS,
                name=_text(name_node),
                span=_span(stmt),
                source=_text(stmt),
                aliased_type=_text(value_node) if value_node else "",
            )
        )

    # ``X: TypeAlias = Y``
    assign_caps = _captures("assignments", root)
    lhs_nodes = assign_caps.get("lhs", [])
    type_nodes = assign_caps.get("type", [])

    type_by_row: dict[int, Node] = {n.start_point.row: n for n in type_nodes}
    for lhs_node in lhs_nodes:
        assignment = lhs_node.parent
        if assignment is None:
            continue
        type_n = type_by_row.get(lhs_node.start_point.row)
        if type_n and _text(type_n) in ("TypeAlias", "typing.TypeAlias"):
            right = assignment.child_by_field_name("right")
            aliases.append(
                TypeAliasNode(
                    node_type=NodeType.TYPE_ALIAS,
                    name=_text(lhs_node),
                    span=_span(assignment),
                    source=_text(assignment),
                    aliased_type=_text(right) if right else "",
                )
            )
    return aliases


# ---------------------------------------------------------------------------
# DuckType extraction
# ---------------------------------------------------------------------------


def _extract_duck_types(root: Node) -> tuple[list[DuckTypeNode], dict[str, tuple[str, ...]]]:
    """Infer DuckType nodes from untyped parameters.

    Returns the deduplicated list and a mapping of
    ``function_name -> tuple of DuckType names``.
    """
    seen: dict[frozenset[str], DuckTypeNode] = {}
    func_refs: dict[str, list[str]] = {}

    for func_child in root.children:
        actual = func_child
        if func_child.type == "decorated_definition":
            for sub in func_child.children:
                if sub.type == "function_definition":
                    actual = sub
                    break
        if actual.type != "function_definition":
            continue

        name_node = actual.child_by_field_name("name")
        params_node = actual.child_by_field_name("parameters")
        body_node = actual.child_by_field_name("body")
        if not name_node or not params_node or not body_node:
            continue

        func_name = _text(name_node)
        untyped = _untyped_param_names(params_node)
        if not untyped:
            continue

        param_methods = _collect_method_calls(body_node, untyped)
        refs: list[str] = []
        for _param, methods in param_methods.items():
            if not methods:
                continue
            key = frozenset(methods)
            if key not in seen:
                dt_name = "DuckType{" + ", ".join(sorted(key)) + "}"
                seen[key] = DuckTypeNode(
                    node_type=NodeType.DUCK_TYPE,
                    name=dt_name,
                    span=SourceSpan(0, 0, 0, 0),
                    methods=key,
                )
            refs.append(seen[key].name)

        if refs:
            func_refs[func_name] = refs

    return list(seen.values()), {k: tuple(v) for k, v in func_refs.items()}


def _untyped_param_names(params_node: Node) -> set[str]:
    names: set[str] = set()
    for p in params_node.children:
        if p.type == "identifier":
            name = _text(p)
            if name not in ("self", "cls"):
                names.add(name)
        elif p.type == "default_parameter":
            n = p.child_by_field_name("name")
            if n:
                names.add(_text(n))
    return names


def _collect_method_calls(body: Node, param_names: set[str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {n: set() for n in param_names}

    def _walk(node: Node, depth: int = 0) -> None:
        if depth > MAX_AST_DEPTH:
            return
        if node.type == "call":
            func = node.child_by_field_name("function")
            if func and func.type == "attribute":
                obj = func.child_by_field_name("object")
                attr = func.child_by_field_name("attribute")
                if obj and attr and obj.type == "identifier":
                    obj_name = _text(obj)
                    if obj_name in result:
                        result[obj_name].add(_text(attr))
        for child in node.children:
            _walk(child, depth + 1)

    _walk(body)
    return result


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------


def _extract_imports(root: Node) -> list[ImportNode]:
    """Extract top-level import statements."""
    imports: list[ImportNode] = []
    for child in root.children:
        if child.type == "import_statement":
            for name_child in child.children:
                if name_child.type == "dotted_name":
                    module = _text(name_child)
                    imports.append(
                        ImportNode(
                            node_type=NodeType.IMPORT,
                            name=module,
                            span=_span(child),
                            source=_text(child),
                            module=module,
                            names=(module,),
                        )
                    )
                elif name_child.type == "aliased_import":
                    dotted = name_child.child_by_field_name("name")
                    alias_node = name_child.child_by_field_name("alias")
                    if dotted:
                        module = _text(dotted)
                        alias = _text(alias_node) if alias_node else None
                        imports.append(
                            ImportNode(
                                node_type=NodeType.IMPORT,
                                name=module,
                                span=_span(child),
                                source=_text(child),
                                module=module,
                                names=(module,),
                                alias=alias,
                            )
                        )

        elif child.type == "import_from_statement":
            module_node = child.child_by_field_name("module_name")
            module = _text(module_node) if module_node else ""

            if not module:
                for c in child.children:
                    if c.type in ("dotted_name", "relative_import"):
                        module = _text(c)
                        break

            is_wildcard = any(c.type == "wildcard_import" for c in child.children)
            if is_wildcard:
                imports.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name=f"from {module} import *",
                        span=_span(child),
                        source=_text(child),
                        module=module,
                        names=(),
                        is_wildcard=True,
                    )
                )
            else:
                names: list[str] = []
                alias: str | None = None
                for c in child.children:
                    if c.type == "dotted_name" and c != module_node:
                        names.append(_text(c))
                    elif c.type == "aliased_import":
                        name_n = c.child_by_field_name("name")
                        if name_n:
                            names.append(_text(name_n))
                        alias_n = c.child_by_field_name("alias")
                        if alias_n and not alias:
                            alias = _text(alias_n)
                    elif c.type == "import_list":
                        for item in c.children:
                            if item.type == "dotted_name":
                                names.append(_text(item))
                            elif item.type == "aliased_import":
                                name_n = item.child_by_field_name("name")
                                if name_n:
                                    names.append(_text(name_n))

                if not names and not module_node:
                    for c in child.children:
                        if c.type == "dotted_name":
                            first_dotted = _text(c)
                            if first_dotted != module:
                                names.append(first_dotted)

                imports.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name=f"from {module} import {', '.join(names) if names else '...'}",
                        span=_span(child),
                        source=_text(child),
                        module=module,
                        names=tuple(names),
                        alias=alias,
                    )
                )

    return imports


# ---------------------------------------------------------------------------
# Call extraction
# ---------------------------------------------------------------------------


def _extract_call_arguments(call_node: Node) -> tuple[str, ...]:
    """Extract positional argument texts from a call node's argument_list."""
    arg_list = call_node.child_by_field_name("arguments")
    if arg_list is None:
        return ()
    args: list[str] = []
    for child in arg_list.children:
        if child.type in ("(", ")", ","):
            continue
        if child.type == "keyword_argument":
            break
        args.append(_text(child))
    return tuple(args)


def _extract_assign_target(call_node: Node) -> str | None:
    """If the call is the RHS of ``x = call(...)``, return ``x``."""
    parent = call_node.parent
    if parent is None:
        return None
    if parent.type == "assignment":
        left = parent.child_by_field_name("left")
        if left and left.type == "identifier":
            return _text(left)
    return None


def _extract_calls(root: Node) -> list[CallNode]:
    """Extract function/method call sites from function bodies."""
    calls: list[CallNode] = []

    def _context_name(node: Node) -> str | None:
        """Walk up to find enclosing lambda, function, or class name."""
        curr = node.parent
        while curr:
            if curr.type == "lambda" and curr.is_named:
                return _lambda_name(curr)
            if curr.type == "function_definition":
                n = curr.child_by_field_name("name")
                if n:
                    return _text(n)
            elif curr.type == "class_definition":
                n = curr.child_by_field_name("name")
                if n:
                    return _text(n)
            curr = curr.parent
        return None

    def _walk_calls(node: Node, depth: int = 0) -> None:
        if depth > MAX_AST_DEPTH:
            return
        if node.type == "call":
            func = node.child_by_field_name("function")
            if func:
                callee: str = ""
                receiver: str | None = None
                if func.type == "attribute":
                    obj = func.child_by_field_name("object")
                    attr = func.child_by_field_name("attribute")
                    if obj and attr:
                        callee = _text(attr)
                        receiver = _text(obj)
                elif func.type == "identifier":
                    callee = _text(func)
                else:
                    callee = _text(func)

                args = _extract_call_arguments(node)
                assign_target = _extract_assign_target(node)

                calls.append(
                    CallNode(
                        node_type=NodeType.CALL,
                        name=callee,
                        span=_span(node),
                        callee=callee,
                        receiver=receiver,
                        full_expression=_text(node),
                        context=_context_name(node),
                        arguments=args,
                        assign_target=assign_target,
                    )
                )
        for child in node.children:
            _walk_calls(child, depth + 1)

    for child in root.children:
        if child.type == "function_definition":
            body = child.child_by_field_name("body")
            if body:
                _walk_calls(body)
        elif child.type == "decorated_definition":
            for sub in child.children:
                if sub.type == "function_definition":
                    body = sub.child_by_field_name("body")
                    if body:
                        _walk_calls(body)
        elif child.type == "class_definition":
            cls_body = child.child_by_field_name("body")
            if cls_body:
                for member in cls_body.children:
                    actual = member
                    if member.type == "decorated_definition":
                        for sub in member.children:
                            if sub.type == "function_definition":
                                actual = sub
                                break
                    if actual.type == "function_definition":
                        body = actual.child_by_field_name("body")
                        if body:
                            _walk_calls(body)
        else:
            # Module-level expressions (including lambdas)
            _walk_calls(child)

    return calls


# ---------------------------------------------------------------------------
# Code block extraction
# ---------------------------------------------------------------------------

_DEFINITION_TYPES = frozenset(
    {
        "function_definition",
        "decorated_definition",
        "class_definition",
        "type_alias_statement",
    }
)


def _is_skippable_expression(node: Node) -> bool:
    """Check if an expression_statement is an assignment, annotation, or docstring."""
    if node.type != "expression_statement":
        return False
    child = node.children[0] if node.children else None
    if child is None:
        return False
    return child.type in ("assignment", "augmented_assignment", "type", "string", "concatenated_string")


def _extract_code_blocks(root: Node, path: Path) -> list[CodeBlockNode]:
    """Extract contiguous root-level code between definitions.

    Imports become ``CodeBlockNode``s for export/chunking while ``ImportNode``s
    remain separately for the resolver. Comments and assignments expand an
    adjacent block so its source preserves the complete contiguous region.
    Assignment-only regions remain represented solely by ``PropertyNode``s.
    """
    blocks: list[CodeBlockNode] = []
    group: list[Node] = []

    def _flush_group() -> None:
        if not group:
            return
        if all(_is_skippable_expression(node) for node in group):
            group.clear()
            return
        first = group[0]
        last = group[-1]
        line_start = first.start_point.row + 1
        name = f"{path.as_posix()}@L{line_start}"
        root_text = root.text or b""
        source = root_text[first.start_byte : last.end_byte].decode("utf-8")
        blocks.append(
            CodeBlockNode(
                node_type=NodeType.CODE_BLOCK,
                name=name,
                span=SourceSpan(
                    line_start=line_start,
                    line_end=last.end_point.row + 1,
                    col_start=first.start_point.column,
                    col_end=last.end_point.column,
                ),
                source=source,
            )
        )
        group.clear()

    for child in root.children:
        if child.type in _DEFINITION_TYPES:
            _flush_group()
            continue
        group.append(child)

    _flush_group()
    return blocks


# ---------------------------------------------------------------------------
# Public parser class
# ---------------------------------------------------------------------------


class PythonParser(BaseLanguageParser):
    """Parse Python source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(self._parse_sync, path, source)

    def _parse_sync(self, path: Path, source: bytes) -> FileNode:
        tree = self._parser.parse(source)
        root = tree.root_node

        classes, interfaces, enums = _extract_classes(root)
        functions = _extract_functions(root)
        module_lambdas = _extract_module_lambdas(root)
        properties = _extract_properties(root)
        type_aliases = _extract_type_aliases(root)
        code_blocks = _extract_code_blocks(root, path)
        duck_types, duck_refs = _extract_duck_types(root)
        imports = _extract_imports(root)
        calls = _extract_calls(root)

        patched: list[FunctionNode] = []
        for fn in functions:
            refs = duck_refs.get(fn.name)
            if refs:
                fn = FunctionNode(
                    node_type=fn.node_type,
                    name=fn.name,
                    span=fn.span,
                    docstring=fn.docstring,
                    source=fn.source,
                    children=fn.children,
                    owner=fn.owner,
                    func_type=fn.func_type,
                    parameters=fn.parameters,
                    return_type=fn.return_type,
                    decorators=fn.decorators,
                    is_async=fn.is_async,
                    cyclomatic_complexity=fn.cyclomatic_complexity,
                    duck_type_refs=refs,
                )
            patched.append(fn)

        all_children: list[
            ClassNode
            | InterfaceNode
            | EnumNode
            | FunctionNode
            | PropertyNode
            | TypeAliasNode
            | DuckTypeNode
            | CodeBlockNode
            | ImportNode
            | CallNode
        ] = []
        all_children.extend(classes)
        all_children.extend(interfaces)
        all_children.extend(enums)
        all_children.extend(patched)
        all_children.extend(module_lambdas)
        all_children.extend(properties)
        all_children.extend(type_aliases)
        all_children.extend(duck_types)
        all_children.extend(code_blocks)
        all_children.extend(imports)
        all_children.extend(calls)

        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=_span(root),
            children=tuple(all_children),
            path=str(path),
            language="python",
        )
