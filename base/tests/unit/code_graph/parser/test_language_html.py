"""Tests for the HTML language parser."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import FileNode


def _parse(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _children_by_type(file_node: FileNode, node_type: NodeType) -> list:
    return [c for c in file_node.children if c.node_type == node_type]


def _find_by_name(children, name: str):
    for c in children:
        if c.name == name:
            return c
    return None


# ---------------------------------------------------------------------------
# Element structure
# ---------------------------------------------------------------------------


class TestElements:
    def test_top_level_element(self):
        r = _parse("<html><body></body></html>")
        modules = _children_by_type(r, NodeType.MODULE)
        assert len(modules) == 1
        assert modules[0].name == "html"

    def test_nested_elements(self):
        r = _parse("<html><head><title>Hi</title></head><body><div><p>Text</p></div></body></html>")
        html_node = _children_by_type(r, NodeType.MODULE)[0]
        assert html_node.name == "html"
        head = _find_by_name(html_node.children, "head")
        body = _find_by_name(html_node.children, "body")
        assert head is not None
        assert body is not None
        title = _find_by_name(head.children, "title")
        assert title is not None
        div_ = _find_by_name(body.children, "div")
        assert div_ is not None
        p = _find_by_name(div_.children, "p")
        assert p is not None

    def test_sibling_elements(self):
        r = _parse("<div><span>A</span><span>B</span></div>")
        div_ = _children_by_type(r, NodeType.MODULE)[0]
        spans = [c for c in div_.children if c.name == "span"]
        assert len(spans) == 2

    def test_self_closing_element(self):
        r = _parse("<div><img /><br /></div>")
        div_ = _children_by_type(r, NodeType.MODULE)[0]
        assert div_.name == "div"


# ---------------------------------------------------------------------------
# Script / Style blocks
# ---------------------------------------------------------------------------


class TestScriptStyle:
    def test_script_block(self):
        r = _parse("<html><body><script>alert('hello');</script></body></html>")
        html_node = _children_by_type(r, NodeType.MODULE)[0]
        body = _find_by_name(html_node.children, "body")
        assert body is not None
        scripts = [c for c in body.children if c.node_type == NodeType.CODE_BLOCK and c.name == "<script>"]
        assert len(scripts) == 1
        assert "alert" in scripts[0].source

    def test_style_block(self):
        r = _parse("<html><head><style>body { margin: 0; }</style></head></html>")
        html_node = _children_by_type(r, NodeType.MODULE)[0]
        head = _find_by_name(html_node.children, "head")
        assert head is not None
        styles = [c for c in head.children if c.node_type == NodeType.CODE_BLOCK and c.name == "<style>"]
        assert len(styles) == 1
        assert "margin" in styles[0].source

    def test_multiple_scripts(self):
        r = _parse("<body><script>var a=1;</script><script>var b=2;</script></body>")
        body = _children_by_type(r, NodeType.MODULE)[0]
        scripts = [c for c in body.children if c.name == "<script>"]
        assert len(scripts) == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_html(self):
        r = _parse("")
        assert len(r.children) == 0

    def test_doctype_skipped(self):
        r = _parse("<!DOCTYPE html><html></html>")
        modules = _children_by_type(r, NodeType.MODULE)
        assert len(modules) == 1
        assert modules[0].name == "html"

    def test_text_nodes_skipped(self):
        r = _parse("<div>Some plain text</div>")
        div_ = _children_by_type(r, NodeType.MODULE)[0]
        assert len(div_.children) == 0


# ---------------------------------------------------------------------------
# File metadata
# ---------------------------------------------------------------------------


class TestFileMeta:
    def test_language_is_html(self):
        r = _parse("<div></div>")
        assert r.language == "html"
