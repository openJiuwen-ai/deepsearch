# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent 循环的 fixture 回放测试（Phase 0 基线思想）：
不连任何外部服务，用脚本化 LLM + 内存检索器驱动完整多轮轨迹，
断言终止路径、最终结果与记忆注入行为。
"""

from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.config.llm import LLMConfig, LLMSuite
from openjiuwen_codesearch.domain.result import Termination
from openjiuwen_codesearch.framework.openjiuwen.agent import CodeSearchAgent
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import CodeSearchRunContext
from openjiuwen_codesearch.llm.factory import LLMResponse
from openjiuwen_codesearch.retrieval.base import InMemoryRetriever

from tests.conftest import FakeLLM, make_filter_llm, make_snippet, run, tool_call_response


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


def test_happy_path_submit():
    """轨迹：turn1 = repo map + 搜索（过滤保存 a.py 两行）→ turn2 = 提交。"""
    main_llm = FakeLLM(
        responses=[
            tool_call_response(
                [
                    ("view_repo_map", {}),
                    ("search_codebase", {"search_query": "alpha beta", "use_trigram": False}),
                ],
                cost=0.02,
            ),
            tool_call_response([("submit_final_snippets", {"snippet_ids": [1]})], cost=0.01),
        ]
    )
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({"a.py": (11, 12)}, cost=0.005))
    result = run(CodeSearchAgent().run(ctx))

    assert result.termination == Termination.SUBMITTED
    assert result.turns == 2
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert (hit.file_path, hit.start_line, hit.end_line) == ("a.py", 11, 12)
    assert "second line" in hit.text and "alpha beta gamma" not in hit.text

    # 成本按 stage 归账：主模型 2 次 + 过滤 1 次
    assert abs(result.total_cost - 0.035) < 1e-9

    # 第二轮的首条消息应重写注入记忆（含已保存行），且包含 repo map 工具结果历史
    second_turn_messages = main_llm.calls[1][0]
    head = second_turn_messages[0].content
    assert "CURRENT SAVED SNIPPETS" in head and "11: second line" in head
    roles = [m.role for m in second_turn_messages]
    assert roles.count("tool") == 2  # repo_map + search 的工具结果都在历史中


def test_stagnation_early_termination():
    """连续 stagnation_rounds 个检索轮零新增 → STAGNATED（不再陪跑满 20 轮）。"""
    search_call = tool_call_response(
        [("search_codebase", {"search_query": "alpha beta", "use_trigram": False})]
    )
    main_llm = FakeLLM(responses=[search_call, search_call, search_call, search_call])
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({}), stagnation_rounds=3)
    result = run(CodeSearchAgent().run(ctx))

    assert result.termination == Termination.STAGNATED
    assert result.turns == 3          # 第 3 个零新增检索轮触发
    assert result.hits == []
    assert len(main_llm.calls) == 3   # 未消费第 4 个脚本响应


def test_no_tool_call_falls_back_to_memory():
    main_llm = FakeLLM(
        responses=[
            tool_call_response(
                [("search_codebase", {"search_query": "alpha beta", "use_trigram": False})]
            ),
            LLMResponse(content="I think I'm done.", tool_calls=[]),
        ]
    )
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({"a.py": (10, 10)}))
    result = run(CodeSearchAgent().run(ctx))

    assert result.termination == Termination.NO_TOOL_CALL
    # 降级返回记忆现存内容
    assert [h.file_path for h in result.hits] == ["a.py"]


def test_index_not_ready_fail_fast():
    main_llm = FakeLLM()
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({}), revision="unknown-rev")
    result = run(CodeSearchAgent().run(ctx))

    assert result.termination == Termination.INDEX_NOT_READY
    assert result.hits == []
    assert main_llm.calls == []  # 一次 LLM 都没调（旧实现会空搜 20 轮）


def test_max_turns_warning_injected():
    """max_turns=3：最后 warn_before_turns 轮后追加强制提交警告。"""
    search_call = tool_call_response(
        [("search_codebase", {"search_query": "alpha beta", "use_trigram": False})]
    )
    main_llm = FakeLLM(responses=[search_call, search_call, search_call])
    # stagnation_rounds 设大避免先触发停滞
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({"a.py": (10, 10)}),
               max_turns=3, stagnation_rounds=99)
    result = run(CodeSearchAgent().run(ctx))

    assert result.termination == Termination.MAX_TURNS
    # 第 2 轮（turn_idx=1 >= 3-2）后应注入警告，第 3 轮的消息里能看到
    third_turn_messages = main_llm.calls[2][0]
    assert any("SYSTEM WARNING" in m.content for m in third_turn_messages)


def test_llm_error_degrades_gracefully():
    class BoomLLM:
        def __init__(self):
            self.calls = []

        async def invoke(self, messages, tools=None):
            raise RuntimeError("provider down")

    ctx = _ctx(SNIPPETS, BoomLLM(), make_filter_llm({}))
    result = run(CodeSearchAgent().run(ctx))
    assert result.termination == Termination.LLM_ERROR
    assert "provider down" in result.error


def test_unknown_tool_reported_not_fatal():
    main_llm = FakeLLM(
        responses=[
            tool_call_response([("hack_the_planet", {})]),
            tool_call_response([("submit_final_snippets", {"snippet_ids": []})]),
        ]
    )
    ctx = _ctx(SNIPPETS, main_llm, make_filter_llm({}))
    result = run(CodeSearchAgent().run(ctx))
    assert result.termination == Termination.SUBMITTED
    second_turn_messages = main_llm.calls[1][0]
    assert any("unknown tool" in m.content for m in second_turn_messages if m.role == "tool")


def test_submit_truncated_to_top_k():
    s_list = [make_snippet(i, f"f{i}.py", 1, ["alpha beta"]) for i in range(1, 6)]
    main_llm = FakeLLM(
        responses=[
            tool_call_response(
                [("search_codebase", {"search_query": "alpha beta", "use_trigram": False})]
            ),
            tool_call_response([("submit_final_snippets", {"snippet_ids": [1, 2, 3, 4, 5]})]),
        ]
    )
    filter_llm = make_filter_llm({f"f{i}.py": (1, 1) for i in range(1, 6)})
    ctx = CodeSearchRunContext(
        config=_config(),
        query="q",
        revision="local",
        top_k=2,  # 提交 5 个只保留前 2 个
        retriever=InMemoryRetriever(s_list, revision="local"),
        main_llm=main_llm,
        filter_llm=filter_llm,
    )
    result = run(CodeSearchAgent().run(ctx))
    assert result.termination == Termination.SUBMITTED
    assert len(result.hits) == 2
