"""Tests for the JavaScript language parser."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import FileNode


def _parse(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _children_by_type(file_node: FileNode, node_type: NodeType) -> list:
    return [c for c in file_node.children if c.node_type == node_type]


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


class TestClasses:
    @staticmethod
    def test_basic_class():
        r = _parse("class Foo { }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "Foo"

    @staticmethod
    def test_class_extends():
        r = _parse("class Dog extends Animal { }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert "Animal" in cls.bases

    @staticmethod
    def test_class_methods():
        r = _parse("""
class Greeter {
  greet(name) { return name; }
  async fetchData() { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 2
        assert methods[0].name == "Greeter.greet"
        assert methods[0].func_type == "method"
        assert methods[1].is_async is True

    @staticmethod
    def test_export_class():
        r = _parse("export class Exported { }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1

    @staticmethod
    def test_static_method():
        r = _parse("""
class Utils {
  static helper() { return 1; }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Utils.helper"

    @staticmethod
    def test_getter_setter():
        r = _parse("""
class Box {
  get value() { return this._v; }
  set value(v) { this._v = v; }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 2


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


class TestFunctions:
    @staticmethod
    def test_function_declaration():
        r = _parse("function greet(name) { return name; }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "greet" for f in fns)

    @staticmethod
    def test_async_function():
        r = _parse("async function fetch(url) { }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        fn = next(f for f in fns if f.name == "fetch")
        assert fn.is_async is True

    @staticmethod
    def test_arrow_function():
        r = _parse("const add = (a, b) => a + b;")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "add" for f in fns)

    @staticmethod
    def test_function_expression():
        r = _parse("const multiply = function(a, b) { return a * b; };")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "multiply" for f in fns)

    @staticmethod
    def test_exported_function():
        r = _parse("export function hello() { }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "hello" for f in fns)

    @staticmethod
    def test_nested_function():
        r = _parse("""
function outer() {
  function inner() { return 1; }
  return inner();
}
""")
        fns = _children_by_type(r, NodeType.FUNCTION)
        names = [f.name for f in fns]
        assert "outer" in names
        assert "outer.inner" in names
        inner = next(f for f in fns if f.name == "outer.inner")
        assert inner.func_type == "nested"
        assert inner.owner == "outer"

    @staticmethod
    def test_nested_arrow():
        r = _parse("""
function outer() {
  const helper = () => 42;
  return helper();
}
""")
        fns = _children_by_type(r, NodeType.FUNCTION)
        names = [f.name for f in fns]
        assert "outer.helper" in names


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    @staticmethod
    def test_const_variable():
        r = _parse("const URL = 'https://example.com';")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 1
        assert props[0].name == "URL"

    @staticmethod
    def test_let_variable():
        r = _parse("let count = 0;")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 1
        assert props[0].default_value == "0"

    @staticmethod
    def test_arrow_not_property():
        r = _parse("const fn = () => 1;")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0


# ---------------------------------------------------------------------------
# Code blocks
# ---------------------------------------------------------------------------


class TestCodeBlocks:
    @staticmethod
    def test_if_statement():
        r = _parse("if (true) { console.log(1); }")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) >= 1

    @staticmethod
    def test_code_between_definitions():
        r = _parse("""
function a() { }
console.log("between");
function b() { }
""")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1

    @staticmethod
    def test_consecutive_code_grouped():
        r = _parse("""
console.log(1);
console.log(2);
console.log(3);
""")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1

    @staticmethod
    def test_imports_skipped():
        r = _parse("import { Foo } from './foo';")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 0


# ---------------------------------------------------------------------------
# File metadata
# ---------------------------------------------------------------------------


class TestGeneratorFunctions:
    @staticmethod
    def test_generator_function():
        r = _parse("function* gen() { yield 1; yield 2; }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "gen" for f in fns)

    @staticmethod
    def test_async_generator():
        r = _parse("async function* asyncGen() { yield 1; }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "asyncGen" for f in fns)

    @staticmethod
    def test_generator_not_code_block():
        r = _parse("function* gen() { yield 1; }")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 0


class TestClassExpressions:
    @staticmethod
    def test_class_expression_captured():
        r = _parse("const MyClass = class extends Base { method() {} };")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "MyClass"
        assert "Base" in cls[0].bases

    @staticmethod
    def test_class_expression_methods():
        r = _parse("const Foo = class { bar() {} };")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Foo.bar"

    @staticmethod
    def test_class_expression_not_property():
        r = _parse("const MyClass = class { };")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0


class TestDestructuring:
    @staticmethod
    def test_object_destructuring_skipped():
        r = _parse("const { a, b } = obj;")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0

    @staticmethod
    def test_array_destructuring_skipped():
        r = _parse("const [x, y] = arr;")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0


class TestFileMeta:
    @staticmethod
    def test_language_is_javascript():
        r = _parse("const x = 1;")
        assert r.language == "javascript"
