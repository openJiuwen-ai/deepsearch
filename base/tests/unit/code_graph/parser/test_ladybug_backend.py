"""Tests for the Ladybug export backend and query helpers."""

import asyncio
import builtins
import os
import tempfile
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph.backends import ladybug as ladybug_backend
from openjiuwen_search_base.codegraph.lbug_query import (
    LadybugUnavailableError,
    LadybugWriteConfig,
    get_node,
    neighbors,
    search_nodes,
)
from openjiuwen_search_base.codegraph.parser.graph_export import export_graph_to_backends


def _write_source_file(source: str) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as file_obj:
        file_obj.write(source)
        return Path(file_obj.name)


def _require_ladybug_integration() -> None:
    if os.environ.get("JIUWEN_RUN_LADYBUG_TESTS") != "1":
        pytest.skip("Set JIUWEN_RUN_LADYBUG_TESTS=1 to run real Ladybug integration tests.")


class TestLadybugExport:
    @staticmethod
    def test_table_names_are_type_specific():
        assert ladybug_backend._node_table_name("FunctionNode") == "JiuwenNode_FunctionNode"
        assert (
            ladybug_backend._edge_table_name("CALLS", "FunctionNode", "FunctionNode")
            == "JiuwenEdge_CALLS__FunctionNode__FunctionNode"
        )

    @staticmethod
    def test_round_trip_with_small_batches():
        _require_ladybug_integration()
        pytest.importorskip("real_ladybug", reason="real_ladybug not installed")
        src_path = _write_source_file("def hello():\n    pass\n")
        try:
            with tempfile.TemporaryDirectory() as out_dir:
                artifacts, nodes, edges = asyncio.run(
                    export_graph_to_backends(
                        [src_path],
                        out_dir,
                        root=src_path.parent,
                        backend="ladybug",
                        node_batch_size=1,
                        edge_batch_size=1,
                        db_export_workers=2,
                    )
                )
                assert artifacts.ladybug_path is not None
                assert artifacts.ladybug_path.exists()
                assert artifacts.nodes_path is None
                assert artifacts.edges_path is None
                assert artifacts.jcp_path is None

                function_node = next(node for node in nodes if node["type"] == "FunctionNode")
                file_node = next(node for node in nodes if node["type"] == "FileNode")

                fetched = get_node(artifacts.ladybug_path, function_node["id"])
                assert fetched is not None
                assert fetched["id"] == function_node["id"]
                assert fetched["span"] == function_node["span"]

                matches = search_nodes(artifacts.ladybug_path, name="hello", node_type="function", limit=5)
                assert any(match["id"] == function_node["id"] for match in matches)

                adjacent = neighbors(artifacts.ladybug_path, file_node["id"], relation="CONTAINS")
                assert any(item["node"]["id"] == function_node["id"] for item in adjacent)

                node_count = ladybug_backend.execute(artifacts.ladybug_path, "MATCH (n) RETURN count(n)")
                edge_count = ladybug_backend.execute(artifacts.ladybug_path, "MATCH ()-[e]->() RETURN count(e)")
                assert node_count == [[len(nodes)]]
                assert edge_count == [[len(edges)]]
        finally:
            src_path.unlink()

    @staticmethod
    def test_missing_bindings_raise_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        real_import = builtins.__import__

        def fake_import(name: str, _globals=None, _locals=None, fromlist=(), level: int = 0):
            if name == "real_ladybug":
                raise ImportError("not installed")
            return real_import(name, _globals, _locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(LadybugUnavailableError, match="real_ladybug"):
            ladybug_backend.write_ladybug_graph(
                [],
                [],
                config=LadybugWriteConfig(path=tmp_path / "graph.lbug"),
            )
