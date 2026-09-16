"""Tests for the calls resolution pass."""

import pytest

from openjiuwen_search_base.codegraph.parser.constants import EdgeType
from openjiuwen_search_base.codegraph.parser.custom_types import Parameter
from openjiuwen_search_base.codegraph.parser.resolver.indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from openjiuwen_search_base.codegraph.parser.resolver.passes.calls import resolve_calls

from .conftest import (
    make_call_node,
    make_class_node,
    make_file_node,
    make_function_node,
    make_hooks_map,
    make_import_node,
    make_node_id,
    make_property_node,
)


def _get_python_callable_wrappers() -> list[str]:
    """Return all callable_wrappers defined in PythonHooks."""
    hooks_map = make_hooks_map()
    return sorted(hooks_map["python"].callable_wrappers)


def test_call_to_imported_function_tier1():
    target = make_function_node("do_work", line=1)
    file_def = make_file_node(path="lib.py", children=(target,))

    imp = make_import_node("lib", names=("do_work",), line=1)
    caller = make_function_node("main", line=3)
    call = make_call_node("do_work", context="main", line=5)
    file_use = make_file_node(path="app.py", children=(imp, caller, call))

    sym = SymbolIndex([file_def, file_use], make_node_id)
    imp_idx = ImportIndex([file_use], make_node_id)
    cm_idx = ClassMethodIndex([file_def, file_use], make_node_id)

    edges = resolve_calls([file_use], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.CALLS
    assert edges[0].confidence == 1.0
    assert edges[0].resolved_by == "import_exact"


def test_call_to_local_function_tier2():
    helper = make_function_node("helper", line=1)
    main_fn = make_function_node("main", line=5)
    call = make_call_node("helper", context="main", line=7)
    fnode = make_file_node(path="app.py", children=(helper, main_fn, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].relation == EdgeType.CALLS
    assert edges[0].confidence == 0.9
    assert edges[0].resolved_by == "local_scope"


def test_method_call_with_receiver_tier3():
    method = make_function_node("execute", line=3, owner="Engine", func_type="method")
    engine_cls = make_class_node("Engine", line=1, children=(method,))

    caller = make_function_node(
        "run",
        line=10,
        parameters=(Parameter("eng", "Engine", None),),
    )
    call = make_call_node("execute", receiver="eng", context="run", line=12)
    fnode = make_file_node(path="app.py", children=(engine_cls, caller, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    assert any(e.resolved_by == "method_receiver" and e.confidence == 0.7 for e in edges)


def test_name_match_single_candidate_tier4():
    target = make_function_node("unique_fn", line=1)
    file_def = make_file_node(path="lib.py", children=(target,))

    caller = make_function_node("main", line=1)
    call = make_call_node("unique_fn", context="main", line=3)
    file_use = make_file_node(path="app.py", children=(caller, call))

    sym = SymbolIndex([file_def, file_use], make_node_id)
    imp_idx = ImportIndex([file_use], make_node_id)
    cm_idx = ClassMethodIndex([file_def, file_use], make_node_id)

    edges = resolve_calls([file_use], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].confidence == 0.5
    assert edges[0].resolved_by == "name_match"


def test_ambiguous_name_match_produces_no_edge():
    fn1 = make_function_node("process", line=1)
    file1 = make_file_node(path="a.py", children=(fn1,))

    fn2 = make_function_node("process", line=1)
    file2 = make_file_node(path="b.py", children=(fn2,))

    caller = make_function_node("main", line=1)
    call = make_call_node("process", context="main", line=3)
    file_use = make_file_node(path="app.py", children=(caller, call))

    sym = SymbolIndex([file1, file2, file_use], make_node_id)
    imp_idx = ImportIndex([file_use], make_node_id)
    cm_idx = ClassMethodIndex([file1, file2, file_use], make_node_id)

    edges = resolve_calls([file_use], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    assert edges == []


def test_builtin_call_produces_no_edge():
    caller = make_function_node("main", line=1)
    call = make_call_node("print", context="main", line=3)
    fnode = make_file_node(path="app.py", children=(caller, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    assert edges == []


def test_local_annotation_resolves_method_call():
    method = make_function_node("Engine.do_work", line=3, owner="Engine", func_type="method")
    engine_cls = make_class_node("Engine", line=1, children=(method,))

    local_prop = make_property_node("obj", line=11, owner="run", type_annotation="Engine")
    caller = make_function_node("run", line=10, children=(local_prop,))
    call = make_call_node("do_work", receiver="obj", context="run", line=12)
    fnode = make_file_node(path="app.py", children=(engine_cls, caller, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    method_edges = [e for e in edges if e.resolved_by == "method_receiver"]
    assert len(method_edges) == 1
    assert method_edges[0].confidence == 0.7


@pytest.mark.parametrize("qualified_wrapper", _get_python_callable_wrappers())
def test_wrapper_resolves_via_from_import(qualified_wrapper: str):
    """``from <module> import <name>`` then ``<name>(Widget)`` should resolve."""
    module, _, func_name = qualified_wrapper.rpartition(".")
    cls = make_class_node("Widget", line=1)
    imp_widget = make_import_node("widgets", names=("Widget",), line=1)
    imp_wrapper = make_import_node(module, names=(func_name,), line=2)

    caller = make_function_node("build", line=5)
    wrapper_call = make_call_node(
        func_name,
        context="build",
        line=7,
        arguments=("Widget",),
        assign_target="make",
    )
    make_call = make_call_node("make", context="build", line=8, assign_target="obj")
    fnode = make_file_node(
        path="app.py",
        children=(imp_widget, imp_wrapper, cls, caller, wrapper_call, make_call),
    )

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    resolved = [e for e in edges if "Widget" in e.target_id and e.relation == EdgeType.CALLS]
    assert len(resolved) >= 1


@pytest.mark.parametrize("qualified_wrapper", _get_python_callable_wrappers())
def test_wrapper_resolves_via_module_import(qualified_wrapper: str):
    """``import <module>`` then ``<module>.<name>(Widget)`` should resolve."""
    module, _, func_name = qualified_wrapper.rpartition(".")
    cls = make_class_node("Widget", line=1)
    imp_module = make_import_node(module, line=2)

    caller = make_function_node("build", line=5)
    wrapper_call = make_call_node(
        func_name,
        receiver=module,
        context="build",
        line=7,
        arguments=("Widget",),
        assign_target="make",
    )
    make_call = make_call_node("make", context="build", line=8, assign_target="obj")
    fnode = make_file_node(
        path="app.py",
        children=(imp_module, cls, caller, wrapper_call, make_call),
    )

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    resolved = [e for e in edges if "Widget" in e.target_id and e.relation == EdgeType.CALLS]
    assert len(resolved) >= 1


@pytest.mark.parametrize("qualified_wrapper", _get_python_callable_wrappers())
def test_local_function_same_name_as_wrapper_not_aliased(qualified_wrapper: str):
    """A local ``def <name>`` should not be treated as the stdlib wrapper."""
    _, _, func_name = qualified_wrapper.rpartition(".")
    user_func = make_function_node(func_name, line=1)
    caller = make_function_node("build", line=5)
    wrapper_call = make_call_node(
        func_name,
        context="build",
        line=7,
        arguments=("Widget",),
        assign_target="make",
    )
    fnode = make_file_node(path="app.py", children=(user_func, caller, wrapper_call))

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    for e in edges:
        assert e.resolved_by != "import_exact" or "Widget" not in e.target_id


@pytest.mark.parametrize("qualified_wrapper", _get_python_callable_wrappers())
def test_same_name_imported_from_different_module_not_aliased(qualified_wrapper: str):
    """``from other_lib import <name>`` should not trigger wrapper aliasing."""
    _, _, func_name = qualified_wrapper.rpartition(".")
    cls = make_class_node("Widget", line=1)
    imp = make_import_node("other_lib", names=(func_name,), line=2)

    caller = make_function_node("build", line=5)
    wrapper_call = make_call_node(
        func_name,
        context="build",
        line=7,
        arguments=("Widget",),
        assign_target="make",
    )
    make_call = make_call_node("make", context="build", line=8)
    fnode = make_file_node(
        path="app.py",
        children=(imp, cls, caller, wrapper_call, make_call),
    )

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    wrapper_resolved = [e for e in edges if "Widget" in e.target_id and e.resolved_by == "import_exact"]
    assert wrapper_resolved == []


@pytest.mark.parametrize("qualified_wrapper", _get_python_callable_wrappers())
def test_method_same_name_on_user_class_not_aliased(qualified_wrapper: str):
    """``obj.<name>(Widget)`` on a user class should not trigger wrapper aliasing."""
    _, _, func_name = qualified_wrapper.rpartition(".")
    method = make_function_node(
        f"MyClass.{func_name}",
        line=3,
        owner="MyClass",
        func_type="method",
    )
    cls = make_class_node("MyClass", line=1, children=(method,))
    target_cls = make_class_node("Widget", line=10)

    caller = make_function_node(
        "build",
        line=15,
        parameters=(Parameter("obj", "MyClass", None),),
    )
    wrapper_call = make_call_node(
        func_name,
        receiver="obj",
        context="build",
        line=17,
        arguments=("Widget",),
        assign_target="make",
    )
    make_call = make_call_node("make", context="build", line=18)
    fnode = make_file_node(
        path="app.py",
        children=(cls, target_cls, caller, wrapper_call, make_call),
    )

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    wrapper_resolved = [e for e in edges if e.resolved_by == "import_exact" and "Widget" in e.target_id]
    assert wrapper_resolved == []


@pytest.mark.parametrize("qualified_wrapper", _get_python_callable_wrappers())
def test_no_wrapper_without_import(qualified_wrapper: str):
    """Calling ``<name>(X)`` with no import at all should not trigger wrapper aliasing."""
    _, _, func_name = qualified_wrapper.rpartition(".")
    caller = make_function_node("build", line=1)
    wrapper_call = make_call_node(
        func_name,
        context="build",
        line=3,
        arguments=("Widget",),
        assign_target="make",
    )
    make_call = make_call_node("make", context="build", line=4)
    fnode = make_file_node(path="app.py", children=(caller, wrapper_call, make_call))

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    wrapper_resolved = [e for e in edges if "Widget" in e.target_id and e.resolved_by == "import_exact"]
    assert wrapper_resolved == []


def test_static_method_via_imported_class_receiver():
    """``Widget.create()`` resolves when Widget is imported from another file."""
    method = make_function_node("Widget.create", line=3, owner="Widget", func_type="method")
    widget_cls = make_class_node("Widget", line=1, children=(method,))
    lib = make_file_node(path="lib.py", children=(widget_cls,))

    imp = make_import_node("lib", names=("Widget",), line=1)
    call = make_call_node("create", receiver="Widget", context=None, line=3)
    app = make_file_node(path="app.py", children=(imp, call))

    sym = SymbolIndex([lib, app], make_node_id)
    imp_idx = ImportIndex([app], make_node_id)
    cm_idx = ClassMethodIndex([lib, app], make_node_id)

    edges = resolve_calls([app], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    method_edges = [e for e in edges if e.resolved_by == "method_receiver"]
    assert len(method_edges) == 1
    assert "Widget.create" in method_edges[0].target_id
    assert method_edges[0].confidence == 0.7


def test_static_method_via_aliased_import():
    """``W.create()`` resolves when Widget is imported as W."""
    method = make_function_node("Widget.create", line=3, owner="Widget", func_type="method")
    widget_cls = make_class_node("Widget", line=1, children=(method,))
    lib = make_file_node(path="lib.py", children=(widget_cls,))

    imp = make_import_node("lib", names=("Widget",), alias="W", line=1)
    call = make_call_node("create", receiver="W", context=None, line=3)
    app = make_file_node(path="app.py", children=(imp, call))

    sym = SymbolIndex([lib, app], make_node_id)
    imp_idx = ImportIndex([app], make_node_id)
    cm_idx = ClassMethodIndex([lib, app], make_node_id)

    edges = resolve_calls([app], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    method_edges = [e for e in edges if e.resolved_by == "method_receiver"]
    assert len(method_edges) == 1
    assert "Widget.create" in method_edges[0].target_id


def test_static_method_via_local_class_receiver():
    """Same-file ``Widget.create()`` resolves without an import."""
    method = make_function_node("Widget.create", line=3, owner="Widget", func_type="method")
    widget_cls = make_class_node("Widget", line=1, children=(method,))
    call = make_call_node("create", receiver="Widget", context=None, line=10)
    fnode = make_file_node(path="app.py", children=(widget_cls, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp_idx = ImportIndex([fnode], make_node_id)
    cm_idx = ClassMethodIndex([fnode], make_node_id)

    edges = resolve_calls([fnode], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    method_edges = [e for e in edges if e.resolved_by == "method_receiver"]
    assert len(method_edges) == 1
    assert "Widget.create" in method_edges[0].target_id


def test_imported_function_as_receiver_no_false_positive():
    """An imported function name used as receiver must not invent a class method edge."""
    helper = make_function_node("helper", line=1)
    method = make_function_node("helper.create", line=3, owner="helper", func_type="method")
    decoy_cls = make_class_node("Other", line=1, children=(method,))
    lib = make_file_node(path="lib.py", children=(helper,))
    decoy = make_file_node(path="decoy.py", children=(decoy_cls,))

    imp = make_import_node("lib", names=("helper",), line=1)
    call = make_call_node("create", receiver="helper", context=None, line=3)
    app = make_file_node(path="app.py", children=(imp, call))

    sym = SymbolIndex([lib, app, decoy], make_node_id)
    imp_idx = ImportIndex([app], make_node_id)
    cm_idx = ClassMethodIndex([lib, app, decoy], make_node_id)

    edges = resolve_calls([app], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    method_edges = [e for e in edges if e.resolved_by == "method_receiver" and "create" in e.target_id]
    assert method_edges == []


def test_module_level_call_attributed_to_code_block():
    """Module-level calls inside a CodeBlockNode use that block as CALLS source."""
    from openjiuwen_search_base.codegraph.parser.constants import NodeType
    from openjiuwen_search_base.codegraph.parser.custom_types import SourceSpan
    from openjiuwen_search_base.codegraph.parser.models.core import CodeBlockNode

    target = make_function_node("helper", line=1)
    lib = make_file_node(path="lib.py", children=(target,))

    imp = make_import_node("lib", names=("helper",), line=1)
    block = CodeBlockNode(
        node_type=NodeType.CODE_BLOCK,
        name='if __name__ == "__main__":',
        span=SourceSpan(5, 7, 0, 0),
        source='if __name__ == "__main__":\n    helper()\n',
    )
    call = make_call_node("helper", context=None, line=6)
    app = make_file_node(path="app.py", children=(imp, block, call))

    sym = SymbolIndex([lib, app], make_node_id)
    imp_idx = ImportIndex([app], make_node_id)
    cm_idx = ClassMethodIndex([lib, app], make_node_id)

    edges = resolve_calls([app], sym, imp_idx, cm_idx, make_node_id, make_hooks_map())

    assert len(edges) == 1
    assert edges[0].source_id.endswith("::__code_block_L5")
    assert "helper" in edges[0].target_id
