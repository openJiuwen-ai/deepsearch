# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for expand_imports tool and KG IMPORTS edges (no tree-sitter / bm25s)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openjiuwen_codesearch.algorithm.prompts import load_prompt
from openjiuwen_codesearch.algorithm.prompts.retropus import build_system_prompt
from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import RetrievalTools
from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig
from openjiuwen_codesearch.retropus.graph.graph_types import (
    FileNode,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
)
from openjiuwen_codesearch.retropus.graph.imports import (
    ImportIndex,
    build_import_index,
    build_imports_edges,
    import_index_from_kg,
    resolve_import_targets,
    resolve_module_to_existing_file,
)


class _FakeKG:
    def __init__(self, rels: list[str] | None = None):
        self._rels = rels or []

    def get_file_nodes(self):
        out = []
        for rel in self._rels:
            node = SimpleNamespace(relative_path=rel)
            out.append(SimpleNamespace(node=node))
        return out


class _FakeRetriever:
    pass


class _KgWithImports:
    """Minimal KG stand-in exposing IMPORTS edges built by ``build_imports_edges``."""

    def __init__(self, edges, labels, file_nodes):
        self._edges = edges
        self._labels = labels
        self._file_nodes = file_nodes

    def get_imports_edges(self):
        return list(self._edges)

    def get_file_nodes(self):
        return list(self._file_nodes)

    def get_imports_label(self, source_id: int, target_id: int) -> str:
        return self._labels.get((source_id, target_id), "")

    def get_import_neighbors(self, file_node):
        out = []
        seen = {file_node.node_id}
        for e in self._edges:
            neighbor = None
            if e.source.node_id == file_node.node_id:
                neighbor = e.target
            elif e.target.node_id == file_node.node_id:
                neighbor = e.source
            if neighbor is None or neighbor.node_id in seen:
                continue
            seen.add(neighbor.node_id)
            out.append(neighbor)
        return out


def _write_pkg(repo: Path) -> list[str]:
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "from pkg import b\nfrom pkg.c import Helper\n",
        "pkg/b.py": "import pkg.c as cmod\n",
        "pkg/c.py": "class Helper:\n    pass\n",
        "pkg/d.py": "from pkg.a import something\n",
        "pkg/tests/test_a.py": "from pkg.a import something\n",
    }
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return list(files.keys())


def _file_nodes(rels: list[str]) -> list[KnowledgeGraphNode]:
    nodes = []
    for i, rel in enumerate(rels):
        nodes.append(
            KnowledgeGraphNode(
                i,
                FileNode(basename=Path(rel).name, relative_path=rel),
            )
        )
    return nodes


def _tools(repo: Path, rels: list[str], **cfg) -> RetrievalTools:
    opts = dict(
        feat_expand_imports=True,
        feat_ban_tests=False,
        feat_second_file_probe=False,
        feat_same_file_expand=False,
        feat_anti_early_finish=False,
        feat_inherits_expand=False,
    )
    opts.update(cfg)
    return RetrievalTools(
        _FakeKG(rels),
        _FakeRetriever(),
        repo,
        RetropusSearchAgentConfig(**opts),
    )


def test_expand_imports_off_by_default():
    assert RetropusSearchAgentConfig().feat_expand_imports is False
    appendix = load_prompt("expand_imports")
    assert appendix not in build_system_prompt()
    assert appendix in build_system_prompt(expand_imports=True)


def test_resolve_module_and_outgoing_edges(tmp_path: Path):
    rels = set(_write_pkg(tmp_path))
    assert resolve_module_to_existing_file("pkg/c", rels) == "pkg/c.py"
    assert resolve_module_to_existing_file("pkg/missing", rels) is None

    text = (tmp_path / "pkg/a.py").read_text(encoding="utf-8")
    targets = dict(resolve_import_targets(text, "pkg/a.py", rels))
    assert targets["pkg/b.py"] == "b"
    assert targets["pkg/c.py"] == "Helper"


def test_build_import_index_inverted(tmp_path: Path):
    rels = _write_pkg(tmp_path)
    index = build_import_index(tmp_path, rels)
    assert isinstance(index, ImportIndex)

    out_a = {t for t, _ in index.imports_of("pkg/a.py")}
    assert out_a == {"pkg/b.py", "pkg/c.py"}

    importers_a = {src for src, _ in index.importers_of("pkg/a.py")}
    assert "pkg/d.py" in importers_a
    assert "pkg/tests/test_a.py" in importers_a

    importers_c = {src for src, _ in index.importers_of("pkg/c.py")}
    assert "pkg/a.py" in importers_c
    assert "pkg/b.py" in importers_c


def test_build_imports_edges_and_tool_from_kg(tmp_path: Path):
    rels = _write_pkg(tmp_path)
    file_nodes = _file_nodes(rels)
    edges, labels = build_imports_edges(file_nodes, tmp_path)
    assert edges
    assert all(e.type == KnowledgeGraphEdgeType.imports for e in edges)

    pairs = {
        (
            e.source.node.relative_path.replace("\\", "/"),
            e.target.node.relative_path.replace("\\", "/"),
        )
        for e in edges
    }
    assert ("pkg/a.py", "pkg/b.py") in pairs
    assert ("pkg/a.py", "pkg/c.py") in pairs
    assert ("pkg/d.py", "pkg/a.py") in pairs

    kg = _KgWithImports(edges, labels, file_nodes)
    a_node = next(
        n for n in file_nodes if n.node.relative_path.replace("\\", "/") == "pkg/a.py"
    )
    neighbor_paths = {
        n.node.relative_path.replace("\\", "/") for n in kg.get_import_neighbors(a_node)
    }
    assert "pkg/b.py" in neighbor_paths
    assert "pkg/d.py" in neighbor_paths

    a_to_b = next(
        e
        for e in edges
        if e.source.node.relative_path.endswith("pkg/a.py")
        and e.target.node.relative_path.endswith("pkg/b.py")
    )
    assert kg.get_imports_label(a_to_b.source.node_id, a_to_b.target.node_id) == "b"

    index = import_index_from_kg(kg)
    assert "pkg/b.py" in {t for t, _ in index.imports_of("pkg/a.py")}
    assert "pkg/d.py" in {s for s, _ in index.importers_of("pkg/a.py")}

    tools = RetrievalTools(
        kg,
        _FakeRetriever(),
        tmp_path,
        RetropusSearchAgentConfig(feat_expand_imports=True, feat_ban_tests=False),
    )
    tools.add_context("pkg/a.py", 1, 2)
    out = tools.expand_imports(direction="both", depth=1)
    assert "Import neighbors from knowledge-graph IMPORTS" in out
    assert "] imports pkg/b.py" in out
    assert "] imported_by pkg/d.py" in out


def test_expand_imports_tool_out_and_in(tmp_path: Path):
    rels = _write_pkg(tmp_path)
    tools = _tools(tmp_path, rels)
    tools.add_context("pkg/a.py", 1, 2)

    both = tools.expand_imports(direction="both", depth=1)
    assert "imports pkg/b.py" in both or "imports pkg/c.py" in both
    assert "imported_by pkg/d.py" in both

    out_only = tools.expand_imports(direction="out", depth=1)
    assert "] imports pkg/b.py" in out_only or "] imports pkg/c.py" in out_only
    assert "] imported_by " not in out_only

    in_only = tools.expand_imports(path="pkg/a.py", direction="in", depth=1)
    assert "] imported_by pkg/d.py" in in_only
    assert "] imports pkg/" not in in_only


def test_expand_imports_respects_ban_tests(tmp_path: Path):
    rels = _write_pkg(tmp_path)
    tools = _tools(tmp_path, rels, feat_ban_tests=True)
    out = tools.expand_imports(path="pkg/a.py", direction="in", depth=1)
    assert "pkg/d.py" in out
    assert "test_a.py" not in out


def test_expand_imports_marks_second_file_probe(tmp_path: Path):
    rels = _write_pkg(tmp_path)
    tools = _tools(tmp_path, rels, feat_second_file_probe=True)
    tools.add_context("pkg/a.py", 1, 2)
    blocked = tools.finish()
    assert blocked.startswith("finish blocked")
    tools.expand_imports(direction="out")
    assert tools.finish() == "Finishing retrieval."


def test_expand_imports_not_in_schema_when_off(tmp_path: Path):
    rels = _write_pkg(tmp_path)
    tools = _tools(tmp_path, rels, feat_expand_imports=False)
    names = [s["function"]["name"] for s in tools.tool_schemas()]
    assert "expand_imports" not in names

    tools_on = _tools(tmp_path, rels, feat_expand_imports=True)
    names_on = [s["function"]["name"] for s in tools_on.tool_schemas()]
    assert "expand_imports" in names_on


def test_java_and_js_resolve(tmp_path: Path):
    files = {
        "src/main/java/com/example/util/Helper.java": "package com.example.util;\n",
        "src/main/java/com/example/App.java": (
            "package com.example;\n"
            "import com.example.util.Helper;\n"
            "import java.util.List;\n"
        ),
        "src/util.ts": "export const x = 1;\n",
        "src/index.ts": "import { x } from './util';\n",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    rels = set(files)

    java = (tmp_path / "src/main/java/com/example/App.java").read_text()
    j_targets = dict(
        resolve_import_targets(java, "src/main/java/com/example/App.java", rels)
    )
    assert "src/main/java/com/example/util/Helper.java" in j_targets
    assert len(j_targets) == 1

    ts = (tmp_path / "src/index.ts").read_text()
    assert "src/util.ts" in dict(resolve_import_targets(ts, "src/index.ts", rels))
