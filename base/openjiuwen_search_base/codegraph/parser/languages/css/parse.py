"""CSS language parser using tree-sitter."""

import asyncio
import logging
from pathlib import Path

import tree_sitter_css
from tree_sitter import Language, Node, Parser

from ...constants import NodeType
from ...models.core import BaseNode, ClassNode, CodeBlockNode, PropertyNode
from ...models.extensions.module import ModuleNode
from ...models.structural import FileNode
from .. import BaseLanguageParser
from .._common import first_child_of_type, span, text

logger = logging.getLogger(__name__)

_CSS_LANG = Language(tree_sitter_css.language())

# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _extract_declarations(block: Node, owner: str | None = None) -> list[PropertyNode]:
    """Extract CSS declarations from a rule block."""
    props: list[PropertyNode] = []
    for child in block.children:
        if child.type != "declaration":
            continue
        prop_name_node = first_child_of_type(child, "property_name")
        if prop_name_node is None:
            continue
        name = text(prop_name_node)
        value_parts: list[str] = []
        after_colon = False
        for c in child.children:
            if c.type == ":":
                after_colon = True
                continue
            if after_colon and c.type != ";":
                value_parts.append(text(c))
        value = " ".join(value_parts).strip() or None
        props.append(
            PropertyNode(
                node_type=NodeType.PROPERTY,
                name=name,
                span=span(child),
                source=text(child),
                owner=owner,
                default_value=value,
            )
        )
    return props


def _extract_rule_set(node: Node) -> ClassNode | None:
    """Convert a CSS rule_set into a ClassNode."""
    selectors = first_child_of_type(node, "selectors")
    if selectors is None:
        return None
    selector_text = text(selectors)
    block = first_child_of_type(node, "block")
    declarations = _extract_declarations(block, owner=selector_text) if block else []

    return ClassNode(
        node_type=NodeType.CLASS,
        name=selector_text,
        span=span(node),
        source=text(node),
        children=tuple(declarations),
    )


def _extract_at_rule(node: Node) -> ModuleNode:
    """Convert a @media or @supports statement into a ModuleNode with nested rules."""
    parts: list[str] = []
    for c in node.children:
        if c.type == "block":
            break
        parts.append(text(c))
    name = " ".join(parts).strip()

    block = first_child_of_type(node, "block")
    children: list[BaseNode] = []
    if block:
        for child in block.children:
            if child.type == "rule_set":
                rule = _extract_rule_set(child)
                if rule:
                    children.append(rule)

    return ModuleNode(
        node_type=NodeType.MODULE,
        name=name,
        span=span(node),
        source=text(node),
        children=tuple(children),
    )


# ---------------------------------------------------------------------------
# Public parser class
# ---------------------------------------------------------------------------


class CssParser(BaseLanguageParser):
    """Parse CSS files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(_CSS_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(self._parse_sync, path, source)

    def _parse_sync(self, path: Path, source: bytes) -> FileNode:
        tree = self._parser.parse(source)
        root = tree.root_node

        all_children: list[BaseNode] = []
        for child in root.children:
            if child.type == "rule_set":
                rule = _extract_rule_set(child)
                if rule:
                    all_children.append(rule)
            elif child.type in ("media_statement", "supports_statement"):
                all_children.append(_extract_at_rule(child))
            elif child.type == "keyframes_statement":
                kf_name = first_child_of_type(child, "keyframes_name")
                all_children.append(
                    CodeBlockNode(
                        node_type=NodeType.CODE_BLOCK,
                        name=f"@keyframes {text(kf_name)}" if kf_name else "@keyframes",
                        span=span(child),
                        source=text(child),
                    )
                )

        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=span(root),
            children=tuple(all_children),
            path=str(path),
            language="css",
        )
