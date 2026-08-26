"""Markdown parser using a simple rule-based heading tree."""

import asyncio
import re
from pathlib import Path

from ...constants import NodeType
from ...custom_types import SourceSpan
from ...models.extensions import ModuleNode
from ...models.structural import FileNode
from .. import BaseLanguageParser

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")


class MarkdownParser(BaseLanguageParser):
    """Parse Markdown files into a heading-based tree.

    Each ``# heading`` becomes a :class:`ModuleNode` whose *children*
    are the sub-headings beneath it.  The body text under a heading is
    stored in *source*.
    """

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(self._parse_sync, path, source)

    def _parse_sync(self, path: Path, source: bytes) -> FileNode:
        text = source.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)

        # Each entry: (level, title, line_start, body_lines)
        sections: list[tuple[int, str, int, list[str]]] = []

        for lineno_0, line in enumerate(lines):
            m = _HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()
                sections.append((level, title, lineno_0 + 1, []))
            elif sections:
                sections[-1][3].append(line)

        # Build a tree from the flat section list using a stack
        root_children: list[ModuleNode] = []
        stack: list[tuple[int, list[ModuleNode]]] = []  # (level, children_accumulator)

        def _flush(up_to_level: int) -> None:
            """Pop stack entries deeper than *up_to_level* and attach as children."""
            while stack and stack[-1][0] >= up_to_level:
                _, finished_children = stack.pop()
                node = _make_section(finished_children)
                if stack:
                    stack[-1][1].append(node)
                else:
                    root_children.append(node)

        for level, title, line_start, body_lines in sections:
            _flush(level)
            body = "".join(body_lines).strip()
            line_end = line_start + len(body_lines)
            placeholder = ModuleNode(
                node_type=NodeType.MODULE,
                name=title,
                span=SourceSpan(line_start, line_end, 0, 0),
                source=body or None,
            )
            stack.append((level, [placeholder]))

        # Flush remaining
        _flush(0)

        total_lines = len(lines)
        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=SourceSpan(1, total_lines, 0, 0),
            children=tuple(root_children),
            path=str(path),
            language="markdown",
        )


def _make_section(accumulated: list[ModuleNode]) -> ModuleNode:
    """Collapse accumulated nodes: first is the heading, rest are children."""
    if not accumulated:
        return ModuleNode(node_type=NodeType.MODULE, name="", span=SourceSpan(0, 0, 0, 0))
    head = accumulated[0]
    children = tuple(accumulated[1:])
    if children:
        max_end = max(c.span.line_end for c in children)
        extended_end = max(head.span.line_end, max_end)
        span = SourceSpan(head.span.line_start, extended_end, 0, 0) if extended_end != head.span.line_end else head.span
        return ModuleNode(
            node_type=head.node_type,
            name=head.name,
            span=span,
            docstring=head.docstring,
            source=head.source,
            children=children,
        )
    return head
