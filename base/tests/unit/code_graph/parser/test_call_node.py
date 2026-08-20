"""Tests for CallNode extraction in Python and TypeScript parsers."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import CallNode, FileNode


def _parse_py(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _parse_ts(source: str, ext: str = ".ts") -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=ext, mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _calls(file_node: FileNode) -> list[CallNode]:
    return [c for c in file_node.children if c.node_type == NodeType.CALL]


# ---------------------------------------------------------------------------
# Python calls
# ---------------------------------------------------------------------------


class TestPythonCalls:
    @staticmethod
    def test_simple_call():
        r = _parse_py("def f():\n    foo()\n")
        cs = _calls(r)
        assert len(cs) == 1
        assert cs[0].callee == "foo"
        assert cs[0].receiver is None

    @staticmethod
    def test_method_call():
        r = _parse_py("def f():\n    obj.method()\n")
        cs = _calls(r)
        assert len(cs) == 1
        assert cs[0].callee == "method"
        assert cs[0].receiver == "obj"

    @staticmethod
    def test_chained_call():
        r = _parse_py("def f():\n    a.b.c()\n")
        cs = _calls(r)
        assert any(c.callee == "c" and c.receiver == "a.b" for c in cs)

    @staticmethod
    def test_self_call():
        r = _parse_py("class X:\n    def m(self):\n        self.foo()\n")
        cs = _calls(r)
        assert len(cs) == 1
        assert cs[0].callee == "foo"
        assert cs[0].receiver == "self"

    @staticmethod
    def test_context_is_function_name():
        r = _parse_py("def process():\n    helper()\n")
        cs = _calls(r)
        assert cs[0].context == "process"

    @staticmethod
    def test_context_is_method_name():
        r = _parse_py("class Foo:\n    def bar(self):\n        baz()\n")
        cs = _calls(r)
        assert cs[0].context == "bar"

    @staticmethod
    def test_multiple_calls():
        r = _parse_py("def f():\n    a()\n    b()\n    c()\n")
        cs = _calls(r)
        callees = [c.callee for c in cs]
        assert "a" in callees
        assert "b" in callees
        assert "c" in callees


# ---------------------------------------------------------------------------
# TypeScript calls
# ---------------------------------------------------------------------------


class TestTypeScriptCalls:
    @staticmethod
    def test_simple_call():
        r = _parse_ts("function f() { foo(); }")
        cs = _calls(r)
        assert any(c.callee == "foo" for c in cs)

    @staticmethod
    def test_method_call():
        r = _parse_ts("function f() { obj.method(); }")
        cs = _calls(r)
        assert any(c.callee == "method" and c.receiver == "obj" for c in cs)

    @staticmethod
    def test_new_expression():
        r = _parse_ts("function f() { new Foo(); }")
        cs = _calls(r)
        assert any(c.callee == "Foo" for c in cs)

    @staticmethod
    def test_class_method_call():
        r = _parse_ts("class X { m() { this.helper(); } }")
        cs = _calls(r)
        assert any(c.callee == "helper" and c.receiver == "this" for c in cs)

    @staticmethod
    def test_arrow_function_calls():
        r = _parse_ts("const f = () => { doStuff(); };")
        cs = _calls(r)
        assert any(c.callee == "doStuff" for c in cs)
