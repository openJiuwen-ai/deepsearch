"""HTML language parser using tree-sitter."""

import asyncio
import logging
from pathlib import Path

import tree_sitter_html
from tree_sitter import Language, Node, Parser

from ...constants import MAX_AST_DEPTH, NodeType
from ...models.core import BaseNode, CodeBlockNode
from ...models.extensions.module import ModuleNode
from ...models.structural import FileNode
from .. import BaseLanguageParser
from .._common import first_child_of_type, span, text

logger = logging.getLogger(__name__)

_HTML_LANG = Language(tree_sitter_html.language())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tag_name(element: Node) -> str:
    """Extract the tag name from an element's start_tag."""
    start_tag = first_child_of_type(element, "start_tag")
    if start_tag:
        tn = first_child_of_type(start_tag, "tag_name")
        if tn:
            return text(tn)
    return "<unknown>"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _walk_element(node: Node, depth: int = 0) -> BaseNode | None:
    """Recursively convert an HTML element to a ModuleNode tree."""
    if depth > MAX_AST_DEPTH:
        return None

    if node.type == "script_element":
        raw = first_child_of_type(node, "raw_text")
        source = text(raw).strip() if raw else ""
        return CodeBlockNode(
            node_type=NodeType.CODE_BLOCK,
            name="<script>",
            span=span(node),
            source=source or None,
        )

    if node.type == "style_element":
        raw = first_child_of_type(node, "raw_text")
        source = text(raw).strip() if raw else ""
        return CodeBlockNode(
            node_type=NodeType.CODE_BLOCK,
            name="<style>",
            span=span(node),
            source=source or None,
        )

    if node.type == "element":
        name = _tag_name(node)
        children: list[BaseNode] = []
        for child in node.children:
            if child.type in ("element", "script_element", "style_element"):
                child_node = _walk_element(child, depth + 1)
                if child_node:
                    children.append(child_node)
        return ModuleNode(
            node_type=NodeType.MODULE,
            name=name,
            span=span(node),
            source=text(node),
            children=tuple(children),
        )

    return None


# ---------------------------------------------------------------------------
# Public parser class
# ---------------------------------------------------------------------------


class HtmlParser(BaseLanguageParser):
    """Parse HTML files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(_HTML_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(self._parse_sync, path, source)

    def _parse_sync(self, path: Path, source: bytes) -> FileNode:
        tree = self._parser.parse(source)
        root = tree.root_node

        top_level: list[BaseNode] = []
        for child in root.children:
            if child.type in ("element", "script_element", "style_element"):
                node = _walk_element(child)
                if node:
                    top_level.append(node)

        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=span(root),
            children=tuple(top_level),
            path=str(path),
            language="html",
        )
