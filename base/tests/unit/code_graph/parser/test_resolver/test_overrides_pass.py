"""Tests for the OVERRIDES resolution pass."""

from openjiuwen_search_base.codegraph.parser.constants import EdgeType
from openjiuwen_search_base.codegraph.parser.custom_types import Parameter
from openjiuwen_search_base.codegraph.parser.resolver.indexes import ImportIndex, SymbolIndex
from openjiuwen_search_base.codegraph.parser.resolver.passes.inheritance import resolve_inheritance
from openjiuwen_search_base.codegraph.parser.resolver.passes.overrides import resolve_overrides

from .conftest import (
    make_class_node,
    make_file_node,
    make_function_node,
    make_hooks_map,
    make_node_id,
)

_SELF = Parameter(name="self")
_X = Parameter(name="x")
_Y = Parameter(name="y")


def _overrides(file_nodes):
    sym = SymbolIndex(file_nodes, make_node_id)
    imp = ImportIndex(file_nodes, make_node_id)
    inheritance = resolve_inheritance(file_nodes, sym, imp, make_node_id, make_hooks_map())
    return resolve_overrides(file_nodes, inheritance, sym, make_node_id), inheritance


def test_direct_override_same_arity():
    shape_area = make_function_node("area", line=3, owner="Shape", func_type="method", parameters=(_SELF,))
    shape = make_class_node("Shape", line=1, children=(shape_area,))
    circle_area = make_function_node("area", line=8, owner="Circle", func_type="method", parameters=(_SELF,))
    circle = make_class_node("Circle", line=6, bases=("Shape",), children=(circle_area,))
    fnode = make_file_node(children=(shape, circle))

    edges, _ = _overrides([fnode])
    assert len(edges) == 1
    assert edges[0].relation == EdgeType.OVERRIDES
    assert edges[0].confidence == 1.0
    assert edges[0].resolved_by == "override_match"
    assert "Circle" in edges[0].source_id and "area" in edges[0].source_id
    assert "Shape" in edges[0].target_id and "area" in edges[0].target_id


def test_no_edge_for_child_only_or_parent_only_methods():
    shape_draw = make_function_node("draw", line=3, owner="Shape", func_type="method", parameters=(_SELF,))
    shape = make_class_node("Shape", line=1, children=(shape_draw,))
    circle_area = make_function_node("area", line=8, owner="Circle", func_type="method", parameters=(_SELF,))
    circle = make_class_node("Circle", line=6, bases=("Shape",), children=(circle_area,))
    fnode = make_file_node(children=(shape, circle))

    edges, _ = _overrides([fnode])
    assert edges == []


def test_multi_level_skips_empty_intermediate():
    a_m = make_function_node("m", line=2, owner="A", func_type="method", parameters=(_SELF,))
    a = make_class_node("A", line=1, children=(a_m,))
    b = make_class_node("B", line=5, bases=("A",))
    c_m = make_function_node("m", line=9, owner="C", func_type="method", parameters=(_SELF,))
    c = make_class_node("C", line=8, bases=("B",), children=(c_m,))
    fnode = make_file_node(children=(a, b, c))

    edges, _ = _overrides([fnode])
    assert len(edges) == 1
    assert "C" in edges[0].source_id
    assert "A" in edges[0].target_id


def test_nearest_ancestor_wins():
    a_m = make_function_node("m", line=2, owner="A", func_type="method", parameters=(_SELF,))
    a = make_class_node("A", line=1, children=(a_m,))
    b_m = make_function_node("m", line=6, owner="B", func_type="method", parameters=(_SELF,))
    b = make_class_node("B", line=5, bases=("A",), children=(b_m,))
    c_m = make_function_node("m", line=10, owner="C", func_type="method", parameters=(_SELF,))
    c = make_class_node("C", line=9, bases=("B",), children=(c_m,))
    fnode = make_file_node(children=(a, b, c))

    edges, _ = _overrides([fnode])
    # C→B and B→A
    assert len(edges) == 2
    c_edge = next(e for e in edges if "C" in e.source_id)
    assert "B" in c_edge.target_id
    assert "A" not in c_edge.target_id
    b_edge = next(e for e in edges if "B" in e.source_id and "C" not in e.source_id)
    assert "A" in b_edge.target_id


def test_arity_mismatch_produces_no_edge():
    parent_m = make_function_node("m", line=2, owner="Parent", func_type="method", parameters=(_SELF, _X))
    parent = make_class_node("Parent", line=1, children=(parent_m,))
    child_m = make_function_node("m", line=6, owner="Child", func_type="method", parameters=(_SELF, _X, _Y))
    child = make_class_node("Child", line=5, bases=("Parent",), children=(child_m,))
    fnode = make_file_node(children=(parent, child))

    edges, _ = _overrides([fnode])
    assert edges == []


def test_qualified_python_method_names_match():
    """Python parser names methods ``Class.method``; basename must still match."""
    shape_area = make_function_node("Shape.area", line=3, owner="Shape", func_type="method", parameters=(_SELF,))
    shape = make_class_node("Shape", line=1, children=(shape_area,))
    circle_area = make_function_node("Circle.area", line=8, owner="Circle", func_type="method", parameters=(_SELF,))
    circle = make_class_node("Circle", line=6, bases=("Shape",), children=(circle_area,))
    fnode = make_file_node(children=(shape, circle))

    edges, _ = _overrides([fnode])
    assert len(edges) == 1
    assert edges[0].relation == EdgeType.OVERRIDES
    assert "Circle.area" in edges[0].source_id
    assert "Shape.area" in edges[0].target_id


def test_override_annotation_resolved_by():
    base_m = make_function_node("run", line=2, owner="Base", func_type="method", parameters=(_SELF,))
    base = make_class_node("Base", line=1, children=(base_m,))
    child_m = make_function_node(
        "run",
        line=6,
        owner="Child",
        func_type="method",
        parameters=(_SELF,),
        decorators=("@Override",),
    )
    child = make_class_node("Child", line=5, bases=("Base",), children=(child_m,))
    fnode = make_file_node(children=(base, child))

    edges, _ = _overrides([fnode])
    assert len(edges) == 1
    assert edges[0].resolved_by == "override_annotation"
