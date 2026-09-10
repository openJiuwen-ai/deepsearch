"""JavaScript language parser using tree-sitter.

Reuses the shared extraction logic from :mod:`.typescript` — JS is a strict
subset of TS at the AST level, so the TS extractors work unchanged (TS-only
node types like ``interface_declaration`` simply never appear in JS ASTs).
"""

import asyncio
from pathlib import Path

import tree_sitter_javascript
from tree_sitter import Language, Parser

from ...models.structural import FileNode
from .. import BaseLanguageParser
from ..typescript import parse_sync

_JS_LANG = Language(tree_sitter_javascript.language())


class JavaScriptParser(BaseLanguageParser):
    """Parse JavaScript source files using tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(_JS_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(parse_sync, self._parser, path, source, "javascript")
