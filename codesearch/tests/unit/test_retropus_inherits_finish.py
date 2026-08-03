# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""inherits_expand is suggest-only on finish (aligned with upstream ContextBench)."""

from pathlib import Path

from openjiuwen_codesearch.algorithm.prompts.retropus import build_system_prompt
from openjiuwen_codesearch.algorithm.prompts import load_prompt
from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import RetrievalTools
from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig
from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    FileNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
)


class _FakeKG:
    def __init__(self, edges=None, file_by_ast=None):
        self._edges = edges or []
        self._file_by_ast = file_by_ast or {}

    def get_file_nodes(self):
        return []

    def get_inherits_edges(self):
        return self._edges

    def get_inheritance_neighbors(self, node):
        out = []
        for e in self._edges:
            if e.source.node_id == node.node_id:
                out.append(e.target)
            elif e.target.node_id == node.node_id:
                out.append(e.source)
        return out

    def get_file_for_ast(self, node):
        return self._file_by_ast.get(node.node_id)


class _FakeRetriever:
    def score_files_and_defs(self, query, top_k=10, max_defs_per_file=20):
        return []


def test_inherits_prompt_is_suggest_only():
    appendix = load_prompt("inherits")
    assert appendix in build_system_prompt(inherits_expand=True)
    assert appendix not in build_system_prompt(inherits_expand=False)
    assert "recommended, not required" in appendix
    assert "may still `finish`" in appendix


def test_finish_suggests_but_does_not_block_inheritance(tmp_path: Path):
    base_file = tmp_path / "pkg" / "base.py"
    child_file = tmp_path / "pkg" / "child.py"
    base_file.parent.mkdir(parents=True)
    base_file.write_text("class Base:\n    pass\n", encoding="utf-8")
    child_file.write_text("class Child(Base):\n    pass\n", encoding="utf-8")

    base_ast = KnowledgeGraphNode(
        1, ASTNode("class_definition", 1, 2, "class Base:\n    pass\n")
    )
    sub_ast = KnowledgeGraphNode(
        2, ASTNode("class_definition", 1, 2, "class Child(Base):\n    pass\n")
    )
    file_base = KnowledgeGraphNode(10, FileNode("base.py", "pkg/base.py"))
    file_sub = KnowledgeGraphNode(11, FileNode("child.py", "pkg/child.py"))

    edge = KnowledgeGraphEdge(sub_ast, base_ast, KnowledgeGraphEdgeType.inherits)
    kg = _FakeKG(
        [edge],
        file_by_ast={base_ast.node_id: file_base, sub_ast.node_id: file_sub},
    )
    cfg = RetropusSearchAgentConfig(
        feat_inherits_expand=True,
        feat_anti_early_finish=False,
        feat_same_file_expand=False,
        feat_second_file_probe=False,
        feat_ban_tests=False,
        feat_expand_imports=False,
    )
    tools = RetrievalTools(kg, _FakeRetriever(), tmp_path, cfg)
    tools._definitions_in_file = lambda rel: [  # type: ignore[method-assign]
        (1, 2, sub_ast) if rel == "pkg/child.py" else (1, 2, base_ast)
    ]

    tools.add_context("pkg/child.py", 1, 2)
    allowed = tools.finish()
    assert not allowed.startswith("finish blocked")
    assert allowed.startswith("Finishing retrieval.")
    assert "expand_inheritance" in allowed or "INHERITS" in allowed
    assert tools.finish_allowed()
