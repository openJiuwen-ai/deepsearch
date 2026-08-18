# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Live OpenJiuwenLLMClient calls against an OpenAI-compatible endpoint."""

import os

import pytest

from tests.unit.conftest_helpers import run

pytestmark = pytest.mark.system


def _require_llm_env() -> None:
    if os.getenv("RUN_LLM_TESTS", "").strip() != "1":
        pytest.skip("Set RUN_LLM_TESTS=1 to run live LLM system tests")
    missing = [
        key
        for key in ("BASE_LLM_API_KEY", "BASE_LLM_BASE_URL", "BASE_LLM_MODEL")
        if not os.getenv(key, "").strip()
    ]
    if missing:
        pytest.skip("Missing LLM env: " + ", ".join(missing))


def _llm_config():
    from openjiuwen_search_base.llm import LLMConfig

    verify_raw = os.getenv("BASE_LLM_VERIFY_SSL", "true").strip().lower()
    return LLMConfig(
        model_name=os.environ["BASE_LLM_MODEL"].strip(),
        api_key=os.environ["BASE_LLM_API_KEY"].strip(),
        base_url=os.environ["BASE_LLM_BASE_URL"].strip(),
        verify_ssl=verify_raw not in {"0", "false", "no"},
        temperature=0.0,
        max_tokens=256,
    )


def test_invoke_plain_text(openjiuwen_pkg):
    _require_llm_env()
    from openjiuwen_search_base.llm import ChatMessage, create_llm_client

    client = create_llm_client(_llm_config(), client_id="base_system_text")
    response = run(
        client.invoke([ChatMessage(role="user", content="Reply with the single word pong.")])
    )
    assert response.content
    assert response.input_tokens >= 0
    assert response.output_tokens >= 0


def test_invoke_tool_then_tool_message(openjiuwen_pkg):
    _require_llm_env()
    from openjiuwen_search_base.llm import ChatMessage, create_llm_client, normalize_tool_calls

    client = create_llm_client(_llm_config(), client_id="base_system_tools")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "echo_tool",
                "description": "Echo a short string back to the caller.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        }
    ]
    tool_result = "hello"
    user = ChatMessage(
        role="user",
        content=(
            "Call echo_tool with text set to hello. "
            "After you receive the tool result, ask the user whether they have received "
            f"a message with the exact content {tool_result}."
        ),
    )
    first = run(client.invoke([user], tools=tools))
    calls = first.tool_calls or normalize_tool_calls(getattr(first.raw, "tool_calls", None))
    assert calls, "model did not emit tool calls"
    call = calls[0]
    assert call.name == "echo_tool"
    assert isinstance(call.arguments, dict)

    follow = run(
        client.invoke(
            [
                user,
                ChatMessage(role="assistant", content=first.content or "", raw=first.raw),
                ChatMessage(role="tool", tool_call_id=call.call_id, content=tool_result),
            ],
            tools=tools,
        )
    )
    assert follow.content
    assert tool_result in follow.content
