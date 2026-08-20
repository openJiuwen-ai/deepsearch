"""Tests for Parameter type extraction in Python and TypeScript parsers."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import Parameter, parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import FileNode


def _parse_py(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _parse_ts(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _functions(file_node: FileNode):
    return [c for c in file_node.children if c.node_type == NodeType.FUNCTION]


# ---------------------------------------------------------------------------
# Python parameter types
# ---------------------------------------------------------------------------


class TestPythonParameters:
    def test_untyped(self):
        r = _parse_py("def f(a, b):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (Parameter(name="a"), Parameter(name="b"))

    def test_typed(self):
        r = _parse_py("def f(x: int, y: str):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (
            Parameter(name="x", type_annotation="int"),
            Parameter(name="y", type_annotation="str"),
        )

    def test_default_value(self):
        r = _parse_py("def f(x: int = 5):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (Parameter(name="x", type_annotation="int", default="5"),)

    def test_untyped_default(self):
        r = _parse_py("def f(x=10):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (Parameter(name="x", default="10"),)

    def test_complex_annotation(self):
        r = _parse_py("def f(items: list[str]):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters[0].type_annotation == "list[str]"

    def test_splat_params(self):
        r = _parse_py("def f(*args, **kwargs):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (
            Parameter(name="*args"),
            Parameter(name="**kwargs"),
        )

    def test_signature_includes_types(self):
        r = _parse_py("def f(x: int, y: str = 'hi'):\n    pass\n")
        fn = _functions(r)[0]
        sig = fn.signature
        assert "x: int" in sig
        assert "y: str = 'hi'" in sig


# ---------------------------------------------------------------------------
# TypeScript parameter types
# ---------------------------------------------------------------------------


class TestTypeScriptParameters:
    def test_typed_params(self):
        r = _parse_ts("function f(x: number, y: string) { }")
        fn = _functions(r)[0]
        assert fn.parameters[0].name == "x"
        assert fn.parameters[0].type_annotation == "number"
        assert fn.parameters[1].name == "y"
        assert fn.parameters[1].type_annotation == "string"

    def test_default_value(self):
        r = _parse_ts("function f(x: number = 5) { }")
        fn = _functions(r)[0]
        assert fn.parameters[0].name == "x"
        assert fn.parameters[0].type_annotation == "number"
        assert fn.parameters[0].default == "5"

    def test_untyped(self):
        r = _parse_ts("function f(a, b) { }")
        fn = _functions(r)[0]
        assert fn.parameters[0].name == "a"
        assert fn.parameters[1].name == "b"
