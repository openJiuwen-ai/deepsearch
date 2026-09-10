"""Rust language parser using tree-sitter."""

import asyncio
from dataclasses import replace
from pathlib import Path

import tree_sitter_rust
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
from ...models.extensions import EnumNode, ModuleNode, StructNode, TypeAliasNode
from ...models.structural import FileNode
from .. import BaseLanguageParser
from .._common import first_child_of_type, span, text

_RUST_LANG = Language(tree_sitter_rust.language())

_BRANCH_TYPES = frozenset(
    {
        "if_expression",
        "match_expression",
        "while_expression",
        "for_expression",
        "loop_expression",
        "match_arm",
    }
)


def _type_text(node: Node | None) -> str:
    """Reconstruct a type annotation string from a Rust type AST node."""
    if node is None:
        return ""
    return text(node).strip()


def _simple_type_name(node: Node | None) -> str:
    """Short type name (last path segment) for owners / bases."""
    if node is None:
        return ""
    if node.type == "generic_type":
        inner = node.child_by_field_name("type")
        return _simple_type_name(inner)
    if node.type == "reference_type":
        return _simple_type_name(node.child_by_field_name("type"))
    if node.type in ("type_identifier", "primitive_type", "identifier"):
        return text(node)
    if node.type == "scoped_type_identifier":
        name = node.child_by_field_name("name")
        return text(name) if name else text(node).rsplit("::", 1)[-1]
    raw = text(node).strip()
    raw = raw.split("<", 1)[0].strip()
    return raw.rsplit("::", 1)[-1]


def _collect_preceding_attrs(node: Node) -> tuple[str, ...]:
    """Collect outer ``#[…]`` attributes immediately preceding *node*."""
    attrs: list[str] = []
    prev = node.prev_named_sibling
    while prev is not None and prev.type == "attribute_item":
        attr = first_child_of_type(prev, "attribute")
        if attr is not None:
            attrs.append(f"#{text(attr)}")
        prev = prev.prev_named_sibling
    attrs.reverse()
    return tuple(attrs)


def _is_async(node: Node) -> bool:
    mods = first_child_of_type(node, "function_modifiers")
    if mods is None:
        return False
    return any(c.type == "async" for c in mods.children)


def _rust_complexity(node: Node) -> int:
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
    """Walk up to nearest function_item / function_signature_item name."""
    current = node.parent
    depth = 0
    while current is not None and depth < MAX_AST_DEPTH:
        if current.type in ("function_item", "function_signature_item"):
            name_node = current.child_by_field_name("name")
            return text(name_node) if name_node else None
        current = current.parent
        depth += 1
    return None


class RustParser(BaseLanguageParser):
    """Parse Rust source into a :class:`FileNode` tree."""

    def __init__(self) -> None:
        self._parser = Parser(_RUST_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        return await asyncio.to_thread(self._parse_sync, path, source)

    def _parse_sync(self, path: Path, source: bytes) -> FileNode:
        tree = self._parser.parse(source)
        root = tree.root_node

        children: list[BaseNode] = []
        trait_impls: list[tuple[str, str]] = []  # (type_name, trait_name)
        type_index: dict[str, int] = {}  # name -> index in children

        # Leading attributes are consumed when attached to the following item.
        i = 0
        named = [c for c in root.children if c.is_named]
        while i < len(named):
            child = named[i]
            if child.type == "attribute_item":
                i += 1
                continue
            if child.type == "inner_attribute_item":
                i += 1
                continue

            built = self._dispatch_item(child, trait_impls)
            if built is None:
                i += 1
                continue
            if isinstance(built, list):
                for node in built:
                    if isinstance(node, (StructNode, EnumNode, InterfaceNode)):
                        type_index[node.name] = len(children)
                    children.append(node)
            else:
                if isinstance(built, (StructNode, EnumNode, InterfaceNode)):
                    type_index[built.name] = len(children)
                children.append(built)
            i += 1

        # Merge trait impls into type bases
        for type_name, trait_name in trait_impls:
            idx = type_index.get(type_name)
            if idx is None:
                continue
            node = children[idx]
            if isinstance(node, (StructNode, EnumNode)):
                if trait_name not in node.bases:
                    children[idx] = replace(node, bases=(*node.bases, trait_name))

        children.extend(self._extract_calls(root))

        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=span(root),
            path=str(path),
            language="rust",
            source=source.decode("utf-8", errors="replace"),
            children=tuple(children),
        )

    def _dispatch_item(
        self,
        node: Node,
        trait_impls: list[tuple[str, str]],
    ) -> BaseNode | list[BaseNode] | None:
        t = node.type
        if t == "use_declaration":
            return self._build_use(node)
        if t == "function_item":
            return self._build_function(node, owner=None)
        if t == "struct_item":
            return self._build_struct(node)
        if t == "enum_item":
            return self._build_enum(node)
        if t == "trait_item":
            return self._build_trait(node)
        if t == "impl_item":
            return self._build_impl(node, trait_impls)
        if t == "mod_item":
            return self._build_mod(node, trait_impls)
        if t == "type_item":
            return self._build_type_alias(node)
        if t in ("const_item", "static_item"):
            return self._build_const_or_static(node)
        return None

    # -- use ------------------------------------------------------------------

    def _build_use(self, node: Node) -> ImportNode | list[ImportNode] | None:
        arg = node.child_by_field_name("argument")
        if arg is None:
            # Some grammars put the path as first named child
            for c in node.children:
                if c.is_named and c.type != "visibility_modifier":
                    arg = c
                    break
        if arg is None:
            return None

        if arg.type == "use_wildcard":
            path_text = text(arg).rstrip("*").rstrip(":").rstrip()
            module = path_text.rstrip(":")
            return ImportNode(
                node_type=NodeType.IMPORT,
                name="*",
                span=span(node),
                source=text(node),
                module=module or path_text,
                names=(),
                is_wildcard=True,
            )

        if arg.type == "use_as_clause":
            path_node = arg.child_by_field_name("path")
            alias_node = arg.child_by_field_name("alias")
            path_txt = text(path_node) if path_node else text(arg)
            alias = text(alias_node) if alias_node else path_txt.rsplit("::", 1)[-1]
            module, name = self._split_path(path_txt)
            return ImportNode(
                node_type=NodeType.IMPORT,
                name=alias,
                span=span(node),
                source=text(node),
                module=module,
                names=(name,),
                alias=alias if alias != name else None,
            )

        if arg.type == "scoped_use_list":
            return self._build_use_list(node, arg)

        if arg.type in ("scoped_identifier", "identifier", "crate", "super", "self"):
            path_txt = text(arg)
            module, name = self._split_path(path_txt)
            return ImportNode(
                node_type=NodeType.IMPORT,
                name=name,
                span=span(node),
                source=text(node),
                module=module,
                names=(name,),
            )

        # Fallback: whole text
        path_txt = text(arg)
        module, name = self._split_path(path_txt)
        return ImportNode(
            node_type=NodeType.IMPORT,
            name=name,
            span=span(node),
            source=text(node),
            module=module,
            names=(name,),
        )

    def _build_use_list(self, use_node: Node, scoped: Node) -> list[ImportNode]:
        path_node = scoped.child_by_field_name("path")
        list_node = scoped.child_by_field_name("list")
        base = text(path_node) if path_node else ""
        imports: list[ImportNode] = []
        if list_node is None:
            return imports
        for item in list_node.children:
            if not item.is_named:
                continue
            if item.type == "use_as_clause":
                p = item.child_by_field_name("path")
                a = item.child_by_field_name("alias")
                name = text(p) if p else ""
                alias = text(a) if a else name
                imports.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name=alias,
                        span=span(use_node),
                        source=text(use_node),
                        module=base,
                        names=(name,),
                        alias=alias if alias != name else None,
                    )
                )
            elif item.type == "self":
                short = base.rsplit("::", 1)[-1] if base else "self"
                imports.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name=short,
                        span=span(use_node),
                        source=text(use_node),
                        module=base,
                        names=(short,),
                    )
                )
            elif item.type in ("identifier", "type_identifier", "scoped_identifier"):
                name = text(item)
                imports.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name=name.rsplit("::", 1)[-1],
                        span=span(use_node),
                        source=text(use_node),
                        module=base,
                        names=(name,),
                    )
                )
            elif item.type == "use_wildcard":
                imports.append(
                    ImportNode(
                        node_type=NodeType.IMPORT,
                        name="*",
                        span=span(use_node),
                        source=text(use_node),
                        module=base,
                        names=(),
                        is_wildcard=True,
                    )
                )
        return imports

    @staticmethod
    def _split_path(path_txt: str) -> tuple[str, str]:
        parts = [p for p in path_txt.split("::") if p]
        if not parts:
            return "", path_txt
        if len(parts) == 1:
            return "", parts[0]
        return "::".join(parts[:-1]), parts[-1]

    # -- functions ------------------------------------------------------------

    def _build_function(
        self,
        node: Node,
        *,
        owner: str | None,
        signature_only: bool = False,
    ) -> FunctionNode | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        raw_name = text(name_node)
        qualified = f"{owner}.{raw_name}" if owner else raw_name

        params = self._build_parameters(node.child_by_field_name("parameters"))
        ret = node.child_by_field_name("return_type")
        return_type = _type_text(ret) if ret else None
        body = node.child_by_field_name("body")
        locals_: list[LocalVarNode] = []
        if body is not None and not signature_only:
            locals_ = self._extract_locals(body)

        return FunctionNode(
            node_type=NodeType.FUNCTION,
            name=qualified,
            span=span(node),
            source=text(node),
            children=tuple(locals_),
            owner=owner,
            func_type="method" if owner else "function",
            parameters=params,
            return_type=return_type,
            decorators=_collect_preceding_attrs(node),
            is_async=_is_async(node),
            cyclomatic_complexity=_rust_complexity(body) if body else 1,
        )

    def _build_parameters(self, params_node: Node | None) -> tuple[Parameter, ...]:
        if params_node is None:
            return ()
        result: list[Parameter] = []
        for child in params_node.children:
            if child.type == "self_parameter":
                result.append(Parameter(name="self", type_annotation=_type_text(child) or "Self"))
            elif child.type == "parameter":
                pat = child.child_by_field_name("pattern")
                typ = child.child_by_field_name("type")
                name = text(pat) if pat else text(child)
                # Strip mut from pattern text for the name
                if name.startswith("mut "):
                    name = name[4:].strip()
                result.append(
                    Parameter(
                        name=name,
                        type_annotation=_type_text(typ) if typ else None,
                    )
                )
        return tuple(result)

    # -- struct / enum / trait -----------------------------------------------

    def _build_struct(self, node: Node) -> StructNode | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            name_node = first_child_of_type(node, "type_identifier")
        if name_node is None:
            return None
        name = text(name_node)
        fields: list[PropertyNode] = []
        body = node.child_by_field_name("body")
        if body is not None and body.type == "field_declaration_list":
            for field in body.children:
                if field.type != "field_declaration":
                    continue
                fname = field.child_by_field_name("name")
                ftype = field.child_by_field_name("type")
                if fname is None:
                    continue
                fields.append(
                    PropertyNode(
                        node_type=NodeType.PROPERTY,
                        name=text(fname),
                        span=span(field),
                        source=text(field),
                        owner=name,
                        type_annotation=_type_text(ftype) if ftype else None,
                    )
                )
        return StructNode(
            node_type=NodeType.STRUCT,
            name=name,
            span=span(node),
            source=text(node),
            fields=tuple(fields),
            children=tuple(fields),
            bases=(),
        )

    def _build_enum(self, node: Node) -> EnumNode | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            name_node = first_child_of_type(node, "type_identifier")
        if name_node is None:
            return None
        name = text(name_node)
        members: list[str] = []
        body = node.child_by_field_name("body")
        if body is not None:
            for variant in body.children:
                if variant.type != "enum_variant":
                    continue
                vname = variant.child_by_field_name("name")
                if vname is not None:
                    members.append(text(vname))
        return EnumNode(
            node_type=NodeType.ENUM,
            name=name,
            span=span(node),
            source=text(node),
            members=tuple(members),
            bases=(),
        )

    def _build_trait(self, node: Node) -> InterfaceNode | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            name_node = first_child_of_type(node, "type_identifier")
        if name_node is None:
            return None
        name = text(name_node)
        bases: list[str] = []
        bounds = node.child_by_field_name("bounds")
        if bounds is not None:
            for c in bounds.children:
                if c.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                    bases.append(_simple_type_name(c))

        methods: list[FunctionNode] = []
        body = node.child_by_field_name("body")
        if body is not None:
            for item in body.children:
                if item.type == "function_signature_item":
                    fn = self._build_function(item, owner=name, signature_only=True)
                    if fn is not None:
                        methods.append(fn)
                elif item.type == "function_item":
                    fn = self._build_function(item, owner=name)
                    if fn is not None:
                        methods.append(fn)

        return InterfaceNode(
            node_type=NodeType.INTERFACE,
            name=name,
            span=span(node),
            source=text(node),
            bases=tuple(bases),
            children=tuple(methods),
        )

    # -- impl -----------------------------------------------------------------

    def _build_impl(
        self,
        node: Node,
        trait_impls: list[tuple[str, str]],
    ) -> list[BaseNode]:
        trait_node = node.child_by_field_name("trait")
        type_node = node.child_by_field_name("type")
        owner = _simple_type_name(type_node)
        if not owner:
            return []

        if trait_node is not None:
            trait_name = _simple_type_name(trait_node)
            if trait_name:
                trait_impls.append((owner, trait_name))

        results: list[BaseNode] = []
        body = node.child_by_field_name("body")
        if body is None:
            return results
        for item in body.children:
            if item.type == "function_item":
                fn = self._build_function(item, owner=owner)
                if fn is not None:
                    results.append(fn)
            elif item.type == "type_item":
                alias = self._build_type_alias(item)
                if alias is not None:
                    results.append(alias)
            elif item.type in ("const_item", "static_item"):
                prop = self._build_const_or_static(item, owner=owner)
                if prop is not None:
                    results.append(prop)
        return results

    # -- mod / type / const ---------------------------------------------------

    def _build_mod(
        self,
        node: Node,
        trait_impls: list[tuple[str, str]],
    ) -> ModuleNode | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            name_node = first_child_of_type(node, "identifier")
        if name_node is None:
            return None
        name = text(name_node)
        nested: list[BaseNode] = []
        body = node.child_by_field_name("body")
        if body is not None:
            for item in body.children:
                if not item.is_named or item.type in ("attribute_item", "inner_attribute_item"):
                    continue
                built = self._dispatch_item(item, trait_impls)
                if built is None:
                    continue
                if isinstance(built, list):
                    nested.extend(built)
                else:
                    nested.append(built)
        return ModuleNode(
            node_type=NodeType.MODULE,
            name=name,
            span=span(node),
            source=text(node),
            children=tuple(nested),
        )

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

    def _build_const_or_static(self, node: Node, owner: str | None = None) -> PropertyNode | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            name_node = first_child_of_type(node, "identifier")
        if name_node is None:
            return None
        typ = node.child_by_field_name("type")
        val = node.child_by_field_name("value")
        return PropertyNode(
            node_type=NodeType.PROPERTY,
            name=text(name_node),
            span=span(node),
            source=text(node),
            owner=owner,
            type_annotation=_type_text(typ) if typ else None,
            default_value=text(val) if val else None,
        )

    # -- locals ---------------------------------------------------------------

    def _extract_locals(self, body: Node) -> list[LocalVarNode]:
        locals_: list[LocalVarNode] = []

        def _walk(n: Node, depth: int) -> None:
            if depth > MAX_AST_DEPTH:
                return
            if n.type == "let_declaration":
                typ = n.child_by_field_name("type")
                if typ is not None:
                    pat = n.child_by_field_name("pattern")
                    raw_name = text(pat) if pat else ""
                    if raw_name.startswith("mut "):
                        raw_name = raw_name[4:].strip()
                    # Simple identifier patterns only
                    if raw_name and " " not in raw_name and "(" not in raw_name:
                        line = n.start_point.row + 1
                        scoped = f"{raw_name}@L{line}@D{depth}"
                        val = n.child_by_field_name("value")
                        locals_.append(
                            LocalVarNode(
                                node_type=NodeType.LOCAL_VAR,
                                name=scoped,
                                span=span(n),
                                source=text(n),
                                type_annotation=_type_text(typ),
                                default_value=text(val) if val else None,
                            )
                        )
            for child in n.children:
                next_depth = depth + 1 if n.type == "block" else depth
                _walk(child, next_depth)

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

        if func.type == "field_expression":
            field = func.child_by_field_name("field")
            value = func.child_by_field_name("value")
            callee = text(field) if field else ""
            receiver = text(value) if value else None
        elif func.type == "scoped_identifier":
            path_txt = text(func)
            parts = path_txt.split("::")
            if len(parts) >= 2:
                receiver = "::".join(parts[:-1])
                callee = parts[-1]
            else:
                callee = path_txt
        elif func.type == "generic_function":
            inner = func.child_by_field_name("function")
            if inner is not None and inner.type == "scoped_identifier":
                path_txt = text(inner)
                parts = path_txt.split("::")
                if len(parts) >= 2:
                    receiver = "::".join(parts[:-1])
                    callee = parts[-1]
                else:
                    callee = path_txt
            elif inner is not None:
                callee = text(inner)
            else:
                callee = text(func)
        elif func.type in ("identifier", "type_identifier"):
            callee = text(func)
        else:
            callee = text(func)

        if not callee:
            return None

        args_node = node.child_by_field_name("arguments")
        arguments: list[str] = []
        if args_node is not None:
            for child in args_node.children:
                if child.is_named:
                    arguments.append(text(child))

        context = _enclosing_fn_context(node)
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
