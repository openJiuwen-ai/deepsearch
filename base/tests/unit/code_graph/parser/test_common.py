"""Tests for shared models and constants."""

import pytest

from openjiuwen_search_base.codegraph.parser.constants import NodeType, detect_language
from openjiuwen_search_base.codegraph.parser.custom_types import SourceSpan
from openjiuwen_search_base.codegraph.parser.models import (
    BaseNode,
    DuckTypeNode,
    FunctionNode,
    PropertyNode,
)


class TestNodeType:
    @staticmethod
    def test_string_value():
        assert NodeType.CLASS == "class"
        assert NodeType.FUNCTION == "function"
        assert NodeType.DUCK_TYPE == "duck_type"

    @staticmethod
    def test_all_members_exist():
        expected = {
            "FOLDER",
            "FILE",
            "CLASS",
            "CODE_BLOCK",
            "DUCK_TYPE",
            "INTERFACE",
            "FUNCTION",
            "PROPERTY",
            "IMPORT",
            "CALL",
            "LOCAL_VAR",
            "ENUM",
            "STRUCT",
            "UNION",
            "MACRO",
            "MODULE",
            "TYPE_ALIAS",
            "ANNOTATION",
        }
        assert expected == {m.name for m in NodeType}


class TestDetectLanguage:
    @staticmethod
    def test_python():
        assert detect_language("foo.py") == "python"
        assert detect_language("types.pyi") == "python"

    @staticmethod
    def test_markdown():
        assert detect_language("README.md") == "markdown"
        assert detect_language("notes.markdown") == "markdown"

    @staticmethod
    def test_javascript():
        assert detect_language("app.js") == "javascript"
        assert detect_language("component.jsx") == "javascript"
        assert detect_language("main.mjs") == "javascript"

    @staticmethod
    def test_typescript():
        assert detect_language("app.ts") == "typescript"
        assert detect_language("component.tsx") == "tsx"

    @staticmethod
    def test_html_css():
        assert detect_language("index.html") == "html"
        assert detect_language("page.htm") == "html"
        assert detect_language("style.css") == "css"

    @staticmethod
    def test_makefile():
        assert detect_language("Makefile") == "makefile"
        assert detect_language("makefile") == "makefile"
        assert detect_language("GNUmakefile") == "makefile"
        assert detect_language("build.mk") == "makefile"
        assert detect_language("build.mak") == "makefile"
        assert detect_language("rules.make") == "makefile"
        assert detect_language("Makefile.am") == "makefile"
        assert detect_language("Makefile.in") == "makefile"
        assert detect_language("makefile.rules") == "makefile"

    @staticmethod
    def test_c():
        assert detect_language("main.c") == "c"

    @staticmethod
    def test_cpp():
        assert detect_language("main.cpp") == "cpp"
        assert detect_language("util.cc") == "cpp"
        assert detect_language("lib.cxx") == "cpp"
        assert detect_language("header.h") == "cpp"
        assert detect_language("api.hpp") == "cpp"
        assert detect_language("impl.hh") == "cpp"
        assert detect_language("core.hxx") == "cpp"

    @staticmethod
    def test_unknown():
        assert detect_language("image.png") is None
        assert detect_language("data.json") is None

    @staticmethod
    def test_no_parent_path_leak():
        assert detect_language("foo.py") == "python"


class TestSourceSpan:
    @staticmethod
    def test_creation():
        s = SourceSpan(line_start=1, line_end=10, col_start=0, col_end=5)
        assert s.line_start == 1
        assert s.line_end == 10

    @staticmethod
    def test_defaults():
        s = SourceSpan(1, 10)
        assert s.col_start == 0
        assert s.col_end == 0


class TestBaseNode:
    @staticmethod
    def test_frozen():
        node = BaseNode(node_type=NodeType.FILE, name="test", span=SourceSpan(1, 1))
        with pytest.raises(AttributeError):
            node.name = "other"  # type: ignore[misc]

    @staticmethod
    def test_defaults():
        node = BaseNode(node_type=NodeType.FILE, name="x", span=SourceSpan(1, 1))
        assert node.docstring is None
        assert node.source is None
        assert node.children == ()


class TestFunctionNode:
    @staticmethod
    def test_free_function():
        fn = FunctionNode(
            node_type=NodeType.FUNCTION,
            name="foo",
            span=SourceSpan(1, 5),
            func_type="function",
        )
        assert fn.owner is None
        assert fn.func_type == "function"

    @staticmethod
    def test_method():
        fn = FunctionNode(
            node_type=NodeType.FUNCTION,
            name="Cls.bar",
            span=SourceSpan(2, 4),
            owner="Cls",
            func_type="method",
        )
        assert fn.owner == "Cls"
        assert fn.func_type == "method"

    @staticmethod
    def test_nested():
        fn = FunctionNode(
            node_type=NodeType.FUNCTION,
            name="outer.inner",
            span=SourceSpan(3, 6),
            owner="outer",
            func_type="nested",
        )
        assert fn.func_type == "nested"


class TestPropertyNode:
    @staticmethod
    def test_module_level():
        prop = PropertyNode(
            node_type=NodeType.PROPERTY,
            name="X",
            span=SourceSpan(1, 1),
            type_annotation="int",
            default_value="42",
        )
        assert prop.owner is None

    @staticmethod
    def test_class_level():
        prop = PropertyNode(
            node_type=NodeType.PROPERTY,
            name="x",
            span=SourceSpan(2, 2),
            owner="Foo",
        )
        assert prop.owner == "Foo"


class TestDuckTypeNode:
    @staticmethod
    def test_identity_by_methods():
        dt1 = DuckTypeNode(
            node_type=NodeType.DUCK_TYPE,
            name="DuckType{embed}",
            span=SourceSpan(0, 0),
            methods=frozenset({"embed"}),
        )
        dt2 = DuckTypeNode(
            node_type=NodeType.DUCK_TYPE,
            name="DuckType{embed}",
            span=SourceSpan(0, 0),
            methods=frozenset({"embed"}),
        )
        assert dt1 == dt2

    @staticmethod
    def test_different_methods_differ():
        dt1 = DuckTypeNode(
            node_type=NodeType.DUCK_TYPE,
            name="DuckType{a}",
            span=SourceSpan(0, 0),
            methods=frozenset({"a"}),
        )
        dt2 = DuckTypeNode(
            node_type=NodeType.DUCK_TYPE,
            name="DuckType{a, b}",
            span=SourceSpan(0, 0),
            methods=frozenset({"a", "b"}),
        )
        assert dt1 != dt2
