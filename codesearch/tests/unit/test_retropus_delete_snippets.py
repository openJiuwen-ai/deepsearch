# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""FEAT_DELETE_SNIPPETS: register + execute CodeSearch delete_snippets on Retropus spans."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen_codesearch.algorithm.search_tools.memory_tools import execute_delete
from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import (
    build_retropus_registry,
)
from tests.conftest import run
from tests.unit.retropus_fixtures import make_retropus_tools


def test_feat_delete_snippets_registers_tool(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    tools = make_retropus_tools(tmp_path, feat_delete_snippets=True)
    names = [s["function"]["name"] for s in tools.tool_schemas()]
    assert names[-2:] == ["delete_snippets", "finish"]

    registry = build_retropus_registry(tools)
    assert registry["delete_snippets"].executor is execute_delete


def test_feat_delete_snippets_off_omits_tool(tmp_path):
    tools = make_retropus_tools(tmp_path, feat_delete_snippets=False)
    names = [s["function"]["name"] for s in tools.tool_schemas()]
    assert "delete_snippets" not in names


def test_delete_snippets_removes_span_by_id(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    tools = make_retropus_tools(tmp_path, feat_delete_snippets=True)

    msg1 = tools.add_context("a.py", 1, 2)
    msg2 = tools.add_context("b.py", 1, 2)
    assert "id=1" in msg1
    assert "id=2" in msg2
    assert len(tools.final_spans()) == 2

    env = SimpleNamespace(tools=tools, memory=tools.memory)
    outcome = run(
        execute_delete(env, {"snippet_ids": [1], "reasoning": "wrong file"})
    )
    assert "Successfully deleted 1 snippets" in outcome.message
    spans = tools.final_spans()
    assert len(spans) == 1
    assert spans[0]["file"] == "b.py"
    assert spans[0]["id"] == 2

    # Deleted span can be re-added.
    again = tools.add_context("a.py", 1, 2)
    assert "id=3" in again
    assert len(tools.final_spans()) == 2
