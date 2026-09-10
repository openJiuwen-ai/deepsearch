# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retropus prompt_cache_key binding — no openjiuwen / network."""

from openjiuwen_codesearch.algorithm.prompts.retropus import (
    build_system_prompt,
    stable_prompt_cache_key,
)
from openjiuwen_codesearch.domain.result import Termination
from openjiuwen_codesearch.framework.openjiuwen.agent import RetropusCodeSearchAgent

from tests.conftest import FakeLLM, run, tool_call_response
from tests.unit.test_retropus_agent import FakeRetropusTools, _ctx


def test_stable_prompt_cache_key_deterministic():
    system = build_system_prompt(inherits_expand=True)
    tools = [{"type": "function", "function": {"name": "finish", "parameters": {}}}]
    a = stable_prompt_cache_key(system, tools)
    b = stable_prompt_cache_key(system, tools)
    assert a == b
    assert a.startswith("retropus:")
    assert len(a) == len("retropus:") + 24


def test_stable_prompt_cache_key_changes_with_flags_or_tools():
    base = build_system_prompt(inherits_expand=False)
    with_inherits = build_system_prompt(inherits_expand=True)
    tools_a = [{"type": "function", "function": {"name": "finish", "parameters": {}}}]
    tools_b = [
        {"type": "function", "function": {"name": "search_code", "parameters": {}}},
        {"type": "function", "function": {"name": "finish", "parameters": {}}},
    ]
    assert stable_prompt_cache_key(base, tools_a) != stable_prompt_cache_key(
        with_inherits, tools_a
    )
    assert stable_prompt_cache_key(base, tools_a) != stable_prompt_cache_key(
        base, tools_b
    )


def test_retropus_passes_prompt_cache_key_to_llm(tmp_path):
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")
    main_llm = FakeLLM(
        responses=[
            tool_call_response(
                [("add_context", {"file": "a.py", "start_line": 1, "end_line": 1})]
            ),
            tool_call_response([("finish", {})]),
        ]
    )
    ctx = _ctx(main_llm, tools=FakeRetropusTools(), repo_dir=tmp_path)
    result = run(RetropusCodeSearchAgent().run(ctx))

    assert result.termination == Termination.SUBMITTED
    assert ctx.prompt_cache_key
    assert ctx.prompt_cache_key.startswith("retropus:")
    assert main_llm.call_kwargs
    for kwargs in main_llm.call_kwargs:
        assert kwargs.get("prompt_cache_key") == ctx.prompt_cache_key
