# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RetropusCodeSearchAgent fixture replay — no tree-sitter / Milvus."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.config.llm import LLMConfig, LLMSuite
from openjiuwen_codesearch.domain.result import Termination
from openjiuwen_codesearch.framework.openjiuwen.agent import (
    AbstractReactEngine,
    CodeSearchAgent,
    RetropusCodeSearchAgent,
    spans_to_hits,
)
from openjiuwen_codesearch.framework.openjiuwen.retropus_context import RetropusRunContext
from openjiuwen_codesearch.api.retriever import CodeSearchRetriever
from openjiuwen_codesearch.llm.factory import LLMResponse

from tests.conftest import FakeLLM, run, tool_call_response


CORE_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": n,
            "description": n,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    for n in (
        "search_code",
        "search_text",
        "get_repo_structure",
        "read_file",
        "add_context",
        "finish",
    )
]


class FakeRetropusTools:
    def __init__(self, spans=None):
        self._all_spans = list(spans or [])
        self._new_since_drain: list = []
        self._dispatched: list = []
        self.config = SimpleNamespace(
            feat_same_file_expand=False,
            feat_inherits_expand=False,
        )

    def tool_schemas(self):
        return list(CORE_SCHEMAS)

    def dispatch(self, name: str, args: dict) -> str:
        self._dispatched.append((name, args))
        if name == "add_context":
            span = {
                "file": args["file"],
                "start": int(args["start_line"]),
                "end": int(args["end_line"]),
            }
            self._all_spans.append(span)
            self._new_since_drain.append(span)
            return "added"
        if name == "finish":
            return "Finished. Recorded spans."
        if name == "search_code":
            return "Result 1:\npath: a.py"
        return f"ok:{name}"

    def drain_new_spans(self):
        spans = self._new_since_drain
        self._new_since_drain = []
        return spans

    def final_spans(self):
        return list(self._all_spans)

    def has_spans(self):
        return bool(self._all_spans)

    def add_context(self, file, start, end, reason=None):
        span = {"file": file, "start": start, "end": end}
        self._all_spans.append(span)
        self._new_since_drain.append(span)


def _config():
    cfg = CodeSearchConfig(llm=LLMSuite(main=LLMConfig(model_name="fake")))
    cfg.agent.engine = "retropus"
    cfg.agent.trace_dir = ""
    return cfg


def _ctx(main_llm, tools=None, repo_dir=None):
    tools = tools or FakeRetropusTools()
    # Avoid constructing real KG — pass MagicMock placeholders
    return RetropusRunContext(
        config=_config(),
        retropus_config=_config().retropus,
        query="fix alpha in a.py",
        top_k=10,
        repo_dir=Path(repo_dir or "."),
        kg=MagicMock(),
        retriever=MagicMock(),
        main_llm=main_llm,
        tools=tools,
        issue_text="fix alpha in a.py",
    )


def test_retropus_happy_path_finish_maps_hits(tmp_path):
    src = tmp_path / "a.py"
    src.write_text("line1\nline2\nalpha beta\nline4\n", encoding="utf-8")

    main_llm = FakeLLM(
        responses=[
            tool_call_response(
                [
                    ("search_code", {"query": "alpha"}),
                    (
                        "add_context",
                        {"file": "a.py", "start_line": 3, "end_line": 3},
                    ),
                ]
            ),
            tool_call_response([("finish", {})]),
        ]
    )
    ctx = _ctx(main_llm, repo_dir=tmp_path)
    result = run(RetropusCodeSearchAgent().run(ctx))

    assert result.termination == Termination.SUBMITTED
    assert len(result.hits) == 1
    assert (result.hits[0].file_path, result.hits[0].start_line) == ("a.py", 3)
    assert "alpha beta" in result.hits[0].text

    # Only retropus tool schemas were offered to the LLM
    for _messages, tools in main_llm.calls:
        names = {t["function"]["name"] for t in (tools or [])}
        assert "search_codebase" not in names
        assert "submit_final_snippets" not in names
        assert "search_code" in names
        assert "finish" in names


def test_retropus_unknown_codesearch_tool_name():
    """If the model invents a CodeSearch tool name, it is rejected (not executed)."""
    tools = FakeRetropusTools()
    main_llm = FakeLLM(
        responses=[
            tool_call_response([("search_codebase", {"search_query": "x"})]),
            tool_call_response(
                [("add_context", {"file": "a.py", "start_line": 1, "end_line": 1})]
            ),
            tool_call_response([("finish", {})]),
        ]
    )
    ctx = _ctx(main_llm, tools=tools)
    result = run(RetropusCodeSearchAgent().run(ctx))
    assert result.termination == Termination.SUBMITTED
    tool_msgs = [m.content for m in ctx.history if m.role == "tool"]
    assert any("unknown tool 'search_codebase'" in m for m in tool_msgs)
    # Only add_context/finish were dispatched — not search_codebase
    assert all(name != "search_codebase" for name, _ in getattr(tools, "_dispatched", []))


def test_spans_to_hits_sort_order(tmp_path):
    (tmp_path / "b.py").write_text("b\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    spans = [
        {"file": "b.py", "start": 1, "end": 1},
        {"file": "a.py", "start": 1, "end": 1},
    ]
    hits = spans_to_hits(spans, tmp_path, top_k=10)
    assert [h.file_path for h in hits] == ["a.py", "b.py"]


def test_retriever_search_index_not_ready():
    cfg = _config()
    r = CodeSearchRetriever(config=cfg, main_llm=FakeLLM(), filter_llm=FakeLLM())
    result = run(r.search("query"))
    assert result.termination == Termination.INDEX_NOT_READY
    assert result.hits == []


def test_agents_subclass_abstract_react_engine():
    assert issubclass(CodeSearchAgent, AbstractReactEngine)
    assert issubclass(RetropusCodeSearchAgent, AbstractReactEngine)


def test_retropus_nudge_continues_to_second_llm_turn(tmp_path):
    """No tool_calls + no spans → nudge; empty pending skips tools; next turn runs."""
    src = tmp_path / "a.py"
    src.write_text("alpha\n", encoding="utf-8")

    main_llm = FakeLLM(
        responses=[
            LLMResponse(content="thinking...", tool_calls=[]),
            tool_call_response(
                [
                    (
                        "add_context",
                        {"file": "a.py", "start_line": 1, "end_line": 1},
                    ),
                    ("finish", {}),
                ]
            ),
        ]
    )
    ctx = _ctx(main_llm, repo_dir=tmp_path)
    result = run(RetropusCodeSearchAgent().run(ctx))

    assert result.termination == Termination.SUBMITTED
    assert len(main_llm.calls) == 2
    assert ctx.nudges == 1
    assert any(
        m.role == "user" and "not recorded any context" in m.content for m in ctx.history
    )
    assert len(result.hits) == 1


def test_retriever_retropus_index_skips_milvus(monkeypatch):
    cfg = _config()
    r = CodeSearchRetriever(config=cfg, main_llm=FakeLLM(), filter_llm=FakeLLM())

    def boom(*_a, **_k):
        raise AssertionError("MilvusStore must not be constructed for retropus")

    monkeypatch.setattr(r, "_ensure_store", boom)

    def fake_build(repo_path, reset=False):
        r._retropus_kg = MagicMock()
        r._retropus_retriever = MagicMock()
        r._retropus_repo_dir = Path(repo_path)
        from openjiuwen_codesearch.api.models import IndexReport

        return IndexReport(files_total=2, files_new=2, chunks_inserted=1)

    monkeypatch.setattr(r, "_build_retropus_index", fake_build)
    report = run(r.index_repository("/tmp/fake_repo"))
    assert report.files_total == 2
    assert r._store is None
