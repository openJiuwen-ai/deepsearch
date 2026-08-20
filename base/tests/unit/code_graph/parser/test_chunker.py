"""Tests for the chunker module."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.chunker import Chunk, ChunkEdge, chunks_from_file, chunks_from_file_nodes
from openjiuwen_search_base.codegraph.parser.constants import EdgeType, NodeType


def _parse(source: str, *, run_resolver: bool = False) -> tuple[list[Chunk], list[ChunkEdge]]:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        file_node = asyncio.run(parse_file(path))
        return chunks_from_file(file_node, run_resolver=run_resolver)
    finally:
        path.unlink()


class TestTopLevelOnly:
    def test_class_with_method_collapses(self):
        src = "class Foo:\n    def bar(self, x):\n        pass\n"
        chunks, _ = _parse(src)
        fn_chunks = [c for c in chunks if c.node_type == NodeType.FUNCTION]
        cls_chunks = [c for c in chunks if c.node_type == NodeType.CLASS]
        assert len(fn_chunks) == 0
        assert len(cls_chunks) == 1
        assert "Foo.bar" in cls_chunks[0].collapsed_names
        assert cls_chunks[0].span.line_start <= 1
        assert cls_chunks[0].span.line_end >= 3

    def test_nested_function_collapses(self):
        src = "def outer():\n    def inner(x):\n        return x\n"
        chunks, _ = _parse(src)
        fn_chunks = [c for c in chunks if c.node_type == NodeType.FUNCTION]
        assert len(fn_chunks) == 1
        assert fn_chunks[0].name == "outer"
        assert "outer.inner" in fn_chunks[0].collapsed_names

    def test_class_property_collapses(self):
        src = "class Cfg:\n    debug: bool = False\n"
        chunks, _ = _parse(src)
        prop_chunks = [c for c in chunks if c.node_type == NodeType.PROPERTY]
        cls_chunks = [c for c in chunks if c.node_type == NodeType.CLASS]
        assert len(prop_chunks) == 0
        assert len(cls_chunks) == 1
        assert any("debug" in n for n in cls_chunks[0].collapsed_names)

    def test_file_chunk_always_present(self):
        chunks, _ = _parse("def top():\n    pass\n")
        file_chunks = [c for c in chunks if c.node_type == NodeType.FILE]
        assert len(file_chunks) == 1

    def test_no_chunks_for_imports(self):
        src = "from typing import List\n\ndef f(x: List) -> None:\n    pass\n"
        chunks, _ = _parse(src)
        assert all(c.node_type != NodeType.IMPORT for c in chunks)


class TestFunctionSignatures:
    def test_simple_function(self):
        chunks, _ = _parse("def hello(name):\n    return name\n")
        fn_chunks = [c for c in chunks if c.node_type == NodeType.FUNCTION]
        assert len(fn_chunks) == 1
        assert fn_chunks[0].signature == "def hello(name)"

    def test_async_function(self):
        chunks, _ = _parse("async def fetch(url):\n    pass\n")
        fn_chunks = [c for c in chunks if c.node_type == NodeType.FUNCTION]
        assert fn_chunks[0].signature == "async def fetch(url)"

    def test_return_type_in_signature(self):
        chunks, _ = _parse("def add(a, b) -> int:\n    return a + b\n")
        fn_chunks = [c for c in chunks if c.node_type == NodeType.FUNCTION]
        assert fn_chunks[0].signature == "def add(a, b) -> int"

    def test_method_folded_into_class_signature(self):
        src = "class Foo:\n    def bar(self, x):\n        pass\n"
        chunks, _ = _parse(src)
        cls_chunks = [c for c in chunks if c.node_type == NodeType.CLASS]
        assert cls_chunks[0].signature == "class Foo"
        assert "Foo.bar" in cls_chunks[0].collapsed_names


class TestPropertySignatures:
    def test_typed_property(self):
        chunks, _ = _parse("X: int = 42\n")
        prop_chunks = [c for c in chunks if c.node_type == NodeType.PROPERTY]
        assert len(prop_chunks) == 1
        assert prop_chunks[0].signature == "X: int = 42"

    def test_untyped_property(self):
        chunks, _ = _parse("Y = 'hello'\n")
        prop_chunks = [c for c in chunks if c.node_type == NodeType.PROPERTY]
        assert prop_chunks[0].signature == "Y = 'hello'"


class TestContext:
    def test_top_level_has_no_context(self):
        chunks, _ = _parse("def top():\n    pass\n")
        fn_chunks = [c for c in chunks if c.node_type == NodeType.FUNCTION]
        assert fn_chunks[0].context == ()


class TestChunkText:
    def test_signature_prepended_to_source(self):
        chunks, _ = _parse("def f(x) -> int:\n    return x\n")
        fn_chunks = [c for c in chunks if c.node_type == NodeType.FUNCTION]
        # Source already starts with the signature line; do not duplicate it.
        assert fn_chunks[0].signature == "def f(x) -> int"
        assert fn_chunks[0].text.startswith("def f(x) -> int")
        assert not fn_chunks[0].text.startswith("def f(x) -> int\ndef f(x) -> int")

    def test_signature_not_duplicated_for_class(self):
        chunks, _ = _parse("class Canvas:\n    pass\n")
        cls = next(c for c in chunks if c.node_type == NodeType.CLASS)
        assert cls.signature == "class Canvas"
        assert cls.text.startswith("class Canvas:")
        assert not cls.text.startswith("class Canvas\nclass Canvas")

    def test_no_signature_when_disabled(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("def f():\n    pass\n")
            path = Path(f.name)
        try:
            file_node = asyncio.run(parse_file(path))
            chunks, _ = chunks_from_file(file_node, include_signature=False, run_resolver=False)
            fn_chunks = [c for c in chunks if c.node_type == NodeType.FUNCTION]
            assert fn_chunks[0].signature is None
        finally:
            path.unlink()

    def test_docstring_fallback(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write('def f():\n    """Doc."""\n    pass\n')
            path = Path(f.name)
        try:
            file_node = asyncio.run(parse_file(path))
            chunks, _ = chunks_from_file(file_node, include_source=False, include_docstring=True, run_resolver=False)
            fn_chunks = [c for c in chunks if c.node_type == NodeType.FUNCTION]
            assert "Doc." in fn_chunks[0].text
        finally:
            path.unlink()

    def test_min_chars_filter(self):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("x = 1\n")
            path = Path(f.name)
        try:
            file_node = asyncio.run(parse_file(path))
            chunks, _ = chunks_from_file(file_node, min_chars=9999, run_resolver=False)
            # File chunk still emitted; symbol chunks filtered
            assert all(c.node_type == NodeType.FILE for c in chunks)
        finally:
            path.unlink()


class TestClassChunks:
    def test_class_has_signature(self):
        chunks, _ = _parse("class Foo:\n    pass\n")
        cls_chunks = [c for c in chunks if c.node_type == NodeType.CLASS]
        assert cls_chunks[0].signature == "class Foo"

    def test_class_with_bases(self):
        chunks, _ = _parse("class Bar(Base, Mixin):\n    pass\n")
        cls_chunks = [c for c in chunks if c.node_type == NodeType.CLASS]
        assert cls_chunks[0].signature == "class Bar(Base, Mixin)"

    def test_interface_signature(self):
        src = "from typing import Protocol\n\nclass Readable(Protocol):\n    def read(self) -> str: ...\n"
        chunks, _ = _parse(src)
        iface_chunks = [c for c in chunks if c.node_type == NodeType.INTERFACE]
        assert len(iface_chunks) == 1
        assert iface_chunks[0].signature == "interface Readable(Protocol)"

    def test_enum_signature(self):
        src = "from enum import Enum\n\nclass Color(Enum):\n    RED = 1\n"
        chunks, _ = _parse(src)
        enum_chunks = [c for c in chunks if c.node_type == NodeType.ENUM]
        assert len(enum_chunks) == 1
        assert enum_chunks[0].signature == "enum Color"


class TestCodeBlockChunks:
    def test_if_name_main(self):
        src = 'if __name__ == "__main__":\n    print("hello")\n'
        chunks, _ = _parse(src)
        cb_chunks = [c for c in chunks if c.node_type == NodeType.CODE_BLOCK]
        assert len(cb_chunks) == 1
        assert cb_chunks[0].signature == 'if __name__ == "__main__":'

    def test_consecutive_code_grouped(self):
        src = "for i in range(10):\n    print(i)\nprint('done')\n"
        chunks, _ = _parse(src)
        cb_chunks = [c for c in chunks if c.node_type == NodeType.CODE_BLOCK]
        assert len(cb_chunks) == 1
        assert "for i in range(10):" in cb_chunks[0].signature

    def test_code_between_definitions_separate(self):
        src = "print('start')\n\ndef f():\n    pass\n\nprint('end')\n"
        chunks, _ = _parse(src)
        cb_chunks = [c for c in chunks if c.node_type == NodeType.CODE_BLOCK]
        assert len(cb_chunks) == 2

    def test_assignment_expands_adjacent_code_block(self):
        src = "x = 42\nif True:\n    pass\n"
        chunks, _ = _parse(src)
        cb_chunks = [c for c in chunks if c.node_type == NodeType.CODE_BLOCK]
        assert len(cb_chunks) == 1
        assert cb_chunks[0].signature == "x = 42"
        assert cb_chunks[0].text == src.rstrip()


class TestStructuralEdges:
    def test_file_contains_top_level(self):
        chunks, edges = _parse("def f():\n    pass\n", run_resolver=False)
        contains = [e for e in edges if e.relation is EdgeType.CONTAINS]
        assert len(contains) >= 1
        file_id = next(c.id for c in chunks if c.node_type == NodeType.FILE)
        fn_id = next(c.id for c in chunks if c.node_type == NodeType.FUNCTION)
        assert any(e.source_chunk_id == file_id and e.target_chunk_id == fn_id for e in contains)

    def test_relations_attached_to_chunks(self):
        chunks, edges = _parse("def f():\n    pass\n", run_resolver=False)
        file_chunk = next(c for c in chunks if c.node_type == NodeType.FILE)
        assert len(file_chunk.relations) > 0
        assert all(isinstance(r, ChunkEdge) for r in file_chunk.relations)


class TestResolverEdges:
    def test_calls_remapped_with_original_endpoints(self):
        # Nested method CALLS folds into the class chunk; originals keep method ids.
        src = "class Foo:\n    def bar(self):\n        helper()\n\ndef helper():\n    pass\n"
        chunks, edges = _parse(src, run_resolver=True)
        cls = next(c for c in chunks if c.node_type == NodeType.CLASS)
        helper = next(c for c in chunks if c.node_type == NodeType.FUNCTION and c.name == "helper")
        calls = [e for e in edges if e.relation is EdgeType.CALLS]
        assert any(
            e.source_chunk_id == cls.id
            and e.target_chunk_id == helper.id
            and "bar" in e.original_lhs
            and "helper" in e.original_rhs
            for e in calls
        )

    def test_run_resolver_false_skips_semantic_edges(self):
        src = "class Foo:\n    def bar(self):\n        self.baz()\n    def baz(self):\n        pass\n"
        _, edges = _parse(src, run_resolver=False)
        assert all(e.relation is EdgeType.CONTAINS for e in edges)

    def test_multi_file_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = root / "lib.py"
            app = root / "app.py"
            lib.write_text("def helper():\n    return 1\n")
            app.write_text("from lib import helper\n\ndef main():\n    helper()\n")

            lib_node = asyncio.run(parse_file(lib))
            app_node = asyncio.run(parse_file(app))
            chunks, edges = chunks_from_file_nodes([lib_node, app_node], run_resolver=True)

            imports = [e for e in edges if e.relation is EdgeType.IMPORTS]
            assert len(imports) >= 1
            chunk_ids = {c.id for c in chunks}
            for e in imports:
                assert e.source_chunk_id in chunk_ids
                assert e.target_chunk_id in chunk_ids
                assert e.original_lhs
                assert e.original_rhs
