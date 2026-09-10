"""Integration test for the full resolution pipeline."""

from openjiuwen_search_base.codegraph.parser.constants import EdgeType
from openjiuwen_search_base.codegraph.parser.languages import register_builtins
from openjiuwen_search_base.codegraph.parser.resolver import resolve_graph

from .conftest import (
    make_call_node,
    make_class_node,
    make_file_node,
    make_function_node,
    make_import_node,
    make_interface_node,
    make_node_id,
    make_property_node,
)

register_builtins()


def test_full_pipeline_multi_file():
    """Create a mini project with multiple relationship types and verify all edges."""
    # File 1: defines a base class and an interface
    base_method = make_function_node("save", line=3, owner="BaseModel", func_type="method")
    base_cls = make_class_node("BaseModel", line=1, children=(base_method,))
    iface = make_interface_node("Serializable", line=10)
    decorator_fn = make_function_node("log_calls", line=15)
    file1 = make_file_node(path="core.py", children=(base_cls, iface, decorator_fn))

    # File 2: imports from file1, defines a subclass, makes calls
    imp_base = make_import_node("core", names=("BaseModel",), line=1)
    imp_iface = make_import_node("core", names=("Serializable",), line=2)
    imp_dec = make_import_node("core", names=("log_calls",), line=3)

    child_method = make_function_node("process", line=12, owner="User", func_type="method")
    override_save = make_function_node("save", line=14, owner="User", func_type="method")
    child_cls = make_class_node(
        "User",
        line=10,
        bases=("BaseModel", "Serializable"),
        decorators=("@log_calls",),
        children=(child_method, override_save),
    )

    factory_fn = make_function_node(
        "create_user",
        line=20,
        return_type="User",
    )
    call_node = make_call_node("User", context="create_user", line=22)
    prop = make_property_node("current_user", line=25, type_annotation="User")

    file2 = make_file_node(
        path="app.py",
        children=(imp_base, imp_iface, imp_dec, child_cls, factory_fn, call_node, prop),
    )

    edges, _synth_nodes, _synth_edges = resolve_graph([file1, file2], node_id_fn=make_node_id)

    relations = {e.relation for e in edges}

    assert EdgeType.IMPORTS in relations
    assert EdgeType.INHERITS in relations
    assert EdgeType.IMPLEMENTS in relations
    assert EdgeType.OVERRIDES in relations
    assert EdgeType.DECORATED_BY in relations
    assert EdgeType.INSTANTIATES in relations
    assert EdgeType.TYPE_OF in relations

    # Verify specific edges
    import_edges = [e for e in edges if e.relation == EdgeType.IMPORTS]
    assert len(import_edges) >= 3

    inherits = [e for e in edges if e.relation == EdgeType.INHERITS]
    assert any("User" in e.source_id and "BaseModel" in e.target_id for e in inherits)

    implements = [e for e in edges if e.relation == EdgeType.IMPLEMENTS]
    assert any("User" in e.source_id and "Serializable" in e.target_id for e in implements)

    overrides = [e for e in edges if e.relation == EdgeType.OVERRIDES]
    assert any("User" in e.source_id and "save" in e.source_id and "BaseModel" in e.target_id for e in overrides)

    decorated = [e for e in edges if e.relation == EdgeType.DECORATED_BY]
    assert any("User" in e.source_id and "log_calls" in e.target_id for e in decorated)

    instantiates = [e for e in edges if e.relation == EdgeType.INSTANTIATES]
    assert any("User" in e.target_id for e in instantiates)

    type_of = [e for e in edges if e.relation == EdgeType.TYPE_OF]
    assert any("User" in e.target_id for e in type_of)


def test_pipeline_empty_input():
    """Empty file list produces no edges."""
    edges, synth_nodes, synth_edges = resolve_graph([], node_id_fn=make_node_id)
    assert edges == []
    assert synth_nodes == []
    assert synth_edges == []


def test_pipeline_no_resolvable_references():
    """Files with no cross-references produce no edges."""
    func = make_function_node("standalone", line=1)
    fnode = make_file_node(path="isolated.py", children=(func,))

    edges, _, _ = resolve_graph([fnode], node_id_fn=make_node_id)
    assert edges == []
