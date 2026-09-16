"""Plain-text parser: a FileNode with no structural children."""

import asyncio
from pathlib import Path

from ...constants import NodeType
from ...custom_types import SourceSpan
from ...models.structural import FileNode
from .. import BaseLanguageParser


class TxtParser(BaseLanguageParser):
    """Treat a file as unstructured UTF-8 text.

    Used for ``.txt`` files and as the fallback when ``parse_file`` /
    ``parse_files`` are called with ``errors="as_txt"``.
    """

    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* in a thread and return a :class:`FileNode`."""
        return await asyncio.to_thread(self._parse_sync, path, source)

    def _parse_sync(self, path: Path, source: bytes) -> FileNode:
        text = source.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total_lines = len(lines) if lines else (1 if text else 0)
        return FileNode(
            node_type=NodeType.FILE,
            name=path.name,
            span=SourceSpan(1, total_lines, 0, 0),
            source=text,
            path=str(path),
            language="txt",
        )
