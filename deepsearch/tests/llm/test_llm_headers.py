"""Caller-provided OfficeAce headers reach primary and fallback LLM clients."""

from unittest.mock import MagicMock
import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from openjiuwen_deepsearch.config.config import LLMConfig
from openjiuwen_deepsearch.llm.llm_wrapper import create_llm_obj
from openjiuwen_deepsearch.framework.openjiuwen.llm import llm_model_factory


@pytest.mark.parametrize("use_aliases", [False, True])
@pytest.mark.parametrize("with_headers", [False, True])
def test_headers_reach_primary_and_fallback_clients(monkeypatch, use_aliases, with_headers):
    model = MagicMock()
    monkeypatch.setattr(llm_model_factory, "Model", model)
    headers = {
        "x-officeace-client": "officeace",
        "x-officeace-message-meta": '{"message_id":"message-123"}',
    }
    values = {
        (name if use_aliases else name.replace("-", "_")): value
        for name, value in headers.items()
    } if with_headers else {}
    config = LLMConfig(
        model_name="test-model",
        base_url="https://api.deepseek.com/v1",
        api_key=bytearray(b"test-key"),
        **values,
    )
    # Config serialization and reconstruction must preserve caller values.
    config = LLMConfig.model_validate(config.model_dump())
    result = create_llm_obj(config, thinking_enabled=False)

    assert result["thinking_fallback_model"] is not None
    assert model.call_count == 2
    for call in model.call_args_list:
        client_config = call.kwargs["model_client_config"]
        assert client_config.api_key == "test-key"
        assert client_config.custom_headers == (headers if with_headers else None)
        request_config = call.kwargs["model_config"].model_dump()
        assert not any(name in request_config for name in headers)


def test_only_supplied_header_is_sent(monkeypatch):
    model = MagicMock()
    monkeypatch.setattr(llm_model_factory, "Model", model)
    create_llm_obj(LLMConfig(
        model_name="test-model",
        base_url="https://example.com/v1",
        api_key=bytearray(b"test-key"),
        x_officeace_client="officeace",
    ))
    assert model.call_args.kwargs["model_client_config"].custom_headers == {
        "x-officeace-client": "officeace",
    }


@pytest.mark.parametrize("field", ["x_officeace_client", "x_officeace_message_meta"])
def test_header_values_must_be_strings(field):
    with pytest.raises(ValidationError):
        LLMConfig(**{field: {"message_id": "123"}})


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_officeace_headers_are_sent_in_http_requests(monkeypatch, streaming):
    from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient

    expected = {
        "x-officeace-client": "miniprogram|vivo|trial",
        "x-officeace-message-meta": "scen|sessionId|invokationId|message",
    }
    captured = []

    def handle(request):
        captured.append(request)
        if streaming:
            chunk = {
                "id": "test", "object": "chat.completion.chunk", "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}],
            }
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    # Keep the real openJiuwen/OpenAI request builders; replace only HTTP transport.
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        async with AsyncOpenAI(api_key="test-key", base_url="https://example.com/v1",
                               http_client=http_client) as client:
            monkeypatch.setattr(OpenAIModelClient, "_create_async_openai_client",
                                lambda self, timeout=None: client)
            llm = create_llm_obj(LLMConfig(
                model_name="test-model", base_url="https://api.deepseek.com/v1",
                api_key=bytearray(b"test-key"), **expected,
            ), thinking_enabled=False)
            for model in (llm["model"], llm["thinking_fallback_model"]):
                messages = [{"role": "user", "content": "hello"}]
                if streaming:
                    chunks = [chunk async for chunk in model.stream(messages, model="test-model")]
                    assert chunks
                else:
                    response = await model.invoke(messages, model="test-model")
                    assert response.content == "ok"

    assert len(captured) == 2
    for request in captured:
        assert request.url.path == "/v1/chat/completions"
        for name, value in expected.items():
            assert request.headers[name] == value
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert not any(name in body for name in expected)
