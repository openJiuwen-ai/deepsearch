"""Real OpenAI SDK and OpenJiuwen parsing against an in-memory HTTP transport."""

import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils import llm_utils
from openjiuwen_deepsearch.utils.common_utils.moderation_detection import detect_error
from openjiuwen_deepsearch.utils.common_utils.moderation_mask import RETRY_FEEDBACK_PROMPT
from openjiuwen_deepsearch.utils.common_utils.moderation_content import evidence_spans
from openjiuwen_deepsearch.utils.common_utils.moderation_retry import moderation_retry_scope
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context


MODEL = "test-moderation-model"
MESSAGES = [{"role": "user", "content": evidence_spans("blockedword research", [(0, 20)])}]


class Answer(BaseModel):
    value: str


def _completion(reason="stop", content='{"value":"ok"}'):
    return {"id": "test", "object": "chat.completion", "created": 0, "model": MODEL,
            "choices": [{"index": 0, "finish_reason": reason,
                         "message": {"role": "assistant", "content": content}}]}


def _sse(*events):
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"


def _chunk(content="", reason=None):
    return {"id": "test", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": content},
                         "finish_reason": reason}]}


@asynccontextmanager
async def _model(monkeypatch, tmp_path, handler, enabled=True):
    async with AsyncOpenAI(
        api_key="test-not-a-real-key", base_url="https://moderation.invalid/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        monkeypatch.setattr(OpenAIModelClient, "_create_async_openai_client", lambda *a, **k: sdk)
        monkeypatch.setattr(llm_utils, "_is_llm_stats_enabled", lambda: False)
        monkeypatch.setattr(llm_utils, "_resolve_agent_llm_timeout", lambda **k: None)
        model = Model(
            model_client_config=ModelClientConfig(client_provider="OpenAI", api_key="test-not-a-real-key",
                                                  api_base="https://moderation.invalid/v1"),
            model_config=ModelRequestConfig(model_name=MODEL),
        )
        (tmp_path / "lex.txt").write_text("blockedword\n", encoding="utf-8")
        yield {"model": model, "model_name": MODEL, "moderation_mask_retry": enabled,
               "moderation_mask_lexicon_dir": str(tmp_path)}


@moderation_retry_scope
async def _operation(llm, agent, attempts=3, messages=MESSAGES):
    for attempt in range(attempts):
        try:
            return await llm_utils.ainvoke_llm_with_stats(
                llm=llm, messages=messages, agent_name=agent, schema=Answer,
            )
        except Exception:
            if attempt == attempts - 1:
                raise


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_filter_before_json_parse_uses_existing_next_attempt(monkeypatch, tmp_path, caplog, stream):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if stream:
            events = [_chunk("not valid JSON", "content_filter")] if len(calls) == 1 else [
                _chunk('{"value":"ok"}', "stop")]
            return httpx.Response(200, text=_sse(*events), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=_completion("content_filter", "not valid JSON")
                              if len(calls) == 1 else _completion())

    agent = "AI" if stream else NodeId.FIND_ACTION_SPACE.value
    caplog.set_level(logging.INFO, logger=llm_utils.__name__)
    async with _model(monkeypatch, tmp_path, handler) as llm:
        result = await _operation(llm, agent)
    assert result.value == "ok" and len(calls) == 2
    assert calls[0]["messages"][0]["content"] == "blockedword research"
    assert calls[1]["messages"][0]["content"] == "*********** research"
    assert "side=unknown strategy=input_mask" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_real_template_evidence_scope_survives_sdk_without_metadata_leak(monkeypatch, tmp_path, enabled):
    calls = []
    payload = {"task": {"step_title": "blockedword task"},
               "document": {"title": "blockedword title", "url": "https://normal.example",
                            "key_passages": ["blockedword evidence"]},
               "webpage_content": "blockedword body", "max_content_length": 100}
    messages = build_prompt_messages("collector_webpage_enrichment_compress", {
        "payload": json.dumps(payload, ensure_ascii=False),
    })

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"code": "data_inspection_failed",
                                  "message": "Input data may contain inappropriate content"}})
        return httpx.Response(200, json=_completion())

    async with _model(monkeypatch, tmp_path, handler, enabled=enabled) as llm:
        result = await _operation(llm, NodeId.FIND_ACTION_SPACE.value, messages=messages)
    assert result.value == "ok" and len(calls) == 2
    assert calls[0]["messages"] == messages
    prepared = json.loads(calls[1]["messages"][-1]["content"])
    expected = {**payload, "document": {**payload["document"], "key_passages": ["*********** evidence"]},
                "webpage_content": "*********** body"} if enabled else payload
    assert prepared == expected
    assert json.loads(messages[-1]["content"]) == payload
    assert all(set(message) == {"role", "content"} for call in calls for message in call["messages"])


@pytest.mark.asyncio
async def test_stream_error_after_partial_body_selects_output_constraint(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        events = [_chunk('{"value":"partial"}'), {"error": {
            "code": "data_inspection_failed", "message": "Output data may contain inappropriate content.",
        }}] if len(calls) == 1 else [_chunk('{"value":"ok"}', "stop")]
        return httpx.Response(200, text=_sse(*events), headers={"content-type": "text/event-stream"})

    async with _model(monkeypatch, tmp_path, handler) as llm:
        result = await _operation(llm, "AI")
    assert result.value == "ok" and len(calls) == 2
    assert calls[1]["messages"][0]["content"] == "blockedword research"
    assert calls[1]["messages"][-1]["content"] == RETRY_FEEDBACK_PROMPT


@pytest.mark.asyncio
async def test_glm_outer_fields_survive_sdk_and_framework_wrapping(monkeypatch, tmp_path):
    body = {"contentFilter": [{"role": "user", "level": 1}], "error": {"code": "1301", "message": "unsafe"}}

    def handler(request):
        return httpx.Response(400, json=body)

    async with _model(monkeypatch, tmp_path, handler) as llm:
        with pytest.raises(CustomValueException) as caught:
            await _operation(llm, NodeId.FIND_ACTION_SPACE.value, attempts=1)
    signal = detect_error(caught.value)
    assert signal.side == "input" and signal.code == "1301"
    assert caught.value.__cause__ is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [1, 3, 4])
async def test_repeated_filter_preserves_failure_type_budget_and_caller_fallback(monkeypatch, tmp_path, attempts):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_completion("content_filter"))

    async with _model(monkeypatch, tmp_path, handler) as llm:
        try:
            await _operation(llm, NodeId.FIND_ACTION_SPACE.value, attempts)
        except CustomValueException as error:
            assert error.error_code == StatusCode.LLM_CALL_FAILED.code
            fallback = "original caller fallback"
    assert fallback == "original caller fallback" and len(calls) == attempts


@pytest.mark.asyncio
async def test_no_scope_still_fails_once_without_new_retry(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_completion("content_filter"))

    async with _model(monkeypatch, tmp_path, handler) as llm:
        with pytest.raises(CustomValueException):
            await llm_utils.ainvoke_llm_with_stats(
                llm=llm, messages=MESSAGES, agent_name=NodeId.FIND_ACTION_SPACE.value,
            )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_disabled_switch_retains_original_response_processing(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(200, json=_completion("content_filter", '{"value":"unchanged"}'))

    async with _model(monkeypatch, tmp_path, handler, enabled=False) as llm:
        result = await _operation(llm, NodeId.FIND_ACTION_SPACE.value)
    assert result.value == "unchanged"


@pytest.mark.asyncio
async def test_non_moderation_sdk_error_keeps_original_attempts_and_messages(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(401, json={"error": {"code": "invalid_api_key", "message": "invalid key"}})

    async with _model(monkeypatch, tmp_path, handler) as llm:
        with pytest.raises(CustomValueException) as caught:
            await _operation(llm, NodeId.FIND_ACTION_SPACE.value)
    assert detect_error(caught.value) is None
    assert len(calls) == 3
    assert all(c["messages"] == calls[0]["messages"] for c in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [
    (400, {"error": {"code": "SensitiveContentDetected", "message": "input may contain sensitive information"}}),
    (400, {"error": {"code": "data_inspection_failed", "message": "Input data may contain inappropriate content"}}),
    (400, {"error": {"code": "data_inspection_failed", "message": "Input text data may contain inappropriate content"}}),
    (422, {"error": {"type": "unprocessable_entity_error", "message": "input new_sensitive (1026)",
                     "http_code": "422"}}),
])
async def test_other_input_error_envelopes_reach_existing_next_attempt(monkeypatch, tmp_path, status, body):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(status, json=body) if len(calls) == 1 else httpx.Response(200, json=_completion())

    async with _model(monkeypatch, tmp_path, handler) as llm:
        result = await _operation(llm, NodeId.FIND_ACTION_SPACE.value)
    assert result.value == "ok" and len(calls) == 2
    assert calls[1]["messages"][0]["content"] == "*********** research"


@pytest.mark.asyncio
async def test_filtered_chunk_not_forwarded_and_partial_response_not_parsed(monkeypatch, tmp_path):
    def handler(request):
        return httpx.Response(200, text=_sse(
            _chunk('{"value":"partial"}'), _chunk("filtered payload", "content_filter"),
        ), headers={"content-type": "text/event-stream"})

    session = SimpleNamespace(write_custom_stream=AsyncMock(), get_global_state=lambda name: None)
    token = session_context.set(session)
    try:
        async with _model(monkeypatch, tmp_path, handler) as llm:
            def unexpected_parse(content):
                pytest.fail("filtered response must not reach JSON extraction")

            monkeypatch.setattr(llm_utils, "_extract_json", unexpected_parse)
            with pytest.raises(CustomValueException) as caught:
                await llm_utils.ainvoke_llm_with_stats(
                    llm=llm, messages=MESSAGES, need_stream_out=True, schema=Answer,
                )
    finally:
        session_context.reset(token)
    assert detect_error(caught.value).side == "unknown"
    payloads = [call.args[0] for call in session.write_custom_stream.await_args_list]
    assert any(p.get("content") == '{"value":"partial"}' for p in payloads)
    assert not any(p.get("content") == "filtered payload" for p in payloads)
    assert payloads[-1]["event"] == llm_utils.StreamEvent.DONE.value
