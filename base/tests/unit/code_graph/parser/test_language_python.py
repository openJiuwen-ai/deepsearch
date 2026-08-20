"""Tests for the Python language parser."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import Parameter, parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import (
    FileNode,
)


def _parse(source: str) -> FileNode:
    """Synchronous helper: write source to a temp file and parse it."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _children_by_type(file_node: FileNode, node_type: NodeType) -> list:
    return [c for c in file_node.children if c.node_type == node_type]


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


class TestFunctions:
    def test_simple_function(self):
        r = _parse("def hello(name):\n    return name\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        assert fns[0].name == "hello"
        assert fns[0].parameters == (Parameter(name="name"),)
        assert fns[0].func_type == "function"
        assert fns[0].owner is None

    def test_async_function(self):
        r = _parse("async def fetch(url):\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        assert fns[0].is_async is True

    def test_decorated_function(self):
        r = _parse("@app.route('/')\ndef index():\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        assert any("app.route" in d for d in fns[0].decorators)

    def test_return_type(self):
        r = _parse("def add(a, b) -> int:\n    return a + b\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].return_type == "int"

    def test_multiple_params(self):
        r = _parse("def f(a, b, c=1, *args, **kwargs):\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].parameters == (
            Parameter(name="a"),
            Parameter(name="b"),
            Parameter(name="c", default="1"),
            Parameter(name="*args"),
            Parameter(name="**kwargs"),
        )


# ---------------------------------------------------------------------------
# Nested functions
# ---------------------------------------------------------------------------


class TestNestedFunctions:
    def test_nested_detected(self):
        r = _parse("def outer():\n    def inner():\n        pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        assert fns[0].name == "outer"
        nested = fns[0].children
        assert len(nested) == 1
        assert nested[0].name == "outer.inner"
        assert nested[0].func_type == "nested"
        assert nested[0].owner == "outer"

    def test_deep_nesting(self):
        src = "def a():\n    def b():\n        def c():\n            pass\n"
        r = _parse(src)
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].name == "a"
        b = fns[0].children[0]
        assert b.name == "a.b"
        c = b.children[0]
        assert c.name == "a.b.c"
        assert c.owner == "a.b"

    def test_no_duplicate_at_top(self):
        r = _parse("def outer():\n    def inner():\n        pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        names = [f.name for f in fns]
        assert "inner" not in names
        assert "outer.inner" not in names


# ---------------------------------------------------------------------------
# Classes and methods
# ---------------------------------------------------------------------------


class TestClasses:
    def test_simple_class(self):
        r = _parse("class Foo:\n    pass\n")
        classes = _children_by_type(r, NodeType.CLASS)
        assert len(classes) == 1
        assert classes[0].name == "Foo"
        assert classes[0].bases == ()

    def test_inheritance(self):
        r = _parse("class Bar(Foo, Mixin):\n    pass\n")
        classes = _children_by_type(r, NodeType.CLASS)
        assert classes[0].bases == ("Foo", "Mixin")

    def test_metaclass(self):
        r = _parse("class Meta(type):\n    pass\n\nclass Foo(metaclass=Meta):\n    pass\n")
        classes = _children_by_type(r, NodeType.CLASS)
        foo = [c for c in classes if c.name == "Foo"][0]
        assert foo.metaclass == "Meta"

    def test_methods_are_children(self):
        src = "class Foo:\n    def bar(self):\n        pass\n    def baz(self):\n        pass\n"
        r = _parse(src)
        classes = _children_by_type(r, NodeType.CLASS)
        methods = [c for c in classes[0].children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 2
        assert methods[0].name == "Foo.bar"
        assert methods[0].func_type == "method"
        assert methods[0].owner == "Foo"

    def test_class_properties(self):
        src = "class Cfg:\n    x = 10\n    y: str = 'hi'\n"
        r = _parse(src)
        classes = _children_by_type(r, NodeType.CLASS)
        props = [c for c in classes[0].children if c.node_type == NodeType.PROPERTY]
        assert len(props) == 2
        assert props[0].owner == "Cfg"

    def test_method_with_nested(self):
        src = "class A:\n    def m(self):\n        def helper():\n            pass\n"
        r = _parse(src)
        cls = _children_by_type(r, NodeType.CLASS)[0]
        method = [c for c in cls.children if c.node_type == NodeType.FUNCTION][0]
        assert method.name == "A.m"
        nested = method.children
        assert len(nested) == 1
        assert nested[0].name == "A.m.helper"
        assert nested[0].func_type == "nested"


# ---------------------------------------------------------------------------
# Interfaces (Protocol)
# ---------------------------------------------------------------------------


class TestInterfaces:
    def test_protocol_detected(self):
        src = (
            "from typing import Protocol\n\nclass Embeddable(Protocol):\n    def embed(self, text: str) -> list: ...\n"
        )
        r = _parse(src)
        interfaces = _children_by_type(r, NodeType.INTERFACE)
        assert len(interfaces) == 1
        assert interfaces[0].name == "Embeddable"
        assert "Protocol" in interfaces[0].bases


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_enum_detected(self):
        src = "from enum import Enum\n\nclass Color(Enum):\n    RED = 1\n    GREEN = 2\n    BLUE = 3\n"
        r = _parse(src)
        enums = _children_by_type(r, NodeType.ENUM)
        assert len(enums) == 1
        assert enums[0].name == "Color"
        assert set(enums[0].members) == {"RED", "GREEN", "BLUE"}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_module_level_assignment(self):
        r = _parse("X = 42\nY: str = 'hello'\n")
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 2
        x = [p for p in props if p.name == "X"][0]
        assert x.default_value == "42"
        assert x.owner is None
        y = [p for p in props if p.name == "Y"][0]
        assert y.type_annotation == "str"


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------


class TestTypeAliases:
    def test_type_statement(self):
        r = _parse("type Vector = list[float]\n")
        aliases = _children_by_type(r, NodeType.TYPE_ALIAS)
        assert len(aliases) == 1
        assert aliases[0].name == "Vector"
        assert aliases[0].aliased_type == "list[float]"


# ---------------------------------------------------------------------------
# DuckType extraction
# ---------------------------------------------------------------------------


class TestDuckTypes:
    def test_single_method(self):
        src = "def run(obj):\n    return obj.embed('x')\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 1
        assert dts[0].methods == frozenset({"embed"})

    def test_multiple_methods(self):
        src = "def run(obj):\n    obj.foo()\n    obj.bar()\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 1
        assert dts[0].methods == frozenset({"foo", "bar"})

    def test_deduplication(self):
        src = "def f1(a):\n    a.x()\n\ndef f2(b):\n    b.x()\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 1

    def test_typed_param_ignored(self):
        src = "def f(obj: Foo):\n    obj.bar()\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 0

    def test_self_cls_ignored(self):
        src = "class X:\n    def m(self):\n        self.foo()\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 0

    def test_duck_type_refs_on_function(self):
        src = "def run(obj):\n    return obj.embed('x')\n"
        r = _parse(src)
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].duck_type_refs == ("DuckType{embed}",)

    def test_distinct_duck_types(self):
        src = "def f1(a):\n    a.x()\n\ndef f2(b):\n    b.x()\n    b.y()\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 2
        method_sets = {dt.methods for dt in dts}
        assert frozenset({"x"}) in method_sets
        assert frozenset({"x", "y"}) in method_sets


# ---------------------------------------------------------------------------
# Docstrings
# ---------------------------------------------------------------------------


class TestDocstrings:
    def test_function_docstring(self):
        src = 'def foo():\n    """Does stuff."""\n    pass\n'
        r = _parse(src)
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].docstring == "Does stuff."

    def test_class_docstring(self):
        src = 'class Foo:\n    """A foo."""\n    pass\n'
        r = _parse(src)
        classes = _children_by_type(r, NodeType.CLASS)
        assert classes[0].docstring == "A foo."


# ---------------------------------------------------------------------------
# Complexity
# ---------------------------------------------------------------------------


class TestComplexity:
    def test_simple_function_complexity_1(self):
        r = _parse("def f():\n    return 1\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].cyclomatic_complexity == 1

    def test_if_adds_complexity(self):
        src = "def f(x):\n    if x > 0:\n        return x\n    return -x\n"
        r = _parse(src)
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].cyclomatic_complexity >= 2


# ---------------------------------------------------------------------------
# Decorated methods
# ---------------------------------------------------------------------------


class TestDecoratedMethods:
    def test_decorated_method(self):
        src = "class Foo:\n    @staticmethod\n    def bar():\n        pass\n"
        r = _parse(src)
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Foo.bar"
        assert any("staticmethod" in d for d in methods[0].decorators)

    def test_decorated_nested_function(self):
        src = "def outer():\n    @wraps(outer)\n    def inner():\n        pass\n"
        r = _parse(src)
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].name == "outer"
        nested = fns[0].children
        assert len(nested) == 1
        assert nested[0].name == "outer.inner"


# ---------------------------------------------------------------------------
# TypeAlias via annotation pattern
# ---------------------------------------------------------------------------


class TestTypeAliasAnnotation:
    def test_typing_type_alias(self):
        src = "from typing import TypeAlias\n\nVector: TypeAlias = list[float]\n"
        r = _parse(src)
        aliases = _children_by_type(r, NodeType.TYPE_ALIAS)
        assert len(aliases) == 1
        assert aliases[0].name == "Vector"
        assert aliases[0].aliased_type == "list[float]"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_tuple_assignment_not_property(self):
        src = "a, b = 1, 2\n"
        r = _parse(src)
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0

    def test_no_params_function(self):
        r = _parse("def f():\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].parameters == ()

    def test_function_no_return_type(self):
        r = _parse("def f():\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].return_type is None

    def test_class_with_no_body_methods(self):
        r = _parse("class Empty:\n    pass\n")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert cls.children == ()


class TestCodeBlocks:
    def test_if_guard_captured(self):
        src = 'if __name__ == "__main__":\n    main()\n'
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert blocks[0].name.endswith("@L1")
        assert blocks[0].signature == 'if __name__ == "__main__":'

    def test_bare_loop(self):
        src = "for x in items:\n    process(x)\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert blocks[0].name.endswith("@L1")
        assert blocks[0].signature == "for x in items:"

    def test_imports_captured_as_code_block(self):
        src = "import os\nfrom sys import argv\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert "import os" in blocks[0].source
        assert "from sys import argv" in blocks[0].source

    def test_imports_still_produce_import_nodes(self):
        src = "import os\nfrom sys import argv\n"
        r = _parse(src)
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) >= 2
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1

    def test_imports_split_by_definition(self):
        src = "import os\n\ndef f():\n    pass\n\nfrom sys import path\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 2
        assert "import os" in blocks[0].source
        assert "from sys import path" in blocks[1].source

    def test_comments_attach_to_import_block(self):
        src = "# setup\nimport os\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert "# setup" in blocks[0].source
        assert "import os" in blocks[0].source

    def test_assignments_not_captured(self):
        src = "x = 1\ny: int = 2\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 0

    def test_assignments_expand_adjacent_code_block(self):
        src = (
            "# bootstrap\n"
            "from widget import Widget\n"
            "\n"
            'DEFAULT_NAME = "demo"\n'
            "active = Widget.create(DEFAULT_NAME)\n"
            "active.run()\n"
            "\n"
            'if __name__ == "__main__":\n'
            '    Widget.create("main")\n'
        )
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert blocks[0].span.line_start == 1
        assert blocks[0].span.line_end == 9
        assert blocks[0].source == src.rstrip()

    def test_consecutive_grouped(self):
        src = "print('a')\nprint('b')\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert "print('a')" in blocks[0].source
        assert "print('b')" in blocks[0].source

    def test_split_by_definitions(self):
        src = "print('before')\n\ndef f():\n    pass\n\nprint('after')\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 2

    def test_code_block_source_complete(self):
        src = "if True:\n    x = 1\n    y = 2\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert "x = 1" in blocks[0].source
        assert "y = 2" in blocks[0].source


# ---------------------------------------------------------------------------
# Nested classes
# ---------------------------------------------------------------------------


class TestNestedClasses:
    def test_nested_class_is_child_of_outer(self):
        r = _parse(
            "class Outer:\n"
            "    class Inner:\n"
            "        def method(self):\n"
            "            pass\n"
            "    def outer_method(self):\n"
            "        pass\n"
        )
        outers = _children_by_type(r, NodeType.CLASS)
        assert len(outers) == 1
        assert outers[0].name == "Outer"
        inner = [c for c in outers[0].children if c.node_type == NodeType.CLASS]
        assert len(inner) == 1
        assert inner[0].name == "Inner"

    def test_nested_class_not_at_top_level(self):
        r = _parse("class Outer:\n    class Inner:\n        pass\n")
        top_classes = _children_by_type(r, NodeType.CLASS)
        names = [c.name for c in top_classes]
        assert "Inner" not in names

    def test_nested_class_methods(self):
        r = _parse("class Outer:\n    class Inner:\n        def do_stuff(self):\n            pass\n")
        outer = _children_by_type(r, NodeType.CLASS)[0]
        inner = [c for c in outer.children if c.node_type == NodeType.CLASS][0]
        methods = [c for c in inner.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Inner.do_stuff"

    def test_class_inside_function_not_top_level(self):
        r = _parse("def factory():\n    class Product:\n        pass\n    return Product\n")
        top_classes = _children_by_type(r, NodeType.CLASS)
        assert len(top_classes) == 0

    def test_decorated_nested_class(self):
        r = _parse("class Outer:\n    @some_decorator\n    class Inner:\n        pass\n")
        outer = _children_by_type(r, NodeType.CLASS)[0]
        inner = [c for c in outer.children if c.node_type == NodeType.CLASS]
        assert len(inner) == 1
        assert inner[0].name == "Inner"


# ---------------------------------------------------------------------------
# Property getter/setter/deleter
# ---------------------------------------------------------------------------


class TestPropertyDecorators:
    def test_setter_has_unique_name(self):
        r = _parse(
            "class Foo:\n"
            "    @property\n"
            "    def bar(self):\n"
            "        return self._bar\n"
            "    @bar.setter\n"
            "    def bar(self, v):\n"
            "        self._bar = v\n"
        )
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        names = [m.name for m in methods]
        assert len(names) == len(set(names))
        assert "Foo.bar" in names
        assert "Foo.bar.setter" in names

    def test_deleter_has_unique_name(self):
        r = _parse(
            "class Foo:\n"
            "    @property\n"
            "    def bar(self): ...\n"
            "    @bar.setter\n"
            "    def bar(self, v): ...\n"
            "    @bar.deleter\n"
            "    def bar(self): ...\n"
        )
        cls = _children_by_type(r, NodeType.CLASS)[0]
        names = [c.name for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(set(names)) == 3
        assert "Foo.bar.deleter" in names


# ---------------------------------------------------------------------------
# Overloaded functions
# ---------------------------------------------------------------------------


class TestOverloads:
    def test_overloaded_functions_have_unique_names(self):
        r = _parse(
            "from typing import overload\n"
            "@overload\n"
            "def process(x: int) -> int: ...\n"
            "@overload\n"
            "def process(x: str) -> str: ...\n"
            "def process(x):\n"
            "    return x\n"
        )
        fns = _children_by_type(r, NodeType.FUNCTION)
        names = [f.name for f in fns]
        assert len(names) == len(set(names))
        assert "process" in names
        assert any("[" in n for n in names)

    def test_overloaded_name_contains_types(self):
        r = _parse(
            "from typing import overload\n@overload\ndef process(x: int) -> int: ...\ndef process(x):\n    return x\n"
        )
        fns = _children_by_type(r, NodeType.FUNCTION)
        overloaded = [f for f in fns if "@overload" in f.decorators]
        assert len(overloaded) == 1
        assert "int" in overloaded[0].name


# ---------------------------------------------------------------------------
# Local annotations
# ---------------------------------------------------------------------------


class TestLocalAnnotations:
    def test_bare_annotation_inside_function(self):
        r = _parse("def func(obj):\n    obj: Foo\n    return obj\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 1
        assert props[0].name == "obj"
        assert props[0].type_annotation == "Foo"

    def test_annotated_assignment_inside_function(self):
        r = _parse("def func():\n    x: int = 5\n    return x\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 1
        assert props[0].name == "x"
        assert props[0].type_annotation == "int"
        assert props[0].default_value == "5"

    def test_unannotated_assignment_not_extracted(self):
        r = _parse("def func():\n    x = 5\n    return x\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 0

    def test_local_annotation_owner_is_function(self):
        r = _parse("def my_func(obj):\n    obj: Bar\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert props[0].name == "obj"

    def test_call_arguments_and_assign_target(self):
        r = _parse("from functools import partial\ndef func():\n    make = partial(Foo)\n    obj = make()\n")
        from openjiuwen_search_base.codegraph.parser.models.core import CallNode

        calls = [c for c in r.children if isinstance(c, CallNode)]
        partial_call = next(c for c in calls if c.callee == "partial")
        assert partial_call.arguments == ("Foo",)
        assert partial_call.assign_target == "make"

        make_call = next(c for c in calls if c.callee == "make")
        assert make_call.assign_target == "obj"


# ---------------------------------------------------------------------------
# Lambdas
# ---------------------------------------------------------------------------


class TestLambdas:
    def test_module_level_assigned(self):
        r = _parse("f = lambda x: x\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name == "lambda(x)@L1@C5"
        assert lambdas[0].owner is None
        assert lambdas[0].parameters == (Parameter(name="x"),)

    def test_nested_under_function(self):
        r = _parse("def outer():\n    h = lambda a, b: a + b\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        outer = next(f for f in fns if f.name == "outer")
        lambdas = [c for c in outer.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name.startswith("lambda(a, b)@L2@C")
        assert lambdas[0].owner == "outer"

    def test_inline_no_assignment(self):
        r = _parse("(lambda a, b: a + b)(1, 2)\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name.startswith("lambda(a, b)@L1@C")
        assert lambdas[0].func_type == "lambda"

    def test_same_line_column_differs(self):
        r = _parse("map(lambda x: x, filter(lambda y: y, xs))\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 2
        names = {ln.name for ln in lambdas}
        assert names == {"lambda(x)@L1@C5", "lambda(y)@L1@C25"}

    def test_star_and_kwargs_in_name(self):
        r = _parse("f = lambda x, y=1, *args, **kwargs: x\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name == "lambda(x, y, *args, **kwargs)@L1@C5"
        assert [p.name for p in lambdas[0].parameters] == ["x", "y", "*args", "**kwargs"]

    def test_empty_params(self):
        r = _parse("f = lambda: None\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name == "lambda()@L1@C5"

    def test_call_inside_lambda_context(self):
        r = _parse("def outer():\n    return lambda: foo()\n")
        from openjiuwen_search_base.codegraph.parser.models.core import CallNode

        calls = [c for c in r.children if isinstance(c, CallNode)]
        foo_calls = [c for c in calls if c.callee == "foo"]
        assert len(foo_calls) == 1
        assert foo_calls[0].context is not None
        assert foo_calls[0].context.startswith("lambda()@L2@C")

    def test_method_body_lambda(self):
        r = _parse("class Foo:\n    def bar(self):\n        return lambda x: x\n")
        classes = _children_by_type(r, NodeType.CLASS)
        methods = [c for c in classes[0].children if c.node_type == NodeType.FUNCTION]
        bar = next(m for m in methods if m.name == "Foo.bar")
        lambdas = [c for c in bar.children if c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].owner == "Foo.bar"
        assert lambdas[0].name.startswith("lambda(x)@L3@C")
