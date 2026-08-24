"""C language parser using tree-sitter.

Provides :class:`CBaseParser` which handles all C constructs.
The C++ parser (:class:`CppParser`) extends this class.
"""

import logging
from pathlib import Path

import tree_sitter_c
from tree_sitter import Language, Node, Parser

from ...constants import MAX_AST_DEPTH, NodeType
from ...custom_types import Parameter
from ...models.core import (
    BaseNode,
    CallNode,
    FunctionNode,
    ImportNode,
    LocalVarNode,
    PropertyNode,
)
from ...models.extensions import EnumNode, MacroNode, StructNode, TypeAliasNode, UnionNode
from ...models.structural import FileNode
from .. import BaseLanguageParser
from .._common import first_child_of_type, span, text

logger = logging.getLogger(__name__)

_C_LANG = Language(tree_sitter_c.language())

_COMPLEXITY_BRANCH = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "do_statement",
        "switch_statement",
        "case_statement",
        "conditional_expression",
        "goto_statement",
    }
)

_STORAGE_CLASSES = frozenset({"static", "extern", "register", "inline", "_Noreturn"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _complexity(node: Node) -> int:
    """Cyclomatic complexity for C/C++ function bodies."""
    count = 1

    def _walk(n: Node, depth: int = 0) -> None:
        nonlocal count
        if depth > MAX_AST_DEPTH:
            return
        if n.type in _COMPLEXITY_BRANCH:
            count += 1
        for child in n.children:
            _walk(child, depth + 1)

    _walk(node)
    return count


def _declarator_name(node: Node) -> str:
    """Extract the identifier name from a (possibly nested) declarator."""
    if node.type == "identifier":
        return text(node)
    if node.type == "field_identifier":
        return text(node)
    for child in node.children:
        if child.type in ("identifier", "field_identifier"):
            return text(child)
        if child.type in (
            "pointer_declarator",
            "array_declarator",
            "function_declarator",
            "parenthesized_declarator",
        ):
            return _declarator_name(child)
    return text(node)


def _type_text_from_declaration(node: Node) -> str:
    """Extract a human-readable type string from a declaration node.

    Combines the type specifiers (everything before the declarator) into
    a single string, stripping storage classes.
    """
    parts: list[str] = []
    for child in node.children:
        if child.type in ("storage_class_specifier", "type_qualifier"):
            word = text(child)
            if word not in _STORAGE_CLASSES:
                parts.append(word)
        elif child.type in (
            "primitive_type",
            "sized_type_specifier",
            "type_identifier",
            "qualified_identifier",
            "template_type",
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
        ):
            parts.append(text(child))
        elif child.type in (
            "init_declarator",
            "function_declarator",
            "pointer_declarator",
            "array_declarator",
            "identifier",
            "field_identifier",
            ";",
        ):
            break
    return " ".join(parts)


def _extract_decorators(node: Node) -> tuple[str, ...]:
    """Extract storage class and qualifier decorators from a declaration."""
    decorators: list[str] = []
    for child in node.children:
        if child.type == "storage_class_specifier":
            word = text(child)
            if word in _STORAGE_CLASSES:
                decorators.append(f"@{word}")
        elif child.type == "type_qualifier":
            word = text(child)
            if word in ("const", "volatile", "restrict"):
                decorators.append(f"@{word}")
    return tuple(decorators)


def _parse_params(node: Node) -> tuple[Parameter, ...]:
    """Parse function parameters from a parameter_list node."""
    if node is None:
        return ()
    params: list[Parameter] = []
    for child in node.children:
        if child.type == "parameter_declaration":
            name = ""
            type_parts: list[str] = []
            for part in child.children:
                if part.type in ("identifier", "field_identifier"):
                    name = text(part)
                elif part.type in (
                    "pointer_declarator",
                    "array_declarator",
                    "abstract_pointer_declarator",
                    "abstract_array_declarator",
                ):
                    inner_name = _declarator_name(part)
                    if inner_name:
                        name = inner_name
                    if "pointer" in part.type:
                        type_parts.append("*")
                    elif "array" in part.type:
                        type_parts.append("[]")
                elif part.type in (
                    "primitive_type",
                    "sized_type_specifier",
                    "type_identifier",
                    "struct_specifier",
                    "union_specifier",
                    "enum_specifier",
                    "type_qualifier",
                ):
                    type_parts.append(text(part))
            type_ann = " ".join(type_parts) if type_parts else None
            if name:
                params.append(Parameter(name=name, type_annotation=type_ann))
        elif child.type == "variadic_parameter":
            params.append(Parameter(name="...", type_annotation=None))
    return tuple(params)


def _preceding_comment(node: Node) -> str | None:
    """Extract a doc comment (/** ... */ or /// style) preceding a node."""
    prev = node.prev_named_sibling
    if prev is None:
        return None
    if prev.type == "comment":
        t = text(prev)
        if t.startswith("/**") or t.startswith("///"):
            lines = t.strip().split("\n")
            cleaned: list[str] = []
            for line in lines:
                line = line.strip()
                if line.startswith("/**"):
                    line = line[3:]
                elif line.startswith("///"):
                    line = line[3:]
                elif line.startswith("*/"):
                    continue
                elif line.startswith("*"):
                    line = line[1:]
                if line.startswith(" "):
                    line = line[1:]
                cleaned.append(line)
            result = "\n".join(cleaned).strip()
            if result.endswith("*/"):
                result = result[:-2].strip()
            return result or None
    return None


def _find_body(node: Node) -> Node | None:
    """Find the compound_statement (body) of a function definition."""
    return first_child_of_type(node, "compound_statement")


_C_PRIMITIVE_TYPES = frozenset(
    {
        "int",
        "char",
        "float",
        "double",
        "void",
        "long",
        "short",
        "unsigned",
        "signed",
        "bool",
        "auto",
        "size_t",
        "ssize_t",
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
    }
)


def _extract_local_vars(body: Node, depth: int = 0) -> list[LocalVarNode]:
    """Recursively extract local variable declarations from a compound_statement."""
    results: list[LocalVarNode] = []
    if body is None:
        return results

    for child in body.children:
        if child.type == "declaration":
            type_str = _type_text_from_declaration(child)
            if not type_str or type_str in _C_PRIMITIVE_TYPES:
                continue
            for sub in child.children:
                if sub.type == "init_declarator":
                    decl_child = first_child_of_type(sub, "identifier", "pointer_declarator", "array_declarator")
                    if decl_child:
                        name = _declarator_name(decl_child)
                        if name:
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
                elif sub.type in ("identifier", "pointer_declarator", "array_declarator"):
                    name = _declarator_name(sub)
                    if name and name != type_str:
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

        # Recurse into nested compound statements
        if child.type == "compound_statement":
            results.extend(_extract_local_vars(child, depth + 1))
        elif child.type in ("if_statement", "while_statement", "do_statement", "switch_statement"):
            for sub in child.children:
                if sub.type == "compound_statement":
                    results.extend(_extract_local_vars(sub, depth + 1))
        elif child.type in ("for_statement", "for_range_loop"):
            # for-loop init declaration
            for sub in child.children:
                if sub.type == "declaration":
                    type_str = _type_text_from_declaration(sub)
                    if type_str and type_str not in _C_PRIMITIVE_TYPES:
                        for inner in sub.children:
                            if inner.type == "init_declarator":
                                decl_child = first_child_of_type(
                                    inner, "identifier", "pointer_declarator", "array_declarator"
                                )
                                if decl_child:
                                    name = _declarator_name(decl_child)
                                    if name:
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
                elif sub.type == "compound_statement":
                    results.extend(_extract_local_vars(sub, depth + 1))

    return results


def _context_name(node: Node) -> str | None:
    """Walk up the AST from *node* to find the enclosing function/method name.

    Returns the short function name (not qualified) for use in CallNode.context.
    """
    curr = node.parent
    while curr:
        if curr.type == "function_definition":
            decl = first_child_of_type(curr, "function_declarator")
            if decl is None:
                ptr = first_child_of_type(curr, "pointer_declarator")
                if ptr:
                    decl = first_child_of_type(ptr, "function_declarator")
            if decl:
                name = _declarator_name(decl)
                if name:
                    return name
        curr = curr.parent
    return None


# ---------------------------------------------------------------------------
# File-level call extraction
# ---------------------------------------------------------------------------


def _extract_calls_c(root: Node) -> list[CallNode]:
    """Walk the entire file AST and extract all call_expression nodes as flat CallNodes."""
    calls: list[CallNode] = []

    def _walk(node: Node, depth: int = 0) -> None:
        if depth > MAX_AST_DEPTH:
            return
        if node.type == "call_expression":
            call = _build_call_c(node)
            if call:
                calls.append(call)
        for child in node.children:
            _walk(child, depth + 1)

    _walk(root)
    return calls


def _build_call_c(node: Node) -> CallNode | None:
    """Build a CallNode from a C call_expression."""
    func_node = node.children[0] if node.children else None
    if func_node is None:
        return None

    callee: str = ""
    receiver: str | None = None

    if func_node.type == "identifier":
        callee = text(func_node)
    elif func_node.type == "field_expression":
        # obj.method or ptr->method
        field_id = first_child_of_type(func_node, "field_identifier")
        if field_id:
            callee = text(field_id)
        arg_node = func_node.children[0] if func_node.children else None
        if arg_node and arg_node.type == "identifier":
            receiver = text(arg_node)
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

    context = _context_name(node)

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
# CBaseParser
# ---------------------------------------------------------------------------


class CBaseParser(BaseLanguageParser):
    """Parser for C source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(_C_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse C source into a FileNode tree."""
        tree = self._parser.parse(source)
        root = tree.root_node
        children = self._extract_top_level(root, str(path))
        # Extract calls at file level (flat, with context)
        calls = self._extract_file_calls(root)
        children.extend(calls)
        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=span(root),
            path=str(path),
            language="c",
            children=tuple(children),
        )

    def _get_language_name(self) -> str:
        return "c"

    def _extract_file_calls(self, root: Node) -> list[CallNode]:
        """Extract all calls from the file as a flat list with context."""
        return _extract_calls_c(root)

    def _extract_top_level(self, root: Node, file_path: str) -> list[BaseNode]:
        """Walk top-level children and dispatch to handlers."""
        children: list[BaseNode] = []
        for node in root.children:
            extracted = self._dispatch_node(node, context=None)
            if extracted:
                children.extend(extracted)
        return children

    def _dispatch_node(self, node: Node, context: str | None) -> list[BaseNode]:
        """Dispatch a single tree-sitter node to the appropriate handler."""
        t = node.type
        if t == "function_definition":
            fn = self._build_function(node, context)
            if fn:
                return [fn]
        elif t == "struct_specifier":
            s = self._build_struct(node)
            if s:
                return [s]
        elif t == "union_specifier":
            u = self._build_union(node)
            if u:
                return [u]
        elif t == "enum_specifier":
            e = self._build_enum(node)
            if e:
                return [e]
        elif t == "type_definition":
            return self._build_typedef(node)
        elif t == "preproc_def":
            m = self._build_macro(node)
            if m:
                return [m]
        elif t == "preproc_function_def":
            m = self._build_function_macro(node)
            if m:
                return [m]
        elif t == "preproc_include":
            imp = self._build_include(node)
            if imp:
                return [imp]
        elif t == "declaration":
            return self._build_declaration(node, context)
        return []

    # -- Functions --

    def _build_function(self, node: Node, context: str | None) -> FunctionNode | None:
        """Build a FunctionNode from a function_definition."""
        declarator = first_child_of_type(node, "function_declarator")
        if declarator is None:
            ptr_decl = first_child_of_type(node, "pointer_declarator")
            if ptr_decl:
                declarator = first_child_of_type(ptr_decl, "function_declarator")
        if declarator is None:
            return None

        name = _declarator_name(declarator)
        if not name:
            return None

        param_list = first_child_of_type(declarator, "parameter_list")
        params = _parse_params(param_list)
        return_type = _type_text_from_declaration(node) or None
        body = _find_body(node)
        decorators = _extract_decorators(node)
        doc = _preceding_comment(node)

        func_type: str = "method" if context else "function"
        owner = context

        local_vars = _extract_local_vars(body) if body else []

        return FunctionNode(
            node_type=NodeType.FUNCTION,
            name=f"{context}.{name}" if context else name,
            span=span(node),
            docstring=doc,
            source=text(node),
            owner=owner,
            func_type=func_type,
            parameters=params,
            return_type=return_type,
            decorators=decorators,
            is_async=False,
            cyclomatic_complexity=_complexity(body) if body else 1,
            children=tuple(local_vars),
        )

    # -- Structs --

    def _build_struct(self, node: Node, name_override: str | None = None) -> StructNode | None:
        """Build a StructNode from a struct_specifier."""
        name_node = first_child_of_type(node, "type_identifier")
        name = name_override or (text(name_node) if name_node else None)
        if not name:
            return None

        body = first_child_of_type(node, "field_declaration_list")
        if body is None:
            return None

        fields: list[PropertyNode] = []
        for field_node in body.children:
            if field_node.type == "field_declaration":
                for prop in self._build_field(field_node, name):
                    fields.append(prop)

        doc = _preceding_comment(node)
        return StructNode(
            node_type=NodeType.STRUCT,
            name=name,
            span=span(node),
            docstring=doc,
            source=text(node),
            fields=tuple(fields),
            children=tuple(fields),
        )

    # -- Unions --

    def _build_union(self, node: Node, name_override: str | None = None) -> UnionNode | None:
        """Build a UnionNode from a union_specifier."""
        name_node = first_child_of_type(node, "type_identifier")
        name = name_override or (text(name_node) if name_node else None)
        if not name:
            return None

        body = first_child_of_type(node, "field_declaration_list")
        if body is None:
            return None

        variants: list[str] = []
        for field_node in body.children:
            if field_node.type == "field_declaration":
                field_name_node = first_child_of_type(field_node, "field_identifier")
                if field_name_node is None:
                    decl = first_child_of_type(field_node, "pointer_declarator", "array_declarator")
                    if decl:
                        field_name_node_inner = _declarator_name(decl)
                        if field_name_node_inner:
                            variants.append(field_name_node_inner)
                else:
                    variants.append(text(field_name_node))

        doc = _preceding_comment(node)
        return UnionNode(
            node_type=NodeType.UNION,
            name=name,
            span=span(node),
            docstring=doc,
            source=text(node),
            variants=tuple(variants),
        )

    # -- Enums --

    def _build_enum(self, node: Node, name_override: str | None = None) -> EnumNode | None:
        """Build an EnumNode from an enum_specifier."""
        name_node = first_child_of_type(node, "type_identifier")
        name = name_override or (text(name_node) if name_node else None)
        if not name:
            return None

        body = first_child_of_type(node, "enumerator_list")
        members: list[str] = []
        if body:
            for child in body.children:
                if child.type == "enumerator":
                    id_node = first_child_of_type(child, "identifier")
                    if id_node:
                        members.append(text(id_node))

        doc = _preceding_comment(node)
        return EnumNode(
            node_type=NodeType.ENUM,
            name=name,
            span=span(node),
            docstring=doc,
            source=text(node),
            members=tuple(members),
        )

    # -- Typedefs --

    def _build_typedef(self, node: Node) -> list[BaseNode]:
        """Handle typedef: may wrap struct/union/enum or be a plain type alias."""
        struct_spec = first_child_of_type(node, "struct_specifier")
        union_spec = first_child_of_type(node, "union_specifier")
        enum_spec = first_child_of_type(node, "enum_specifier")

        typedef_name: str | None = None
        children = list(node.children)
        for child in reversed(children):
            if child.type == ";":
                continue
            if child.type == "type_identifier":
                typedef_name = text(child)
                break
            if child.type == "primitive_type" and not struct_spec and not union_spec and not enum_spec:
                typedef_name = text(child)
                break
            if child.type in ("pointer_declarator", "array_declarator"):
                typedef_name = _declarator_name(child)
                break
            if child.type == "function_declarator":
                typedef_name = _declarator_name(child)
                break
            if child.type == "identifier":
                typedef_name = text(child)
                break

        if not typedef_name:
            return []

        if struct_spec:
            s = self._build_struct(struct_spec, name_override=typedef_name)
            return [s] if s else []

        if union_spec:
            u = self._build_union(union_spec, name_override=typedef_name)
            return [u] if u else []

        if enum_spec:
            e = self._build_enum(enum_spec, name_override=typedef_name)
            return [e] if e else []

        # Plain typedef
        type_parts: list[str] = []
        hit_typedef = False
        for child in children:
            if child.type == "typedef":
                hit_typedef = True
                continue
            if not hit_typedef:
                continue
            if text(child) == typedef_name:
                break
            if child.type == ";":
                break
            type_parts.append(text(child))

        aliased = " ".join(type_parts).strip()
        return [
            TypeAliasNode(
                node_type=NodeType.TYPE_ALIAS,
                name=typedef_name,
                span=span(node),
                source=text(node),
                aliased_type=aliased,
            )
        ]

    # -- Macros --

    def _build_macro(self, node: Node) -> MacroNode | None:
        """Build a MacroNode from preproc_def (object-like macro)."""
        name_node = first_child_of_type(node, "identifier")
        if name_node is None:
            return None
        name = text(name_node)

        value_node = first_child_of_type(node, "preproc_arg")
        expansion = text(value_node).strip() if value_node else ""

        return MacroNode(
            node_type=NodeType.MACRO,
            name=name,
            span=span(node),
            source=text(node),
            parameters=(),
            expansion=expansion,
        )

    def _build_function_macro(self, node: Node) -> MacroNode | None:
        """Build a MacroNode from preproc_function_def (function-like macro)."""
        name_node = first_child_of_type(node, "identifier")
        if name_node is None:
            return None
        name = text(name_node)

        params_node = first_child_of_type(node, "preproc_params")
        params: list[str] = []
        if params_node:
            for child in params_node.children:
                if child.type == "identifier":
                    params.append(text(child))
                elif child.type == "...":
                    params.append("...")

        value_node = first_child_of_type(node, "preproc_arg")
        expansion = text(value_node).strip() if value_node else ""

        return MacroNode(
            node_type=NodeType.MACRO,
            name=name,
            span=span(node),
            source=text(node),
            parameters=tuple(params),
            expansion=expansion,
        )

    # -- Includes --

    def _build_include(self, node: Node) -> ImportNode | None:
        """Build an ImportNode from preproc_include."""
        path_node = first_child_of_type(node, "string_literal", "system_lib_string")
        if path_node is None:
            return None
        raw = text(path_node)
        if raw.startswith('"') and raw.endswith('"'):
            module = raw[1:-1]
        elif raw.startswith("<") and raw.endswith(">"):
            module = raw[1:-1]
        else:
            module = raw

        header_name = Path(module).stem

        return ImportNode(
            node_type=NodeType.IMPORT,
            name=text(node).strip(),
            span=span(node),
            source=text(node),
            module=module,
            names=(header_name,),
        )

    # -- Declarations (variables/fields) --

    def _build_declaration(self, node: Node, context: str | None) -> list[BaseNode]:
        """Build PropertyNode(s) from a top-level declaration."""
        type_str = _type_text_from_declaration(node)
        results: list[BaseNode] = []

        for child in node.children:
            if child.type == "init_declarator":
                decl_child = first_child_of_type(child, "identifier", "pointer_declarator", "array_declarator")
                if decl_child:
                    name = _declarator_name(decl_child)
                    if name:
                        eq_idx = None
                        for i, c in enumerate(child.children):
                            if c.type == "=":
                                eq_idx = i
                                break
                        default = None
                        if eq_idx is not None and eq_idx + 1 < len(child.children):
                            default = text(child.children[eq_idx + 1]).strip()

                        results.append(
                            PropertyNode(
                                node_type=NodeType.PROPERTY,
                                name=name,
                                span=span(node),
                                source=text(node),
                                owner=context,
                                type_annotation=type_str or None,
                                default_value=default,
                            )
                        )
            elif child.type in ("identifier", "pointer_declarator", "array_declarator"):
                name = _declarator_name(child)
                if name and name != type_str:
                    results.append(
                        PropertyNode(
                            node_type=NodeType.PROPERTY,
                            name=name,
                            span=span(node),
                            source=text(node),
                            owner=context,
                            type_annotation=type_str or None,
                        )
                    )

        return results

    def _build_field(self, node: Node, owner: str) -> list[PropertyNode]:
        """Build PropertyNode(s) from a field_declaration inside a struct/union."""
        type_str = _type_text_from_declaration(node)
        results: list[PropertyNode] = []

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
            elif child.type in ("pointer_declarator", "array_declarator"):
                name = _declarator_name(child)
                if name:
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
