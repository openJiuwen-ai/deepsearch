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
    @staticmethod
    def test_untyped():
        r = _parse_py("def f(a, b):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (Parameter(name="a"), Parameter(name="b"))

    @staticmethod
    def test_typed():
        r = _parse_py("def f(x: int, y: str):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (
            Parameter(name="x", type_annotation="int"),
            Parameter(name="y", type_annotation="str"),
        )

    @staticmethod
    def test_default_value():
        r = _parse_py("def f(x: int = 5):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (Parameter(name="x", type_annotation="int", default="5"),)

    @staticmethod
    def test_untyped_default():
        r = _parse_py("def f(x=10):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (Parameter(name="x", default="10"),)

    @staticmethod
    def test_complex_annotation():
        r = _parse_py("def f(items: list[str]):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters[0].type_annotation == "list[str]"

    @staticmethod
    def test_splat_params():
        r = _parse_py("def f(*args, **kwargs):\n    pass\n")
        fn = _functions(r)[0]
        assert fn.parameters == (
            Parameter(name="*args"),
            Parameter(name="**kwargs"),
        )

    @staticmethod
    def test_signature_includes_types():
        r = _parse_py("def f(x: int, y: str = 'hi'):\n    pass\n")
        fn = _functions(r)[0]
        sig = fn.signature
        assert "x: int" in sig
        assert "y: str = 'hi'" in sig


# ---------------------------------------------------------------------------
# TypeScript parameter types
# ---------------------------------------------------------------------------


class TestTypeScriptParameters:
    @staticmethod
    def test_typed_params():
        r = _parse_ts("function f(x: number, y: string) { }")
        fn = _functions(r)[0]
        assert fn.parameters[0].name == "x"
        assert fn.parameters[0].type_annotation == "number"
        assert fn.parameters[1].name == "y"
        assert fn.parameters[1].type_annotation == "string"

    @staticmethod
    def test_default_value():
        r = _parse_ts("function f(x: number = 5) { }")
        fn = _functions(r)[0]
        assert fn.parameters[0].name == "x"
        assert fn.parameters[0].type_annotation == "number"
        assert fn.parameters[0].default == "5"

    @staticmethod
    def test_untyped():
        r = _parse_ts("function f(a, b) { }")
        fn = _functions(r)[0]
        assert fn.parameters[0].name == "a"
        assert fn.parameters[1].name == "b"
