from __future__ import annotations

import pytest

from openjiuwen_deepsearch.config.config import AgentConfig, PerQuestionParams
from openjiuwen_deepsearch.framework.openjiuwen.agent import workflow as workflow_module
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
    SimpleReactSearchAgent,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_simple_react_telemetry_uses_built_tool_map(monkeypatch) -> None:
    captured_events: list[tuple[str, dict]] = []
    observed_search_context: dict[str, str] = {}

    def _fake_emit(event_type: str, payload: dict, **_kwargs) -> None:
        captured_events.append((event_type, payload))

    async def _fake_run_llm_via_ainvoke(**_kwargs):
        observed_search_context.update(
            {
                name: wrapper.__class__.__name__
                for name, wrapper in (workflow_module.web_search_context.get() or {}).items()
            }
        )
        raise RuntimeError("stop after startup telemetry")

    monkeypatch.setattr(workflow_module, "emit", _fake_emit)
    monkeypatch.setattr(
        workflow_module, "emit_messages_updated", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        workflow_module, "_run_llm_via_ainvoke", _fake_run_llm_via_ainvoke
    )
    monkeypatch.setattr(
        workflow_module.LogManager,
        "get_log_dir",
        classmethod(lambda cls: "./output/logs"),
    )
    monkeypatch.setattr(
        workflow_module.LogManager, "is_sensitive", classmethod(lambda cls: False)
    )

    agent_config = AgentConfig(
        search_mode="react",
        llm_config={
            "general": {
                "model_name": "mock-model",
                "model_type": "openai",
                "base_url": "https://example.com/v1",
                "api_key": bytearray(b"x"),
            }
        },
        search_workflow_per_question_params=PerQuestionParams(tool_map="search_fetch"),
        web_search_engine_config={
            "search_engine_name": "jina",
            "search_api_key": bytearray(b"search-key"),
        },
        web_fetch_provider_config={
            "provider_name": "jina",
            "api_key": bytearray(b"fetch-key"),
        },
    ).model_dump()

    agent = SimpleReactSearchAgent()
    results = [
        chunk
        async for chunk in agent.run(
            message="who is the current US president?",
            conversation_id="react_toolmap_test",
            agent_config=agent_config,
        )
    ]

    assert len(results) == 1
    start_payload = next(
        payload for event, payload in captured_events if event == "react_run_started"
    )
    assert start_payload["tool_map"] == {
        "web_fetch": "WebFetch",
        "web_search": "WebSearch",
    }
    assert observed_search_context == {"jina": "JinaSearchAPIWrapper"}
