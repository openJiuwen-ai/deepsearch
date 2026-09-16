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
    @staticmethod
    def test_basic_class():
        r = _parse("public class Foo { }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "Foo"

    @staticmethod
    def test_class_extends():
        r = _parse("class Dog extends Animal { }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert "Animal" in cls.bases

    @staticmethod
    def test_class_implements():
        r = _parse("class Service extends Base implements IService, Runnable { }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert "Base" in cls.bases
        assert "IService" in cls.bases
        assert "Runnable" in cls.bases

    @staticmethod
    def test_generic_implements():
        r = _parse("class Box implements Comparable<Box> { }")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        assert any("Comparable" in b for b in cls.bases)

    @staticmethod
    def test_class_methods():
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

    @staticmethod
    def test_class_fields():
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

    @staticmethod
    def test_constructor():
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

    @staticmethod
    def test_annotations():
        r = _parse("""
public class Foo {
    @Override
    public String toString() { return ""; }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        methods = [c for c in cls.children if c.node_type == NodeType.FUNCTION]
        assert "@Override" in methods[0].decorators

    @staticmethod
    def test_inner_class():
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

    @staticmethod
    def test_static_initializer():
        r = _parse("""
public class Foo {
    static { System.out.println("init"); }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        blocks = [c for c in cls.children if c.node_type == NodeType.CODE_BLOCK]
        assert len(blocks) == 1
        assert blocks[0].name == "Foo.<clinit>"

    @staticmethod
    def test_instance_initializer():
        r = _parse("""
public class Foo {
    { System.out.println("instance init"); }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        blocks = [c for c in cls.children if c.node_type == NodeType.CODE_BLOCK]
        assert len(blocks) == 1
        assert blocks[0].name == "Foo.<init-block>"

    @staticmethod
    def test_abstract_class():
        r = _parse("public abstract class Base { abstract void doWork(); }")
        cls = _children_by_type(r, NodeType.CLASS)
        assert len(cls) == 1
        assert cls[0].name == "Base"

    @staticmethod
    def test_multiple_variable_declarators():
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
    @staticmethod
    def test_basic_interface():
        r = _parse("interface Foo { void bar(); }")
        ifaces = _children_by_type(r, NodeType.INTERFACE)
        assert len(ifaces) == 1
        assert ifaces[0].name == "Foo"

    @staticmethod
    def test_interface_methods():
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

    @staticmethod
    def test_interface_extends():
        r = _parse("interface B extends A { }")
        iface = _children_by_type(r, NodeType.INTERFACE)[0]
        assert "A" in iface.bases

    @staticmethod
    def test_interface_constants():
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

    @staticmethod
    def test_interface_default_method():
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
    @staticmethod
    def test_basic_enum():
        r = _parse("enum Color { RED, GREEN, BLUE }")
        enums = _children_by_type(r, NodeType.ENUM)
        assert len(enums) == 1
        assert enums[0].name == "Color"
        assert set(enums[0].members) == {"RED", "GREEN", "BLUE"}

    @staticmethod
    def test_enum_with_methods():
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
    @staticmethod
    def test_basic_record():
        r = _parse("record Point(int x, int y) { }")
        structs = _children_by_type(r, NodeType.STRUCT)
        assert len(structs) == 1
        assert structs[0].name == "Point"
        assert len(structs[0].fields) == 2
        assert structs[0].fields[0].name == "x"
        assert structs[0].fields[0].type_annotation == "int"

    @staticmethod
    def test_record_with_methods():
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
    @staticmethod
    def test_annotation_type():
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
    @staticmethod
    def test_basic_import():
        r = _parse("import java.util.List;")
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) == 1
        assert imports[0].module == "java.util"
        assert imports[0].names == ("List",)

    @staticmethod
    def test_wildcard_import():
        r = _parse("import java.util.*;")
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) == 1
        assert imports[0].is_wildcard
        assert imports[0].module == "java.util"

    @staticmethod
    def test_static_import():
        r = _parse("import static java.lang.Math.PI;")
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) == 1
        assert "static" in imports[0].name
        assert imports[0].names == ("PI",)

    @staticmethod
    def test_static_wildcard_import():
        r = _parse("import static java.util.Collections.*;")
        imports = _children_by_type(r, NodeType.IMPORT)
        assert len(imports) == 1
        assert imports[0].is_wildcard
        assert "static" in imports[0].name


# ---------------------------------------------------------------------------
# Javadoc
# ---------------------------------------------------------------------------


class TestJavadoc:
    @staticmethod
    def test_class_javadoc():
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

    @staticmethod
    def test_method_javadoc():
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

    @staticmethod
    def test_field_javadoc():
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

    @staticmethod
    def test_no_javadoc():
        r = _parse("""
public class Foo {
    // regular comment
    private int x;
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        props = [c for c in cls.children if c.node_type == NodeType.PROPERTY]
        assert props[0].docstring is None

    @staticmethod
    def test_block_comment_not_javadoc():
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
    @staticmethod
    def test_method_invocation():
        r = _parse("""
public class Foo {
    void bar() { System.out.println("hello"); }
}
""")
        calls = _children_by_type(r, NodeType.CALL)
        assert any(c.callee == "println" for c in calls)

    @staticmethod
    def test_constructor_call():
        r = _parse("""
public class Foo {
    void bar() { Foo f = new Foo(); }
}
""")
        calls = _children_by_type(r, NodeType.CALL)
        assert any(c.callee == "Foo" for c in calls)

    @staticmethod
    def test_method_reference():
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
    @staticmethod
    def test_basic_params():
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

    @staticmethod
    def test_varargs():
        r = _parse("""
public class Foo {
    public void method(String... args) { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        m = [c for c in cls.children if c.node_type == NodeType.FUNCTION][0]
        assert len(m.parameters) == 1
        assert "args" in m.parameters[0].name

    @staticmethod
    def test_generic_param():
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
    @staticmethod
    def test_void_return():
        r = _parse("""
public class Foo {
    public void method() { }
}
""")
        cls = _children_by_type(r, NodeType.CLASS)[0]
        m = [c for c in cls.children if c.node_type == NodeType.FUNCTION][0]
        assert m.return_type == "void"

    @staticmethod
    def test_generic_return():
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
    @staticmethod
    def test_language_is_java():
        r = _parse("class X { }")
        assert r.language == "java"
