"""Tests for the indirect calls resolution pass."""

import asyncio
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph.parser.languages.c import CppParser
from openjiuwen_search_base.codegraph.parser.languages.c.hooks import CppHooks
from openjiuwen_search_base.codegraph.parser.languages.java.hooks import JavaHooks
from openjiuwen_search_base.codegraph.parser.languages.python.hooks import PythonHooks
from openjiuwen_search_base.codegraph.parser.models.core import BaseNode
from openjiuwen_search_base.codegraph.parser.resolver.indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from openjiuwen_search_base.codegraph.parser.resolver.passes.indirect_calls import (
    _strip_receiver,
    resolve_indirect_calls,
)


def _node_id(fp: str, node: BaseNode) -> str:
    return f"{fp}::{node.name}"


class TestStripReceiver:
    @staticmethod
    def test_simple_subscript():
        base, depth, deref = _strip_receiver("objects[i]")
        assert base == "objects"
        assert depth == 1
        assert deref is False

    @staticmethod
    def test_double_subscript():
        base, depth, deref = _strip_receiver("objects[f][objIndex]")
        assert base == "objects"
        assert depth == 2
        assert deref is False

    @staticmethod
    def test_self_dot_subscript():
        base, depth, deref = _strip_receiver("self.particles[0]")
        assert base == "self.particles"
        assert depth == 1
        assert deref is False

    @staticmethod
    def test_this_arrow_subscript():
        base, depth, deref = _strip_receiver("this->objects[i]")
        assert base == "this->objects"
        assert depth == 1
        assert deref is False

    @staticmethod
    def test_deref_prefix():
        base, depth, deref = _strip_receiver("*ptr")
        assert base == "ptr"
        assert depth == 0
        assert deref is True

    @staticmethod
    def test_no_subscript_no_deref():
        base, depth, deref = _strip_receiver("obj")
        assert base == "obj"
        assert depth == 0
        assert deref is False


class TestCppUnwrap:
    @pytest.fixture
    @staticmethod
    def hooks():
        return CppHooks()

    @staticmethod
    def test_vector_shared_ptr(hooks):
        result = hooks.unwrap_receiver_type("std::vector<std::shared_ptr<SceneObject>>", 1)
        assert result == "SceneObject"

    @staticmethod
    def test_nested_vector(hooks):
        result = hooks.unwrap_receiver_type("std::vector<std::vector<std::shared_ptr<SceneObject>>>", 2)
        assert result == "SceneObject"

    @staticmethod
    def test_raw_pointer(hooks):
        result = hooks.unwrap_receiver_type("SceneObject*", 0)
        assert result == "SceneObject"

    @staticmethod
    def test_map_subscript(hooks):
        result = hooks.unwrap_receiver_type("std::map<std::string, Material>", 1)
        assert result == "Material"

    @staticmethod
    def test_primitive_returns_none(hooks):
        result = hooks.unwrap_receiver_type("int*", 0)
        assert result is None

    @staticmethod
    def test_unique_ptr(hooks):
        result = hooks.unwrap_receiver_type("std::unique_ptr<Widget>", 0)
        assert result == "Widget"

    @staticmethod
    def test_const_vector(hooks):
        result = hooks.unwrap_receiver_type("const std::vector<Enemy>", 1)
        assert result == "Enemy"


class TestJavaUnwrap:
    @pytest.fixture
    @staticmethod
    def hooks():
        return JavaHooks()

    @staticmethod
    def test_array_subscript(hooks):
        result = hooks.unwrap_receiver_type("Enemy[]", 1)
        assert result == "Enemy"

    @staticmethod
    def test_2d_array(hooks):
        result = hooks.unwrap_receiver_type("SceneObject[][]", 2)
        assert result == "SceneObject"

    @staticmethod
    def test_primitive_array_returns_none(hooks):
        result = hooks.unwrap_receiver_type("int[]", 1)
        assert result is None

    @staticmethod
    def test_non_array_returns_none(hooks):
        result = hooks.unwrap_receiver_type("Enemy", 1)
        assert result is None


class TestPythonUnwrap:
    @pytest.fixture
    @staticmethod
    def hooks():
        return PythonHooks()

    @staticmethod
    def test_list_subscript(hooks):
        result = hooks.unwrap_receiver_type("list[Particle]", 1)
        assert result == "Particle"

    @staticmethod
    def test_dict_subscript(hooks):
        result = hooks.unwrap_receiver_type("dict[str, Enemy]", 1)
        assert result == "Enemy"

    @staticmethod
    def test_tuple_subscript(hooks):
        result = hooks.unwrap_receiver_type("tuple[Node, ...]", 1)
        assert result == "Node"

    @staticmethod
    def test_nested_list(hooks):
        result = hooks.unwrap_receiver_type("list[list[Foo]]", 2)
        assert result == "Foo"

    @staticmethod
    def test_no_generic_returns_none(hooks):
        result = hooks.unwrap_receiver_type("int", 1)
        assert result is None

    @staticmethod
    def test_typing_list(hooks):
        result = hooks.unwrap_receiver_type("List[Widget]", 1)
        assert result == "Widget"


class TestIndirectCallsResolution:
    """End-to-end test using a real C++ parser."""

    @staticmethod
    def test_resolve_subscripted_method_call():
        cpp_parser = CppParser()
        code = b"""
class SceneObject {
public:
    void setMaterial(int mat) {}
    void render() {}
};

class MyCamera {
    std::vector<SceneObject*> objects;

    void update() {
        objects[0]->setMaterial(1);
        objects[0]->render();
    }
};
"""
        fnode = asyncio.run(cpp_parser.parse(Path("test.cpp"), code))
        file_nodes = [fnode]

        symbol_idx = SymbolIndex(file_nodes, _node_id)
        import_idx = ImportIndex(file_nodes, _node_id)
        class_method_idx = ClassMethodIndex(file_nodes, _node_id)

        hooks_map = {"cpp": CppHooks()}
        already_resolved: set[tuple[str, str, str]] = set()

        edges = resolve_indirect_calls(
            file_nodes,
            symbol_idx,
            import_idx,
            class_method_idx,
            _node_id,
            hooks_map,
            already_resolved,
        )

        # Should resolve at least one of setMaterial or render
        callees = {e.target_id.split("::")[-1] for e in edges}
        assert "SceneObject.setMaterial" in callees or any("setMaterial" in c for c in callees)
        assert all(e.confidence == 0.6 for e in edges)
        assert all(e.resolved_by == "indirect_receiver" for e in edges)
