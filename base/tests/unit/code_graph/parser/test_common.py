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
    def test_string_value(self):
        assert NodeType.CLASS == "class"
        assert NodeType.FUNCTION == "function"
        assert NodeType.DUCK_TYPE == "duck_type"

    def test_all_members_exist(self):
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
    def test_python(self):
        assert detect_language("foo.py") == "python"
        assert detect_language("types.pyi") == "python"

    def test_markdown(self):
        assert detect_language("README.md") == "markdown"
        assert detect_language("notes.markdown") == "markdown"

    def test_javascript(self):
        assert detect_language("app.js") == "javascript"
        assert detect_language("component.jsx") == "javascript"
        assert detect_language("main.mjs") == "javascript"

    def test_typescript(self):
        assert detect_language("app.ts") == "typescript"
        assert detect_language("component.tsx") == "tsx"

    def test_html_css(self):
        assert detect_language("index.html") == "html"
        assert detect_language("page.htm") == "html"
        assert detect_language("style.css") == "css"

    def test_makefile(self):
        assert detect_language("Makefile") == "makefile"
        assert detect_language("makefile") == "makefile"
        assert detect_language("GNUmakefile") == "makefile"
        assert detect_language("build.mk") == "makefile"
        assert detect_language("build.mak") == "makefile"
        assert detect_language("rules.make") == "makefile"
        assert detect_language("Makefile.am") == "makefile"
        assert detect_language("Makefile.in") == "makefile"
        assert detect_language("makefile.rules") == "makefile"

    def test_c(self):
        assert detect_language("main.c") == "c"

    def test_cpp(self):
        assert detect_language("main.cpp") == "cpp"
        assert detect_language("util.cc") == "cpp"
        assert detect_language("lib.cxx") == "cpp"
        assert detect_language("header.h") == "cpp"
        assert detect_language("api.hpp") == "cpp"
        assert detect_language("impl.hh") == "cpp"
        assert detect_language("core.hxx") == "cpp"

    def test_unknown(self):
        assert detect_language("image.png") is None
        assert detect_language("data.json") is None

    def test_no_parent_path_leak(self):
        assert detect_language("foo.py") == "python"


class TestSourceSpan:
    def test_creation(self):
        s = SourceSpan(line_start=1, line_end=10, col_start=0, col_end=5)
        assert s.line_start == 1
        assert s.line_end == 10

    def test_defaults(self):
        s = SourceSpan(1, 10)
        assert s.col_start == 0
        assert s.col_end == 0


class TestBaseNode:
    def test_frozen(self):
        node = BaseNode(node_type=NodeType.FILE, name="test", span=SourceSpan(1, 1))
        with pytest.raises(AttributeError):
            node.name = "other"  # type: ignore[misc]

    def test_defaults(self):
        node = BaseNode(node_type=NodeType.FILE, name="x", span=SourceSpan(1, 1))
        assert node.docstring is None
        assert node.source is None
        assert node.children == ()


class TestFunctionNode:
    def test_free_function(self):
        fn = FunctionNode(
            node_type=NodeType.FUNCTION,
            name="foo",
            span=SourceSpan(1, 5),
            func_type="function",
        )
        assert fn.owner is None
        assert fn.func_type == "function"

    def test_method(self):
        fn = FunctionNode(
            node_type=NodeType.FUNCTION,
            name="Cls.bar",
            span=SourceSpan(2, 4),
            owner="Cls",
            func_type="method",
        )
        assert fn.owner == "Cls"
        assert fn.func_type == "method"

    def test_nested(self):
        fn = FunctionNode(
            node_type=NodeType.FUNCTION,
            name="outer.inner",
            span=SourceSpan(3, 6),
            owner="outer",
            func_type="nested",
        )
        assert fn.func_type == "nested"


class TestPropertyNode:
    def test_module_level(self):
        prop = PropertyNode(
            node_type=NodeType.PROPERTY,
            name="X",
            span=SourceSpan(1, 1),
            type_annotation="int",
            default_value="42",
        )
        assert prop.owner is None

    def test_class_level(self):
        prop = PropertyNode(
            node_type=NodeType.PROPERTY,
            name="x",
            span=SourceSpan(2, 2),
            owner="Foo",
        )
        assert prop.owner == "Foo"


class TestDuckTypeNode:
    def test_identity_by_methods(self):
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

    def test_different_methods_differ(self):
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
