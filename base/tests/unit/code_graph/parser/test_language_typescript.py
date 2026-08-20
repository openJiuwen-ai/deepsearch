"""Tests for the TypeScript/TSX language parsers."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import FileNode


def _parse(source: str, ext: str = ".ts") -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=ext, mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _children_by_type(file_node: FileNode, node_type: NodeType) -> list:
    return [c for c in file_node.children if c.node_type == node_type]


def _child_by_name(file_node: FileNode, name: str):
    for c in file_node.children:
        if c.name == name:
            return c
    return None


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


class TestClasses:
    def test_basic_class(self):
        r = _parse("class Foo { }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "Foo"

    def test_class_extends(self):
        r = _parse("class Dog extends Animal { }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert "Animal" in cls.bases

    def test_class_implements(self):
        r = _parse("class Service extends Base implements IService { }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert "Base" in cls.bases
        assert "IService" in cls.bases

    def test_class_methods(self):
        r = _parse("""
class Greeter {
  greet(name: string): string { return name; }
  async fetchData(): Promise<void> { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 2
        assert methods[0].name == "Greeter.greet"
        assert methods[0].func_type == "method"
        assert methods[0].owner == "Greeter"
        assert methods[1].is_async is True

    def test_class_fields(self):
        r = _parse("""
class Config {
  private name: string;
  readonly version: number;
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        props = [c for c in cls.children if c.node_type == NodeType.PROPERTY]
        assert len(props) == 2
        assert props[0].name == "name"
        assert props[0].owner == "Config"

    def test_export_class(self):
        r = _parse("export class Exported { }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "Exported"

    def test_decorators(self):
        r = _parse("""
@Injectable()
class MyService { }
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert len(cls.decorators) > 0


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------


class TestInterfaces:
    def test_basic_interface(self):
        r = _parse("interface Foo { bar(): void; }")
        ifaces = _children_by_type(r, NodeType.INTERFACE)
        assert len(ifaces) == 1
        assert ifaces[0].name == "Foo"

    def test_interface_methods(self):
        r = _parse("""
interface Reader {
  read(buf: Buffer): number;
  close(): void;
}
""")
        iface = _children_by_type(r, NodeType.INTERFACE)[0]
        methods = [c for c in iface.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 2
        assert methods[0].name == "Reader.read"
        assert methods[0].func_type == "method"

    def test_interface_properties(self):
        r = _parse("""
interface Config {
  host: string;
  port: number;
}
""")
        iface = _children_by_type(r, NodeType.INTERFACE)[0]
        props = [c for c in iface.children if c.node_type == NodeType.PROPERTY]
        assert len(props) == 2
        assert props[0].name == "host"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_basic_enum(self):
        r = _parse("enum Color { Red, Green, Blue }")
        enums = _children_by_type(r, NodeType.ENUM)
        assert len(enums) == 1
        assert enums[0].name == "Color"
        assert set(enums[0].members) == {"Red", "Green", "Blue"}

    def test_enum_with_values(self):
        r = _parse("""
enum Direction {
  Up = "UP",
  Down = "DOWN",
}
""")
        enums = _children_by_type(r, NodeType.ENUM)[0]
        assert "Up" in enums.members
        assert "Down" in enums.members

    def test_export_enum(self):
        r = _parse("export enum Status { Active, Inactive }")
        enums = _children_by_type(r, NodeType.ENUM)
        assert len(enums) == 1


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------


class TestTypeAliases:
    def test_basic_type_alias(self):
        r = _parse("type Point = { x: number; y: number };")
        aliases = _children_by_type(r, NodeType.TYPE_ALIAS)
        assert len(aliases) == 1
        assert aliases[0].name == "Point"

    def test_union_type(self):
        r = _parse("type Result = Success | Failure;")
        aliases = _children_by_type(r, NodeType.TYPE_ALIAS)
        assert len(aliases) == 1
        assert aliases[0].name == "Result"


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


class TestFunctions:
    def test_function_declaration(self):
        r = _parse("function greet(name: string): string { return name; }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "greet" for f in fns)
        fn = next(f for f in fns if f.name == "greet")
        assert fn.func_type == "function"

    def test_async_function(self):
        r = _parse("async function fetch(url: string): Promise<Response> { return null as any; }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        fn = next(f for f in fns if f.name == "fetch")
        assert fn.is_async is True

    def test_arrow_function(self):
        r = _parse("const add = (a: number, b: number): number => a + b;")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "add" for f in fns)

    def test_function_expression(self):
        r = _parse("const multiply = function(a: number, b: number) { return a * b; };")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "multiply" for f in fns)

    def test_exported_function(self):
        r = _parse("export function hello() { }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "hello" for f in fns)

    def test_nested_function(self):
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


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_module_variable(self):
        r = _parse("const API_URL: string = 'https://example.com';")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 1
        assert props[0].name == "API_URL"

    def test_variable_without_type(self):
        r = _parse("let count = 0;")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 1
        assert props[0].name == "count"
        assert props[0].default_value == "0"

    def test_arrow_not_captured_as_property(self):
        r = _parse("const fn = () => 1;")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1


# ---------------------------------------------------------------------------
# Code blocks
# ---------------------------------------------------------------------------


class TestCodeBlocks:
    def test_if_statement(self):
        r = _parse("if (true) { console.log(1); }")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) >= 1

    def test_code_between_definitions(self):
        r = _parse("""
function a() { }
console.log("between");
function b() { }
""")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1

    def test_imports_skipped(self):
        r = _parse("import { Foo } from './foo';")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 0


# ---------------------------------------------------------------------------
# TSX
# ---------------------------------------------------------------------------


class TestTsx:
    def test_tsx_class(self):
        r = _parse("class App extends React.Component { render() { return <div />; } }", ext=".tsx")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "App"

    def test_tsx_arrow_component(self):
        r = _parse("const MyComp = () => <div>Hello</div>;", ext=".tsx")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "MyComp" for f in fns)


# ---------------------------------------------------------------------------
# File-level metadata
# ---------------------------------------------------------------------------


class TestAbstractClass:
    def test_abstract_class_parsed(self):
        r = _parse("abstract class Base { abstract method(): void; }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "Base"

    def test_abstract_method_as_member(self):
        r = _parse("abstract class Base { abstract doWork(x: number): string; }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Base.doWork"
        assert methods[0].func_type == "method"

    def test_abstract_not_code_block(self):
        r = _parse("abstract class Base { }")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 0


class TestGeneratorFunctions:
    def test_generator_function(self):
        r = _parse("function* gen() { yield 1; }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert any(f.name == "gen" for f in fns)

    def test_generator_not_code_block(self):
        r = _parse("function* gen() { yield 1; }")
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 0


class TestClassExpressions:
    def test_class_expression_captured(self):
        r = _parse("const MyClass = class { method() {} };")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "MyClass"

    def test_class_expression_not_property(self):
        r = _parse("const MyClass = class { };")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0

    def test_class_expression_with_members(self):
        r = _parse("const Foo = class { bar() { return 1; } };")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Foo.bar"


class TestDestructuring:
    def test_object_destructuring_skipped(self):
        r = _parse("const { a, b } = obj;")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0

    def test_array_destructuring_skipped(self):
        r = _parse("const [x, y] = arr;")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0


class TestFileMeta:
    def test_language_is_typescript(self):
        r = _parse("const x = 1;")
        assert r.language == "typescript"

    def test_language_is_tsx(self):
        r = _parse("const x = 1;", ext=".tsx")
        assert r.language == "tsx"


class TestLocalAnnotations:
    def test_typed_let_inside_function(self):
        r = _parse("function test() { let x: Foo = new Foo(); }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 1
        assert props[0].name == "x@L1@D0"
        assert props[0].type_annotation == "Foo"

    def test_typed_const_inside_function(self):
        r = _parse("function test() { const y: Bar = getBar(); }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 1
        assert props[0].name == "y@L1@D0"
        assert props[0].type_annotation == "Bar"

    def test_untyped_let_not_extracted(self):
        r = _parse("function test() { let x = 5; }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 0

    def test_arrow_function_local_annotations(self):
        r = _parse("const fn = () => { let x: Baz = new Baz(); };")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 1
        assert props[0].name == "x@L1@D0"
        assert props[0].type_annotation == "Baz"

    def test_local_annotation_not_property(self):
        r = _parse("function myFunc() { let x: Foo = new Foo(); }")
        fns = _children_by_type(r, NodeType.FUNCTION)
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 1
        assert props[0].name == "x@L1@D0"
