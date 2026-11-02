"""reStructuredText parser using tree-sitter-rst.

Maps RST sections to :class:`ModuleNode` (same as the Markdown parser),
directives to :class:`PropertyNode`, and ``.. include::`` / ``.. toctree::``
to :class:`ImportNode`.

RST heading levels are determined by the *order of first appearance* of the
adornment character — the first character encountered is level 1, the next
new character is level 2, etc.  Tree-sitter-rst does **not** nest sections
hierarchically, so this parser rebuilds the tree using a stack (the same
strategy as the Markdown parser).
"""

import asyncio
import logging
from pathlib import Path

import tree_sitter_rst
from tree_sitter import Language, Node, Parser

from ...constants import NodeType
from ...custom_types import SourceSpan
from ...models.core import BaseNode, ImportNode, PropertyNode
from ...models.extensions import ModuleNode
from ...models.structural import FileNode
from .. import BaseLanguageParser
from .._common import first_child_of_type, span, text

logger = logging.getLogger(__name__)

_RST_LANG = Language(tree_sitter_rst.language())

_INCLUDE_DIRECTIVES = frozenset({"include", "literalinclude", "toctree"})


class RstParser(BaseLanguageParser):
    """Parse reStructuredText / Sphinx files into a heading-based tree."""

    async def parse(self, path: Path, source: bytes) -> FileNode:
        return await asyncio.to_thread(self._parse_sync, path, source)

    def _parse_sync(self, path: Path, source: bytes) -> FileNode:
        parser = Parser(_RST_LANG)
        tree = parser.parse(source)
        root = tree.root_node

        # First pass: gather flat list of (level, node_or_parsed) items.
        adornment_order: dict[str, int] = {}
        flat: list[tuple[int, ModuleNode | BaseNode]] = []

        for child in root.children:
            if child.type == "section":
                level = self._section_level(child, source, adornment_order)
                section_node, section_children = self._section_flat(child, source)
                flat.append((level, section_node))
                for sc in section_children:
                    flat.append((0, sc))
            elif child.type == "directive":
                d = self._directive(child, source)
                if d is not None:
                    flat.append((0, d))
            elif child.type == "ERROR":
                logger.debug("skipping ERROR node at line %d", child.start_point.row + 1)
            elif child.type in _BODY_TYPES:
                body_text = _node_text(child, source).strip()
                if body_text:
                    flat.append((0, _body_placeholder(child, body_text)))

        # Second pass: build tree from flat list using a stack.
        root_children: list[BaseNode] = []
        # Each stack entry: (level, heading_node, children_accumulated)
        stack: list[tuple[int, ModuleNode, list[BaseNode]]] = []

        def _flush(up_to_level: int) -> None:
            while stack and stack[-1][0] >= up_to_level:
                lvl, heading, kids = stack.pop()
                finished = _attach_children(heading, kids)
                if stack:
                    stack[-1][2].append(finished)
                else:
                    root_children.append(finished)

        for level, node in flat:
            if level > 0 and isinstance(node, ModuleNode):
                _flush(level)
                stack.append((level, node, []))
            elif stack:
                stack[-1][2].append(node)
            else:
                root_children.append(node)

        _flush(0)

        final = [c for c in root_children if not isinstance(c, _BodyText)]
        total_lines = source.count(b"\n") + 1
        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=SourceSpan(1, total_lines, 0, 0),
            children=tuple(final),
            path=str(path),
            language="rst",
        )

    def _section_level(self, node: Node, source: bytes, order: dict[str, int]) -> int:
        """Determine the heading level from the adornment character."""
        adornment = first_child_of_type(node, "adornment")
        if adornment is None:
            return 1
        char = text(adornment).strip()[:1]
        if not char:
            return 1
        if char not in order:
            order[char] = len(order) + 1
        return order[char]

    def _section_flat(self, node: Node, source: bytes) -> tuple[ModuleNode, list[BaseNode]]:
        """Parse a section node into a heading :class:`ModuleNode` and
        a flat list of non-section children (directives collected as body)."""
        title_node = first_child_of_type(node, "title")
        title = text(title_node).strip() if title_node else ""

        body_parts: list[str] = []
        inline_children: list[BaseNode] = []

        for child in node.children:
            if child.type in ("title", "adornment"):
                continue
            if child.type == "directive":
                d = self._directive(child, source)
                if d is not None:
                    inline_children.append(d)
            elif child.type in _BODY_TYPES:
                body_parts.append(_node_text(child, source))

        body = "\n".join(body_parts).strip() or None

        heading = ModuleNode(
            node_type=NodeType.MODULE,
            name=title,
            span=span(node),
            source=body,
        )
        return heading, inline_children

    def _directive(self, node: Node, source: bytes) -> BaseNode | None:
        """Convert a ``directive`` into a :class:`PropertyNode` or :class:`ImportNode`."""
        type_node = first_child_of_type(node, "type")
        directive_type = text(type_node).strip() if type_node else ""

        if not directive_type:
            return None

        body_node = first_child_of_type(node, "body")

        if directive_type in _INCLUDE_DIRECTIVES:
            return self._include_directive(node, directive_type, body_node, source)

        docstring = _extract_content(body_node, source) if body_node else None

        return PropertyNode(
            node_type=NodeType.PROPERTY,
            name=f".. {directive_type}::",
            span=span(node),
            docstring=docstring,
            source=_node_text(node, source),
        )

    def _include_directive(
        self, node: Node, directive_type: str, body_node: Node | None, source: bytes
    ) -> ImportNode | PropertyNode:
        """Handle include/toctree directives as imports."""
        if body_node is None:
            return PropertyNode(
                node_type=NodeType.PROPERTY,
                name=f".. {directive_type}::",
                span=span(node),
                source=_node_text(node, source),
            )

        if directive_type == "toctree":
            return self._toctree(node, body_node, source)

        args_node = first_child_of_type(body_node, "arguments")
        path_str = text(args_node).strip() if args_node else ""
        return ImportNode(
            node_type=NodeType.IMPORT,
            name=f"include {path_str}",
            span=span(node),
            module=path_str,
            source=_node_text(node, source),
        )

    def _toctree(self, node: Node, body_node: Node, source: bytes) -> ImportNode:
        """Parse a ``toctree`` directive into an :class:`ImportNode` listing referenced docs."""
        content_node = first_child_of_type(body_node, "content")
        names: list[str] = []
        if content_node:
            for line in text(content_node).splitlines():
                entry = line.strip()
                if not entry or entry.startswith(":"):
                    continue
                if "<" in entry and ">" in entry:
                    entry = entry.split("<", 1)[1].rstrip(">").strip()
                names.append(entry)

        return ImportNode(
            node_type=NodeType.IMPORT,
            name="toctree",
            span=span(node),
            module="",
            names=tuple(names),
            source=_node_text(node, source),
        )


_BODY_TYPES = frozenset(
    {
        "paragraph",
        "block_quote",
        "bullet_list",
        "enumerated_list",
        "definition_list",
        "field_list",
        "line_block",
        "literal_block",
        "table",
        "grid_table",
        "simple_table",
        "transition",
        "comment",
        "substitution_definition",
        "target",
        "footnote",
        "citation",
    }
)


class _BodyText(BaseNode):
    """Ephemeral node holding body text to merge into a parent section."""


def _body_placeholder(node: Node, body: str) -> _BodyText:
    return _BodyText(node_type=NodeType.PROPERTY, name="", span=span(node), source=body)


def _attach_children(heading: ModuleNode, kids: list[BaseNode]) -> ModuleNode:
    """Return a copy of *heading* with *kids* as children.

    :class:`_BodyText` entries are merged into *heading*.source instead of
    being kept as children.
    """
    real_kids: list[BaseNode] = []
    extra_body: list[str] = []
    for k in kids:
        if isinstance(k, _BodyText):
            if k.source:
                extra_body.append(k.source)
        else:
            real_kids.append(k)

    source = heading.source
    if extra_body:
        parts = [source] if source else []
        parts.extend(extra_body)
        source = "\n".join(parts)

    if not real_kids and source == heading.source:
        return heading

    extended_span = _extend_span(heading.span, real_kids + [k for k in kids if isinstance(k, _BodyText)])
    return ModuleNode(
        node_type=heading.node_type,
        name=heading.name,
        span=extended_span,
        docstring=heading.docstring,
        source=source,
        children=tuple(real_kids),
    )


def _extend_span(parent: SourceSpan, children: list[BaseNode]) -> SourceSpan:
    """Extend *parent* span to cover all *children*."""
    if not children:
        return parent
    max_end = parent.line_end
    max_col = parent.col_end
    for c in children:
        if c.span.line_end > max_end:
            max_end = c.span.line_end
            max_col = c.span.col_end
        elif c.span.line_end == max_end and c.span.col_end > max_col:
            max_col = c.span.col_end
    if max_end == parent.line_end and max_col == parent.col_end:
        return parent
    return SourceSpan(parent.line_start, max_end, parent.col_start, max_col)


def _node_text(node: Node, source: bytes) -> str:
    """Extract text of a node from source bytes."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _extract_content(body_node: Node, source: bytes) -> str | None:
    """Extract the content portion of a directive body."""
    content = first_child_of_type(body_node, "content")
    if content:
        return _node_text(content, source).strip() or None
    args = first_child_of_type(body_node, "arguments")
    if args:
        return text(args).strip() or None
    return None
