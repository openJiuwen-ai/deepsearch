# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from openjiuwen_codesearch.algorithm.search_tools import build_default_registry
from openjiuwen_codesearch.algorithm.search_tools import (
    expand_context,
    memory_tools,
    search_codebase,
)
from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.config.llm import LLMConfig, LLMSuite
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import CodeSearchRunContext
from openjiuwen_codesearch.retrieval.base import InMemoryRetriever

from tests.conftest import FakeLLM, make_filter_llm, make_snippet, run


def _config():
    cfg = CodeSearchConfig(llm=LLMSuite(main=LLMConfig(model_name="fake")))
    cfg.agent.trace_dir = ""
    return cfg


def _ctx(snippets, filter_llm=None, revision="local"):
    return CodeSearchRunContext(
        config=_config(),
        query="alpha beta issue",
        revision=revision,
        top_k=20,
        retriever=InMemoryRetriever(snippets, revision="local"),
        main_llm=FakeLLM(),
        filter_llm=filter_llm or make_filter_llm({}),
    )


def test_registry_exposes_all_five_tools():
    registry = build_default_registry()
    assert set(registry) == {
        "view_repo_map",
        "search_codebase",
        "expand_context",
        "delete_snippets",
        "submit_final_snippets",
    }
    for spec in registry.values():
        assert spec.schema_["function"]["name"] == spec.name


def test_search_adds_filtered_snippets_to_memory():
    s = make_snippet(1, "a.py", 10, ["alpha beta gamma", "delta"])
    ctx = _ctx([s], filter_llm=make_filter_llm({"a.py": (10, 10)}))
    outcome = run(search_codebase.execute(ctx, {"search_query": "alpha beta", "use_trigram": False}))
    assert outcome.added_snippets == 1 and outcome.searched
    assert "1 new snippets" in outcome.message
    assert ctx.working_memory.saved[1] == [(10, 10)]


def test_search_all_processed_message():
    s = make_snippet(1, "a.py", 10, ["alpha beta"])
    ctx = _ctx([s], filter_llm=make_filter_llm({"a.py": (10, 10)}))
    run(search_codebase.execute(ctx, {"search_query": "alpha beta", "use_trigram": False}))
    outcome = run(search_codebase.execute(ctx, {"search_query": "alpha beta", "use_trigram": False}))
    assert outcome.added_snippets == 0
    assert "ALL retrieved chunks were already processed" in outcome.message


def test_search_no_relevant_lines_message():
    s = make_snippet(1, "a.py", 10, ["alpha beta"])
    ctx = _ctx([s])  # filter 恒返回空 selections
    outcome = run(search_codebase.execute(ctx, {"search_query": "alpha beta", "use_trigram": False}))
    assert "Filter Agent found NO relevant lines" in outcome.message


def test_search_target_file_prefix_hack():
    s = make_snippet(1, "pkg/target.py", 1, ["File: pkg/target.py alpha"])
    ctx = _ctx([s], filter_llm=make_filter_llm({"pkg/target.py": (1, 1)}))
    outcome = run(
        search_codebase.execute(
            ctx, {"search_query": "alpha", "use_trigram": False, "target_file": "pkg/target.py"}
        )
    )
    assert outcome.added_snippets == 1


def test_expand_context_clips_to_chunk_bounds():
    s = make_snippet(5, "a.py", 10, [f"l{i}" for i in range(10, 21)])  # L10-L20
    ctx = _ctx([s])
    outcome = run(
        expand_context.execute(ctx, {"target_file": "a.py", "start_line": 1, "end_line": 100})
    )
    # 修复 notes #15：区间裁剪到 chunk 自身边界，不再越界
    assert ctx.working_memory.saved[5] == [(10, 20)]
    assert outcome.added_snippets == 1


def test_expand_context_no_match():
    ctx = _ctx([])
    outcome = run(
        expand_context.execute(ctx, {"target_file": "x.py", "start_line": 1, "end_line": 5})
    )
    assert outcome.message == "No surrounding lines found in index."


def test_delete_and_submit():
    s = make_snippet(1, "a.py", 1, ["x"])
    ctx = _ctx([s])
    ctx.working_memory.add_ranges(s, [(1, 1)])
    outcome = run(memory_tools.execute_delete(ctx, {"snippet_ids": [1, 9], "reasoning": "noise"}))
    assert "deleted 1 snippets" in outcome.message and "noise" in outcome.message

    outcome = run(memory_tools.execute_submit(ctx, {"snippet_ids": [3, 1, "bad"]}))
    assert outcome.submitted_ids == [3, 1]
