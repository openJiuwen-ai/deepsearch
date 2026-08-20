"""Tests for the Java language parser."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import FileNode


def _parse(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
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
        r = _parse("public class Foo { }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "Foo"

    def test_class_extends(self):
        r = _parse("class Dog extends Animal { }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert "Animal" in cls.bases

    def test_class_implements(self):
        r = _parse("class Service extends Base implements IService, Runnable { }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert "Base" in cls.bases
        assert "IService" in cls.bases
        assert "Runnable" in cls.bases

    def test_generic_implements(self):
        r = _parse("class Box implements Comparable<Box> { }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert any("Comparable" in b for b in cls.bases)

    def test_class_methods(self):
        r = _parse("""
public class Greeter {
    public String greet(String name) { return name; }
    public void close() { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 2
        assert methods[0].name == "Greeter.greet"
        assert methods[0].func_type == "method"
        assert methods[0].owner == "Greeter"

    def test_class_fields(self):
        r = _parse("""
public class Config {
    private String name;
    public int count = 0;
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        props = [c for c in cls.children if c.node_type == NodeType.PROPERTY]
        assert len(props) == 2
        assert props[0].name == "name"
        assert props[0].owner == "Config"
        assert props[0].type_annotation == "String"
        assert props[1].name == "count"
        assert props[1].default_value == "0"

    def test_constructor(self):
        r = _parse("""
public class Foo {
    public Foo(int x, String y) { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Foo.<init>"
        assert methods[0].func_type == "method"
        assert len(methods[0].parameters) == 2
        assert methods[0].parameters[0].name == "x"
        assert methods[0].parameters[0].type_annotation == "int"

    def test_annotations(self):
        r = _parse("""
public class Foo {
    @Override
    public String toString() { return ""; }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert "@Override" in methods[0].decorators

    def test_inner_class(self):
        r = _parse("""
public class Outer {
    public class Inner {
        void method() { }
    }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        inner_classes = [c for c in cls.children if c.node_type == NodeType.CLASS]
        assert len(inner_classes) == 1
        assert inner_classes[0].name == "Inner"

    def test_static_initializer(self):
        r = _parse("""
public class Foo {
    static { System.out.println("init"); }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        blocks = [c for c in cls.children if c.node_type == NodeType.CODE_BLOCK]
        assert len(blocks) == 1
        assert blocks[0].name == "Foo.<clinit>"

    def test_instance_initializer(self):
        r = _parse("""
public class Foo {
    { System.out.println("instance init"); }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        blocks = [c for c in cls.children if c.node_type == NodeType.CODE_BLOCK]
        assert len(blocks) == 1
        assert blocks[0].name == "Foo.<init-block>"

    def test_abstract_class(self):
        r = _parse("public abstract class Base { abstract void doWork(); }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "Base"

    def test_multiple_variable_declarators(self):
        r = _parse("""
public class Foo {
    int x = 1, y = 2;
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        props = [c for c in cls.children if c.node_type == NodeType.PROPERTY]
        assert len(props) == 2
        names = {p.name for p in props}
        assert names == {"x", "y"}


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------


class TestInterfaces:
    def test_basic_interface(self):
        r = _parse("interface Foo { void bar(); }")
        ifaces = _children_by_type(r, NodeType.INTERFACE)
        assert len(ifaces) == 1
        assert ifaces[0].name == "Foo"

    def test_interface_methods(self):
        r = _parse("""
interface Reader {
    int read(byte[] buf);
    void close();
}
""")
        iface = _children_by_type(r, NodeType.INTERFACE)[0]
        methods = [c for c in iface.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 2
        assert methods[0].name == "Reader.read"
        assert methods[0].func_type == "method"

    def test_interface_extends(self):
        r = _parse("interface B extends A { }")
        iface = _children_by_type(r, NodeType.INTERFACE)[0]
        assert "A" in iface.bases

    def test_interface_constants(self):
        r = _parse("""
interface Constants {
    int MAX = 100;
    String NAME = "test";
}
""")
        iface = _children_by_type(r, NodeType.INTERFACE)[0]
        props = [c for c in iface.children if c.node_type == NodeType.PROPERTY]
        assert len(props) == 2
        assert props[0].name == "MAX"
        assert props[0].type_annotation == "int"

    def test_interface_default_method(self):
        r = _parse("""
interface Greeter {
    default String greet(String name) { return "Hello " + name; }
}
""")
        iface = _children_by_type(r, NodeType.INTERFACE)[0]
        methods = [c for c in iface.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Greeter.greet"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_basic_enum(self):
        r = _parse("enum Color { RED, GREEN, BLUE }")
        enums = _children_by_type(r, NodeType.ENUM)
        assert len(enums) == 1
        assert enums[0].name == "Color"
        assert set(enums[0].members) == {"RED", "GREEN", "BLUE"}

    def test_enum_with_methods(self):
        r = _parse("""
enum Status {
    ACTIVE, INACTIVE;
    public String label() { return name().toLowerCase(); }
}
""")
        enums = _children_by_type(r, NodeType.ENUM)[0]
        assert "ACTIVE" in enums.members
        methods = [c for c in enums.children if c.node_type == NodeType.FUNCTION]
        assert len(methods) == 1
        assert methods[0].name == "Status.label"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class TestRecords:
    def test_basic_record(self):
        r = _parse("record Point(int x, int y) { }")
        structs = _children_by_type(r, NodeType.STRUCT)
        assert len(structs) == 1
        assert structs[0].name == "Point"
        assert len(structs[0].fields) == 2
        assert structs[0].fields[0].name == "x"
        assert structs[0].fields[0].type_annotation == "int"

    def test_record_with_methods(self):
        r = _parse("""
record Vec(double x, double y) {
    public double length() { return Math.sqrt(x*x + y*y); }
}
""")
        rec = _children_by_type(r, NodeType.STRUCT)[0]
        methods = [c for c in rec.children if c.node_type == NodeType.FUNCTION]
        assert any(m.name == "Vec.length" for m in methods)


# ---------------------------------------------------------------------------
# Annotation types
# ---------------------------------------------------------------------------


class TestAnnotationTypes:
    def test_annotation_type(self):
        r = _parse("""
@interface MyAnnotation {
    String value();
    int priority() default 0;
}
""")
        anns = _children_by_type(r, NodeType.ANNOTATION)
        assert len(anns) == 1
        assert anns[0].name == "MyAnnotation"
        props = [c for c in anns[0].children if c.node_type == NodeType.PROPERTY]
        assert len(props) == 2
        names = {p.name for p in props}
        assert "value" in names
        assert "priority" in names


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


class TestImports:
    def test_basic_import(self):
        r = _parse("import java.util.List;")
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) == 1
        assert imports[0].module == "java.util"
        assert imports[0].names == ("List",)

    def test_wildcard_import(self):
        r = _parse("import java.util.*;")
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) == 1
        assert imports[0].is_wildcard
        assert imports[0].module == "java.util"

    def test_static_import(self):
        r = _parse("import static java.lang.Math.PI;")
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) == 1
        assert "static" in imports[0].name
        assert imports[0].names == ("PI",)

    def test_static_wildcard_import(self):
        r = _parse("import static java.util.Collections.*;")
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) == 1
        assert imports[0].is_wildcard
        assert "static" in imports[0].name


# ---------------------------------------------------------------------------
# Javadoc
# ---------------------------------------------------------------------------


class TestJavadoc:
    def test_class_javadoc(self):
        r = _parse("""
/**
 * A sample class.
 * @author Test
 */
public class Foo { }
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert cls.docstring is not None
        assert "A sample class." in cls.docstring

    def test_method_javadoc(self):
        r = _parse("""
public class Foo {
    /**
     * Does something.
     * @param x the value
     * @return result
     */
    public int doSomething(int x) { return x; }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert methods[0].docstring is not None
        assert "Does something." in methods[0].docstring
        assert "@param x" in methods[0].docstring

    def test_field_javadoc(self):
        r = _parse("""
public class Foo {
    /** The name. */
    private String name;
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        props = [c for c in cls.children if c.node_type == NodeType.PROPERTY]
        assert props[0].docstring is not None
        assert "The name." in props[0].docstring

    def test_no_javadoc(self):
        r = _parse("""
public class Foo {
    // regular comment
    private int x;
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        props = [c for c in cls.children if c.node_type == NodeType.PROPERTY]
        assert props[0].docstring is None

    def test_block_comment_not_javadoc(self):
        r = _parse("""
public class Foo {
    /* regular block comment */
    private int x;
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        props = [c for c in cls.children if c.node_type == NodeType.PROPERTY]
        assert props[0].docstring is None


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


class TestCalls:
    def test_method_invocation(self):
        r = _parse("""
public class Foo {
    void bar() { System.out.println("hello"); }
}
""")
        calls = _children_by_type(r, NodeType.CALL)
        assert any(c.callee == "println" for c in calls)

    def test_constructor_call(self):
        r = _parse("""
public class Foo {
    void bar() { Foo f = new Foo(); }
}
""")
        calls = _children_by_type(r, NodeType.CALL)
        assert any(c.callee == "Foo" for c in calls)

    def test_method_reference(self):
        r = _parse("""
public class Foo {
    void bar() { list.stream().map(String::valueOf); }
}
""")
        calls = _children_by_type(r, NodeType.CALL)
        assert any("String.valueOf" in c.callee for c in calls)


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


class TestParameters:
    def test_basic_params(self):
        r = _parse("""
public class Foo {
    public void method(int x, String y) { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        m = [c for c in cls.children if c.node_type == NodeType.FUNCTION][0]
        assert len(m.parameters) == 2
        assert m.parameters[0].name == "x"
        assert m.parameters[0].type_annotation == "int"
        assert m.parameters[1].name == "y"
        assert m.parameters[1].type_annotation == "String"

    def test_varargs(self):
        r = _parse("""
public class Foo {
    public void method(String... args) { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        m = [c for c in cls.children if c.node_type == NodeType.FUNCTION][0]
        assert len(m.parameters) == 1
        assert "args" in m.parameters[0].name

    def test_generic_param(self):
        r = _parse("""
public class Foo {
    public <T> void method(List<T> items) { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        m = [c for c in cls.children if c.node_type == NodeType.FUNCTION][0]
        assert len(m.parameters) == 1
        assert "List" in m.parameters[0].type_annotation


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


class TestReturnTypes:
    def test_void_return(self):
        r = _parse("""
public class Foo {
    public void method() { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        m = [c for c in cls.children if c.node_type == NodeType.FUNCTION][0]
        assert m.return_type == "void"

    def test_generic_return(self):
        r = _parse("""
public class Foo {
    public List<String> getItems() { return null; }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        m = [c for c in cls.children if c.node_type == NodeType.FUNCTION][0]
        assert "List" in m.return_type
        assert "String" in m.return_type


# ---------------------------------------------------------------------------
# File metadata
# ---------------------------------------------------------------------------


class TestFileMeta:
    def test_language_is_java(self):
        r = _parse("class X { }")
        assert r.language == "java"
