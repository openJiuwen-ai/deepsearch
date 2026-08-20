"""Tests for C++ language parser."""

import asyncio
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.languages.c import CppParser
from openjiuwen_search_base.codegraph.parser.models.core import (
    CallNode,
    ClassNode,
    FunctionNode,
    PropertyNode,
)
from openjiuwen_search_base.codegraph.parser.models.extensions import (
    EnumNode,
    ModuleNode,
    StructNode,
    TypeAliasNode,
)


@pytest.fixture
def parser():
    return CppParser()


def _parse(parser, code: str):
    return asyncio.run(parser.parse(Path("test.cpp"), code.encode()))


class TestCppClasses:
    def test_basic_class(self, parser):
        code = """
        class Widget {
        public:
            void draw() {}
        private:
            int x_;
        };
        """
        result = _parse(parser, code)
        classes = [c for c in result.children if isinstance(c, ClassNode)]
        assert len(classes) == 1
        assert classes[0].name == "Widget"

    def test_inheritance(self, parser):
        code = """
        class Base {};
        class Derived : public Base {
            void method() {}
        };
        """
        result = _parse(parser, code)
        classes = [c for c in result.children if isinstance(c, ClassNode)]
        derived = next(c for c in classes if c.name == "Derived")
        assert "Base" in derived.bases

    def test_multiple_inheritance(self, parser):
        code = """
        class A {};
        class B {};
        class C : public A, public B {
            void foo() {}
        };
        """
        result = _parse(parser, code)
        classes = [c for c in result.children if isinstance(c, ClassNode)]
        c_cls = next(c for c in classes if c.name == "C")
        assert "A" in c_cls.bases
        assert "B" in c_cls.bases

    def test_class_members(self, parser):
        code = """
        class Foo {
        public:
            void method() {}
            int field;
        };
        """
        result = _parse(parser, code)
        cls = [c for c in result.children if isinstance(c, ClassNode)][0]
        methods = [m for m in cls.children if isinstance(m, FunctionNode)]
        fields = [m for m in cls.children if isinstance(m, PropertyNode)]
        assert len(methods) == 1
        assert methods[0].name == "Foo.method"
        assert methods[0].owner == "Foo"
        assert len(fields) == 1

    def test_access_specifiers(self, parser):
        code = """
        class Foo {
        public:
            void pub_method() {}
        private:
            void priv_method() {}
        };
        """
        result = _parse(parser, code)
        cls = [c for c in result.children if isinstance(c, ClassNode)][0]
        methods = [m for m in cls.children if isinstance(m, FunctionNode)]
        pub = next(m for m in methods if "pub_method" in m.name)
        priv = next(m for m in methods if "priv_method" in m.name)
        assert "@public" in pub.decorators
        assert "@private" in priv.decorators


class TestCppConstructors:
    def test_constructor(self, parser):
        code = """
        class Foo {
        public:
            Foo() {}
        };
        """
        result = _parse(parser, code)
        cls = [c for c in result.children if isinstance(c, ClassNode)][0]
        ctors = [m for m in cls.children if isinstance(m, FunctionNode) and "<init>" in m.name]
        assert len(ctors) == 1
        assert ctors[0].name == "Foo.<init>"

    def test_overloaded_constructors(self, parser):
        code = """
        class Foo {
        public:
            Foo() {}
            Foo(int n) {}
            Foo(int n, double d) {}
        };
        """
        result = _parse(parser, code)
        cls = [c for c in result.children if isinstance(c, ClassNode)][0]
        ctors = [m for m in cls.children if isinstance(m, FunctionNode) and "<init>" in m.name]
        assert len(ctors) == 3
        names = {c.name for c in ctors}
        assert len(names) == 3

    def test_destructor(self, parser):
        code = """
        class Foo {
        public:
            ~Foo() {}
        };
        """
        result = _parse(parser, code)
        cls = [c for c in result.children if isinstance(c, ClassNode)][0]
        dtors = [m for m in cls.children if isinstance(m, FunctionNode) and "<destroy>" in m.name]
        assert len(dtors) == 1
        assert dtors[0].name == "Foo.<destroy>"


class TestCppNamespaces:
    def test_basic_namespace(self, parser):
        code = """
        namespace mylib {
            void helper() {}
        }
        """
        result = _parse(parser, code)
        modules = [c for c in result.children if isinstance(c, ModuleNode)]
        assert len(modules) == 1
        assert modules[0].name == "mylib"
        funcs = [c for c in modules[0].children if isinstance(c, FunctionNode)]
        assert len(funcs) == 1

    def test_nested_namespace(self, parser):
        code = """
        namespace outer {
            namespace inner {
                class Foo {};
            }
        }
        """
        result = _parse(parser, code)
        outer = [c for c in result.children if isinstance(c, ModuleNode)][0]
        assert outer.name == "outer"
        inner = [c for c in outer.children if isinstance(c, ModuleNode)]
        assert len(inner) == 1
        assert inner[0].name == "inner"


class TestCppTemplates:
    def test_template_class(self, parser):
        code = """
        template<typename T>
        class Container {
        public:
            void push(T item) {}
        };
        """
        result = _parse(parser, code)
        classes = [c for c in result.children if isinstance(c, ClassNode)]
        assert len(classes) == 1
        assert classes[0].name == "Container"

    def test_template_function(self, parser):
        code = """
        template<typename T>
        T identity(T x) { return x; }
        """
        result = _parse(parser, code)
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        assert len(funcs) == 1
        assert funcs[0].name == "identity"


class TestCppLambdas:
    def test_lambda_assignment(self, parser):
        code = "auto helper = [](int x) -> int { return x * 2; };"
        result = _parse(parser, code)
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        assert len(funcs) == 1
        assert funcs[0].name == "helper"
        assert funcs[0].func_type == "lambda"


class TestCppCalls:
    def test_member_call(self, parser):
        code = """
        class Foo {
        public:
            void method() {
                obj.doSomething();
            }
        };
        """
        result = _parse(parser, code)
        calls = [c for c in result.children if isinstance(c, CallNode)]
        assert len(calls) >= 1
        assert calls[0].callee == "doSomething"
        assert calls[0].receiver == "obj"

    def test_scoped_call(self, parser):
        code = """
        void func() {
            std::sort(v.begin(), v.end());
        }
        """
        result = _parse(parser, code)
        calls = [c for c in result.children if isinstance(c, CallNode)]
        sort_calls = [c for c in calls if c.callee == "sort"]
        assert len(sort_calls) == 1
        assert sort_calls[0].receiver == "std"


class TestCppUsingAlias:
    def test_using_alias(self, parser):
        code = "using Vec3 = std::array<double, 3>;"
        result = _parse(parser, code)
        aliases = [c for c in result.children if isinstance(c, TypeAliasNode)]
        assert len(aliases) == 1
        assert aliases[0].name == "Vec3"


class TestCppEnumClass:
    def test_enum_class(self, parser):
        code = "enum class Direction { North, South, East, West };"
        result = _parse(parser, code)
        enums = [c for c in result.children if isinstance(c, EnumNode)]
        assert len(enums) == 1
        assert enums[0].name == "Direction"
        assert "North" in enums[0].members


class TestCppFileNode:
    def test_language(self, parser):
        result = _parse(parser, "int x;")
        assert result.language == "cpp"
        assert result.node_type == NodeType.FILE

    def test_struct_in_cpp(self, parser):
        code = """
        struct Point {
            double x;
            double y;
        };
        """
        result = _parse(parser, code)
        structs = [c for c in result.children if isinstance(c, StructNode)]
        assert len(structs) == 1
        assert structs[0].name == "Point"


class TestCppVirtualOverride:
    def test_virtual_method(self, parser):
        code = """
        class Base {
        public:
            virtual void draw() {}
        };
        """
        result = _parse(parser, code)
        cls = [c for c in result.children if isinstance(c, ClassNode)][0]
        methods = [m for m in cls.children if isinstance(m, FunctionNode)]
        assert "@virtual" in methods[0].decorators


class TestCppOutOfClass:
    def test_out_of_class_method(self, parser):
        code = """
        class MyCamera {
        public:
            void renderFrame();
            void updateRays();
        };

        void MyCamera::renderFrame() {
            updateRays();
        }

        void MyCamera::updateRays() {
        }
        """
        result = _parse(parser, code)
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        assert len(funcs) == 2
        render = next(f for f in funcs if "renderFrame" in f.name)
        assert render.name == "MyCamera.renderFrame"
        assert render.owner == "MyCamera"
        assert render.func_type == "method"

    def test_out_of_class_call_context(self, parser):
        code = """
        class Foo {
        public:
            void bar();
        };

        void Foo::bar() {
            helper();
        }
        """
        result = _parse(parser, code)
        calls = [c for c in result.children if isinstance(c, CallNode)]
        helper_calls = [c for c in calls if c.callee == "helper"]
        assert len(helper_calls) == 1
        assert helper_calls[0].context == "bar"

    def test_out_of_class_destructor(self, parser):
        code = """
        class Foo {
        public:
            ~Foo();
        };

        Foo::~Foo() {}
        """
        result = _parse(parser, code)
        funcs = [c for c in result.children if isinstance(c, FunctionNode)]
        assert len(funcs) == 1
        assert funcs[0].name == "Foo.<destroy>"
        assert funcs[0].owner == "Foo"
