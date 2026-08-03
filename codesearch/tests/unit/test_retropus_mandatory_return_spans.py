# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for min_mandatory_return_spans end-of-run padding."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.config.llm import LLMConfig, LLMSuite
from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig
from openjiuwen_codesearch.framework.openjiuwen.agent import RetropusCodeSearchAgent
from openjiuwen_codesearch.framework.openjiuwen.retropus_context import RetropusRunContext
from tests.unit.retropus_fixtures import FakeRetriever, make_retropus_tools


def _ast(rel: str, start: int, end: int, name: str = "foo"):
    node = SimpleNamespace(
        type="function_definition",
        start_line=start,
        end_line=end,
        text=f"def {name}():\n    pass\n",
        relative_path=rel,
    )
    return SimpleNamespace(node=node)


def _write_repo(tmp_path: Path) -> None:
    for rel, body in (
        ("pkg/a.py", "\n".join(f"line_{i}" for i in range(1, 21)) + "\n"),
        ("pkg/b.py", "\n".join(f"line_{i}" for i in range(1, 21)) + "\n"),
        ("pkg/c.py", "\n".join(f"line_{i}" for i in range(1, 21)) + "\n"),
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _ranked_three_files(tmp_path: Path):
    entries = []
    for score, rel, start, end, name in (
        (3.0, "pkg/a.py", 1, 2, "a"),
        (2.0, "pkg/b.py", 3, 4, "b"),
        (1.0, "pkg/c.py", 5, 6, "c"),
    ):
        file_node = SimpleNamespace(node=SimpleNamespace(relative_path=rel))
        entries.append(
            {
                "file_node": file_node,
                "score": score,
                "defs": [(_ast(rel, start, end, name), score)],
            }
        )
    return entries


def _ctx(tmp_path: Path, ranked, *, min_mandatory: int, tools=None) -> RetropusRunContext:
    cfg = RetropusSearchAgentConfig(
        min_mandatory_return_spans=min_mandatory,
        feat_ban_tests=False,
    )
    retriever = FakeRetriever(ranked)
    tools = tools or make_retropus_tools(
        tmp_path,
        retriever=retriever,
        min_mandatory_return_spans=min_mandatory,
    )
    cs = CodeSearchConfig(llm=LLMSuite(main=LLMConfig(model_name="fake")))
    cs.agent.engine = "retropus"
    return RetropusRunContext(
        config=cs,
        retropus_config=cfg,
        query="issue about foo",
        top_k=10,
        repo_dir=tmp_path,
        kg=MagicMock(),
        retriever=retriever,
        main_llm=MagicMock(),
        tools=tools,
        issue_text="issue about foo",
    )


def test_pad_spans_reaches_mandatory_target(tmp_path: Path):
    _write_repo(tmp_path)
    ranked = _ranked_three_files(tmp_path)
    ctx = _ctx(tmp_path, ranked, min_mandatory=3)

    RetropusCodeSearchAgent().pad_spans_from_retriever(ctx, target_count=3)
    spans = ctx.tools.final_spans()
    assert len(spans) == 3
    assert {s["file"] for s in spans} == {"pkg/a.py", "pkg/b.py", "pkg/c.py"}
    assert all(s.get("reason") == "mandatory_fallback" for s in spans)


def test_pad_spans_only_fills_deficit(tmp_path: Path):
    _write_repo(tmp_path)
    ranked = _ranked_three_files(tmp_path)
    tools = make_retropus_tools(
        tmp_path,
        retriever=FakeRetriever(ranked),
        min_mandatory_return_spans=3,
    )
    tools.add_context("pkg/a.py", 1, 2, reason="agent")
    ctx = _ctx(tmp_path, ranked, min_mandatory=3, tools=tools)

    RetropusCodeSearchAgent().pad_spans_from_retriever(ctx, target_count=3)
    spans = tools.final_spans()
    assert len(spans) == 3
    assert spans[0]["reason"] == "agent"
    assert sum(1 for s in spans if s.get("reason") == "mandatory_fallback") == 2
