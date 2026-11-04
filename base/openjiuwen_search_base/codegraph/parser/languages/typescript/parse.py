"""TypeScript and TSX language parsers using tree-sitter.

Also provides the shared extraction logic reused by the JavaScript parser,
since JS is a strict subset of TS at the AST level.
"""

import asyncio
import logging
from pathlib import Path

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from ...constants import MAX_AST_DEPTH, NodeType
from ...custom_types import Parameter, SourceSpan
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
from ...models.extensions import EnumNode, TypeAliasNode
from ...models.structural import FileNode
from .. import BaseLanguageParser
from .._common import (
    children_of_type,
    complexity,
    first_child_of_type,
    has_child_type,
    span,
    text,
    unwrap_exports,
)

logger = logging.getLogger(__name__)

_TS_LANG = Language(tree_sitter_typescript.language_typescript())
_TSX_LANG = Language(tree_sitter_typescript.language_tsx())

# ---------------------------------------------------------------------------
# TS-specific helpers
# ---------------------------------------------------------------------------


def _type_annotation_text(node: Node) -> str | None:
    """Extract the type from a type_annotation child, stripping the leading `:`."""
    ann = first_child_of_type(node, "type_annotation")
    if ann is None:
        return None
    parts = [text(c) for c in ann.children if c.type != ":"]
    return " ".join(parts).strip() or None


def _params_text(params_node: Node) -> tuple[Parameter, ...]:
    """Extract parameter representations from a formal_parameters node."""
    result: list[Parameter] = []
    for child in params_node.children:
        if child.type in ("required_parameter", "optional_parameter"):
            name_node = first_child_of_type(child, "identifier")
            if name_node is None:
                name_node = first_child_of_type(child, "object_pattern", "array_pattern", "rest_pattern")
            name = text(name_node) if name_node else text(child)
            type_ann = _type_annotation_text(child)
            default: str | None = None
            after_eq = False
            for c in child.children:
                if c.type == "=":
                    after_eq = True
                    continue
                if after_eq:
                    default = text(c)
                    break
            result.append(Parameter(name=name, type_annotation=type_ann, default=default))
        elif child.type == "identifier":
            result.append(Parameter(name=text(child)))
        elif child.type == "assignment_pattern":
            name_node = first_child_of_type(child, "identifier")
            name = text(name_node) if name_node else text(child)
            default = None
            after_eq = False
            for c in child.children:
                if c.type == "=":
                    after_eq = True
                    continue
                if after_eq:
                    default = text(c)
                    break
            result.append(Parameter(name=name, default=default))
        elif child.type in ("rest_pattern", "object_pattern", "array_pattern"):
            result.append(Parameter(name=text(child)))
    return tuple(result)


def _decorators_of(node: Node) -> tuple[str, ...]:
    """Collect decorator children of *node* (TS puts decorators inside the decorated node)."""
    decs: list[str] = []
    for c in node.children:
        if c.type == "decorator":
            decs.append(text(c))
    return tuple(decs) if decs else ()


def _extract_heritage_bases(heritage: Node) -> list[str]:
    """Extract base class / interface names from a class_heritage node.

    Works for both TS (``extends_clause`` / ``implements_clause``) and JS
    (bare ``extends`` keyword) grammars.
    """
    bases: list[str] = []
    extends = first_child_of_type(heritage, "extends_clause")
    if extends:
        for c in extends.children:
            if c.type in ("identifier", "type_identifier", "member_expression"):
                bases.append(text(c))
    else:
        kw = first_child_of_type(heritage, "extends")
        if kw:
            idx = heritage.children.index(kw)
            for c in heritage.children[idx + 1 :]:
                if c.type in ("identifier", "member_expression"):
                    bases.append(text(c))
                    break
    implements = first_child_of_type(heritage, "implements_clause")
    if implements:
        for c in implements.children:
            if c.type in ("identifier", "type_identifier", "generic_type"):
                bases.append(text(c))
    return bases


# ---------------------------------------------------------------------------
# Extraction helpers (shared by TS and JS)
# ---------------------------------------------------------------------------


def _extract_classes(children: list[Node]) -> list[ClassNode]:
    classes: list[ClassNode] = []
    for node in children:
        if node.type not in ("class_declaration", "abstract_class_declaration"):
            continue
        name_node = first_child_of_type(node, "type_identifier", "identifier")
        if name_node is None:
            continue
        name = text(name_node)

        heritage = first_child_of_type(node, "class_heritage")
        bases = _extract_heritage_bases(heritage) if heritage else []

        body_node = first_child_of_type(node, "class_body")
        members = _extract_class_members(body_node, name) if body_node else []
        decs = _decorators_of(node)

        classes.append(
            ClassNode(
                node_type=NodeType.CLASS,
                name=name,
                span=span(node),
                source=text(node),
                children=tuple(members),
                bases=tuple(bases),
                decorators=decs,
            )
        )
    return classes


def _extract_interfaces(children: list[Node]) -> list[InterfaceNode]:
    interfaces: list[InterfaceNode] = []
    for node in children:
        if node.type != "interface_declaration":
            continue
        name_node = first_child_of_type(node, "type_identifier")
        if name_node is None:
            continue
        name = text(name_node)

        bases: list[str] = []
        extends = first_child_of_type(node, "extends_type_clause")
        if extends:
            for c in extends.children:
                if c.type in ("type_identifier", "generic_type"):
                    bases.append(text(c))

        body_node = first_child_of_type(node, "interface_body", "object_type")
        members = _extract_interface_members(body_node, name) if body_node else []

        interfaces.append(
            InterfaceNode(
                node_type=NodeType.INTERFACE,
                name=name,
                span=span(node),
                source=text(node),
                children=tuple(members),
                bases=tuple(bases),
            )
        )
    return interfaces


def _extract_interface_members(
    body: Node,
    iface_name: str,
) -> list[FunctionNode | PropertyNode]:
    members: list[FunctionNode | PropertyNode] = []
    for child in body.children:
        if child.type == "method_signature":
            prop_id = first_child_of_type(child, "property_identifier")
            if prop_id is None:
                continue
            mname = text(prop_id)
            params_node = first_child_of_type(child, "formal_parameters")
            params = _params_text(params_node) if params_node else ()
            ret = _type_annotation_text(child)
            members.append(
                FunctionNode(
                    node_type=NodeType.FUNCTION,
                    name=f"{iface_name}.{mname}",
                    span=span(child),
                    source=text(child),
                    owner=iface_name,
                    func_type="method",
                    parameters=params,
                    return_type=ret,
                )
            )
        elif child.type == "property_signature":
            prop_id = first_child_of_type(child, "property_identifier")
            if prop_id is None:
                continue
            members.append(
                PropertyNode(
                    node_type=NodeType.PROPERTY,
                    name=text(prop_id),
                    span=span(child),
                    owner=iface_name,
                    type_annotation=_type_annotation_text(child),
                )
            )
    return members


def _extract_enums(children: list[Node]) -> list[EnumNode]:
    enums: list[EnumNode] = []
    for node in children:
        if node.type != "enum_declaration":
            continue
        name_node = first_child_of_type(node, "identifier")
        if name_node is None:
            continue
        body = first_child_of_type(node, "enum_body")
        member_names: list[str] = []
        if body:
            for child in body.children:
                if child.type in ("enum_assignment", "property_identifier"):
                    pid = first_child_of_type(child, "property_identifier")
                    if pid:
                        member_names.append(text(pid))
                    elif child.type == "property_identifier":
                        member_names.append(text(child))
        enums.append(
            EnumNode(
                node_type=NodeType.ENUM,
                name=text(name_node),
                span=span(node),
                source=text(node),
                members=tuple(member_names),
            )
        )
    return enums


def _extract_type_aliases(children: list[Node]) -> list[TypeAliasNode]:
    aliases: list[TypeAliasNode] = []
    for node in children:
        if node.type != "type_alias_declaration":
            continue
        name_node = first_child_of_type(node, "type_identifier")
        if name_node is None:
            continue
        parts = []
        after_eq = False
        for c in node.children:
            if c.type == "=":
                after_eq = True
                continue
            if after_eq and c.type != ";":
                parts.append(text(c))
        aliases.append(
            TypeAliasNode(
                node_type=NodeType.TYPE_ALIAS,
                name=text(name_node),
                span=span(node),
                source=text(node),
                aliased_type=" ".join(parts),
            )
        )
    return aliases


def _build_function(
    node: Node,
    *,
    class_name: str | None = None,
    enclosing_func: str | None = None,
) -> FunctionNode:
    """Build a FunctionNode from a function_declaration or similar."""
    name_node = first_child_of_type(node, "identifier", "property_identifier")
    raw_name = text(name_node) if name_node else "<anonymous>"

    if class_name:
        qualified = f"{class_name}.{raw_name}"
        owner = class_name
        ftype = "method"
    elif enclosing_func:
        qualified = f"{enclosing_func}.{raw_name}"
        owner = enclosing_func
        ftype = "nested"
    else:
        qualified = raw_name
        owner = None
        ftype = "function"

    params_node = first_child_of_type(node, "formal_parameters")
    params = _params_text(params_node) if params_node else ()
    ret = _type_annotation_text(node)
    is_async = has_child_type(node, "async")
    body = first_child_of_type(node, "statement_block")
    cc = complexity(body) if body else 1
    decs = _decorators_of(node)
    local_annotations = _extract_local_annotations(body, owner=qualified)

    return FunctionNode(
        node_type=NodeType.FUNCTION,
        name=qualified,
        span=span(node),
        source=text(node),
        children=tuple(local_annotations),
        owner=owner,
        func_type=ftype,
        parameters=params,
        return_type=ret,
        decorators=decs,
        is_async=is_async,
        cyclomatic_complexity=cc,
    )


def _extract_local_annotations(body: Node | None, *, owner: str) -> list[LocalVarNode]:
    """Extract typed variable declarations from a function body.

    Captures ``let x: T = ...`` and ``const y: T = ...`` inside function
    bodies, producing :class:`LocalVarNode` children so the resolver can
    infer receiver types from local annotations.  Recurses into nested
    blocks with @L<line>@D<depth> naming for block-scoped uniqueness.
    """
    if body is None:
        return []
    props: list[LocalVarNode] = []
    _collect_ts_locals(body, owner, props, depth=0)
    return props


_TS_BLOCK_TYPES = frozenset(
    {
        "if_statement",
        "else_clause",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "switch_case",
        "switch_default",
        "try_statement",
        "catch_clause",
        "finally_clause",
    }
)


def _collect_ts_locals(node: Node, owner: str, out: list[LocalVarNode], depth: int) -> None:
    """Walk *node* children, extracting typed declarations and recursing into blocks."""
    for child in node.children:
        if child.type in ("lexical_declaration", "variable_declaration"):
            for declarator in children_of_type(child, "variable_declarator"):
                has_fn_or_cls = any(
                    c.type in ("arrow_function", "function_expression", "function", "class")
                    for c in declarator.children
                )
                if has_fn_or_cls:
                    continue
                type_ann = _type_annotation_text(declarator)
                if type_ann is None:
                    continue
                name_node = first_child_of_type(declarator, "identifier")
                if name_node is None:
                    continue
                default = None
                after_eq = False
                for c in declarator.children:
                    if c.type == "=":
                        after_eq = True
                        continue
                    if after_eq:
                        default = text(c)
                        break
                line = child.start_point[0] + 1
                out.append(
                    LocalVarNode(
                        node_type=NodeType.LOCAL_VAR,
                        name=f"{text(name_node)}@L{line}@D{depth}",
                        span=span(child),
                        type_annotation=type_ann,
                        default_value=default,
                    )
                )
        elif child.type == "statement_block":
            _collect_ts_locals(child, owner, out, depth + 1)
        elif child.type in _TS_BLOCK_TYPES:
            _collect_ts_locals(child, owner, out, depth)


def _extract_functions(children: list[Node]) -> list[FunctionNode]:
    """Extract top-level function_declaration and generator_function_declaration nodes."""
    funcs: list[FunctionNode] = []
    for node in children:
        if node.type in ("function_declaration", "generator_function_declaration"):
            funcs.append(_build_function(node))
    return funcs


def _extract_class_expressions(children: list[Node]) -> list[ClassNode]:
    """Extract class expressions assigned to variables (const X = class {...})."""
    classes: list[ClassNode] = []
    for node in children:
        if node.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for declarator in children_of_type(node, "variable_declarator"):
            cls_node = None
            for c in declarator.children:
                if c.type == "class":
                    cls_node = c
                    break
            if cls_node is None:
                continue
            name_node = first_child_of_type(declarator, "identifier")
            if name_node is None:
                continue
            name = text(name_node)

            heritage = first_child_of_type(cls_node, "class_heritage")
            bases = _extract_heritage_bases(heritage) if heritage else []

            body_node = first_child_of_type(cls_node, "class_body")
            members = _extract_class_members(body_node, name) if body_node else []

            classes.append(
                ClassNode(
                    node_type=NodeType.CLASS,
                    name=name,
                    span=span(node),
                    source=text(node),
                    children=tuple(members),
                    bases=tuple(bases),
                )
            )
    return classes


def _extract_arrow_functions(children: list[Node]) -> list[FunctionNode]:
    """Extract arrow functions and function expressions assigned to variables."""
    funcs: list[FunctionNode] = []
    for node in children:
        if node.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for declarator in children_of_type(node, "variable_declarator"):
            value = None
            for c in declarator.children:
                if c.type in ("arrow_function", "function_expression", "function"):
                    value = c
                    break
            if value is None:
                continue
            name_node = first_child_of_type(declarator, "identifier")
            if name_node is None:
                continue
            raw_name = text(name_node)
            params_node = first_child_of_type(value, "formal_parameters")
            params = _params_text(params_node) if params_node else ()
            ret = _type_annotation_text(value)
            is_async = has_child_type(value, "async")
            body = first_child_of_type(value, "statement_block")
            cc = complexity(body) if body else 1
            local_annotations = _extract_local_annotations(body, owner=raw_name)

            funcs.append(
                FunctionNode(
                    node_type=NodeType.FUNCTION,
                    name=raw_name,
                    span=span(node),
                    source=text(node),
                    children=tuple(local_annotations),
                    func_type="function",
                    parameters=params,
                    return_type=ret,
                    is_async=is_async,
                    cyclomatic_complexity=cc,
                )
            )
    return funcs


def _extract_class_members(
    body: Node,
    class_name: str,
) -> list[FunctionNode | PropertyNode]:
    members: list[FunctionNode | PropertyNode] = []
    for child in body.children:
        if child.type in ("method_definition", "abstract_method_signature"):
            members.append(_build_function(child, class_name=class_name))
        elif child.type == "public_field_definition":
            prop_id = first_child_of_type(child, "property_identifier")
            if prop_id is None:
                continue
            type_ann = _type_annotation_text(child)
            members.append(
                PropertyNode(
                    node_type=NodeType.PROPERTY,
                    name=text(prop_id),
                    span=span(child),
                    source=text(child),
                    owner=class_name,
                    type_annotation=type_ann,
                )
            )
    return members


def _extract_properties(children: list[Node]) -> list[PropertyNode]:
    """Extract module-level variable declarations (skip function/class-valued ones)."""
    props: list[PropertyNode] = []
    for node in children:
        if node.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for declarator in children_of_type(node, "variable_declarator"):
            has_fn_or_cls = any(
                c.type in ("arrow_function", "function_expression", "function", "class") for c in declarator.children
            )
            if has_fn_or_cls:
                continue
            if first_child_of_type(declarator, "object_pattern", "array_pattern"):
                continue
            name_node = first_child_of_type(declarator, "identifier")
            if name_node is None:
                continue
            type_ann = _type_annotation_text(declarator)
            default = None
            after_eq = False
            for c in declarator.children:
                if c.type == "=":
                    after_eq = True
                    continue
                if after_eq:
                    default = text(c)
                    break
            props.append(
                PropertyNode(
                    node_type=NodeType.PROPERTY,
                    name=text(name_node),
                    span=span(node),
                    source=text(node),
                    type_annotation=type_ann,
                    default_value=default,
                )
            )
    return props


def _extract_nested_functions(func_node: FunctionNode, ts_node: Node) -> list[FunctionNode]:
    """Extract nested function declarations and arrow functions inside a function body."""
    body = first_child_of_type(ts_node, "statement_block")
    if body is None:
        return []
    nested: list[FunctionNode] = []
    owner_name = func_node.name
    for child in body.children:
        if child.type in ("function_declaration", "generator_function_declaration"):
            nested.append(_build_function(child, enclosing_func=owner_name))
        elif child.type in ("lexical_declaration", "variable_declaration"):
            for decl in children_of_type(child, "variable_declarator"):
                value = None
                for c in decl.children:
                    if c.type in ("arrow_function", "function_expression"):
                        value = c
                        break
                if value is None:
                    continue
                name_node = first_child_of_type(decl, "identifier")
                if name_node is None:
                    continue
                raw_name = text(name_node)
                params_node = first_child_of_type(value, "formal_parameters")
                params = _params_text(params_node) if params_node else ()
                ret = _type_annotation_text(value)
                is_async = has_child_type(value, "async")
                nested.append(
                    FunctionNode(
                        node_type=NodeType.FUNCTION,
                        name=f"{owner_name}.{raw_name}",
                        span=span(child),
                        source=text(child),
                        owner=owner_name,
                        func_type="nested",
                        parameters=params,
                        return_type=ret,
                        is_async=is_async,
                    )
                )
    return nested


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------


def _extract_ts_imports(children: list[Node]) -> list[ImportNode]:
    """Extract import and re-export statements from TS/JS source."""
    imports: list[ImportNode] = []
    for node in children:
        if node.type == "import_statement":
            source_node = first_child_of_type(node, "string")
            module = text(source_node).strip("'\"") if source_node else ""

            import_clause = first_child_of_type(node, "import_clause")
            if import_clause is None:
                imports.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name=f"import '{module}'",
                        span=span(node),
                        source=text(node),
                        module=module,
                        names=(),
                    )
                )
                continue

            names: list[str] = []
            is_wildcard = False
            for c in import_clause.children:
                if c.type == "identifier":
                    names.append(text(c))
                elif c.type == "named_imports":
                    for spec in c.children:
                        if spec.type == "import_specifier":
                            name_n = first_child_of_type(spec, "identifier")
                            if name_n:
                                names.append(text(name_n))
                elif c.type == "namespace_import":
                    is_wildcard = True
                    id_node = first_child_of_type(c, "identifier")
                    if id_node:
                        names.append(text(id_node))

            imports.append(
                ImportNode(
                    node_type=NodeType.IMPORT,
                    name=f"import from '{module}'",
                    span=span(node),
                    source=text(node),
                    module=module,
                    names=tuple(names),
                    is_wildcard=is_wildcard,
                )
            )

        elif node.type == "export_statement":
            source_node = first_child_of_type(node, "string")
            if source_node is None:
                continue
            module = text(source_node).strip("'\"")
            names: list[str] = []
            export_clause = first_child_of_type(node, "export_clause")
            if export_clause:
                for spec in export_clause.children:
                    if spec.type == "export_specifier":
                        name_n = first_child_of_type(spec, "identifier")
                        if name_n:
                            names.append(text(name_n))

            imports.append(
                ImportNode(
                    node_type=NodeType.IMPORT,
                    name=f"export from '{module}'",
                    span=span(node),
                    source=text(node),
                    module=module,
                    names=tuple(names),
                    is_reexport=True,
                )
            )

    return imports


# ---------------------------------------------------------------------------
# Call extraction
# ---------------------------------------------------------------------------


def _extract_ts_calls(children: list[Node]) -> list[CallNode]:
    """Extract function/method call sites from function bodies."""
    calls: list[CallNode] = []

    def _context_name(node: Node) -> str | None:
        curr = node.parent
        while curr:
            if curr.type in ("function_declaration", "method_definition", "generator_function_declaration"):
                n = first_child_of_type(curr, "identifier", "property_identifier")
                if n:
                    return text(n)
            elif curr.type in ("class_declaration", "abstract_class_declaration"):
                n = first_child_of_type(curr, "type_identifier", "identifier")
                if n:
                    return text(n)
            curr = curr.parent
        return None

    def _walk_calls(node: Node, depth: int = 0) -> None:
        if depth > MAX_AST_DEPTH:
            return
        if node.type in ("call_expression", "new_expression"):
            func = node.child_by_field_name("function") if node.type == "call_expression" else None
            if func is None:
                for c in node.children:
                    if c.type not in ("new", "arguments", "(", ")", ",", "type_arguments"):
                        func = c
                        break

            if func:
                callee: str = ""
                receiver: str | None = None
                if func.type == "member_expression":
                    obj = func.child_by_field_name("object")
                    prop = func.child_by_field_name("property")
                    if obj and prop:
                        callee = text(prop)
                        receiver = text(obj)
                elif func.type == "identifier":
                    callee = text(func)
                else:
                    callee = text(func)

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
        for child in node.children:
            _walk_calls(child, depth + 1)

    for node in children:
        if node.type in ("function_declaration", "generator_function_declaration"):
            body = first_child_of_type(node, "statement_block")
            if body:
                _walk_calls(body)
        elif node.type in ("class_declaration", "abstract_class_declaration"):
            body_node = first_child_of_type(node, "class_body")
            if body_node:
                for member in body_node.children:
                    if member.type in ("method_definition",):
                        mbody = first_child_of_type(member, "statement_block")
                        if mbody:
                            _walk_calls(mbody)
        elif node.type in ("lexical_declaration", "variable_declaration"):
            for decl in children_of_type(node, "variable_declarator"):
                for c in decl.children:
                    if c.type in ("arrow_function", "function_expression", "function"):
                        body = first_child_of_type(c, "statement_block")
                        if body:
                            _walk_calls(body)

    return calls


# ---------------------------------------------------------------------------
# Code block extraction
# ---------------------------------------------------------------------------

_DEFINITION_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "enum_declaration",
        "type_alias_declaration",
        "import_statement",
        "comment",
    }
)

_MAX_CODE_BLOCK_NAME = 60


def _is_skippable(node: Node) -> bool:
    if node.type in _DEFINITION_TYPES:
        return True
    if node.type == "export_statement":
        return True
    if node.type in ("lexical_declaration", "variable_declaration"):
        return True
    return False


def _extract_code_blocks(children: list[Node]) -> list[CodeBlockNode]:
    blocks: list[CodeBlockNode] = []
    group: list[Node] = []

    def _flush() -> None:
        if not group:
            return
        first = group[0]
        last = group[-1]
        first_line = text(first).split("\n", 1)[0]
        name = first_line[:_MAX_CODE_BLOCK_NAME] if len(first_line) > _MAX_CODE_BLOCK_NAME else first_line
        source_parts = [text(n) for n in group]
        blocks.append(
            CodeBlockNode(
                node_type=NodeType.CODE_BLOCK,
                name=name,
                span=SourceSpan(
                    line_start=first.start_point.row + 1,
                    line_end=last.end_point.row + 1,
                    col_start=first.start_point.column,
                    col_end=last.end_point.column,
                ),
                source="\n".join(source_parts),
            )
        )
        group.clear()

    for child in children:
        if _is_skippable(child):
            _flush()
            continue
        group.append(child)

    _flush()
    return blocks


# ---------------------------------------------------------------------------
# Core parse logic (reused by JavaScriptParser)
# ---------------------------------------------------------------------------


def parse_sync(parser: Parser, path: Path, source: bytes, language_name: str) -> FileNode:
    """Parse JS/TS/TSX source into a :class:`FileNode`.

    This is the shared entry point used by TypeScriptParser, TsxParser,
    and JavaScriptParser.  TS-only extractors (interfaces, enums, type
    aliases) harmlessly return empty lists when fed JS input.
    """
    tree = parser.parse(source)
    root = tree.root_node
    children = unwrap_exports(root)

    classes = _extract_classes(children)
    class_exprs = _extract_class_expressions(children)
    interfaces = _extract_interfaces(children)
    enums = _extract_enums(children)
    type_aliases = _extract_type_aliases(children)
    functions = _extract_functions(children)
    arrow_funcs = _extract_arrow_functions(children)
    properties = _extract_properties(children)
    code_blocks = _extract_code_blocks(children)
    imports = _extract_ts_imports(root.children)
    calls = _extract_ts_calls(children)

    all_classes = classes + class_exprs
    all_funcs = functions + arrow_funcs

    nested: list[FunctionNode] = []
    for fn in all_funcs:
        for child in children:
            if child.type in ("function_declaration", "generator_function_declaration"):
                name_node = first_child_of_type(child, "identifier")
                if name_node and text(name_node) == fn.name:
                    nested.extend(_extract_nested_functions(fn, child))

    all_children: list = []
    all_children.extend(all_classes)
    all_children.extend(interfaces)
    all_children.extend(enums)
    all_children.extend(type_aliases)
    all_children.extend(all_funcs)
    all_children.extend(nested)
    all_children.extend(properties)
    all_children.extend(code_blocks)
    all_children.extend(imports)
    all_children.extend(calls)

    return FileNode(
        node_type=NodeType.FILE,
        name=path.name,
        span=span(root),
        children=tuple(all_children),
        path=str(path),
        language=language_name,
    )


# ---------------------------------------------------------------------------
# Public parser classes
# ---------------------------------------------------------------------------


class TypeScriptParser(BaseLanguageParser):
    """Parse TypeScript source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(_TS_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(parse_sync, self._parser, path, source, "typescript")


class TsxParser(BaseLanguageParser):
    """Parse TSX source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(_TSX_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(parse_sync, self._parser, path, source, "tsx")
