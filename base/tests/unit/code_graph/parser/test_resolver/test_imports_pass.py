"""Tests for the imports resolution pass."""

from openjiuwen_search_base.codegraph.parser.constants import EdgeType
from openjiuwen_search_base.codegraph.parser.resolver.indexes import SymbolIndex
from openjiuwen_search_base.codegraph.parser.resolver.passes.imports import resolve_imports

from .conftest import (
    make_class_node,
    make_file_node,
    make_function_node,
    make_import_node,
    make_node_id,
)


def test_import_of_known_class_produces_edge():
    cls = make_class_node("Widget", line=5)
    file_def = make_file_node(path="widgets.py", children=(cls,))

    imp = make_import_node("widgets", names=("Widget",), line=1)
    file_use = make_file_node(path="app.py", children=(imp,))

    idx = SymbolIndex([file_def, file_use], make_node_id)
    edges = resolve_imports([file_use], idx, make_node_id)

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.IMPORTS
    assert edges[0].confidence == 1.0
    assert edges[0].resolved_by == "import_match"
    assert "Widget" in edges[0].target_id
    assert "app.py" in edges[0].source_id


def test_import_of_unknown_module_produces_no_edge():
    imp = make_import_node("nonexistent", names=("Foo",), line=1)
    fnode = make_file_node(path="app.py", children=(imp,))

    idx = SymbolIndex([fnode], make_node_id)
    edges = resolve_imports([fnode], idx, make_node_id)

    assert edges == []


def test_wildcard_import_produces_no_edge():
    cls = make_class_node("Anything", line=1)
    file_def = make_file_node(path="lib.py", children=(cls,))

    imp = make_import_node("lib", is_wildcard=True, line=1)
    file_use = make_file_node(path="app.py", children=(imp,))

    idx = SymbolIndex([file_def, file_use], make_node_id)
    edges = resolve_imports([file_use], idx, make_node_id)

    assert edges == []


def test_reexport_import_produces_edge():
    cls = make_class_node("Model", line=3)
    file_def = make_file_node(path="models.py", children=(cls,))

    imp = make_import_node("models", names=("Model",), is_reexport=True, line=1)
    file_use = make_file_node(path="api.py", children=(imp,))

    idx = SymbolIndex([file_def, file_use], make_node_id)
    edges = resolve_imports([file_use], idx, make_node_id)

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.IMPORTS


def test_import_inside_function_sources_from_function():
    """An import inside a function body should have the function as the edge source."""
    cls = make_class_node("Widget", line=1)
    file_def = make_file_node(path="widgets.py", children=(cls,))

    func = make_function_node("build", line=5, line_end=15)
    imp = make_import_node("widgets", names=("Widget",), line=10)
    file_use = make_file_node(path="app.py", children=(func, imp))

    idx = SymbolIndex([file_def, file_use], make_node_id)
    edges = resolve_imports([file_use], idx, make_node_id)

    assert len(edges) == 1
    assert "build" in edges[0].source_id
    assert "Widget" in edges[0].target_id


def test_top_level_import_sources_from_file():
    """A top-level import should have the file as the edge source."""
    cls = make_class_node("Widget", line=1)
    file_def = make_file_node(path="widgets.py", children=(cls,))

    func = make_function_node("build", line=10, line_end=20)
    imp = make_import_node("widgets", names=("Widget",), line=2)
    file_use = make_file_node(path="app.py", children=(func, imp))

    idx = SymbolIndex([file_def, file_use], make_node_id)
    edges = resolve_imports([file_use], idx, make_node_id)

    assert len(edges) == 1
    assert "app.py" in edges[0].source_id
    assert "build" not in edges[0].source_id
