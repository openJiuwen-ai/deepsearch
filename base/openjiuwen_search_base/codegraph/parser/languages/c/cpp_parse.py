"""C++ language parser extending the C base parser.

Adds support for classes, namespaces, templates, lambdas,
constructors/destructors, operator overloads, scoped calls,
and out-of-class method definitions.
"""

import logging
from pathlib import Path

import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

from ...constants import MAX_AST_DEPTH, NodeType
from ...custom_types import Parameter
from ...models.core import (
    BaseNode,
    CallNode,
    ClassNode,
    FunctionNode,
    PropertyNode,
)
from ...models.extensions import ModuleNode, TypeAliasNode
from ...models.structural import FileNode
from .._common import first_child_of_type, span, text
from .parse import (
    CBaseParser,
    _complexity,
    _declarator_name,
    _extract_decorators,
    _extract_local_vars,
    _find_body,
    _parse_params,
    _preceding_comment,
    _type_text_from_declaration,
)

logger = logging.getLogger(__name__)

_CPP_LANG = Language(tree_sitter_cpp.language())

_CPP_SPECIFIERS = frozenset({"virtual", "override", "final", "explicit", "constexpr", "consteval"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_template_args(name: str) -> str:
    """Strip template arguments: ``Container<int>`` → ``Container``."""
    idx = name.find("<")
    return name[:idx] if idx != -1 else name


def _extract_bases(node: Node) -> tuple[str, ...]:
    """Extract base class names from a base_class_clause."""
    clause = first_child_of_type(node, "base_class_clause")
    if clause is None:
        return ()
    bases: list[str] = []
    for child in clause.children:
        if child.type == "type_identifier":
            bases.append(text(child))
        elif child.type == "template_type":
            name_node = first_child_of_type(child, "type_identifier")
            if name_node:
                bases.append(text(name_node))
        elif child.type == "qualified_identifier":
            bases.append(_strip_template_args(text(child).replace("::", ".")))
    return tuple(bases)


def _access_decorator(node: Node) -> str | None:
    """Determine the access specifier in effect for a class member."""
    prev = node.prev_named_sibling
    while prev:
        if prev.type == "access_specifier":
            return f"@{text(prev).rstrip(':').strip()}"
        prev = prev.prev_named_sibling
    return None


def _cpp_decorators(node: Node) -> tuple[str, ...]:
    """Extract C++ specifiers as synthetic decorators."""
    decorators = list(_extract_decorators(node))
    for child in node.children:
        if child.type == "virtual_specifier":
            word = text(child)
            decorators.append(f"@{word}")
        elif child.type == "virtual":
            decorators.append("@virtual")
        elif child.type == "explicit_function_specifier":
            decorators.append("@explicit")
        elif child.type in ("storage_class_specifier", "type_qualifier"):
            word = text(child)
            if word in _CPP_SPECIFIERS:
                decorators.append(f"@{word}")
    # Check for trailing const / noexcept
    for child in node.children:
        if child.type == "type_qualifier" and text(child) == "const":
            if "@const" not in decorators:
                decorators.append("@const")
        elif child.type == "noexcept" or (child.type == "identifier" and text(child) == "noexcept"):
            decorators.append("@noexcept")
    return tuple(decorators)


def _is_constructor(name: str, class_name: str) -> bool:
    """Check if a function name looks like a constructor."""
    return name == class_name


def _is_destructor_node(declarator: Node) -> bool:
    """Check if a function declarator is a destructor (~ClassName)."""
    if declarator is None:
        return False
    t = text(declarator)
    return "~" in t


def _needs_overload_suffix(parent: Node, raw_name: str, node_type: str) -> bool:
    """Check if a method needs an overload suffix by scanning siblings."""
    count = 0
    for sibling in parent.children:
        if sibling.type != node_type:
            continue
        decl = first_child_of_type(sibling, "function_declarator")
        if decl is None:
            ptr = first_child_of_type(sibling, "pointer_declarator")
            if ptr:
                decl = first_child_of_type(ptr, "function_declarator")
        if decl is None:
            continue
        sib_name = _declarator_name(decl)
        if sib_name == raw_name:
            count += 1
            if count > 1:
                return True
    return False


def _overload_suffix(params: tuple[Parameter, ...]) -> str:
    """Build a type-signature suffix like ``(int, const string&)``."""
    parts: list[str] = []
    for p in params:
        parts.append(p.type_annotation or "?")
    return f"({', '.join(parts)})"


def _cpp_context_name(node: Node) -> str | None:
    """Walk up the AST to find the enclosing function/method name for C++.

    Handles qualified_identifier in function declarators (out-of-class methods).
    Returns the short method name for use in CallNode.context.
    """
    curr = node.parent
    while curr:
        if curr.type == "function_definition":
            decl = first_child_of_type(curr, "function_declarator")
            if decl is None:
                ptr = first_child_of_type(curr, "pointer_declarator", "reference_declarator")
                if ptr:
                    decl = first_child_of_type(ptr, "function_declarator")
            if decl:
                # Check for qualified_identifier (out-of-class: MyClass::method)
                qi = first_child_of_type(decl, "qualified_identifier")
                if qi:
                    parts = text(qi).split("::")
                    return parts[-1] if parts else _declarator_name(decl)
                return _declarator_name(decl)
        elif curr.type == "lambda_expression":
            # Inside a lambda -- find its assigned name
            p = curr.parent
            if p and p.type == "init_declarator":
                id_node = first_child_of_type(p, "identifier")
                if id_node:
                    return text(id_node)
            return None
        curr = curr.parent
    return None


# ---------------------------------------------------------------------------
# File-level call extraction for C++
# ---------------------------------------------------------------------------


def _extract_calls_cpp(root: Node) -> list[CallNode]:
    """Walk the entire file AST and extract all call_expression nodes."""
    calls: list[CallNode] = []

    def _walk(node: Node, depth: int = 0) -> None:
        if depth > MAX_AST_DEPTH:
            return
        if node.type == "call_expression":
            call = _build_call_cpp(node)
            if call:
                calls.append(call)
        for child in node.children:
            _walk(child, depth + 1)

    _walk(root)
    return calls


def _build_call_cpp(node: Node) -> CallNode | None:
    """Build a CallNode from a C++ call_expression."""
    func_node = node.children[0] if node.children else None
    if func_node is None:
        return None

    callee: str = ""
    receiver: str | None = None

    if func_node.type == "identifier":
        callee = text(func_node)
    elif func_node.type == "field_expression":
        # obj.method() or ptr->method()
        field_id = first_child_of_type(func_node, "field_identifier")
        if field_id:
            callee = text(field_id)
        arg_node = func_node.children[0] if func_node.children else None
        if arg_node:
            if arg_node.type == "identifier":
                receiver = text(arg_node)
            elif arg_node.type == "this":
                receiver = "this"
            else:
                receiver = text(arg_node)
    elif func_node.type == "qualified_identifier":
        # Class::method() or ns::func()
        parts = text(func_node).split("::")
        if len(parts) >= 2:
            receiver = "::".join(parts[:-1])
            callee = parts[-1]
        else:
            callee = text(func_node)
    elif func_node.type == "template_function":
        # func<T>(...)
        name_node = first_child_of_type(func_node, "identifier", "qualified_identifier")
        if name_node:
            if name_node.type == "qualified_identifier":
                parts = text(name_node).split("::")
                if len(parts) >= 2:
                    receiver = "::".join(parts[:-1])
                    callee = parts[-1]
                else:
                    callee = text(name_node)
            else:
                callee = text(name_node)
    else:
        callee = text(func_node)

    if not callee:
        return None

    # Extract arguments
    args_node = first_child_of_type(node, "argument_list")
    arguments: list[str] = []
    if args_node:
        for child in args_node.children:
            if child.type not in ("(", ")", ","):
                arguments.append(text(child))

    context = _cpp_context_name(node)

    return CallNode(
        node_type=NodeType.CALL,
        name=callee,
        span=span(node),
        source=text(node),
        callee=callee,
        receiver=receiver,
        full_expression=text(node),
        context=context,
        arguments=tuple(arguments),
    )


# ---------------------------------------------------------------------------
# CppParser
# ---------------------------------------------------------------------------


class CppParser(CBaseParser):
    """Parser for C++ source files, extending CBaseParser with OOP constructs."""

    def __init__(self) -> None:
        self._parser = Parser(_CPP_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse C++ source into a FileNode tree."""
        tree = self._parser.parse(source)
        root = tree.root_node
        children = self._extract_top_level(root, str(path))
        calls = self._extract_file_calls(root)
        children.extend(calls)
        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=span(root),
            path=str(path),
            language="cpp",
            children=tuple(children),
        )

    def _get_language_name(self) -> str:
        return "cpp"

    def _extract_file_calls(self, root: Node) -> list[CallNode]:
        """Extract all C++ calls from the file as a flat list with context."""
        return _extract_calls_cpp(root)

    def _dispatch_node(self, node: Node, context: str | None) -> list[BaseNode]:
        """Extended dispatch for C++ node types."""
        t = node.type
        if t == "class_specifier":
            cls = self._build_class(node)
            if cls:
                return [cls]
        elif t == "namespace_definition":
            ns = self._build_namespace(node)
            if ns:
                return [ns]
        elif t == "template_declaration":
            return self._build_template(node, context)
        elif t == "alias_declaration":
            alias = self._build_alias(node)
            if alias:
                return [alias]
        elif t == "declaration":
            return self._build_cpp_declaration(node, context)
        elif t == "function_definition":
            # Check for out-of-class method definition (qualified_identifier)
            fn = self._build_function_or_method(node, context)
            if fn:
                return [fn]
            return []
        # Fall through to C base for other types
        return super()._dispatch_node(node, context)

    # -- Out-of-class method definitions --

    def _build_function_or_method(self, node: Node, context: str | None) -> FunctionNode | None:
        """Build a FunctionNode, detecting out-of-class methods via qualified_identifier."""
        declarator = first_child_of_type(node, "function_declarator")
        if declarator is None:
            ptr = first_child_of_type(node, "pointer_declarator", "reference_declarator")
            if ptr:
                declarator = first_child_of_type(ptr, "function_declarator")
        if declarator is None:
            return None

        # Check for qualified_identifier (e.g., MyClass::renderFrame)
        qi = first_child_of_type(declarator, "qualified_identifier")
        if qi:
            return self._build_out_of_class_method(node, declarator, qi)

        # Plain function -- delegate to C base
        return self._build_function(node, context)

    def _build_out_of_class_method(self, node: Node, declarator: Node, qi: Node) -> FunctionNode | None:
        """Build a FunctionNode for an out-of-class method definition."""
        full_name = text(qi)
        parts = full_name.split("::")
        if len(parts) < 2:
            return self._build_function(node, None)

        class_name = "::".join(parts[:-1])
        raw_name = parts[-1]

        # Detect destructor
        is_dtor = raw_name.startswith("~")
        is_ctor = _is_constructor(raw_name, class_name.split("::")[-1])

        if is_dtor:
            qualified = f"{class_name}.<destroy>"
        elif is_ctor:
            qualified = f"{class_name}.<init>"
        elif raw_name.startswith("operator"):
            qualified = f"{class_name}.{raw_name}"
        else:
            qualified = f"{class_name}.{raw_name}"

        param_list = first_child_of_type(declarator, "parameter_list")
        params = _parse_params(param_list)

        return_type = _type_text_from_declaration(node) if not is_ctor and not is_dtor else None
        fn_body = _find_body(node)
        decorators = _cpp_decorators(node)
        doc = _preceding_comment(node)

        local_vars = _extract_local_vars(fn_body) if fn_body else []

        return FunctionNode(
            node_type=NodeType.FUNCTION,
            name=qualified,
            span=span(node),
            docstring=doc,
            source=text(node),
            owner=class_name,
            func_type="method",
            parameters=params,
            return_type=return_type,
            decorators=decorators,
            is_async=False,
            cyclomatic_complexity=_complexity(fn_body) if fn_body else 1,
            children=tuple(local_vars),
        )

    # -- Classes --

    def _build_class(self, node: Node) -> ClassNode | None:
        """Build a ClassNode from a class_specifier."""
        name_node = first_child_of_type(node, "type_identifier")
        if name_node is None:
            return None
        name = text(name_node)

        bases = _extract_bases(node)
        body = first_child_of_type(node, "field_declaration_list")
        if body is None:
            return None

        members: list[BaseNode] = []
        for child in body.children:
            extracted = self._extract_class_member(child, name, body)
            members.extend(extracted)

        doc = _preceding_comment(node)
        return ClassNode(
            node_type=NodeType.CLASS,
            name=name,
            span=span(node),
            docstring=doc,
            source=text(node),
            bases=bases,
            children=tuple(members),
        )

    def _extract_class_member(self, node: Node, class_name: str, body: Node) -> list[BaseNode]:
        """Extract a single member from a class body."""
        t = node.type
        if t == "function_definition":
            fn = self._build_method(node, class_name, body)
            if fn:
                return [fn]
        elif t == "declaration":
            decl = first_child_of_type(node, "function_declarator")
            if decl:
                return []
            return self._build_field_from_declaration(node, class_name)
        elif t == "field_declaration":
            return self._build_field_from_declaration(node, class_name)
        elif t in ("class_specifier", "struct_specifier"):
            if t == "class_specifier":
                cls = self._build_class(node)
                return [cls] if cls else []
            else:
                s = self._build_struct(node)
                return [s] if s else []
        elif t == "template_declaration":
            return self._build_template(node, class_name)
        elif t == "alias_declaration":
            alias = self._build_alias(node)
            return [alias] if alias else []
        elif t == "enum_specifier":
            e = self._build_enum(node)
            return [e] if e else []
        return []

    def _build_method(self, node: Node, class_name: str, body_container: Node) -> FunctionNode | None:
        """Build a FunctionNode for a class method (including ctors/dtors)."""
        declarator = first_child_of_type(node, "function_declarator")
        if declarator is None:
            ptr = first_child_of_type(node, "pointer_declarator", "reference_declarator")
            if ptr:
                declarator = first_child_of_type(ptr, "function_declarator")
        if declarator is None:
            return None

        raw_name = _declarator_name(declarator)
        if not raw_name:
            return None

        is_dtor = _is_destructor_node(declarator) or raw_name.startswith("~")
        is_ctor = _is_constructor(raw_name, class_name) and not is_dtor

        if is_dtor:
            qualified = f"{class_name}.<destroy>"
        elif is_ctor:
            qualified = f"{class_name}.<init>"
        elif raw_name.startswith("operator"):
            qualified = f"{class_name}.{raw_name}"
        else:
            qualified = f"{class_name}.{raw_name}"

        param_list = first_child_of_type(declarator, "parameter_list")
        params = _parse_params(param_list)

        # Overload disambiguation
        if is_ctor and _needs_overload_suffix(body_container, raw_name, "function_definition"):
            qualified = f"{qualified}{_overload_suffix(params)}"
        elif not is_ctor and not is_dtor and _needs_overload_suffix(body_container, raw_name, "function_definition"):
            qualified = f"{qualified}{_overload_suffix(params)}"

        return_type = _type_text_from_declaration(node) if not is_ctor and not is_dtor else None
        fn_body = _find_body(node)
        decorators = _cpp_decorators(node)

        access = _access_decorator(node)
        if access:
            decorators = (access,) + decorators

        doc = _preceding_comment(node)
        local_vars = _extract_local_vars(fn_body) if fn_body else []
        return FunctionNode(
            node_type=NodeType.FUNCTION,
            name=qualified,
            span=span(node),
            docstring=doc,
            source=text(node),
            owner=class_name,
            func_type="method",
            parameters=params,
            return_type=return_type,
            decorators=decorators,
            is_async=False,
            cyclomatic_complexity=_complexity(fn_body) if fn_body else 1,
            children=tuple(local_vars),
        )

    def _build_field_from_declaration(self, node: Node, owner: str) -> list[BaseNode]:
        """Build PropertyNode(s) from a field/member declaration."""
        type_str = _type_text_from_declaration(node)
        results: list[BaseNode] = []

        for child in node.children:
            if child.type == "field_identifier":
                results.append(
                    PropertyNode(
                        node_type=NodeType.PROPERTY,
                        name=text(child),
                        span=span(node),
                        source=text(node),
                        owner=owner,
                        type_annotation=type_str or None,
                    )
                )
            elif child.type in (
                "init_declarator",
                "identifier",
                "pointer_declarator",
                "array_declarator",
                "reference_declarator",
            ):
                name = _declarator_name(child)
                if name and name != type_str:
                    results.append(
                        PropertyNode(
                            node_type=NodeType.PROPERTY,
                            name=name,
                            span=span(node),
                            source=text(node),
                            owner=owner,
                            type_annotation=type_str or None,
                        )
                    )

        return results

    # -- Namespaces --

    def _build_namespace(self, node: Node) -> ModuleNode | None:
        """Build a ModuleNode from a namespace_definition."""
        name_node = first_child_of_type(node, "identifier", "namespace_identifier")
        if name_node is None:
            return None
        name = text(name_node)

        body = first_child_of_type(node, "declaration_list")
        children: list[BaseNode] = []
        if body:
            for child in body.children:
                extracted = self._dispatch_node(child, context=None)
                children.extend(extracted)

        return ModuleNode(
            node_type=NodeType.MODULE,
            name=name,
            span=span(node),
            source=text(node),
            children=tuple(children),
        )

    # -- Templates --

    def _build_template(self, node: Node, context: str | None) -> list[BaseNode]:
        """Unwrap a template_declaration and extract the inner declaration."""
        for child in node.children:
            if child.type in (
                "function_definition",
                "class_specifier",
                "struct_specifier",
                "declaration",
                "alias_declaration",
            ):
                return self._dispatch_node(child, context)
        return []

    # -- Using aliases --

    def _build_alias(self, node: Node) -> TypeAliasNode | None:
        """Build a TypeAliasNode from ``using Name = Type;``."""
        name_node = first_child_of_type(node, "type_identifier")
        if name_node is None:
            return None
        name = text(name_node)

        type_parts: list[str] = []
        found_eq = False
        for child in node.children:
            if child.type == "=":
                found_eq = True
                continue
            if found_eq and child.type != ";":
                type_parts.append(text(child))

        aliased = " ".join(type_parts).strip()
        return TypeAliasNode(
            node_type=NodeType.TYPE_ALIAS,
            name=name,
            span=span(node),
            source=text(node),
            aliased_type=aliased,
        )

    # -- Declarations (C++ extensions) --

    def _build_cpp_declaration(self, node: Node, context: str | None) -> list[BaseNode]:
        """Handle C++ declarations including lambda assignments."""
        for child in node.children:
            if child.type == "init_declarator":
                for sub in child.children:
                    if sub.type == "lambda_expression":
                        return self._build_lambda(node, child, sub, context)

        return self._build_declaration(node, context)

    def _build_lambda(self, decl_node: Node, init_decl: Node, lambda_node: Node, context: str | None) -> list[BaseNode]:
        """Build a FunctionNode from a lambda assigned to a variable."""
        name_child = first_child_of_type(init_decl, "identifier", "pointer_declarator")
        if name_child is None:
            return []
        name = _declarator_name(name_child)
        if not name:
            return []

        param_decl = first_child_of_type(lambda_node, "abstract_function_declarator")
        if param_decl is None:
            param_decl = first_child_of_type(lambda_node, "parameter_list")
        params: tuple[Parameter, ...] = ()
        if param_decl:
            p_list = first_child_of_type(param_decl, "parameter_list")
            if p_list:
                params = _parse_params(p_list)
            elif param_decl.type == "parameter_list":
                params = _parse_params(param_decl)

        body = first_child_of_type(lambda_node, "compound_statement")

        local_vars = _extract_local_vars(body) if body else []

        return [
            FunctionNode(
                node_type=NodeType.FUNCTION,
                name=name,
                span=span(decl_node),
                source=text(decl_node),
                owner=context,
                func_type="lambda",
                parameters=params,
                is_async=False,
                cyclomatic_complexity=_complexity(body) if body else 1,
                children=tuple(local_vars),
            )
        ]
