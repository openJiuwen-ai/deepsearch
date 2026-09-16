"""Tests for the decorators resolution pass."""

from openjiuwen_search_base.codegraph.parser.constants import EdgeType
from openjiuwen_search_base.codegraph.parser.resolver.indexes import ImportIndex, SymbolIndex
from openjiuwen_search_base.codegraph.parser.resolver.passes.decorators import resolve_decorators

from .conftest import (
    make_class_node,
    make_file_node,
    make_function_node,
    make_hooks_map,
    make_import_node,
    make_node_id,
)


def test_decorated_function_produces_edge():
    decorator_fn = make_function_node("my_decorator", line=1)
    decorated = make_function_node("target", line=5, decorators=("@my_decorator",))
    fnode = make_file_node(children=(decorator_fn, decorated))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_decorators([fnode], sym, imp, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.DECORATED_BY
    assert edges[0].confidence == 1.0
    assert "target" in edges[0].source_id
    assert "my_decorator" in edges[0].target_id


def test_metaclass_produces_edge():
    meta = make_class_node("ABCMeta", line=1)
    cls = make_class_node("Abstract", line=5, metaclass="ABCMeta")
    fnode = make_file_node(children=(meta, cls))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_decorators([fnode], sym, imp, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.METACLASS
    assert "Abstract" in edges[0].source_id
    assert "ABCMeta" in edges[0].target_id


def test_builtin_decorator_produces_no_edge():
    func = make_function_node("getter", line=1, decorators=("@property",))
    fnode = make_file_node(children=(func,))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_decorators([fnode], sym, imp, make_node_id, make_hooks_map())

    assert edges == []


def test_staticmethod_decorator_produces_no_edge():
    func = make_function_node("utility", line=1, decorators=("@staticmethod",))
    fnode = make_file_node(children=(func,))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_decorators([fnode], sym, imp, make_node_id, make_hooks_map())

    assert edges == []


def test_decorator_resolved_through_import():
    dec_fn = make_function_node("cache_result", line=1)
    file_def = make_file_node(path="decorators.py", children=(dec_fn,))

    imp = make_import_node("decorators", names=("cache_result",), line=1)
    func = make_function_node("compute", line=3, decorators=("@cache_result",))
    file_use = make_file_node(path="app.py", children=(imp, func))

    sym = SymbolIndex([file_def, file_use], make_node_id)
    imp_idx = ImportIndex([file_use], make_node_id)
    edges = resolve_decorators([file_use], sym, imp_idx, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.DECORATED_BY


def test_decorator_with_args_resolved():
    dec_fn = make_function_node("lru_cache", line=1)
    func = make_function_node("expensive", line=5, decorators=("@lru_cache(maxsize=128)",))
    fnode = make_file_node(children=(dec_fn, func))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    edges = resolve_decorators([fnode], sym, imp, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.DECORATED_BY
