# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""图形态（openjiuwen workflow）回放测试：与 react 形态跑同一套场景，
由真实的 openjiuwen Runner 按路由驱动。需要 openjiuwen（integration marker）。
"""

import pytest

pytest.importorskip("openjiuwen", reason="requires openjiuwen (install extras: llm)")

from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.config.llm import LLMConfig, LLMSuite
from openjiuwen_codesearch.domain.result import Termination
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import CodeSearchRunContext
from openjiuwen_codesearch.framework.openjiuwen.workflow import GraphCodeSearchAgent
from openjiuwen_codesearch.llm.factory import LLMResponse
from openjiuwen_codesearch.retrieval.base import InMemoryRetriever

from tests.conftest import FakeLLM, make_filter_llm, make_snippet, run, tool_call_response

pytestmark = pytest.mark.integration


def _config(**agent_overrides):
    cfg = CodeSearchConfig(llm=LLMSuite(main=LLMConfig(model_name="fake")))
    cfg.agent.trace_dir = ""
    for key, value in agent_overrides.items():
        setattr(cfg.agent, key, value)
    return cfg


def _ctx(snippets, main_llm, filter_llm, revision="local", **agent_overrides):
    return CodeSearchRunContext(
        config=_config(**agent_overrides),
        query="fix alpha beta bug",
        revision=revision,
        top_k=10,
        retriever=InMemoryRetriever(snippets, revision="local"),
        main_llm=main_llm,
        filter_llm=filter_llm,
    )


SNIPPETS = [
    make_snippet(1, "a.py", 10, ["alpha beta gamma", "second line", "third line"], name="f"),
    make_snippet(2, "b.py", 5, ["unrelated words entirely"], name="g"),
]


def test_graph_happy_path_submit():
    main_llm = FakeLLM(
        responses=[
            tool_call_response(
                [
                    ("view_repo_map", {}),
                    ("search_codebase", {"search_query": "alpha beta", "use_trigram": False}),
                ],
                tokens=(200, 20),
            ),
            tool_call_response([("submit_final_snippets", {"snippet_ids": [1]})], tokens=(100, 10)),
        ]
    )
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({"a.py": (11, 12)}, tokens=(50, 5)))
    result = run(GraphCodeSearchAgent().run(ctx))

    assert result.termination == Termination.SUBMITTED
    assert result.turns == 2
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert (hit.file_path, hit.start_line, hit.end_line) == ("a.py", 11, 12)
    assert result.total_input_tokens == 350   # 200 + 100 主模型 + 50 过滤
    assert result.total_output_tokens == 35   # 20 + 10 + 5
    # 第二轮首条消息重写注入记忆（与 react 形态同一行为契约）
    head = main_llm.calls[1][0][0].content
    assert "WORKING MEMORY (Current Search)" in head and "11: second line" in head


def test_graph_stagnation():
    search_call = tool_call_response(
        [("search_codebase", {"search_query": "alpha beta", "use_trigram": False})]
    )
    main_llm = FakeLLM(responses=[search_call, search_call, search_call, search_call])
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({}), stagnation_rounds=3)
    result = run(GraphCodeSearchAgent().run(ctx))
    assert result.termination == Termination.STAGNATED
    assert result.turns == 3 and len(main_llm.calls) == 3


def test_graph_index_not_ready_fail_fast():
    main_llm = FakeLLM()
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({}), revision="unknown-rev")
    result = run(GraphCodeSearchAgent().run(ctx))
    assert result.termination == Termination.INDEX_NOT_READY
    assert main_llm.calls == []


def test_graph_no_tool_call_fallback():
    main_llm = FakeLLM(
        responses=[
            tool_call_response(
                [("search_codebase", {"search_query": "alpha beta", "use_trigram": False})]
            ),
            LLMResponse(content="done", tool_calls=[]),
        ]
    )
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({"a.py": (10, 10)}))
    result = run(GraphCodeSearchAgent().run(ctx))
    assert result.termination == Termination.NO_TOOL_CALL
    assert [h.file_path for h in result.hits] == ["a.py"]


def test_graph_and_react_produce_identical_results():
    """双引擎一致性：同一 fixture 下 graph 与 react 输出完全一致。"""
    from openjiuwen_codesearch.framework.openjiuwen.agent import CodeSearchAgent

    def scripted_llm():
        return FakeLLM(
            responses=[
                tool_call_response(
                    [("search_codebase", {"search_query": "alpha beta", "use_trigram": False})]
                ),
                tool_call_response([("submit_final_snippets", {"snippet_ids": [1]})]),
            ]
        )

    ctx_graph = _ctx(SNIPPETS, scripted_llm(), make_filter_llm({"a.py": (11, 12)}))
    ctx_react = _ctx(SNIPPETS, scripted_llm(), make_filter_llm({"a.py": (11, 12)}))

    result_graph = run(GraphCodeSearchAgent().run(ctx_graph))
    result_react = run(CodeSearchAgent().run(ctx_react))

    assert result_graph.model_dump() == result_react.model_dump()
