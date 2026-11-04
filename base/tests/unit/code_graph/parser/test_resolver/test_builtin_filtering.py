"""Tests for builtin name filtering: redefinition guard and FILTER_BUILTIN_NAMES flag."""

import openjiuwen_search_base.codegraph.parser.constants as constants
from openjiuwen_search_base.codegraph.parser.constants import EdgeType
from openjiuwen_search_base.codegraph.parser.resolver.indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from openjiuwen_search_base.codegraph.parser.resolver.passes.calls import resolve_calls
from openjiuwen_search_base.codegraph.parser.resolver.passes.decorators import resolve_decorators
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


def test_builtin_call_skipped_by_default():
    """Calling a builtin like 'print' should not produce a CALLS edge."""
    fn = make_function_node("main", line=1)
    call = make_call_node("print", context="main", line=3)
    fnode = make_file_node(path="app.py", children=(fn, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cm = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp, cm, make_node_id, make_hooks_map())
    assert len(edges) == 0


def test_redefined_builtin_not_skipped():
    """A user-defined function named 'print' should still produce a CALLS edge."""
    user_print = make_function_node("print", line=1)
    caller = make_function_node("main", line=5)
    call = make_call_node("print", context="main", line=7)
    fnode = make_file_node(path="app.py", children=(user_print, caller, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cm = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp, cm, make_node_id, make_hooks_map())
    assert any(e.relation == EdgeType.CALLS for e in edges)


def test_redefined_builtin_class_not_skipped():
    """A user-defined class named 'Error' should still resolve INSTANTIATES."""
    user_error = make_class_node("Error", line=1)
    caller = make_function_node("main", line=5)
    call = make_call_node("Error", context="main", line=7)
    fnode = make_file_node(path="app.py", children=(user_error, caller, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)

    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())
    assert any(e.relation == EdgeType.INSTANTIATES for e in edges)


def test_filter_builtin_names_false_allows_builtins(monkeypatch):
    """When FILTER_BUILTIN_NAMES is False, builtin names are not skipped."""
    monkeypatch.setattr(constants, "FILTER_BUILTIN_NAMES", False)

    fn = make_function_node("main", line=1)
    call = make_call_node("ValueError", context="main", line=3)
    fnode = make_file_node(path="app.py", children=(fn, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cm = ClassMethodIndex([fnode], make_node_id)

    resolve_calls([fnode], sym, imp, cm, make_node_id, make_hooks_map())  # make sure not failing


def test_builtin_type_annotation_skipped():
    """A type annotation using a builtin name like 'int' should not produce a TYPE_OF edge."""
    prop = make_property_node("x", line=1, type_annotation="int")
    fnode = make_file_node(path="app.py", children=(prop,))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)

    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())
    type_of_edges = [e for e in edges if e.relation == EdgeType.TYPE_OF]
    assert len(type_of_edges) == 0


def test_redefined_builtin_type_annotation_resolved():
    """A user-defined class 'int' used in a type annotation should produce a TYPE_OF edge."""
    user_int = make_class_node("int", line=1)
    prop = make_property_node("x", line=5, type_annotation="int")
    fnode = make_file_node(path="app.py", children=(user_int, prop))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)

    edges = resolve_types([fnode], sym, imp, make_node_id, make_hooks_map())
    type_of_edges = [e for e in edges if e.relation == EdgeType.TYPE_OF]
    assert len(type_of_edges) == 1


def test_builtin_decorator_skipped():
    """A decorator named 'property' (a builtin) should be skipped by default."""
    fn = make_function_node("get_name", line=1, decorators=("@property",))
    fnode = make_file_node(path="app.py", children=(fn,))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)

    edges = resolve_decorators([fnode], sym, imp, make_node_id, make_hooks_map())
    assert len(edges) == 0


def test_redefined_builtin_decorator_resolved():
    """A user-defined function named 'property' used as decorator should still resolve."""
    user_property = make_function_node("property", line=1)
    fn = make_function_node("get_name", line=5, decorators=("@property",))
    fnode = make_file_node(path="app.py", children=(user_property, fn))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)

    edges = resolve_decorators([fnode], sym, imp, make_node_id, make_hooks_map())
    decorated = [e for e in edges if e.relation == EdgeType.DECORATED_BY]
    assert len(decorated) == 1
