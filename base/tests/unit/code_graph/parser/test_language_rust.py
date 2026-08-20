"""Tests for the Rust language parser."""

import asyncio
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph.parser.constants import EdgeType, detect_language
from openjiuwen_search_base.codegraph.parser.languages import get_default_registry, register_builtins
from openjiuwen_search_base.codegraph.parser.languages.rust import RustHooks, RustParser
from openjiuwen_search_base.codegraph.parser.models.core import (
    CallNode,
    FunctionNode,
    ImportNode,
    InterfaceNode,
    LocalVarNode,
    PropertyNode,
)
from openjiuwen_search_base.codegraph.parser.models.extensions import (
    EnumNode,
    ModuleNode,
    StructNode,
    TypeAliasNode,
)
from openjiuwen_search_base.codegraph.parser.resolver import resolve_graph

register_builtins()


@pytest.fixture
def parser():
    return RustParser()


def _parse(parser, code: str):
    return asyncio.run(parser.parse(Path("test.rs"), code.encode()))


class TestRustDetect:
    def test_rs_extension(self):
        assert detect_language("main.rs") == "rust"

    def test_registry(self):
        reg = get_default_registry()
        assert reg.get("rust") is not None
        assert isinstance(reg.get_hooks("rust"), RustHooks)


class TestRustImports:
    def test_simple_and_glob_and_group(self, parser):
        code = """
        use std::collections::HashMap;
        use crate::foo::{Bar, Baz as Qux};
        use super::*;
        """
        fnode = _parse(parser, code)
        imports = [c for c in fnode.children if isinstance(c, ImportNode)]
        assert any(i.name == "HashMap" and "collections" in i.module for i in imports)
        assert any(i.name == "Bar" for i in imports)
        assert any(i.name == "Qux" and i.alias == "Qux" for i in imports)
        assert any(i.is_wildcard for i in imports)


class TestRustTypes:
    def test_struct_fields(self, parser):
        code = """
        #[derive(Debug)]
        pub struct Point {
            pub x: i32,
            y: f64,
        }
        """
        fnode = _parse(parser, code)
        structs = [c for c in fnode.children if isinstance(c, StructNode)]
        assert len(structs) == 1
        assert structs[0].name == "Point"
        assert {f.name for f in structs[0].fields} == {"x", "y"}

    def test_enum_variants(self, parser):
        code = """
        pub enum Color {
            Red,
            Green(i32),
            Blue { a: u8 },
        }
        """
        fnode = _parse(parser, code)
        enums = [c for c in fnode.children if isinstance(c, EnumNode)]
        assert len(enums) == 1
        assert enums[0].members == ("Red", "Green", "Blue")

    def test_trait_and_impl(self, parser):
        code = """
        pub trait Drawable: Clone {
            fn draw(&self);
        }
        pub struct Point { x: i32 }
        impl Point {
            pub fn new(x: i32) -> Self { Self { x } }
        }
        impl Drawable for Point {
            fn draw(&self) {}
        }
        """
        fnode = _parse(parser, code)
        traits = [c for c in fnode.children if isinstance(c, InterfaceNode)]
        assert len(traits) == 1
        assert traits[0].name == "Drawable"
        assert "Clone" in traits[0].bases
        assert any(isinstance(m, FunctionNode) and m.name.endswith(".draw") for m in traits[0].children)

        methods = [c for c in fnode.children if isinstance(c, FunctionNode) and c.owner == "Point"]
        names = {m.name for m in methods}
        assert "Point.new" in names
        assert "Point.draw" in names

        point = next(c for c in fnode.children if isinstance(c, StructNode) and c.name == "Point")
        assert "Drawable" in point.bases

    def test_mod_type_const(self, parser):
        code = """
        mod inner {
            pub fn helper() {}
        }
        type Id = u64;
        const MAX: i32 = 10;
        static mut COUNT: i32 = 0;
        """
        fnode = _parse(parser, code)
        assert any(isinstance(c, ModuleNode) and c.name == "inner" for c in fnode.children)
        assert any(isinstance(c, TypeAliasNode) and c.name == "Id" for c in fnode.children)
        props = [c for c in fnode.children if isinstance(c, PropertyNode)]
        assert {p.name for p in props} >= {"MAX", "COUNT"}


class TestRustFunctionsAndCalls:
    def test_free_fn_locals_and_calls(self, parser):
        code = """
        pub struct Point { x: i32 }
        impl Point {
            fn area(&self) -> i32 { self.x }
        }
        pub async fn run(p: &Point) {
            let mut m: Vec<i32> = Vec::new();
            p.area();
            Point::new();
        }
        """
        fnode = _parse(parser, code)
        run = next(c for c in fnode.children if isinstance(c, FunctionNode) and c.name == "run")
        assert run.is_async
        locals_ = [c for c in run.children if isinstance(c, LocalVarNode)]
        assert any(c.name.startswith("m@L") and c.type_annotation == "Vec<i32>" for c in locals_)

        calls = [c for c in fnode.children if isinstance(c, CallNode)]
        assert any(c.callee == "area" and c.receiver == "p" for c in calls)
        assert any(c.callee == "new" and c.receiver == "Point" for c in calls)
        assert any(c.callee == "new" and c.receiver == "Vec" for c in calls)


class TestRustHooks:
    def test_extract_and_unwrap(self):
        hooks = RustHooks()
        assert "Point" in hooks.extract_type_names("&'a mut Point")
        assert hooks.unwrap_receiver_type("Vec<Point>", 1) == "Point"
        mods = hooks.detect_modules("crate/util", frozenset({"mod.rs"}), "/tmp")
        assert mods and mods[0].language == "rust"
        assert mods[0].name == "crate::util"


class TestRustResolverSmoke:
    def test_implements_overrides_calls(self, parser):
        code = """
        pub trait Drawable {
            fn draw(&self);
        }
        pub struct Point { x: i32 }
        impl Drawable for Point {
            fn draw(&self) {}
        }
        pub fn main() {
            let p: Point = Point { x: 1 };
            p.draw();
        }
        """
        fnode = _parse(parser, code)
        edges, _, _ = resolve_graph([fnode])
        relations = {e.relation for e in edges}
        assert EdgeType.IMPLEMENTS in relations
        assert EdgeType.OVERRIDES in relations
        implements = [e for e in edges if e.relation == EdgeType.IMPLEMENTS]
        assert any("Point" in e.source_id and "Drawable" in e.target_id for e in implements)
        overrides = [e for e in edges if e.relation == EdgeType.OVERRIDES]
        assert any("draw" in e.source_id and "Drawable" in e.target_id for e in overrides)
