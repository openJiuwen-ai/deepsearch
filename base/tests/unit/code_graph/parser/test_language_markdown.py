"""Tests for the Markdown language parser."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import FileNode


def _parse(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


class TestBasicParsing:
    @staticmethod
    def test_single_heading():
        r = _parse("# Title\nSome body text.\n")
        assert r.language == "markdown"
        assert len(r.children) == 1
        assert r.children[0].name == "Title"
        assert r.children[0].source == "Some body text."

    @staticmethod
    def test_no_headings():
        r = _parse("Just plain text.\nNo headings here.\n")
        assert len(r.children) == 0

    @staticmethod
    def test_multiple_top_level():
        r = _parse("# One\ntext1\n# Two\ntext2\n# Three\ntext3\n")
        assert len(r.children) == 3
        assert [c.name for c in r.children] == ["One", "Two", "Three"]


class TestNesting:
    @staticmethod
    def test_h2_nested_under_h1():
        r = _parse("# Parent\n## Child\nchild text\n")
        assert len(r.children) == 1
        parent = r.children[0]
        assert parent.name == "Parent"
        assert len(parent.children) == 1
        child = parent.children[0]
        assert child.name == "Child"
        assert child.source == "child text"

    @staticmethod
    def test_deep_nesting():
        r = _parse("# H1\n## H2\n### H3\n#### H4\ndeep\n")
        h1 = r.children[0]
        h2 = h1.children[0]
        h3 = h2.children[0]
        h4 = h3.children[0]
        assert h4.name == "H4"
        assert h4.source == "deep"

    @staticmethod
    def test_sibling_h2s():
        src = "# Top\n## A\nalpha\n## B\nbeta\n"
        r = _parse(src)
        top = r.children[0]
        assert len(top.children) == 2
        assert top.children[0].name == "A"
        assert top.children[1].name == "B"

    @staticmethod
    def test_h2_before_h1_resets():
        src = "## Orphan\norph text\n# Root\nroot text\n"
        r = _parse(src)
        assert len(r.children) == 2
        assert r.children[0].name == "Orphan"
        assert r.children[1].name == "Root"


class TestNodeTypes:
    @staticmethod
    def test_all_sections_are_module_nodes():
        r = _parse("# A\n## B\ntext\n")

        def check(node):
            assert node.node_type == NodeType.MODULE
            for c in node.children:
                check(c)

        for c in r.children:
            check(c)


class TestEdgeCases:
    @staticmethod
    def test_empty_file():
        r = _parse("")
        assert r.children == ()

    @staticmethod
    def test_heading_with_no_body():
        r = _parse("# Empty\n# Another\n")
        assert len(r.children) == 2
        assert r.children[0].source is None
        assert r.children[1].source is None

    @staticmethod
    def test_heading_with_extra_hashes():
        r = _parse("###### Deep\ntext\n")
        assert len(r.children) == 1
        assert r.children[0].name == "Deep"

    @staticmethod
    def test_file_node_properties():
        r = _parse("# Title\n")
        assert r.node_type == NodeType.FILE
        assert r.language == "markdown"
        assert r.path.endswith(".md")
