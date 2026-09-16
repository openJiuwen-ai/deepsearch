"""Tests for the CSS language parser."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import FileNode


def _parse(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".css", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _children_by_type(file_node: FileNode, node_type: NodeType) -> list:
    return [c for c in file_node.children if c.node_type == node_type]


# ---------------------------------------------------------------------------
# Rule sets
# ---------------------------------------------------------------------------


class TestRuleSets:
    @staticmethod
    def test_basic_rule():
        r = _parse("body { margin: 0; padding: 0; }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "body"

    @staticmethod
    def test_class_selector():
        r = _parse(".container { max-width: 1200px; }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == ".container"

    @staticmethod
    def test_id_selector():
        r = _parse("#header { height: 60px; }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert cls[0].name == "#header"

    @staticmethod
    def test_compound_selector():
        r = _parse("h1, h2, h3 { font-weight: bold; }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert "h1" in cls[0].name

    @staticmethod
    def test_declarations_as_properties():
        r = _parse("body { margin: 0; padding: 10px; color: red; }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        props = [c for c in cls.children if c.node_type == NodeType.PROPERTY]
        assert len(props) == 3
        names = {p.name for p in props}
        assert names == {"margin", "padding", "color"}

    @staticmethod
    def test_declaration_values():
        r = _parse(".box { border: 1px solid black; }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        props = [c for c in cls.children if c.node_type == NodeType.PROPERTY]
        border = next(p for p in props if p.name == "border")
        assert border.default_value is not None
        assert "1px" in border.default_value

    @staticmethod
    def test_declaration_owner():
        r = _parse(".card { color: blue; }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        prop = cls.children[0]
        assert prop.owner == ".card"


# ---------------------------------------------------------------------------
# Media queries
# ---------------------------------------------------------------------------


class TestMediaQueries:
    @staticmethod
    def test_media_query_container():
        r = _parse("@media (max-width: 768px) { .mobile { padding: 8px; } }")
        modules = _children_by_type(r, NodeType.MODULE)
        assert len(modules) == 1
        assert "@media" in modules[0].name

    @staticmethod
    def test_nested_rules_in_media():
        r = _parse("""
@media (max-width: 768px) {
  .container { padding: 16px; }
  .sidebar { display: none; }
}
""")
        media = _children_by_type(r, NodeType.MODULE)[0]
        nested_rules = [c for c in media.children if c.node_type == NodeType.CLASS]
        assert len(nested_rules) == 2
        names = {c.name for c in nested_rules}
        assert ".container" in names
        assert ".sidebar" in names


# ---------------------------------------------------------------------------
# Keyframes
# ---------------------------------------------------------------------------


class TestKeyframes:
    @staticmethod
    def test_keyframes():
        r = _parse("@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert "fadeIn" in blocks[0].name


# ---------------------------------------------------------------------------
# Custom properties
# ---------------------------------------------------------------------------


class TestCustomProperties:
    @staticmethod
    def test_root_custom_properties():
        r = _parse(":root { --primary: #007bff; --spacing: 16px; }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == ":root"
        props = [c for c in cls[0].children if c.node_type == NodeType.PROPERTY]
        names = {p.name for p in props}
        assert "--primary" in names
        assert "--spacing" in names


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestSupports:
    @staticmethod
    def test_supports_statement():
        r = _parse("@supports (display: grid) { .grid { display: grid; } }")
        modules = _children_by_type(r, NodeType.MODULE)
        assert len(modules) == 1
        assert "@supports" in modules[0].name

    @staticmethod
    def test_supports_nested_rules():
        r = _parse("""
@supports (display: flex) {
  .flex { display: flex; }
  .row { flex-direction: row; }
}
""")
        module = _children_by_type(r, NodeType.MODULE)[0]
        nested = [c for c in module.children if c.node_type == NodeType.CLASS]
        assert len(nested) == 2


class TestEdgeCases:
    @staticmethod
    def test_empty_css():
        r = _parse("")
        assert len(r.children) == 0

    @staticmethod
    def test_import_skipped():
        r = _parse("@import url('reset.css');")
        assert len(_children_by_type(r, NodeType.CLASS)) == 0
        assert len(_children_by_type(r, NodeType.CODE_BLOCK)) == 0

    @staticmethod
    def test_multiple_rules():
        r = _parse("""
body { margin: 0; }
.header { height: 60px; }
.footer { height: 40px; }
""")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 3


# ---------------------------------------------------------------------------
# File metadata
# ---------------------------------------------------------------------------


class TestFileMeta:
    @staticmethod
    def test_language_is_css():
        r = _parse("body { color: red; }")
        assert r.language == "css"
