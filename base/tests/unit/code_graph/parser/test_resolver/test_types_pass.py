"""Tests for the types resolution pass."""

from openjiuwen_search_base.codegraph.parser.constants import EdgeType
from openjiuwen_search_base.codegraph.parser.resolver.indexes import ImportIndex, SymbolIndex
from openjiuwen_search_base.codegraph.parser.resolver.passes.types import resolve_types

from .conftest import (
    make_call_node,
    make_class_node,
    make_file_node,
    make_function_node,
    make_hooks_map,
    make_node_id,
    make_property_node,
)


def test_constructor_call_produces_instantiates_edge():
    cls = make_class_node("Widget", line=1)
    caller = make_function_node("build", line=5)
    call = make_call_node("Widget", context="build", line=7)
    fnode = make_file_node(path="app.py", children=(cls, caller, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())

    instantiate_edges = [e for e in edges if e.relation == EdgeType.INSTANTIATES]
    assert len(instantiate_edges) == 1
    assert instantiate_edges[0].confidence == 0.9
    assert instantiate_edges[0].resolved_by == "constructor_convention"
    assert "Widget" in instantiate_edges[0].target_id


def test_property_type_annotation_produces_type_of_edge():
    cls = make_class_node("Config", line=1)
    prop = make_property_node("settings", line=5, type_annotation="Config")
    fnode = make_file_node(path="app.py", children=(cls, prop))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())

    type_edges = [e for e in edges if e.relation == EdgeType.TYPE_OF]
    assert len(type_edges) == 1
    assert type_edges[0].confidence == 0.8
    assert type_edges[0].resolved_by == "annotation_match"


def test_function_return_type_produces_type_of_edge():
    cls = make_class_node("Result", line=1)
    func = make_function_node("compute", line=5, return_type="Result")
    fnode = make_file_node(path="app.py", children=(cls, func))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())

    type_edges = [e for e in edges if e.relation == EdgeType.TYPE_OF]
    assert len(type_edges) == 1
    assert "Result" in type_edges[0].target_id


def test_generic_type_extracts_inner_type():
    cls = make_class_node("Item", line=1)
    prop = make_property_node("items", line=5, type_annotation="list[Item]")
    fnode = make_file_node(path="app.py", children=(cls, prop))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())

    type_edges = [e for e in edges if e.relation == EdgeType.TYPE_OF]
    assert len(type_edges) == 1
    assert "Item" in type_edges[0].target_id


def test_optional_type_extracts_inner():
    cls = make_class_node("Handler", line=1)
    prop = make_property_node("handler", line=5, type_annotation="Optional[Handler]")
    fnode = make_file_node(path="app.py", children=(cls, prop))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())

    type_edges = [e for e in edges if e.relation == EdgeType.TYPE_OF]
    assert len(type_edges) == 1


def test_union_type_produces_multiple_edges():
    cls_a = make_class_node("Foo", line=1)
    cls_b = make_class_node("Bar", line=3)
    prop = make_property_node("val", line=5, type_annotation="Foo | Bar")
    fnode = make_file_node(path="app.py", children=(cls_a, cls_b, prop))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())

    type_edges = [e for e in edges if e.relation == EdgeType.TYPE_OF]
    assert len(type_edges) == 2


def test_unknown_type_produces_no_edge():
    prop = make_property_node("val", line=1, type_annotation="NoSuchType")
    fnode = make_file_node(path="app.py", children=(prop,))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())

    assert edges == []


def test_parameter_annotation_produces_type_of_edge():
    from openjiuwen_search_base.codegraph.parser.custom_types import Parameter

    cls = make_class_node("Widget", line=1)
    func = make_function_node("process", line=5, parameters=(Parameter("w", "Widget", None),))
    fnode = make_file_node(path="app.py", children=(cls, func))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())

    type_edges = [e for e in edges if e.relation == EdgeType.TYPE_OF]
    assert len(type_edges) == 1
    assert "Widget" in type_edges[0].target_id
    assert "process" in type_edges[0].source_id
