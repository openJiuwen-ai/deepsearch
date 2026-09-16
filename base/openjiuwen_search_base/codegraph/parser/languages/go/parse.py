"""Go language parser using tree-sitter."""

import asyncio
from pathlib import Path

import tree_sitter_go
from tree_sitter import Language, Node, Parser

from ...constants import MAX_AST_DEPTH, NodeType
from ...custom_types import Parameter
from ...models.core import (
    BaseNode,
    CallNode,
    FunctionNode,
    ImportNode,
    InterfaceNode,
    LocalVarNode,
    PropertyNode,
)
from ...models.extensions import ModuleNode, StructNode, TypeAliasNode
from ...models.structural import FileNode
from .. import BaseLanguageParser
from .._common import first_child_of_type, span, text

_GO_LANG = Language(tree_sitter_go.language())

_BRANCH_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "expression_switch_statement",
        "type_switch_statement",
        "select_statement",
        "communication_case",
        "expression_case",
        "default_case",
    }
)


def _type_text(node: Node | None) -> str:
    if node is None:
        return ""
    return text(node).strip()


def _simple_type_name(node: Node | None) -> str:
    """Short type name for owners / bases (peel pointer / generic)."""
    if node is None:
        return ""
    if node.type == "pointer_type":
        for c in node.named_children:
            return _simple_type_name(c)
        return ""
    if node.type == "generic_type":
        inner = node.child_by_field_name("type")
        return _simple_type_name(inner) if inner else text(node).split("[", 1)[0]
    if node.type == "qualified_type":
        name = node.child_by_field_name("name")
        return text(name) if name else text(node).rsplit(".", 1)[-1]
    if node.type in ("type_identifier", "identifier"):
        return text(node)
    raw = text(node).strip().lstrip("*")
    raw = raw.split("[", 1)[0]
    return raw.rsplit(".", 1)[-1]


def _unquote_path(path_node: Node | None) -> str:
    if path_node is None:
        return ""
    raw = text(path_node).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "`"):
        return raw[1:-1]
    content = first_child_of_type(path_node, "interpreted_string_literal_content", "raw_string_literal_content")
    if content is not None:
        return text(content)
    return raw.strip('"`')


def _go_complexity(node: Node | None) -> int:
    if node is None:
        return 1
    count = 1

    def _walk(n: Node, depth: int = 0) -> None:
        nonlocal count
        if depth > MAX_AST_DEPTH:
            return
        if n.type in _BRANCH_TYPES:
            count += 1
        for child in n.children:
            _walk(child, depth + 1)

    _walk(node)
    return count


def _enclosing_fn_context(node: Node) -> str | None:
    current = node.parent
    depth = 0
    while current is not None and depth < MAX_AST_DEPTH:
        if current.type == "function_declaration":
            name = current.child_by_field_name("name")
            return text(name) if name else None
        if current.type == "method_declaration":
            name = current.child_by_field_name("name")
            recv = current.child_by_field_name("receiver")
            owner = ""
            if recv is not None:
                for pd in recv.named_children:
                    if pd.type == "parameter_declaration":
                        typ = pd.child_by_field_name("type")
                        owner = _simple_type_name(typ)
                        break
            method = text(name) if name else ""
            return f"{owner}.{method}" if owner else method
        current = current.parent
        depth += 1
    return None


class GoParser(BaseLanguageParser):
    """Parse Go source into a :class:`FileNode` tree."""

    def __init__(self) -> None:
        self._parser = Parser(_GO_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        return await asyncio.to_thread(self._parse_sync, path, source)

    def _parse_sync(self, path: Path, source: bytes) -> FileNode:
        tree = self._parser.parse(source)
        root = tree.root_node
        children: list[BaseNode] = []

        for child in root.named_children:
            built = self._dispatch(child)
            if built is None:
                continue
            if isinstance(built, list):
                children.extend(built)
            else:
                children.append(built)

        children.extend(self._extract_calls(root))

        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=span(root),
            path=str(path),
            language="go",
            source=source.decode("utf-8", errors="replace"),
            children=tuple(children),
        )

    def _dispatch(self, node: Node) -> BaseNode | list[BaseNode] | None:
        t = node.type
        if t == "package_clause":
            return self._build_package(node)
        if t == "import_declaration":
            return self._build_imports(node)
        if t == "function_declaration":
            return self._build_function(node)
        if t == "method_declaration":
            return self._build_method(node)
        if t == "type_declaration":
            return self._build_type_declaration(node)
        if t == "const_declaration":
            return self._build_const_or_var(node, is_const=True)
        if t == "var_declaration":
            return self._build_const_or_var(node, is_const=False)
        return None

    # -- package / import -----------------------------------------------------

    def _build_package(self, node: Node) -> ModuleNode | None:
        ident = first_child_of_type(node, "package_identifier")
        if ident is None:
            return None
        return ModuleNode(
            node_type=NodeType.MODULE,
            name=text(ident),
            span=span(node),
            source=text(node),
        )

    def _build_imports(self, node: Node) -> list[ImportNode]:
        specs: list[Node] = []
        for child in node.named_children:
            if child.type == "import_spec":
                specs.append(child)
            elif child.type == "import_spec_list":
                specs.extend(c for c in child.named_children if c.type == "import_spec")

        results: list[ImportNode] = []
        for spec in specs:
            path_node = spec.child_by_field_name("path")
            name_node = spec.child_by_field_name("name")
            module = _unquote_path(path_node)
            if not module:
                continue
            default_name = module.rsplit("/", 1)[-1]

            if name_node is not None and name_node.type == "dot":
                results.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name="*",
                        span=span(spec),
                        source=text(spec),
                        module=module,
                        names=(),
                        is_wildcard=True,
                    )
                )
            elif name_node is not None and name_node.type == "blank_identifier":
                results.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name="_",
                        span=span(spec),
                        source=text(spec),
                        module=module,
                        names=(),
                    )
                )
            elif name_node is not None and name_node.type == "package_identifier":
                alias = text(name_node)
                results.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name=alias,
                        span=span(spec),
                        source=text(spec),
                        module=module,
                        names=(default_name,),
                        alias=alias if alias != default_name else None,
                    )
                )
            else:
                results.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name=default_name,
                        span=span(spec),
                        source=text(spec),
                        module=module,
                        names=(default_name,),
                    )
                )
        return results

    # -- functions / methods --------------------------------------------------

    def _build_function(self, node: Node) -> FunctionNode | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        name = text(name_node)
        params = self._build_parameters(node.child_by_field_name("parameters"))
        result = node.child_by_field_name("result")
        return_type = _type_text(result) if result else None
        body = node.child_by_field_name("body")
        locals_ = self._extract_locals(body) if body else []
        return FunctionNode(
            node_type=NodeType.FUNCTION,
            name=name,
            span=span(node),
            source=text(node),
            children=tuple(locals_),
            owner=None,
            func_type="function",
            parameters=params,
            return_type=return_type,
            cyclomatic_complexity=_go_complexity(body),
        )

    def _build_method(self, node: Node) -> FunctionNode | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        raw_name = text(name_node)
        owner = ""
        recv = node.child_by_field_name("receiver")
        if recv is not None:
            for pd in recv.named_children:
                if pd.type == "parameter_declaration":
                    typ = pd.child_by_field_name("type")
                    owner = _simple_type_name(typ)
                    break
        if not owner:
            return None
        qualified = f"{owner}.{raw_name}"
        params = self._build_parameters(node.child_by_field_name("parameters"))
        # Prepend receiver as first parameter for arity parity with other langs
        recv_params = self._build_parameters(recv) if recv else ()
        all_params = (*recv_params, *params)
        result = node.child_by_field_name("result")
        return_type = _type_text(result) if result else None
        body = node.child_by_field_name("body")
        locals_ = self._extract_locals(body) if body else []
        return FunctionNode(
            node_type=NodeType.FUNCTION,
            name=qualified,
            span=span(node),
            source=text(node),
            children=tuple(locals_),
            owner=owner,
            func_type="method",
            parameters=all_params,
            return_type=return_type,
            cyclomatic_complexity=_go_complexity(body),
        )

    def _build_parameters(self, params_node: Node | None) -> tuple[Parameter, ...]:
        if params_node is None:
            return ()
        result: list[Parameter] = []
        for child in params_node.named_children:
            if child.type != "parameter_declaration":
                continue
            typ = child.child_by_field_name("type")
            type_ann = _type_text(typ) if typ else None
            name_nodes: list[Node] = []
            for i in range(child.child_count):
                if child.field_name_for_child(i) == "name":
                    name_nodes.append(child.child(i))
            if not name_nodes:
                result.append(Parameter(name="_", type_annotation=type_ann))
            else:
                for nn in name_nodes:
                    result.append(Parameter(name=text(nn), type_annotation=type_ann))
        return tuple(result)

    # -- types ----------------------------------------------------------------

    def _build_type_declaration(self, node: Node) -> list[BaseNode]:
        results: list[BaseNode] = []
        for child in node.named_children:
            if child.type == "type_alias":
                alias = self._build_type_alias(child)
                if alias is not None:
                    results.append(alias)
            elif child.type == "type_spec":
                built = self._build_type_spec(child)
                if built is not None:
                    results.append(built)
        return results

    def _build_type_alias(self, node: Node) -> TypeAliasNode | None:
        name_node = node.child_by_field_name("name")
        type_node = node.child_by_field_name("type")
        if name_node is None:
            return None
        return TypeAliasNode(
            node_type=NodeType.TYPE_ALIAS,
            name=text(name_node),
            span=span(node),
            source=text(node),
            aliased_type=_type_text(type_node),
        )

    def _build_type_spec(self, node: Node) -> BaseNode | None:
        name_node = node.child_by_field_name("name")
        type_node = node.child_by_field_name("type")
        if name_node is None or type_node is None:
            return None
        name = text(name_node)
        if type_node.type == "struct_type":
            return self._build_struct(name, node, type_node)
        if type_node.type == "interface_type":
            return self._build_interface(name, node, type_node)
        # Defined type (type MyInt int) → TypeAliasNode
        return TypeAliasNode(
            node_type=NodeType.TYPE_ALIAS,
            name=name,
            span=span(node),
            source=text(node),
            aliased_type=_type_text(type_node),
        )

    def _build_struct(self, name: str, spec: Node, struct_type: Node) -> StructNode:
        fields: list[PropertyNode] = []
        bases: list[str] = []
        field_list = first_child_of_type(struct_type, "field_declaration_list")
        if field_list is not None:
            for field in field_list.named_children:
                if field.type != "field_declaration":
                    continue
                typ = field.child_by_field_name("type")
                type_ann = _type_text(typ) if typ else None
                name_nodes: list[Node] = []
                for i in range(field.child_count):
                    if field.field_name_for_child(i) == "name":
                        name_nodes.append(field.child(i))
                if not name_nodes:
                    # Embedded field
                    embed = _simple_type_name(typ)
                    if embed:
                        bases.append(embed)
                    continue
                for nn in name_nodes:
                    fields.append(
                        PropertyNode(
                            node_type=NodeType.PROPERTY,
                            name=text(nn),
                            span=span(field),
                            source=text(field),
                            owner=name,
                            type_annotation=type_ann,
                        )
                    )
        return StructNode(
            node_type=NodeType.STRUCT,
            name=name,
            span=span(spec),
            source=text(spec),
            fields=tuple(fields),
            children=tuple(fields),
            bases=tuple(bases),
        )

    def _build_interface(self, name: str, spec: Node, iface: Node) -> InterfaceNode:
        methods: list[FunctionNode] = []
        bases: list[str] = []
        for child in iface.named_children:
            if child.type == "method_elem":
                mname = child.child_by_field_name("name")
                params = self._build_parameters(child.child_by_field_name("parameters"))
                result = child.child_by_field_name("result")
                if mname is None:
                    continue
                raw = text(mname)
                methods.append(
                    FunctionNode(
                        node_type=NodeType.FUNCTION,
                        name=f"{name}.{raw}",
                        span=span(child),
                        source=text(child),
                        owner=name,
                        func_type="method",
                        parameters=params,
                        return_type=_type_text(result) if result else None,
                    )
                )
            elif child.type == "type_elem":
                for sub in child.named_children:
                    embed = _simple_type_name(sub)
                    if embed:
                        bases.append(embed)
        return InterfaceNode(
            node_type=NodeType.INTERFACE,
            name=name,
            span=span(spec),
            source=text(spec),
            bases=tuple(bases),
            children=tuple(methods),
        )

    # -- const / var ----------------------------------------------------------

    def _build_const_or_var(self, node: Node, *, is_const: bool) -> list[PropertyNode]:
        _ = is_const
        results: list[PropertyNode] = []
        specs: list[Node] = []
        for child in node.named_children:
            if child.type in ("const_spec", "var_spec"):
                specs.append(child)
            elif child.type == "var_spec_list":
                specs.extend(c for c in child.named_children if c.type == "var_spec")

        for spec in specs:
            typ = spec.child_by_field_name("type")
            val = spec.child_by_field_name("value")
            type_ann = _type_text(typ) if typ else None
            default = text(val) if val else None
            name_nodes: list[Node] = []
            for i in range(spec.child_count):
                if spec.field_name_for_child(i) == "name":
                    name_nodes.append(spec.child(i))
            for nn in name_nodes:
                results.append(
                    PropertyNode(
                        node_type=NodeType.PROPERTY,
                        name=text(nn),
                        span=span(spec),
                        source=text(spec),
                        type_annotation=type_ann,
                        default_value=default,
                    )
                )
        return results

    # -- locals ---------------------------------------------------------------

    def _extract_locals(self, body: Node | None) -> list[LocalVarNode]:
        if body is None:
            return []
        locals_: list[LocalVarNode] = []

        def _add_typed(name: str, type_ann: str, n: Node, depth: int) -> None:
            if not name or name == "_":
                return
            line = n.start_point.row + 1
            locals_.append(
                LocalVarNode(
                    node_type=NodeType.LOCAL_VAR,
                    name=f"{name}@L{line}@D{depth}",
                    span=span(n),
                    source=text(n),
                    type_annotation=type_ann,
                )
            )

        def _walk(n: Node, depth: int) -> None:
            if depth > MAX_AST_DEPTH:
                return
            if n.type == "var_declaration":
                for spec in n.named_children:
                    if spec.type != "var_spec":
                        continue
                    typ = spec.child_by_field_name("type")
                    if typ is None:
                        continue
                    type_ann = _type_text(typ)
                    for i in range(spec.child_count):
                        if spec.field_name_for_child(i) == "name":
                            _add_typed(text(spec.child(i)), type_ann, spec, depth)
            elif n.type == "short_var_declaration":
                # Only emit when RHS suggests typed composite / call — skip untyped for v1
                # Plan: typed locals; short decl often untyped. Skip unless we can infer.
                pass
            for child in n.children:
                next_d = depth + 1 if n.type in ("block", "statement_list") else depth
                _walk(child, next_d)

        _walk(body, 0)
        return locals_

    # -- calls ----------------------------------------------------------------

    def _extract_calls(self, root: Node) -> list[CallNode]:
        calls: list[CallNode] = []

        def _walk(n: Node, depth: int = 0) -> None:
            if depth > MAX_AST_DEPTH:
                return
            if n.type == "call_expression":
                call = self._build_call(n)
                if call is not None:
                    calls.append(call)
            for child in n.children:
                _walk(child, depth + 1)

        _walk(root)
        return calls

    def _build_call(self, node: Node) -> CallNode | None:
        func = node.child_by_field_name("function")
        if func is None:
            return None
        callee = ""
        receiver: str | None = None
        if func.type == "selector_expression":
            field = func.child_by_field_name("field")
            operand = func.child_by_field_name("operand")
            callee = text(field) if field else ""
            receiver = text(operand) if operand else None
        elif func.type == "identifier":
            callee = text(func)
        elif func.type == "generic_type" or func.type == "type_identifier":
            # Type conversion T(x) — treat as call named T
            callee = _simple_type_name(func) or text(func)
        else:
            callee = text(func)
        if not callee:
            return None

        args_node = node.child_by_field_name("arguments")
        arguments: list[str] = []
        if args_node is not None:
            for child in args_node.named_children:
                arguments.append(text(child))

        return CallNode(
            node_type=NodeType.CALL,
            name=callee,
            span=span(node),
            source=text(node),
            callee=callee,
            receiver=receiver,
            full_expression=text(node),
            context=_enclosing_fn_context(node),
            arguments=tuple(arguments),
        )
