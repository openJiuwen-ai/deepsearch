"""Tests for the resolution indexes."""

from openjiuwen_search_base.codegraph.parser.resolver.indexes import ClassMethodIndex, ImportIndex, SymbolIndex

from .conftest import (
    make_class_node,
    make_enum_node,
    make_file_node,
    make_function_node,
    make_import_node,
    make_interface_node,
    make_node_id,
)


class TestSymbolIndex:
    """Tests for SymbolIndex."""

    @staticmethod
    def test_find_class_by_name():
        cls = make_class_node("MyClass", line=5)
        fnode = make_file_node(children=(cls,))
        idx = SymbolIndex([fnode], make_node_id)

        results = idx.lookup("MyClass")
        assert len(results) >= 1
        assert results[0][1] is cls

    @staticmethod
    def test_find_function_by_name():
        func = make_function_node("helper", line=3)
        fnode = make_file_node(children=(func,))
        idx = SymbolIndex([fnode], make_node_id)

        results = idx.lookup("helper")
        assert len(results) >= 1
        assert results[0][1] is func

    @staticmethod
    def test_find_interface_by_name():
        iface = make_interface_node("Drawable", line=7)
        fnode = make_file_node(children=(iface,))
        idx = SymbolIndex([fnode], make_node_id)

        results = idx.lookup("Drawable")
        assert len(results) >= 1
        assert results[0][1] is iface

    @staticmethod
    def test_find_enum_by_name():
        enum = make_enum_node("Color", line=2, members=("RED", "GREEN"))
        fnode = make_file_node(children=(enum,))
        idx = SymbolIndex([fnode], make_node_id)

        results = idx.lookup("Color")
        assert len(results) >= 1
        assert results[0][1] is enum

    @staticmethod
    def test_unknown_name_returns_empty():
        cls = make_class_node("Foo", line=1)
        fnode = make_file_node(children=(cls,))
        idx = SymbolIndex([fnode], make_node_id)

        assert idx.lookup("NonExistent") == []

    @staticmethod
    def test_methods_not_indexed_at_top_level():
        method = make_function_node("do_stuff", line=5, owner="MyClass", func_type="method")
        cls = make_class_node("MyClass", line=1, children=(method,))
        fnode = make_file_node(children=(cls,))
        idx = SymbolIndex([fnode], make_node_id)

        assert idx.lookup("do_stuff") == []
        assert len(idx.lookup("MyClass")) >= 1

    @staticmethod
    def test_get_by_id():
        cls = make_class_node("Widget", line=10)
        fnode = make_file_node(path="src/widget.py", children=(cls,))
        idx = SymbolIndex([fnode], make_node_id)

        nid = make_node_id("src/widget.py", cls)
        assert idx.get_by_id(nid) is cls

    @staticmethod
    def test_get_by_id_unknown():
        fnode = make_file_node(children=())
        idx = SymbolIndex([fnode], make_node_id)

        assert idx.get_by_id("nonexistent::Foo@L1") is None


class TestImportIndex:
    """Tests for ImportIndex."""

    @staticmethod
    def test_maps_imported_name():
        imp = make_import_node("os.path", names=("join",), line=1)
        fnode = make_file_node(path="app.py", children=(imp,))
        idx = ImportIndex([fnode], make_node_id)

        result = idx.resolve_name("app.py", "join")
        assert result is not None
        module, original, _imp_id = result
        assert module == "os.path"
        assert original == "join"

    @staticmethod
    def test_aliased_import():
        imp = make_import_node("numpy", names=("array",), alias="np_array", line=1)
        fnode = make_file_node(path="calc.py", children=(imp,))
        idx = ImportIndex([fnode], make_node_id)

        result = idx.resolve_name("calc.py", "np_array")
        assert result is not None
        module, original, _imp_id = result
        assert module == "numpy"
        assert original == "array"

    @staticmethod
    def test_module_import():
        imp = make_import_node("json", line=1)
        fnode = make_file_node(path="util.py", children=(imp,))
        idx = ImportIndex([fnode], make_node_id)

        result = idx.resolve_name("util.py", "json")
        assert result is not None
        assert result[0] == "json"

    @staticmethod
    def test_wildcard_skipped():
        imp = make_import_node("foo", is_wildcard=True, line=1)
        fnode = make_file_node(path="bar.py", children=(imp,))
        idx = ImportIndex([fnode], make_node_id)

        assert idx.resolve_name("bar.py", "foo") is None

    @staticmethod
    def test_unknown_file():
        fnode = make_file_node(path="x.py", children=())
        idx = ImportIndex([fnode], make_node_id)

        assert idx.resolve_name("other.py", "anything") is None

    @staticmethod
    def test_get_file_imports():
        imp1 = make_import_node("os", names=("getcwd",), line=1)
        imp2 = make_import_node("sys", names=("argv",), line=2)
        fnode = make_file_node(path="main.py", children=(imp1, imp2))
        idx = ImportIndex([fnode], make_node_id)

        imports = idx.get_file_imports("main.py")
        assert "getcwd" in imports
        assert "argv" in imports


class TestClassMethodIndex:
    """Tests for ClassMethodIndex."""

    @staticmethod
    def test_finds_methods():
        m1 = make_function_node("run", line=3, owner="Engine", func_type="method")
        m2 = make_function_node("stop", line=5, owner="Engine", func_type="method")
        cls = make_class_node("Engine", line=1, children=(m1, m2))
        fnode = make_file_node(children=(cls,))
        idx = ClassMethodIndex([fnode], make_node_id)

        methods = idx.get_methods("Engine")
        assert "run" in methods
        assert "stop" in methods

    @staticmethod
    def test_unknown_class_returns_empty():
        fnode = make_file_node(children=())
        idx = ClassMethodIndex([fnode], make_node_id)

        assert idx.get_methods("Missing") == set()

    @staticmethod
    def test_get_class_ids():
        cls = make_class_node("Handler", line=1)
        fnode = make_file_node(path="srv.py", children=(cls,))
        idx = ClassMethodIndex([fnode], make_node_id)

        ids = idx.get_class_ids("Handler")
        assert len(ids) == 1
        assert "Handler" in ids[0]
