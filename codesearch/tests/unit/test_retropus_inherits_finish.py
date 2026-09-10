# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""inherits_expand is suggest-only on finish (aligned with upstream ContextBench)."""

from pathlib import Path

from openjiuwen_codesearch.algorithm.prompts.retropus import build_system_prompt
from openjiuwen_codesearch.algorithm.prompts import load_prompt
from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    FileNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
)
from tests.unit.retropus_fixtures import FakeKG, FakeRetriever, make_retropus_tools


def test_inherits_prompt_is_suggest_only():
    appendix = load_prompt("inherits")
    assert appendix in build_system_prompt(inherits_expand=True)
    assert appendix not in build_system_prompt(inherits_expand=False)
    assert "expand_inheritance" in appendix
    assert "add_context` only on neighbors" in appendix


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
    kg = FakeKG(
        inherits_edges=[edge],
        file_by_ast={base_ast.node_id: file_base, sub_ast.node_id: file_sub},
    )
    tools = make_retropus_tools(
        tmp_path,
        kg=kg,
        retriever=FakeRetriever(),
        feat_inherits_expand=True,
    )

    def _defs_in_file(rel):
        if rel == "pkg/child.py":
            return [(1, 2, sub_ast)]
        return [(1, 2, base_ast)]

    tools.definitions_in_file = _defs_in_file  # type: ignore[method-assign]

    tools.add_context("pkg/child.py", 1, 2)
    allowed = tools.finish()
    assert not allowed.startswith("finish blocked")
    assert allowed.startswith("Finishing retrieval.")
    assert "expand_inheritance" in allowed or "INHERITS" in allowed
    assert tools.finish_allowed()
