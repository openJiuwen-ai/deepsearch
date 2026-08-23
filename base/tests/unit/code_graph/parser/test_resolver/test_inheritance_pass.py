"""Tests for the inheritance resolution pass."""

from openjiuwen_search_base.codegraph.parser.constants import EdgeType
from openjiuwen_search_base.codegraph.parser.resolver.indexes import ImportIndex, SymbolIndex
from openjiuwen_search_base.codegraph.parser.resolver.passes.inheritance import resolve_inheritance

from .conftest import (
    make_class_node,
    make_file_node,
    make_hooks_map,
    make_import_node,
    make_interface_node,
    make_node_id,
)


def test_class_with_base_produces_inherits_edge():
    base = make_class_node("Animal", line=1)
    child = make_class_node("Dog", line=5, bases=("Animal",))
    fnode = make_file_node(children=(base, child))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_inheritance([fnode], sym, imp, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.INHERITS
    assert edges[0].confidence == 1.0
    assert "Dog" in edges[0].source_id
    assert "Animal" in edges[0].target_id


def test_class_implementing_interface_produces_implements_edge():
    iface = make_interface_node("Drawable", line=1)
    cls = make_class_node("Circle", line=5, bases=("Drawable",))
    fnode = make_file_node(children=(iface, cls))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_inheritance([fnode], sym, imp, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.IMPLEMENTS


def test_protocol_base_produces_implements_edge():
    base = make_class_node("MyProtocol", line=1)
    cls = make_class_node("Handler", line=5, bases=("MyProtocol",))
    fnode = make_file_node(children=(base, cls))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_inheritance([fnode], sym, imp, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.IMPLEMENTS


def test_base_resolved_through_import():
    base = make_class_node("Base", line=1)
    file_def = make_file_node(path="core.py", children=(base,))

    imp = make_import_node("core", names=("Base",), line=1)
    cls = make_class_node("Child", line=3, bases=("Base",))
    file_use = make_file_node(path="app.py", children=(imp, cls))

    sym = SymbolIndex([file_def, file_use], make_node_id)
    imp_idx = ImportIndex([file_use], make_node_id)
    edges = resolve_inheritance([file_use], sym, imp_idx, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.INHERITS
    assert edges[0].resolved_by == "import_then_match"


def test_unknown_base_produces_no_edge():
    cls = make_class_node("Orphan", line=1, bases=("UnknownBase",))
    fnode = make_file_node(children=(cls,))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_inheritance([fnode], sym, imp, make_node_id, make_hooks_map())

    assert edges == []
