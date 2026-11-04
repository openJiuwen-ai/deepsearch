"""Tests for the plain-text language parser."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import FileNode


def _parse(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        result = asyncio.run(parse_file(path))
        assert result is not None
        return result
    finally:
        path.unlink()


class TestTxtParser:
    @staticmethod
    def test_language_and_source():
        r = _parse("hello world\n")
        assert r.language == "txt"
        assert r.node_type == NodeType.FILE
        assert r.source == "hello world\n"
        assert r.children == ()

    @staticmethod
    def test_span_covers_lines():
        r = _parse("line one\nline two\nline three\n")
        assert r.span.line_start == 1
        assert r.span.line_end == 3

    @staticmethod
    def test_empty_file():
        r = _parse("")
        assert r.language == "txt"
        assert r.source == ""
        assert r.children == ()
        assert r.span.line_start == 1
        assert r.span.line_end == 0
