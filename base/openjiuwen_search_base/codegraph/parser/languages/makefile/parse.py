"""Makefile language parser using tree-sitter."""

import asyncio
import logging
from pathlib import Path

import tree_sitter_make
from tree_sitter import Language, Node, Parser

from ...constants import NodeType
from ...custom_types import Parameter
from ...models.core import BaseNode, CodeBlockNode, FunctionNode, ImportNode, PropertyNode
from ...models.structural import FileNode
from .. import BaseLanguageParser
from .._common import first_child_of_type, span, text

logger = logging.getLogger(__name__)

_MAKE_LANG = Language(tree_sitter_make.language())

_DOT_DIRECTIVES = frozenset(
    {
        ".PHONY",
        ".SUFFIXES",
        ".DEFAULT",
        ".PRECIOUS",
        ".INTERMEDIATE",
        ".SECONDARY",
        ".SECONDEXPANSION",
        ".DELETE_ON_ERROR",
        ".IGNORE",
        ".LOW_RESOLUTION_TIME",
        ".SILENT",
        ".EXPORT_ALL_VARIABLES",
        ".NOTPARALLEL",
        ".ONESHELL",
        ".POSIX",
    }
)


def _extract_rule(node: Node, pending_comment: str | None) -> BaseNode | None:
    """Convert a make ``rule`` node into a FunctionNode or CodeBlockNode."""
    targets_node = first_child_of_type(node, "targets")
    if targets_node is None:
        return None

    target_names = [text(w) for w in targets_node.children if w.type == "word"]
    if not target_names:
        return None
    target_name = ", ".join(target_names)

    if any(t.startswith(".") and t in _DOT_DIRECTIVES for t in target_names):
        return CodeBlockNode(
            node_type=NodeType.CODE_BLOCK,
            name=target_name,
            span=span(node),
            source=text(node),
            docstring=pending_comment,
        )

    prereqs_node = first_child_of_type(node, "prerequisites")
    params: list[Parameter] = []
    if prereqs_node is not None:
        for w in prereqs_node.children:
            if w.type == "word":
                params.append(Parameter(name=text(w)))

    recipe_node = first_child_of_type(node, "recipe")
    recipe_lines: list[str] = []
    if recipe_node is not None:
        for child in recipe_node.children:
            if child.type == "recipe_line":
                recipe_lines.append(text(child))

    return FunctionNode(
        node_type=NodeType.FUNCTION,
        name=target_name,
        span=span(node),
        source="\n".join(recipe_lines) if recipe_lines else None,
        docstring=pending_comment,
        func_type="function",
        decorators=("target",),
        parameters=tuple(params),
    )


def _extract_variable(node: Node, pending_comment: str | None) -> PropertyNode:
    """Convert a ``variable_assignment`` into a PropertyNode."""
    name_node = first_child_of_type(node, "word")
    var_name = text(name_node) if name_node else "?"

    operator = ""
    for child in node.children:
        if child.type in ("=", ":=", "?=", "+="):
            operator = child.type
            break

    value_node = first_child_of_type(node, "text")
    value = text(value_node).strip() if value_node else None

    return PropertyNode(
        node_type=NodeType.PROPERTY,
        name=var_name,
        span=span(node),
        source=text(node),
        docstring=pending_comment,
        type_annotation=operator or None,
        default_value=value,
    )


def _extract_define(node: Node, pending_comment: str | None) -> FunctionNode:
    """Convert a ``define_directive`` into a FunctionNode."""
    name_node = first_child_of_type(node, "word")
    define_name = text(name_node) if name_node else "?"

    raw_node = first_child_of_type(node, "raw_text")
    body = text(raw_node).strip() if raw_node else ""

    return FunctionNode(
        node_type=NodeType.FUNCTION,
        name=define_name,
        span=span(node),
        source=body or None,
        docstring=pending_comment,
        func_type="nested",
        decorators=("define",),
    )


def _extract_export(node: Node, pending_comment: str | None) -> PropertyNode | CodeBlockNode:
    """Convert an ``export_directive`` into a PropertyNode or CodeBlockNode."""
    inner_var = first_child_of_type(node, "variable_assignment")
    if inner_var is not None:
        prop = _extract_variable(inner_var, pending_comment)
        return PropertyNode(
            node_type=NodeType.PROPERTY,
            name=prop.name,
            span=span(node),
            source=text(node),
            docstring=pending_comment,
            type_annotation=prop.type_annotation,
            default_value=prop.default_value,
        )
    return CodeBlockNode(
        node_type=NodeType.CODE_BLOCK,
        name="export",
        span=span(node),
        source=text(node),
        docstring=pending_comment,
    )


def _extract_include(node: Node, pending_comment: str | None) -> ImportNode | None:
    """Convert an ``include_directive`` into an ImportNode."""
    list_node = first_child_of_type(node, "list")
    if list_node is None:
        return None
    paths = [text(w) for w in list_node.children if w.type == "word"]
    if not paths:
        paths = [text(list_node).strip()]
    if not paths or not paths[0]:
        return None
    return ImportNode(
        node_type=NodeType.IMPORT,
        name=paths[0],
        span=span(node),
        source=text(node),
        docstring=pending_comment,
        module=paths[0],
        names=tuple(paths),
    )


def _parse_sync(parser: Parser, path: Path, source: bytes) -> FileNode:
    """Synchronous parse of Makefile source."""
    tree = parser.parse(source)
    root = tree.root_node

    children: list[BaseNode] = []
    pending_comment: str | None = None

    for child in root.children:
        if child.type == "comment":
            raw = text(child)
            pending_comment = raw.lstrip("# ").strip() if raw.startswith("#") else raw
            continue

        if child.type == "ERROR":
            pending_comment = None
            continue

        node: BaseNode | None = None

        if child.type == "rule":
            node = _extract_rule(child, pending_comment)
        elif child.type == "variable_assignment":
            node = _extract_variable(child, pending_comment)
        elif child.type == "define_directive":
            node = _extract_define(child, pending_comment)
        elif child.type == "conditional":
            node = CodeBlockNode(
                node_type=NodeType.CODE_BLOCK,
                name="conditional",
                span=span(child),
                source=text(child),
                docstring=pending_comment,
            )
        elif child.type == "export_directive":
            node = _extract_export(child, pending_comment)
        elif child.type == "include_directive":
            node = _extract_include(child, pending_comment)

        if node is not None:
            children.append(node)

        pending_comment = None

    return FileNode(
        node_type=NodeType.FILE,
        name=path.name,
        span=span(root),
        children=tuple(children),
        path=str(path),
        language="makefile",
    )


class MakefileParser(BaseLanguageParser):
    """Parse Makefile source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(_MAKE_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(_parse_sync, self._parser, path, source)
