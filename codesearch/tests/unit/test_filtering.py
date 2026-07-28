# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from openjiuwen_codesearch.algorithm.filtering import (
    filter_snippet,
    filter_snippets,
    number_snippet_lines,
)
from openjiuwen_codesearch.llm.factory import LLMResponse

from tests.conftest import FakeLLM, make_filter_llm, make_snippet, run, tool_call_response


def test_numbering_skips_header_and_blank_lines():
    s = make_snippet(3, "a.py", 100, ["def g():", "", "    pass"])
    numbered = number_snippet_lines(s)
    lines = numbered.split("\n")
    assert lines[0].startswith("File: a.py")   # 头部不编号
    assert lines[1] == ""                       # 空行不编号
    assert lines[2] == "100: def g():"
    assert lines[3] == ""
    assert lines[4] == "102:     pass"


def test_filter_snippet_parses_selections_and_cost():
    llm = FakeLLM(
        responses=[
            tool_call_response(
                [(
                    "save_relevant_lines",
                    {"selections": [
                        {"start_line": 5, "end_line": 7, "reasoning": "r"},
                        {"start_line": "bad", "end_line": 9, "reasoning": "r"},  # 非 int 丢弃
                    ]},
                )],
                cost=0.01,
            )
        ]
    )
    s = make_snippet(1, "a.py", 5, ["a", "b", "c"])
    ranges, cost = run(filter_snippet(llm, "issue", s))
    assert ranges == [(5, 7)]
    assert cost == 0.01


def test_filter_snippet_error_returns_empty():
    class BoomLLM:
        async def invoke(self, messages, tools=None):
            raise RuntimeError("boom")

    s = make_snippet(1, "a.py", 5, ["a"])
    ranges, cost = run(filter_snippet(BoomLLM(), "issue", s))
    assert ranges == [] and cost == 0.0


def test_filter_snippets_bounded_concurrency_preserves_order():
    llm = make_filter_llm({"a.py": (1, 2), "b.py": (10, 11)})
    snippets = [
        make_snippet(1, "a.py", 1, ["x", "y"]),
        make_snippet(2, "b.py", 10, ["p", "q"]),
        make_snippet(3, "c.py", 20, ["m"]),
    ]
    results = run(filter_snippets(llm, "issue", snippets, concurrency=2))
    assert [s.id for s, _, _ in results] == [1, 2, 3]
    assert results[0][1] == [(1, 2)]
    assert results[1][1] == [(10, 11)]
    assert results[2][1] == []  # 未命中映射 → 空 selections


def test_filter_ignores_other_tool_names():
    llm = FakeLLM(responses=[
        LLMResponse(tool_calls=[], content="no tools"),
    ])
    s = make_snippet(1, "a.py", 1, ["x"])
    ranges, _ = run(filter_snippet(llm, "issue", s))
    assert ranges == []
