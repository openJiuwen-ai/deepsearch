"""Tests for the graph export module."""

import asyncio
import gzip
import json
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.custom_types import SourceSpan
from openjiuwen_search_base.codegraph.parser.graph_export import export_graph, export_graph_from_file_nodes
from openjiuwen_search_base.codegraph.parser.models.structural import FileNode


def _parse_and_export(source: str) -> tuple[list[dict], list[dict]]:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        file_node = asyncio.run(parse_file(path))
        return export_graph_from_file_nodes([file_node])
    finally:
        path.unlink()


class TestNodeSerialization:
    @staticmethod
    def test_file_node_present():
        nodes, _ = _parse_and_export("x = 1\n")
        file_nodes = [n for n in nodes if n["type"] == "FileNode"]
        assert len(file_nodes) == 1
        assert "id" in file_nodes[0]
        assert file_nodes[0]["node_type"] == "file"

    @staticmethod
    def test_node_has_required_fields():
        nodes, _ = _parse_and_export("def hello():\n    pass\n")
        fn_nodes = [n for n in nodes if n["type"] == "FunctionNode"]
        assert len(fn_nodes) == 1
        fn = fn_nodes[0]
        assert "id" in fn
        assert "type" in fn
        assert "name" in fn
        assert "node_type" in fn
        assert "path" in fn
        assert "span" in fn

    @staticmethod
    def test_signature_included():
        nodes, _ = _parse_and_export("class Foo(Bar):\n    pass\n")
        cls_nodes = [n for n in nodes if n["type"] == "ClassNode"]
        assert cls_nodes[0]["signature"] == "class Foo(Bar)"

    @staticmethod
    def test_ids_unique():
        src = "def a():\n    pass\n\ndef b():\n    pass\n\nclass C:\n    pass\n"
        nodes, _ = _parse_and_export(src)
        ids = [n["id"] for n in nodes]
        assert len(ids) == len(set(ids))

    @staticmethod
    def test_code_block_id_uses_line():
        nodes, _ = _parse_and_export("if True:\n    pass\n")
        cb_nodes = [n for n in nodes if n["type"] == "CodeBlockNode"]
        assert len(cb_nodes) == 1
        assert "__code_block_L" in cb_nodes[0]["id"]
        assert cb_nodes[0]["name"].endswith(".py@L1")

    @staticmethod
    def test_code_block_name_uses_relative_path_and_line(tmp_path):
        source = tmp_path / "pkg" / "app.py"
        source.parent.mkdir()
        source.write_text("print('hello')\n")
        file_node = asyncio.run(parse_file(source))
        nodes, _ = export_graph_from_file_nodes([file_node], root=str(tmp_path))
        code_blocks = [node for node in nodes if node["type"] == "CodeBlockNode"]
        assert code_blocks[0]["name"] == "pkg/app.py@L1"

    @staticmethod
    def test_lambda_id_uses_name_without_extra_line():
        nodes, _ = _parse_and_export("f = lambda x: x\n")
        lambdas = [n for n in nodes if n.get("func_type") == "lambda"]
        assert len(lambdas) == 1
        ln = lambdas[0]
        assert ln["name"] == "lambda(x)@L1@C5"
        assert ln["id"].endswith("::lambda(x)@L1@C5")
        assert not ln["id"].endswith("@L1@C5@L1")


class TestContainsEdges:
    @staticmethod
    def test_file_contains_children():
        _, edges = _parse_and_export("def f():\n    pass\n\nx = 1\n")
        contains = [e for e in edges if e["relation"] == "CONTAINS"]
        assert len(contains) >= 2

    @staticmethod
    def test_class_contains_methods():
        src = "class Foo:\n    def bar(self):\n        pass\n"
        _, edges = _parse_and_export(src)
        contains = [e for e in edges if e["relation"] == "CONTAINS"]
        class_to_method = [e for e in contains if "Foo" in e["source"] and "Foo.bar" in e["target"]]
        assert len(class_to_method) == 1


class TestExpectsEdges:
    @staticmethod
    def test_function_expects_duck_type():
        src = "def f(obj):\n    return obj.method()\n"
        nodes, edges = _parse_and_export(src)
        expects = [e for e in edges if e["relation"] == "EXPECTS"]
        assert len(expects) == 1
        assert "f" in expects[0]["source"]
        assert "DuckType" in expects[0]["target"]


class TestIsSubsetOfEdges:
    @staticmethod
    def test_subset_edge_produced():
        src = "def f1(obj):\n    return obj.embed('x')\n\ndef f2(obj):\n    return obj.embed('x') + obj.search('y')\n"
        nodes, edges = _parse_and_export(src)
        subset_edges = [e for e in edges if e["relation"] == "IS_SUBSET_OF"]
        assert len(subset_edges) == 1
        assert "embed" in subset_edges[0]["source"]
        assert "embed" in subset_edges[0]["target"]
        assert "search" in subset_edges[0]["target"]

    @staticmethod
    def test_no_subset_for_equal_sets():
        src = "def f1(obj):\n    return obj.method()\n\ndef f2(obj):\n    return obj.method()\n"
        _, edges = _parse_and_export(src)
        subset_edges = [e for e in edges if e["relation"] == "IS_SUBSET_OF"]
        assert len(subset_edges) == 0


class TestExportGraphWritesFiles:
    @staticmethod
    def test_writes_jsonl_files():
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("def hello():\n    pass\n")
            src_path = Path(f.name)
        with tempfile.TemporaryDirectory() as out_dir:
            nodes_path, edges_path, jcp_path, _, _ = asyncio.run(export_graph([src_path], out_dir))
            assert nodes_path.exists()
            assert edges_path.exists()
            assert jcp_path.exists()

            with open(nodes_path, encoding="utf-8") as nf:
                lines = nf.readlines()
                assert len(lines) >= 2
                for line in lines:
                    obj = json.loads(line)
                    assert "id" in obj

            with open(edges_path, encoding="utf-8") as ef:
                lines = ef.readlines()
                assert len(lines) >= 1
                for line in lines:
                    obj = json.loads(line)
                    assert "relation" in obj

            with gzip.open(jcp_path, "rt", encoding="utf-8") as jf:
                jcp_lines = jf.readlines()
                markers = [line for line in jcp_lines if '"marker"' in line]
                assert len(markers) == 2
                assert json.loads(markers[0]) == {"marker": "break"}
                assert json.loads(markers[1]) == {"marker": "break"}
        src_path.unlink()


def _file_node(path: str) -> FileNode:
    return FileNode(
        node_type=NodeType.FILE,
        name=Path(path).name,
        span=SourceSpan(1, 1, 0, 0),
        path=path,
        language="python",
    )


class TestFolderSynthesis:
    @staticmethod
    def test_resolved_root_does_not_walk_filesystem_root():
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as file_obj:
            file_obj.write("def hello():\n    pass\n")
            src_path = Path(file_obj.name)
        try:
            file_node = asyncio.run(parse_file(src_path))
            nodes, edges = export_graph_from_file_nodes(
                [file_node],
                root=str(src_path.parent.resolve()),
                run_resolver=False,
                show_progress=False,
            )
        finally:
            src_path.unlink()

        folder_nodes = [node for node in nodes if node["type"] == "FolderNode"]
        assert len(folder_nodes) == 1
        assert folder_nodes[0]["id"] == "folder::."
        file_nodes = [node for node in nodes if node["type"] == "FileNode"]
        assert len(file_nodes) == 1
        assert any(edge["source"] == "folder::." and edge["target"] == file_nodes[0]["id"] for edge in edges)

    @staticmethod
    def test_path_outside_root_does_not_create_filesystem_folders():
        nodes, edges = export_graph_from_file_nodes(
            [_file_node("/unrelated/outside.py")],
            root="/project",
            run_resolver=False,
            show_progress=False,
        )
        folder_nodes = [node for node in nodes if node["type"] == "FolderNode"]
        assert [node["id"] for node in folder_nodes] == ["folder::."]
        assert all(node["path"] != "/" for node in nodes)
        file_nodes = [node for node in nodes if node["type"] == "FileNode"]
        assert any(edge["source"] == "folder::." and edge["target"] == file_nodes[0]["id"] for edge in edges)

    @staticmethod
    def test_nested_folder_language_tags_stay_relative(tmp_path: Path):
        source = tmp_path / "pkg" / "mod" / "app.py"
        source.parent.mkdir(parents=True)
        source.write_text("x = 1\n")
        file_node = asyncio.run(parse_file(source))
        nodes, _ = export_graph_from_file_nodes(
            [file_node],
            root=str(tmp_path.resolve()),
            run_resolver=False,
            show_progress=False,
        )
        folders = {node["name"]: node for node in nodes if node["type"] == "FolderNode"}
        root_folder = next(node for node in nodes if node["id"] == "folder::.")
        assert "pkg" in folders
        assert "mod" in folders
        assert "lang:python" in folders["pkg"]["tags"]
        assert "lang:python" in folders["mod"]["tags"]
        assert "lang:python" in root_folder["tags"]
        assert all(not Path(node["id"].removeprefix("folder::")).is_absolute() for node in folders.values())
