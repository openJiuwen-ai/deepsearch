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
    @staticmethod
    def test_simple_function():
        r = _parse("def hello(name):\n    return name\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        assert fns[0].name == "hello"
        assert fns[0].parameters == (Parameter(name="name"),)
        assert fns[0].func_type == "function"
        assert fns[0].owner is None

    @staticmethod
    def test_async_function():
        r = _parse("async def fetch(url):\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        assert fns[0].is_async is True

    @staticmethod
    def test_decorated_function():
        r = _parse("@app.route('/')\ndef index():\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        assert any("app.route" in d for d in fns[0].decorators)

    @staticmethod
    def test_return_type():
        r = _parse("def add(a, b) -> int:\n    return a + b\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].return_type == "int"

    @staticmethod
    def test_multiple_params():
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
    @staticmethod
    def test_nested_detected():
        r = _parse("def outer():\n    def inner():\n        pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        assert fns[0].name == "outer"
        nested = fns[0].children
        assert len(nested) == 1
        assert nested[0].name == "outer.inner"
        assert nested[0].func_type == "nested"
        assert nested[0].owner == "outer"

    @staticmethod
    def test_deep_nesting():
        src = "def a():\n    def b():\n        def c():\n            pass\n"
        r = _parse(src)
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].name == "a"
        b = fns[0].children[0]
        assert b.name == "a.b"
        c = b.children[0]
        assert c.name == "a.b.c"
        assert c.owner == "a.b"

    @staticmethod
    def test_no_duplicate_at_top():
        r = _parse("def outer():\n    def inner():\n        pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        names = [f.name for f in fns]
        assert "inner" not in names
        assert "outer.inner" not in names


# ---------------------------------------------------------------------------
# Classes and methods
# ---------------------------------------------------------------------------


class TestClasses:
    @staticmethod
    def test_simple_class():
        r = _parse("class Foo:\n    pass\n")
        classes = _children_by_type(r, NodeType.CLASS)
        assert len(classes) == 1
        assert classes[0].name == "Foo"
        assert classes[0].bases == ()

    @staticmethod
    def test_inheritance():
        r = _parse("class Bar(Foo, Mixin):\n    pass\n")
        classes = _children_by_type(r, NodeType.CLASS)
        assert classes[0].bases == ("Foo", "Mixin")

    @staticmethod
    def test_metaclass():
        r = _parse("class Meta(type):\n    pass\n\nclass Foo(metaclass=Meta):\n    pass\n")
        classes = _children_by_type(r, NodeType.CLASS)
        foo = [c for c in classes if c.name == "Foo"][0]
        assert foo.metaclass == "Meta"

    @staticmethod
    def test_methods_are_children():
        src = "class Foo:\n    def bar(self):\n        pass\n    def baz(self):\n        pass\n"
        r = _parse(src)
        classes = _children_by_type(r, NodeType.CLASS)
        methods = [c for c in classes[0].children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 2
        assert methods[0].name == "Foo.bar"
        assert methods[0].func_type == "method"
        assert methods[0].owner == "Foo"

    @staticmethod
    def test_class_properties():
        src = "class Cfg:\n    x = 10\n    y: str = 'hi'\n"
        r = _parse(src)
        classes = _children_by_type(r, NodeType.CLASS)
        props = [c for c in classes[0].children if c.node_type == NodeType.PROPERTY]
        assert len(props) == 2
        assert props[0].owner == "Cfg"

    @staticmethod
    def test_method_with_nested():
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
    @staticmethod
    def test_protocol_detected():
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
    @staticmethod
    def test_enum_detected():
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
    @staticmethod
    def test_module_level_assignment():
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
    @staticmethod
    def test_type_statement():
        r = _parse("type Vector = list[float]\n")
        aliases = _children_by_type(r, NodeType.TYPE_ALIAS)
        assert len(aliases) == 1
        assert aliases[0].name == "Vector"
        assert aliases[0].aliased_type == "list[float]"


# ---------------------------------------------------------------------------
# DuckType extraction
# ---------------------------------------------------------------------------


class TestDuckTypes:
    @staticmethod
    def test_single_method():
        src = "def run(obj):\n    return obj.embed('x')\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 1
        assert dts[0].methods == frozenset({"embed"})

    @staticmethod
    def test_multiple_methods():
        src = "def run(obj):\n    obj.foo()\n    obj.bar()\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 1
        assert dts[0].methods == frozenset({"foo", "bar"})

    @staticmethod
    def test_deduplication():
        src = "def f1(a):\n    a.x()\n\ndef f2(b):\n    b.x()\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 1

    @staticmethod
    def test_typed_param_ignored():
        src = "def f(obj: Foo):\n    obj.bar()\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 0

    @staticmethod
    def test_self_cls_ignored():
        src = "class X:\n    def m(self):\n        self.foo()\n"
        r = _parse(src)
        dts = _children_by_type(r, NodeType.DUCK_TYPE)
        assert len(dts) == 0

    @staticmethod
    def test_duck_type_refs_on_function():
        src = "def run(obj):\n    return obj.embed('x')\n"
        r = _parse(src)
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].duck_type_refs == ("DuckType{embed}",)

    @staticmethod
    def test_distinct_duck_types():
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
    @staticmethod
    def test_function_docstring():
        src = 'def foo():\n    """Does stuff."""\n    pass\n'
        r = _parse(src)
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].docstring == "Does stuff."

    @staticmethod
    def test_class_docstring():
        src = 'class Foo:\n    """A foo."""\n    pass\n'
        r = _parse(src)
        classes = _children_by_type(r, NodeType.CLASS)
        assert classes[0].docstring == "A foo."


# ---------------------------------------------------------------------------
# Complexity
# ---------------------------------------------------------------------------


class TestComplexity:
    @staticmethod
    def test_simple_function_complexity_1():
        r = _parse("def f():\n    return 1\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].cyclomatic_complexity == 1

    @staticmethod
    def test_if_adds_complexity():
        src = "def f(x):\n    if x > 0:\n        return x\n    return -x\n"
        r = _parse(src)
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].cyclomatic_complexity >= 2


# ---------------------------------------------------------------------------
# Decorated methods
# ---------------------------------------------------------------------------


class TestDecoratedMethods:
    @staticmethod
    def test_decorated_method():
        src = "class Foo:\n    @staticmethod\n    def bar():\n        pass\n"
        r = _parse(src)
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Foo.bar"
        assert any("staticmethod" in d for d in methods[0].decorators)

    @staticmethod
    def test_decorated_nested_function():
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
    @staticmethod
    def test_typing_type_alias():
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
    @staticmethod
    def test_tuple_assignment_not_property():
        src = "a, b = 1, 2\n"
        r = _parse(src)
        props = _children_by_type(r, NodeType.PROPERTY)
        assert len(props) == 0

    @staticmethod
    def test_no_params_function():
        r = _parse("def f():\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].parameters == ()

    @staticmethod
    def test_function_no_return_type():
        r = _parse("def f():\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert fns[0].return_type is None

    @staticmethod
    def test_class_with_no_body_methods():
        r = _parse("class Empty:\n    pass\n")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert cls.children == ()


class TestCodeBlocks:
    @staticmethod
    def test_if_guard_captured():
        src = 'if __name__ == "__main__":\n    main()\n'
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert blocks[0].name.endswith("@L1")
        assert blocks[0].signature == 'if __name__ == "__main__":'

    @staticmethod
    def test_bare_loop():
        src = "for x in items:\n    process(x)\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert blocks[0].name.endswith("@L1")
        assert blocks[0].signature == "for x in items:"

    @staticmethod
    def test_imports_captured_as_code_block():
        src = "import os\nfrom sys import argv\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert "import os" in blocks[0].source
        assert "from sys import argv" in blocks[0].source

    @staticmethod
    def test_imports_still_produce_import_nodes():
        src = "import os\nfrom sys import argv\n"
        r = _parse(src)
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) >= 2
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1

    @staticmethod
    def test_imports_split_by_definition():
        src = "import os\n\ndef f():\n    pass\n\nfrom sys import path\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 2
        assert "import os" in blocks[0].source
        assert "from sys import path" in blocks[1].source

    @staticmethod
    def test_comments_attach_to_import_block():
        src = "# setup\nimport os\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert "# setup" in blocks[0].source
        assert "import os" in blocks[0].source

    @staticmethod
    def test_assignments_not_captured():
        src = "x = 1\ny: int = 2\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 0

    @staticmethod
    def test_assignments_expand_adjacent_code_block():
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

    @staticmethod
    def test_consecutive_grouped():
        src = "print('a')\nprint('b')\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 1
        assert "print('a')" in blocks[0].source
        assert "print('b')" in blocks[0].source

    @staticmethod
    def test_split_by_definitions():
        src = "print('before')\n\ndef f():\n    pass\n\nprint('after')\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert len(blocks) == 2

    @staticmethod
    def test_code_block_source_complete():
        src = "if True:\n    x = 1\n    y = 2\n"
        r = _parse(src)
        blocks = _children_by_type(r, NodeType.CODE_BLOCK)
        assert "x = 1" in blocks[0].source
        assert "y = 2" in blocks[0].source


# ---------------------------------------------------------------------------
# Nested classes
# ---------------------------------------------------------------------------


class TestNestedClasses:
    @staticmethod
    def test_nested_class_is_child_of_outer():
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

    @staticmethod
    def test_nested_class_not_at_top_level():
        r = _parse("class Outer:\n    class Inner:\n        pass\n")
        top_classes = _children_by_type(r, NodeType.CLASS)
        names = [c.name for c in top_classes]
        assert "Inner" not in names

    @staticmethod
    def test_nested_class_methods():
        r = _parse("class Outer:\n    class Inner:\n        def do_stuff(self):\n            pass\n")
        outer = _children_by_type(r, NodeType.CLASS)[0]
        inner = [c for c in outer.children if c.node_type == NodeType.CLASS][0]
        methods = [c for c in inner.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Inner.do_stuff"

    @staticmethod
    def test_class_inside_function_not_top_level():
        r = _parse("def factory():\n    class Product:\n        pass\n    return Product\n")
        top_classes = _children_by_type(r, NodeType.CLASS)
        assert len(top_classes) == 0

    @staticmethod
    def test_decorated_nested_class():
        r = _parse("class Outer:\n    @some_decorator\n    class Inner:\n        pass\n")
        outer = _children_by_type(r, NodeType.CLASS)[0]
        inner = [c for c in outer.children if c.node_type == NodeType.CLASS]
        assert len(inner) == 1
        assert inner[0].name == "Inner"


# ---------------------------------------------------------------------------
# Property getter/setter/deleter
# ---------------------------------------------------------------------------


class TestPropertyDecorators:
    @staticmethod
    def test_setter_has_unique_name():
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

    @staticmethod
    def test_deleter_has_unique_name():
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
    @staticmethod
    def test_overloaded_functions_have_unique_names():
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

    @staticmethod
    def test_overloaded_name_contains_types():
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
    @staticmethod
    def test_bare_annotation_inside_function():
        r = _parse("def func(obj):\n    obj: Foo\n    return obj\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        assert len(fns) == 1
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 1
        assert props[0].name == "obj"
        assert props[0].type_annotation == "Foo"

    @staticmethod
    def test_annotated_assignment_inside_function():
        r = _parse("def func():\n    x: int = 5\n    return x\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 1
        assert props[0].name == "x"
        assert props[0].type_annotation == "int"
        assert props[0].default_value == "5"

    @staticmethod
    def test_unannotated_assignment_not_extracted():
        r = _parse("def func():\n    x = 5\n    return x\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert len(props) == 0

    @staticmethod
    def test_local_annotation_owner_is_function():
        r = _parse("def my_func(obj):\n    obj: Bar\n    pass\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        props = [c for c in fns[0].children if c.node_type == NodeType.LOCAL_VAR]
        assert props[0].name == "obj"

    @staticmethod
    def test_call_arguments_and_assign_target():
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
    @staticmethod
    def test_module_level_assigned():
        r = _parse("f = lambda x: x\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name == "lambda(x)@L1@C5"
        assert lambdas[0].owner is None
        assert lambdas[0].parameters == (Parameter(name="x"),)

    @staticmethod
    def test_nested_under_function():
        r = _parse("def outer():\n    h = lambda a, b: a + b\n")
        fns = _children_by_type(r, NodeType.FUNCTION)
        outer = next(f for f in fns if f.name == "outer")
        lambdas = [c for c in outer.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name.startswith("lambda(a, b)@L2@C")
        assert lambdas[0].owner == "outer"

    @staticmethod
    def test_inline_no_assignment():
        r = _parse("(lambda a, b: a + b)(1, 2)\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name.startswith("lambda(a, b)@L1@C")
        assert lambdas[0].func_type == "lambda"

    @staticmethod
    def test_same_line_column_differs():
        r = _parse("map(lambda x: x, filter(lambda y: y, xs))\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 2
        names = {ln.name for ln in lambdas}
        assert names == {"lambda(x)@L1@C5", "lambda(y)@L1@C25"}

    @staticmethod
    def test_star_and_kwargs_in_name():
        r = _parse("f = lambda x, y=1, *args, **kwargs: x\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name == "lambda(x, y, *args, **kwargs)@L1@C5"
        assert [p.name for p in lambdas[0].parameters] == ["x", "y", "*args", "**kwargs"]

    @staticmethod
    def test_empty_params():
        r = _parse("f = lambda: None\n")
        lambdas = [c for c in r.children if c.node_type == NodeType.FUNCTION and c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].name == "lambda()@L1@C5"

    @staticmethod
    def test_call_inside_lambda_context():
        r = _parse("def outer():\n    return lambda: foo()\n")
        from openjiuwen_search_base.codegraph.parser.models.core import CallNode

        calls = [c for c in r.children if isinstance(c, CallNode)]
        foo_calls = [c for c in calls if c.callee == "foo"]
        assert len(foo_calls) == 1
        assert foo_calls[0].context is not None
        assert foo_calls[0].context.startswith("lambda()@L2@C")

    @staticmethod
    def test_method_body_lambda():
        r = _parse("class Foo:\n    def bar(self):\n        return lambda x: x\n")
        classes = _children_by_type(r, NodeType.CLASS)
        methods = [c for c in classes[0].children if c.node_type == NodeType.FUNCTION]
        bar = next(m for m in methods if m.name == "Foo.bar")
        lambdas = [c for c in bar.children if c.func_type == "lambda"]
        assert len(lambdas) == 1
        assert lambdas[0].owner == "Foo.bar"
        assert lambdas[0].name.startswith("lambda(x)@L3@C")
