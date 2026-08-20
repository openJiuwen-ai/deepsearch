"""Tests for the Go language parser."""

import asyncio
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph.parser.constants import EdgeType, detect_language
from openjiuwen_search_base.codegraph.parser.languages import get_default_registry, register_builtins
from openjiuwen_search_base.codegraph.parser.languages.go import GoHooks, GoParser
from openjiuwen_search_base.codegraph.parser.models.core import (
    CallNode,
    FunctionNode,
    ImportNode,
    InterfaceNode,
    LocalVarNode,
    PropertyNode,
)
from openjiuwen_search_base.codegraph.parser.models.extensions import ModuleNode, StructNode, TypeAliasNode
from openjiuwen_search_base.codegraph.parser.resolver import resolve_graph

register_builtins()


@pytest.fixture
def parser():
    return GoParser()


def _parse(parser, code: str):
    return asyncio.run(parser.parse(Path("test.go"), code.encode()))


class TestGoDetect:
    @staticmethod
    def test_go_extension():
        assert detect_language("main.go") == "go"

    @staticmethod
    def test_registry():
        reg = get_default_registry()
        assert reg.get("go") is not None
        assert isinstance(reg.get_hooks("go"), GoHooks)


class TestGoPackageAndImports:
    @staticmethod
    def test_package_and_import_variants(parser):
        code = """
        package demo
        import (
            "fmt"
            m "math"
            . "embed"
            _ "unsafe"
        )
        """
        fnode = _parse(parser, code)
        assert any(isinstance(c, ModuleNode) and c.name == "demo" for c in fnode.children)
        imports = [c for c in fnode.children if isinstance(c, ImportNode)]
        assert any(i.name == "fmt" and i.module == "fmt" for i in imports)
        assert any(i.name == "m" and i.alias == "m" for i in imports)
        assert any(i.is_wildcard for i in imports)
        assert any(i.name == "_" and i.module == "unsafe" for i in imports)


class TestGoTypes:
    @staticmethod
    def test_struct_methods_embedding(parser):
        code = """
        package demo
        type Embedded struct { N int }
        type Point struct {
            X, Y int
            *Embedded
        }
        func (p *Point) Area() int { return p.X * p.Y }
        """
        fnode = _parse(parser, code)
        point = next(c for c in fnode.children if isinstance(c, StructNode) and c.name == "Point")
        assert {f.name for f in point.fields} == {"X", "Y"}
        assert "Embedded" in point.bases
        methods = [c for c in fnode.children if isinstance(c, FunctionNode) and c.owner == "Point"]
        assert any(m.name == "Point.Area" for m in methods)

    @staticmethod
    def test_interface_and_aliases(parser):
        code = """
        package demo
        type Drawable interface {
            Draw()
            fmt.Stringer
        }
        type Id = int64
        type MyInt int
        """
        fnode = _parse(parser, code)
        iface = next(c for c in fnode.children if isinstance(c, InterfaceNode))
        assert iface.name == "Drawable"
        assert "Stringer" in iface.bases
        assert any(isinstance(m, FunctionNode) and m.name.endswith(".Draw") for m in iface.children)
        aliases = {c.name: c for c in fnode.children if isinstance(c, TypeAliasNode)}
        assert "Id" in aliases and aliases["Id"].aliased_type == "int64"
        assert "MyInt" in aliases


class TestGoFuncsCallsLocals:
    @staticmethod
    def test_free_fn_calls_and_typed_var(parser):
        code = """
        package demo
        import "fmt"
        type Point struct { X int }
        func (p *Point) Area() int { return p.X }
        func New(x int) *Point {
            var z int = 1
            p := &Point{X: x}
            p.Area()
            fmt.Println(p)
            return p
        }
        """
        fnode = _parse(parser, code)
        new = next(c for c in fnode.children if isinstance(c, FunctionNode) and c.name == "New")
        locals_ = [c for c in new.children if isinstance(c, LocalVarNode)]
        assert any(c.name.startswith("z@L") and c.type_annotation == "int" for c in locals_)
        calls = [c for c in fnode.children if isinstance(c, CallNode)]
        assert any(c.callee == "Area" and c.receiver == "p" for c in calls)
        assert any(c.callee == "Println" and c.receiver == "fmt" for c in calls)

    @staticmethod
    def test_const_var(parser):
        code = """
        package demo
        const Max = 10
        var Count int = 0
        """
        fnode = _parse(parser, code)
        props = {c.name: c for c in fnode.children if isinstance(c, PropertyNode)}
        assert "Max" in props
        assert props["Count"].type_annotation == "int"


class TestGoHooks:
    @staticmethod
    def test_extract_unwrap_modules():
        hooks = GoHooks()
        assert "Point" in hooks.extract_type_names("*Point")
        assert "Foo" in hooks.extract_type_names("[]pkg.Foo")
        assert hooks.unwrap_receiver_type("[]Point", 1) == "Point"
        mods = hooks.detect_modules("client", frozenset({"client.go"}), "/tmp/sdk")
        assert mods and mods[0].language == "go"
        assert mods[0].name == "client"


class TestGoResolverSmoke:
    @staticmethod
    def test_embedding_implements_and_calls(parser):
        code = """
        package demo
        type Reader interface {
            Read() int
        }
        type File struct {
            Reader
        }
        func (f *File) Read() int { return 1 }
        func main() {
            var f File
            f.Read()
        }
        """
        fnode = _parse(parser, code)
        edges, _, _ = resolve_graph([fnode])
        relations = {e.relation for e in edges}
        assert EdgeType.IMPLEMENTS in relations
        implements = [e for e in edges if e.relation == EdgeType.IMPLEMENTS]
        assert any("File" in e.source_id and "Reader" in e.target_id for e in implements)
