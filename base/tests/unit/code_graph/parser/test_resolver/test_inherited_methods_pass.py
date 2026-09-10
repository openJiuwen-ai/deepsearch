"""Tests for the inherited method guessing pass."""

from openjiuwen_search_base.codegraph.parser.constants import EdgeType
from openjiuwen_search_base.codegraph.parser.custom_types import Parameter
from openjiuwen_search_base.codegraph.parser.resolver.indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from openjiuwen_search_base.codegraph.parser.resolver.passes.inherited_methods import resolve_inherited_methods

from .conftest import (
    make_call_node,
    make_class_node,
    make_file_node,
    make_function_node,
    make_node_id,
)


def test_basic_builtin_inherit():
    """Class inheriting builtin: method called on instance gets guessed."""
    cls = make_class_node("MyList", line=1, bases=("list",))
    fn = make_function_node(
        "process",
        line=5,
        parameters=(Parameter(name="items", type_annotation="MyList", default=None),),
    )
    call = make_call_node("append", receiver="items", context="process", line=7)
    fnode = make_file_node(path="app.py", children=(cls, fn, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cmi = ClassMethodIndex([fnode], make_node_id)

    synth_nodes, synth_edges = resolve_inherited_methods(
        [fnode],
        sym,
        imp,
        cmi,
        make_node_id,
    )

    assert len(synth_nodes) == 1
    node = synth_nodes[0]
    assert node["name"] == "MyList.append"
    assert node["func_type"] == "method-guessed"
    assert node["owner"] == "MyList"
    assert "guessed" in node["tags"]

    assert len(synth_edges) == 1
    assert synth_edges[0]["relation"] == EdgeType.CONTAINS.value


def test_no_guess_for_resolvable_base():
    """If the base class is in the codebase, no guessing occurs."""
    base = make_class_node(
        "Base", line=1, children=(make_function_node("Base.do_work", line=2, owner="Base", func_type="method"),)
    )
    child = make_class_node("Child", line=5, bases=("Base",))
    fn = make_function_node(
        "use",
        line=10,
        parameters=(Parameter(name="c", type_annotation="Child", default=None),),
    )
    call = make_call_node("do_work", receiver="c", context="use", line=12)
    fnode = make_file_node(path="app.py", children=(base, child, fn, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cmi = ClassMethodIndex([fnode], make_node_id)

    synth_nodes, synth_edges = resolve_inherited_methods(
        [fnode],
        sym,
        imp,
        cmi,
        make_node_id,
    )

    assert synth_nodes == []
    assert synth_edges == []


def test_no_guess_for_already_declared_method():
    """If the method is already declared on the class, no guessing."""
    cls = make_class_node(
        "MyList",
        line=1,
        bases=("list",),
        children=(make_function_node("MyList.append", line=2, owner="MyList", func_type="method"),),
    )
    fn = make_function_node(
        "use",
        line=5,
        parameters=(Parameter(name="m", type_annotation="MyList", default=None),),
    )
    call = make_call_node("append", receiver="m", context="use", line=7)
    fnode = make_file_node(path="app.py", children=(cls, fn, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cmi = ClassMethodIndex([fnode], make_node_id)

    synth_nodes, _ = resolve_inherited_methods(
        [fnode],
        sym,
        imp,
        cmi,
        make_node_id,
    )

    assert synth_nodes == []


def test_multiple_methods_guessed():
    """Multiple distinct methods on the same builtin-derived class."""
    cls = make_class_node("MyDict", line=1, bases=("dict",))
    fn = make_function_node(
        "use",
        line=5,
        parameters=(Parameter(name="d", type_annotation="MyDict", default=None),),
    )
    call_keys = make_call_node("keys", receiver="d", context="use", line=7)
    call_values = make_call_node("values", receiver="d", context="use", line=8)
    fnode = make_file_node(path="app.py", children=(cls, fn, call_keys, call_values))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cmi = ClassMethodIndex([fnode], make_node_id)

    synth_nodes, _ = resolve_inherited_methods(
        [fnode],
        sym,
        imp,
        cmi,
        make_node_id,
    )

    names = sorted(n["name"] for n in synth_nodes)
    assert names == ["MyDict.keys", "MyDict.values"]


def test_constructor_assign_detection():
    """Receiver type inferred from constructor assignment ``obj = MyList()``."""
    cls = make_class_node("MyList", line=1, bases=("list",))
    ctor_call = make_call_node("MyList", context="main", line=3, assign_target="obj")
    fn = make_function_node("main", line=5)
    call = make_call_node("append", receiver="obj", context="main", line=7)
    fnode = make_file_node(path="app.py", children=(cls, fn, ctor_call, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cmi = ClassMethodIndex([fnode], make_node_id)

    synth_nodes, _ = resolve_inherited_methods(
        [fnode],
        sym,
        imp,
        cmi,
        make_node_id,
    )

    assert len(synth_nodes) == 1
    assert synth_nodes[0]["name"] == "MyList.append"


def test_class_method_index_updated():
    """After the pass, ClassMethodIndex should contain the guessed methods."""
    cls = make_class_node("MyList", line=1, bases=("list",))
    fn = make_function_node(
        "use",
        line=5,
        parameters=(Parameter(name="m", type_annotation="MyList", default=None),),
    )
    call = make_call_node("extend", receiver="m", context="use", line=7)
    fnode = make_file_node(path="app.py", children=(cls, fn, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cmi = ClassMethodIndex([fnode], make_node_id)

    assert "MyList.extend" not in cmi.get_methods("MyList")

    resolve_inherited_methods([fnode], sym, imp, cmi, make_node_id)

    assert "MyList.extend" in cmi.get_methods("MyList")


def test_depth_ordering():
    """Child of externally-based class is processed before parent-level classes."""
    grandparent = make_class_node("GrandBase", line=1, bases=("list",))
    parent = make_class_node("Parent", line=5, bases=("GrandBase",))

    fn = make_function_node(
        "use",
        line=10,
        parameters=(Parameter(name="g", type_annotation="GrandBase", default=None),),
    )
    call = make_call_node("append", receiver="g", context="use", line=12)
    fnode = make_file_node(path="app.py", children=(grandparent, parent, fn, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cmi = ClassMethodIndex([fnode], make_node_id)

    synth_nodes, _ = resolve_inherited_methods(
        [fnode],
        sym,
        imp,
        cmi,
        make_node_id,
    )

    names = [n["name"] for n in synth_nodes]
    assert "GrandBase.append" in names


def test_no_false_positive_for_unrelated_receiver():
    """Calls on receivers not typed to an externally-based class are ignored."""
    cls = make_class_node("MyList", line=1, bases=("list",))
    fn = make_function_node(
        "use",
        line=5,
        parameters=(Parameter(name="x", type_annotation="SomeOther", default=None),),
    )
    call = make_call_node("do_thing", receiver="x", context="use", line=7)
    fnode = make_file_node(path="app.py", children=(cls, fn, call))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cmi = ClassMethodIndex([fnode], make_node_id)

    synth_nodes, _ = resolve_inherited_methods(
        [fnode],
        sym,
        imp,
        cmi,
        make_node_id,
    )

    assert synth_nodes == []


def test_cross_file_type_annotation():
    """Type annotation in one file references externally-based class from another."""
    cls = make_class_node("MyList", line=1, bases=("list",))
    file_def = make_file_node(path="models.py", children=(cls,))

    fn = make_function_node(
        "process",
        line=1,
        parameters=(Parameter(name="items", type_annotation="MyList", default=None),),
    )
    call = make_call_node("sort", receiver="items", context="process", line=3)
    file_use = make_file_node(path="app.py", children=(fn, call))

    all_files = [file_def, file_use]
    sym = SymbolIndex(all_files, make_node_id)
    imp = ImportIndex(all_files, make_node_id)
    cmi = ClassMethodIndex(all_files, make_node_id)

    synth_nodes, _ = resolve_inherited_methods(
        all_files,
        sym,
        imp,
        cmi,
        make_node_id,
    )

    assert len(synth_nodes) == 1
    assert synth_nodes[0]["name"] == "MyList.sort"


def test_duck_type_integration():
    """After guessing, ClassMethodIndex should enable duck type IMPLEMENTS matching."""
    cls = make_class_node("MyList", line=1, bases=("list",))
    fn = make_function_node(
        "use",
        line=5,
        parameters=(Parameter(name="m", type_annotation="MyList", default=None),),
    )
    call_a = make_call_node("append", receiver="m", context="use", line=7)
    call_e = make_call_node("extend", receiver="m", context="use", line=8)
    fnode = make_file_node(path="app.py", children=(cls, fn, call_a, call_e))

    sym = SymbolIndex([fnode], make_node_id)
    imp = ImportIndex([fnode], make_node_id)
    cmi = ClassMethodIndex([fnode], make_node_id)

    resolve_inherited_methods([fnode], sym, imp, cmi, make_node_id)

    methods = cmi.get_methods("MyList")
    assert "MyList.append" in methods
    assert "MyList.extend" in methods
