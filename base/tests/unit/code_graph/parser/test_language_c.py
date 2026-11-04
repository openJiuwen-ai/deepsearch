"""Tests for C language parser."""

import asyncio
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.languages.c import CBaseParser
from openjiuwen_search_base.codegraph.parser.models.core import CallNode, FunctionNode, ImportNode, PropertyNode
from openjiuwen_search_base.codegraph.parser.models.extensions import (
    EnumNode,
    MacroNode,
    StructNode,
    TypeAliasNode,
    UnionNode,
)


@pytest.fixture
def parser():
    return CBaseParser()


def _parse(parser, code: str):
    return asyncio.run(parser.parse(Path("test.c"), code.encode()))


class TestCFunctions:
    @staticmethod
    def test_basic_function(parser):
        result = _parse(parser, "int add(int a, int b) { return a + b; }")
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        assert len(funcs) == 1
        fn = funcs[0]
        assert fn.name == "add"
        assert fn.return_type == "int"
        assert len(fn.parameters) == 2
        assert fn.parameters[0].name == "a"
        assert fn.parameters[0].type_annotation == "int"
        assert fn.parameters[1].name == "b"

    @staticmethod
    def test_void_function(parser):
        result = _parse(parser, "void process(void) { }")
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        assert len(funcs) == 1
        assert funcs[0].return_type == "void"

    @staticmethod
    def test_pointer_param(parser):
        result = _parse(parser, "void init(int* ptr, char** argv) { }")
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        fn = funcs[0]
        assert fn.parameters[0].name == "ptr"
        assert "int" in (fn.parameters[0].type_annotation or "")

    @staticmethod
    def test_static_function(parser):
        result = _parse(parser, "static inline int helper(void) { return 0; }")
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        assert "@static" in funcs[0].decorators
        assert "@inline" in funcs[0].decorators

    @staticmethod
    def test_cyclomatic_complexity(parser):
        code = """
        int complex(int x) {
            if (x > 0) {
                for (int i = 0; i < x; i++) {
                    if (i % 2 == 0) { }
                }
            }
            return x;
        }
        """
        result = _parse(parser, code)
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        assert funcs[0].cyclomatic_complexity >= 4

    @staticmethod
    def test_variadic_function(parser):
        result = _parse(parser, "int format(const char* fmt, ...) { return 0; }")
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        assert funcs[0].parameters[-1].name == "..."


class TestCStructs:
    @staticmethod
    def test_basic_struct(parser):
        code = "struct Point { int x; int y; };"
        result = _parse(parser, code)
        structs = [c for c in result.children if isinstance(c, StructNode)]
        assert len(structs) == 1
        assert structs[0].name == "Point"
        assert len(structs[0].fields) == 2
        assert structs[0].fields[0].name == "x"
        assert structs[0].fields[1].name == "y"

    @staticmethod
    def test_typedef_struct(parser):
        code = "typedef struct { int x; int y; } Point;"
        result = _parse(parser, code)
        structs = [c for c in result.children if isinstance(c, StructNode)]
        assert len(structs) == 1
        assert structs[0].name == "Point"

    @staticmethod
    def test_forward_declaration_skipped(parser):
        code = "struct Foo;"
        result = _parse(parser, code)
        structs = [c for c in result.children if isinstance(c, StructNode)]
        assert len(structs) == 0


class TestCUnions:
    @staticmethod
    def test_basic_union(parser):
        code = "union Data { int i; float f; char c; };"
        result = _parse(parser, code)
        unions = [c for c in result.children if isinstance(c, UnionNode)]
        assert len(unions) == 1
        assert unions[0].name == "Data"
        assert "i" in unions[0].variants
        assert "f" in unions[0].variants
        assert "c" in unions[0].variants

    @staticmethod
    def test_typedef_union(parser):
        code = "typedef union { int i; double d; } Value;"
        result = _parse(parser, code)
        unions = [c for c in result.children if isinstance(c, UnionNode)]
        assert len(unions) == 1
        assert unions[0].name == "Value"


class TestCEnums:
    @staticmethod
    def test_basic_enum(parser):
        code = "enum Color { RED, GREEN, BLUE };"
        result = _parse(parser, code)
        enums = [c for c in result.children if isinstance(c, EnumNode)]
        assert len(enums) == 1
        assert enums[0].name == "Color"
        assert enums[0].members == ("RED", "GREEN", "BLUE")

    @staticmethod
    def test_typedef_enum(parser):
        code = "typedef enum { LOW, MED, HIGH } Priority;"
        result = _parse(parser, code)
        enums = [c for c in result.children if isinstance(c, EnumNode)]
        assert len(enums) == 1
        assert enums[0].name == "Priority"
        assert "LOW" in enums[0].members


class TestCMacros:
    @staticmethod
    def test_object_macro(parser):
        code = "#define MAX_SIZE 100"
        result = _parse(parser, code)
        macros = [c for c in result.children if isinstance(c, MacroNode)]
        assert len(macros) == 1
        assert macros[0].name == "MAX_SIZE"
        assert macros[0].expansion == "100"
        assert macros[0].parameters == ()

    @staticmethod
    def test_function_macro(parser):
        code = "#define MAX(a, b) ((a) > (b) ? (a) : (b))"
        result = _parse(parser, code)
        macros = [c for c in result.children if isinstance(c, MacroNode)]
        assert len(macros) == 1
        assert macros[0].name == "MAX"
        assert macros[0].parameters == ("a", "b")
        assert "?" in macros[0].expansion

    @staticmethod
    def test_macro_signature(parser):
        code = "#define SQUARE(x) ((x)*(x))"
        result = _parse(parser, code)
        macros = [c for c in result.children if isinstance(c, MacroNode)]
        assert macros[0].signature == "#define SQUARE(x)"


class TestCIncludes:
    @staticmethod
    def test_system_include(parser):
        code = "#include <stdio.h>"
        result = _parse(parser, code)
        imports = [c for c in result.children if isinstance(c, ImportNode)]
        assert len(imports) == 1
        assert imports[0].module == "stdio.h"

    @staticmethod
    def test_local_include(parser):
        code = '#include "myheader.h"'
        result = _parse(parser, code)
        imports = [c for c in result.children if isinstance(c, ImportNode)]
        assert len(imports) == 1
        assert imports[0].module == "myheader.h"


class TestCVariables:
    @staticmethod
    def test_global_variable(parser):
        code = "int count = 0;"
        result = _parse(parser, code)
        props = [c for c in result.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].name == "count"
        assert props[0].type_annotation == "int"
        assert props[0].default_value == "0"


class TestCCalls:
    @staticmethod
    def test_calls_extracted(parser):
        code = """
        void process() {
            int x = add(1, 2);
            printf("hello");
        }
        """
        result = _parse(parser, code)
        calls = [c for c in result.children if isinstance(c, CallNode)]
        callees = {c.callee for c in calls}
        assert "add" in callees
        assert "printf" in callees

    @staticmethod
    def test_call_context(parser):
        code = """
        void outer() {
            inner();
        }
        """
        result = _parse(parser, code)
        calls = [c for c in result.children if isinstance(c, CallNode)]
        assert calls[0].context == "outer"


class TestCTypedefs:
    @staticmethod
    def test_plain_typedef(parser):
        code = "typedef unsigned long size_t;"
        result = _parse(parser, code)
        aliases = [c for c in result.children if isinstance(c, TypeAliasNode)]
        assert len(aliases) == 1
        assert aliases[0].name == "size_t"

    @staticmethod
    def test_function_pointer_typedef(parser):
        code = "typedef int (*Comparator)(const void*, const void*);"
        result = _parse(parser, code)
        aliases = [c for c in result.children if isinstance(c, TypeAliasNode)]
        assert len(aliases) >= 1


class TestCFileNode:
    @staticmethod
    def test_language(parser):
        result = _parse(parser, "int x;")
        assert result.language == "c"
        assert result.node_type == NodeType.FILE
