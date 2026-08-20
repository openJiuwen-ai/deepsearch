"""Tests for the duck type resolution pass."""

from openjiuwen_search_base.codegraph.parser.constants import EdgeType, NodeType
from openjiuwen_search_base.codegraph.parser.custom_types import SourceSpan
from openjiuwen_search_base.codegraph.parser.models.core import (
    ClassNode,
    DuckTypeNode,
    FunctionNode,
    ImportNode,
)
from openjiuwen_search_base.codegraph.parser.models.structural import FileNode
from openjiuwen_search_base.codegraph.parser.resolver.indexes import (
    ClassMethodIndex,
    ImportIndex,
    SymbolIndex,
)
from openjiuwen_search_base.codegraph.parser.resolver.passes.duck_types import resolve_duck_types


def _nid(fp: str, node) -> str:
    return f"{fp}::{node.name}@L{node.span.line_start}"


def _make_class(name: str, methods: list[str], line: int = 1) -> ClassNode:
    children = tuple(
        FunctionNode(
            node_type=NodeType.FUNCTION,
            name=m,
            span=SourceSpan(line + i + 1, line + i + 2, 0, 0),
            owner=name,
            func_type="method",
        )
        for i, m in enumerate(methods)
    )
    return ClassNode(
        node_type=NodeType.CLASS,
        name=name,
        span=SourceSpan(line, line + len(methods) + 1, 0, 0),
        children=children,
    )


def _make_dt(methods: list[str], line: int = 1) -> DuckTypeNode:
    name = "DuckType{" + ", ".join(sorted(methods)) + "}"
    return DuckTypeNode(
        node_type=NodeType.DUCK_TYPE,
        name=name,
        span=SourceSpan(line, line, 0, 0),
        methods=frozenset(methods),
    )


def _resolve(file_nodes: list[FileNode]):
    """Helper that builds all indexes and calls resolve_duck_types."""
    from .conftest import make_hooks_map

    cmi = ClassMethodIndex(file_nodes, _nid)
    si = SymbolIndex(file_nodes, _nid)
    ii = ImportIndex(file_nodes, _nid)
    return resolve_duck_types(file_nodes, cmi, si, ii, _nid, make_hooks_map())


class TestExpectsEdges:
    def test_function_with_duck_type_refs_emits_expects(self):
        dt = _make_dt(["embed"], line=1)
        fn = FunctionNode(
            node_type=NodeType.FUNCTION,
            name="process",
            span=SourceSpan(5, 10, 0, 0),
            duck_type_refs=(dt.name,),
        )
        fnode = FileNode(
            node_type=NodeType.FILE,
            name="test.py",
            span=SourceSpan(1, 20, 0, 0),
            path="test.py",
            children=(dt, fn),
        )
        edges, _, _ = _resolve([fnode])
        expects = [e for e in edges if e.relation == EdgeType.EXPECTS]
        assert len(expects) == 1
        assert expects[0].source_id == _nid("test.py", fn)
        assert expects[0].target_id == _nid("test.py", dt)


class TestImplementsEdges:
    def test_class_in_same_file_implements_duck_type(self):
        dt = _make_dt(["embed", "search"], line=1)
        cls = _make_class("MyService", ["embed", "search", "extra"], line=5)
        fnode = FileNode(
            node_type=NodeType.FILE,
            name="test.py",
            span=SourceSpan(1, 20, 0, 0),
            path="test.py",
            children=(dt, cls),
        )
        edges, _, _ = _resolve([fnode])
        impl = [e for e in edges if e.relation == EdgeType.IMPLEMENTS]
        assert len(impl) == 1
        assert impl[0].resolved_by == "structural_match"

    def test_class_missing_method_does_not_implement(self):
        dt = _make_dt(["embed", "search"], line=1)
        cls = _make_class("Partial", ["embed"], line=5)
        fnode = FileNode(
            node_type=NodeType.FILE,
            name="test.py",
            span=SourceSpan(1, 20, 0, 0),
            path="test.py",
            children=(dt, cls),
        )
        edges, _, _ = _resolve([fnode])
        impl = [e for e in edges if e.relation == EdgeType.IMPLEMENTS]
        assert len(impl) == 0

    def test_class_in_imported_file_implements(self):
        """Class in an imported file should get IMPLEMENTS edge."""
        cls = _make_class("Embedder", ["embed"], line=1)
        cls_file = FileNode(
            node_type=NodeType.FILE,
            name="embedder.py",
            span=SourceSpan(1, 10, 0, 0),
            path="embedder.py",
            children=(cls,),
        )
        imp = ImportNode(
            node_type=NodeType.IMPORT,
            name="Embedder",
            span=SourceSpan(1, 1, 0, 0),
            module="embedder",
            names=("Embedder",),
        )
        dt = _make_dt(["embed"], line=3)
        fn = FunctionNode(
            node_type=NodeType.FUNCTION,
            name="use_it",
            span=SourceSpan(5, 8, 0, 0),
            duck_type_refs=(dt.name,),
        )
        main_file = FileNode(
            node_type=NodeType.FILE,
            name="main.py",
            span=SourceSpan(1, 20, 0, 0),
            path="main.py",
            children=(imp, dt, fn),
        )
        edges, _, _ = _resolve([cls_file, main_file])
        impl = [e for e in edges if e.relation == EdgeType.IMPLEMENTS]
        assert len(impl) == 1
        assert impl[0].source_id == _nid("embedder.py", cls)

    def test_class_in_unreachable_file_not_matched(self):
        """Class in a file NOT reachable via imports should NOT get IMPLEMENTS."""
        cls = _make_class("Embedder", ["embed"], line=1)
        remote_file = FileNode(
            node_type=NodeType.FILE,
            name="remote.py",
            span=SourceSpan(1, 10, 0, 0),
            path="remote.py",
            children=(cls,),
        )
        dt = _make_dt(["embed"], line=1)
        main_file = FileNode(
            node_type=NodeType.FILE,
            name="main.py",
            span=SourceSpan(1, 20, 0, 0),
            path="main.py",
            children=(dt,),
        )
        edges, _, _ = _resolve([remote_file, main_file])
        impl = [e for e in edges if e.relation == EdgeType.IMPLEMENTS]
        assert len(impl) == 0

    def test_superset_dedup_skips_subset_implements(self):
        """If a class matches both DT{a,b} and DT{a}, only DT{a,b} gets IMPLEMENTS."""
        dt_small = _make_dt(["embed"], line=1)
        dt_big = _make_dt(["embed", "search"], line=2)
        cls = _make_class("FullService", ["embed", "search", "extra"], line=5)
        fnode = FileNode(
            node_type=NodeType.FILE,
            name="test.py",
            span=SourceSpan(1, 20, 0, 0),
            path="test.py",
            children=(dt_small, dt_big, cls),
        )
        edges, _, _ = _resolve([fnode])
        impl = [e for e in edges if e.relation == EdgeType.IMPLEMENTS]
        assert len(impl) == 1
        assert impl[0].target_id == _nid("test.py", dt_big)

    def test_multi_hop_import_reaches_class(self):
        """Class reachable via multi-hop imports should get IMPLEMENTS."""
        cls = _make_class("Deep", ["embed"], line=1)
        deep_file = FileNode(
            node_type=NodeType.FILE,
            name="deep.py",
            span=SourceSpan(1, 10, 0, 0),
            path="deep.py",
            children=(cls,),
        )
        imp_deep = ImportNode(
            node_type=NodeType.IMPORT,
            name="Deep",
            span=SourceSpan(1, 1, 0, 0),
            module="deep",
            names=("Deep",),
        )
        mid_file = FileNode(
            node_type=NodeType.FILE,
            name="mid.py",
            span=SourceSpan(1, 5, 0, 0),
            path="mid.py",
            children=(imp_deep,),
        )
        # The SymbolIndex won't have a symbol for mid.py's re-export,
        # but we can import mid's ImportNode name. For the file graph to
        # link main->mid, main needs an import that resolves to mid.
        # Simplest: put a trivial function in mid.py that main imports.
        mid_fn = FunctionNode(
            node_type=NodeType.FUNCTION,
            name="helper",
            span=SourceSpan(3, 5, 0, 0),
        )
        mid_file = FileNode(
            node_type=NodeType.FILE,
            name="mid.py",
            span=SourceSpan(1, 5, 0, 0),
            path="mid.py",
            children=(imp_deep, mid_fn),
        )
        imp_mid = ImportNode(
            node_type=NodeType.IMPORT,
            name="helper",
            span=SourceSpan(1, 1, 0, 0),
            module="mid",
            names=("helper",),
        )
        dt = _make_dt(["embed"], line=3)
        main_file = FileNode(
            node_type=NodeType.FILE,
            name="main.py",
            span=SourceSpan(1, 20, 0, 0),
            path="main.py",
            children=(imp_mid, dt),
        )
        edges, _, _ = _resolve([deep_file, mid_file, main_file])
        impl = [e for e in edges if e.relation == EdgeType.IMPLEMENTS]
        assert len(impl) == 1
        assert impl[0].source_id == _nid("deep.py", cls)


class TestSubsetEdges:
    def test_strict_subset_produces_edge(self):
        dt_small = _make_dt(["embed"], line=1)
        dt_big = _make_dt(["embed", "search"], line=2)
        fnode = FileNode(
            node_type=NodeType.FILE,
            name="test.py",
            span=SourceSpan(1, 20, 0, 0),
            path="test.py",
            children=(dt_small, dt_big),
        )
        edges, _, _ = _resolve([fnode])
        subset = [e for e in edges if e.relation == EdgeType.IS_SUBSET_OF]
        assert len(subset) == 1
        assert subset[0].source_id == _nid("test.py", dt_small)
        assert subset[0].target_id == _nid("test.py", dt_big)


class TestSynthesizeIntermediates:
    def test_intersection_creates_new_duck_type(self):
        dt_a = _make_dt(["embed", "search"], line=1)
        dt_b = _make_dt(["embed", "transform"], line=2)
        fnode = FileNode(
            node_type=NodeType.FILE,
            name="test.py",
            span=SourceSpan(1, 20, 0, 0),
            path="test.py",
            children=(dt_a, dt_b),
        )
        edges, synth_nodes, _ = _resolve([fnode])
        assert len(synth_nodes) == 0

    def test_intersection_with_two_methods_is_synthesized(self):
        dt_a = _make_dt(["embed", "search", "foo"], line=1)
        dt_b = _make_dt(["embed", "search", "bar"], line=2)
        fnode = FileNode(
            node_type=NodeType.FILE,
            name="test.py",
            span=SourceSpan(1, 20, 0, 0),
            path="test.py",
            children=(dt_a, dt_b),
        )
        edges, synth_nodes, _ = _resolve([fnode])
        assert len(synth_nodes) == 1
        assert synth_nodes[0]["name"] == "DuckType{embed, search}"
        subset = [e for e in edges if e.relation == EdgeType.IS_SUBSET_OF and e.source_id == synth_nodes[0]["id"]]
        assert len(subset) == 2
