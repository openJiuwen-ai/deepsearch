"""Tests for local variable extraction across all languages."""

import asyncio
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph.parser.models.core import (
    ClassNode,
    FunctionNode,
    LocalVarNode,
)
from openjiuwen_search_base.codegraph.parser.resolver.passes._utils import _strip_scope, match_name

# ---------------------------------------------------------------------------
# match_name / _strip_scope tests
# ---------------------------------------------------------------------------


class TestMatchNameScopeStripping:
    def test_plain_match(self):
        assert match_name("cam@L15@D0", "cam")

    def test_no_match(self):
        assert not match_name("cam@L15@D0", "other")

    def test_qualified_match(self):
        assert match_name("MyClass.method@L10@D1", "method", "MyClass.method")

    def test_overload_suffix_with_scope(self):
        assert match_name("Foo.bar(int)@L5@D0", "bar", "Foo.bar")

    def test_strip_scope_basic(self):
        assert _strip_scope("cam@L15@D0") == "cam"

    def test_strip_scope_no_suffix(self):
        assert _strip_scope("cam") == "cam"

    def test_strip_scope_preserves_qualified(self):
        assert _strip_scope("MyClass.method@L10@D1") == "MyClass.method"


# ---------------------------------------------------------------------------
# C/C++ local variable extraction
# ---------------------------------------------------------------------------


class TestCppLocalVars:
    @pytest.fixture
    def parser(self):
        from openjiuwen_search_base.codegraph.parser.languages.c.cpp_parse import CppParser

        return CppParser()

    def _parse(self, parser, code: bytes):
        return asyncio.run(parser.parse(Path("test.cpp"), code))

    def test_basic_local_extraction(self, parser):
        code = b"""
void foo() {
    MyCamera cam;
    int x = 5;
}
"""
        fnode = self._parse(parser, code)
        funcs = [c for c in fnode.children if isinstance(c, FunctionNode)]
        assert len(funcs) == 1
        locals_ = [c for c in funcs[0].children if isinstance(c, LocalVarNode)]
        assert len(locals_) == 1
        assert locals_[0].name == "cam@L3@D0"
        assert locals_[0].type_annotation == "MyCamera"

    def test_nested_block_depth(self, parser):
        code = b"""
void foo() {
    std::vector<int> items;
    if (true) {
        MyObject obj;
    }
}
"""
        fnode = self._parse(parser, code)
        funcs = [c for c in fnode.children if isinstance(c, FunctionNode)]
        locals_ = [c for c in funcs[0].children if isinstance(c, LocalVarNode)]
        names = {lv.name for lv in locals_}
        assert "items@L3@D0" in names
        assert "obj@L5@D1" in names

    def test_for_range_loop(self, parser):
        code = b"""
void foo() {
    for (auto& obj : objects) {
        BVHTree tree;
        tree.check();
    }
}
"""
        fnode = self._parse(parser, code)
        funcs = [c for c in fnode.children if isinstance(c, FunctionNode)]
        locals_ = [c for c in funcs[0].children if isinstance(c, LocalVarNode)]
        assert any("tree" in lv.name for lv in locals_)

    def test_primitives_skipped(self, parser):
        code = b"""
void foo() {
    int x = 5;
    float y = 3.14;
    double z = 2.71;
    bool flag = true;
    MyClass obj;
}
"""
        fnode = self._parse(parser, code)
        funcs = [c for c in fnode.children if isinstance(c, FunctionNode)]
        locals_ = [c for c in funcs[0].children if isinstance(c, LocalVarNode)]
        assert len(locals_) == 1
        assert "obj" in locals_[0].name

    def test_template_type_annotation(self, parser):
        code = b"""
void foo() {
    std::vector<SceneObject*> objects;
}
"""
        fnode = self._parse(parser, code)
        funcs = [c for c in fnode.children if isinstance(c, FunctionNode)]
        locals_ = [c for c in funcs[0].children if isinstance(c, LocalVarNode)]
        assert len(locals_) == 1
        assert locals_[0].type_annotation == "std::vector<SceneObject*>"


# ---------------------------------------------------------------------------
# Java local variable extraction
# ---------------------------------------------------------------------------


class TestJavaLocalVars:
    @pytest.fixture
    def parser(self):
        from openjiuwen_search_base.codegraph.parser.languages.java.parse import JavaParser

        return JavaParser()

    def _parse(self, parser, code: bytes):
        return asyncio.run(parser.parse(Path("Test.java"), code))

    def test_basic_extraction(self, parser):
        code = b"""
public class Game {
    public void run() {
        List<Enemy> enemies = new ArrayList<>();
        int x = 5;
    }
}
"""
        fnode = self._parse(parser, code)
        cls = [c for c in fnode.children if isinstance(c, ClassNode)][0]
        method = [c for c in cls.children if isinstance(c, FunctionNode)][0]
        locals_ = [c for c in method.children if isinstance(c, LocalVarNode)]
        assert len(locals_) == 1
        assert locals_[0].name == "enemies@L4@D0"
        assert locals_[0].type_annotation == "List<Enemy>"

    def test_nested_scopes(self, parser):
        code = b"""
public class Game {
    public void run() {
        if (true) {
            Camera cam = new Camera();
        }
        for (int i = 0; i < 10; i++) {
            MyObject obj = getObject(i);
        }
    }
}
"""
        fnode = self._parse(parser, code)
        cls = [c for c in fnode.children if isinstance(c, ClassNode)][0]
        method = [c for c in cls.children if isinstance(c, FunctionNode)][0]
        locals_ = [c for c in method.children if isinstance(c, LocalVarNode)]
        names = {lv.name for lv in locals_}
        assert any("cam" in n and "@D1" in n for n in names)
        assert any("obj" in n and "@D1" in n for n in names)

    def test_primitives_skipped(self, parser):
        code = b"""
public class Game {
    public void run() {
        int x = 5;
        String name = "hello";
        Camera cam = new Camera();
    }
}
"""
        fnode = self._parse(parser, code)
        cls = [c for c in fnode.children if isinstance(c, ClassNode)][0]
        method = [c for c in cls.children if isinstance(c, FunctionNode)][0]
        locals_ = [c for c in method.children if isinstance(c, LocalVarNode)]
        assert len(locals_) == 1
        assert "cam" in locals_[0].name


# ---------------------------------------------------------------------------
# Python local variable extraction
# ---------------------------------------------------------------------------


class TestPythonLocalVars:
    @pytest.fixture
    def parser(self):
        from openjiuwen_search_base.codegraph.parser.languages.python.parse import PythonParser

        return PythonParser()

    def _parse(self, parser, code: bytes):
        return asyncio.run(parser.parse(Path("test.py"), code))

    def test_nested_block_extraction(self, parser):
        code = b"""def process():
    items: list[Enemy] = []
    if True:
        cam: Camera = Camera()
    for i in range(10):
        obj: MyObject = get_object(i)
"""
        fnode = self._parse(parser, code)
        funcs = [c for c in fnode.children if isinstance(c, FunctionNode)]
        locals_ = [c for c in funcs[0].children if isinstance(c, LocalVarNode)]
        names = {lv.name for lv in locals_}
        assert "items" in names
        assert "cam" in names
        assert "obj" in names

    def test_no_scope_suffix(self, parser):
        """Python locals have plain names (function-scoped)."""
        code = b"""def foo():
    x: Foo = Foo()
"""
        fnode = self._parse(parser, code)
        funcs = [c for c in fnode.children if isinstance(c, FunctionNode)]
        locals_ = [c for c in funcs[0].children if isinstance(c, LocalVarNode)]
        assert len(locals_) == 1
        assert locals_[0].name == "x"
        assert "@L" not in locals_[0].name


# ---------------------------------------------------------------------------
# TypeScript local variable extraction
# ---------------------------------------------------------------------------


class TestTypeScriptLocalVars:
    @pytest.fixture
    def parser(self):
        from openjiuwen_search_base.codegraph.parser.languages.typescript.parse import TypeScriptParser

        return TypeScriptParser()

    def _parse(self, parser, code: bytes):
        return asyncio.run(parser.parse(Path("test.ts"), code))

    def test_nested_blocks(self, parser):
        code = b"""
function process() {
    const items: Enemy[] = [];
    if (true) {
        const cam: Camera = new Camera();
    }
    for (let i = 0; i < 10; i++) {
        const obj: MyObject = getObject(i);
    }
}
"""
        fnode = self._parse(parser, code)
        funcs = [c for c in fnode.children if isinstance(c, FunctionNode)]
        locals_ = [c for c in funcs[0].children if isinstance(c, LocalVarNode)]
        names = {lv.name for lv in locals_}
        assert any("items" in n and "@D0" in n for n in names)
        assert any("cam" in n and "@D1" in n for n in names)
        assert any("obj" in n and "@D1" in n for n in names)

    def test_scope_suffix_present(self, parser):
        code = b"function test() { let x: Foo = new Foo(); }"
        fnode = self._parse(parser, code)
        funcs = [c for c in fnode.children if isinstance(c, FunctionNode)]
        locals_ = [c for c in funcs[0].children if isinstance(c, LocalVarNode)]
        assert len(locals_) == 1
        assert locals_[0].name == "x@L1@D0"


# ---------------------------------------------------------------------------
# Integration: indirect resolution using LocalVarNode
# ---------------------------------------------------------------------------


class TestIndirectResolutionWithLocalVars:
    """Verify that cam[i].renderFrame() resolves via LocalVarNode type info."""

    def test_vector_local_resolves(self):
        from openjiuwen_search_base.codegraph.parser.languages.c.cpp_parse import CppParser
        from openjiuwen_search_base.codegraph.parser.languages.c.hooks import CppHooks
        from openjiuwen_search_base.codegraph.parser.resolver.indexes import ClassMethodIndex, ImportIndex, SymbolIndex
        from openjiuwen_search_base.codegraph.parser.resolver.passes.indirect_calls import resolve_indirect_calls

        code = b"""
class MyCamera {
public:
    void renderFrame() {}
    void calibrateRays() {}
};

int main() {
    std::vector<MyCamera> cam;
    cam[0].renderFrame();
    cam[0].calibrateRays();
    return 0;
}
"""
        parser = CppParser()
        fnode = asyncio.run(parser.parse(Path("test.cpp"), code))
        file_nodes = [fnode]

        def _node_id(fp, node):
            return f"{fp}::{node.name}"

        symbol_idx = SymbolIndex(file_nodes, _node_id)
        import_idx = ImportIndex(file_nodes, _node_id)
        class_method_idx = ClassMethodIndex(file_nodes, _node_id)
        hooks_map = {"cpp": CppHooks()}

        edges = resolve_indirect_calls(
            file_nodes,
            symbol_idx,
            import_idx,
            class_method_idx,
            _node_id,
            hooks_map,
            set(),
        )

        callees = {e.target_id.split("::")[-1] for e in edges}
        assert "MyCamera.renderFrame" in callees
        assert "MyCamera.calibrateRays" in callees
        assert all(e.confidence == 0.6 for e in edges)
