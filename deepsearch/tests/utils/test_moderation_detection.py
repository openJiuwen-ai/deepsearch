"""Fixed protocol fixtures, with no real credentials or online calls."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError
from pydantic import BaseModel, ValidationError

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen_deepsearch.utils.moderation.detection import detect_error, detect_response
from openjiuwen_deepsearch.utils.moderation import mask as moderation_mask


@pytest.mark.parametrize("prefix,side", [
    ("Input data", "input"), ("Input text data", "input"),
    ("Output data", "output"), ("Output text data", "output"),
])
def test_inspection_variants(prefix, side):
    payload = {"error": {"code": "data_inspection_failed", "message": f"{prefix} may contain inappropriate content."}}
    signal = detect_response(payload)
    assert (signal.side, signal.code, signal.source) == (side, "data_inspection_failed", "response")
    assert moderation_mask.is_moderation_error(json.dumps(payload))
    assert moderation_mask.moderation_error_side(json.dumps(payload)) == side


@pytest.mark.parametrize("payload,side,code", [
    ({"error": {"code": "data_inspection_failed"}}, "unknown", "data_inspection_failed"),
    ({"error": {"code": "SensitiveContentDetected", "message": "input may contain sensitive information"}},
     "input", "sensitivecontentdetected"),
    ({"error": {"code": "SensitiveContentDetected", "message": "sensitive content detected"}},
     "unknown", "sensitivecontentdetected"),
    ({"contentFilter": [{"role": "user", "level": 1}], "error": {"code": "1301", "message": "unsafe"}},
     "input", "1301"),
    ({"contentFilter": [{"role": "assistant"}], "error": {"code": 1301, "message": "unsafe"}},
     "unknown", "1301"),
    ({"error": {"code": "1301", "message": "unsafe"}}, "unknown", "1301"),
    ({"error": {"type": "unprocessable_entity_error", "message": "input new_sensitive (1026)", "http_code": "422"}},
     "input", "minimax_input_sensitive"),
])
def test_structured_error_families(payload, side, code):
    signal = detect_response(payload)
    assert (signal.side, signal.code) == (side, code)


@pytest.mark.parametrize("response", [
    {"choices": [{"finish_reason": "content_filter", "message": {"role": "assistant"}}]},
    AssistantMessage(content="", finish_reason="content_filter"),
    AssistantMessageChunk(content="", finish_reason="content_filter"),
    SimpleNamespace(finish_reason="content_filter"),
])
def test_filter_is_unknown_side_even_for_assistant_message(response):
    signal = detect_response(response, "stream")
    assert (signal.side, signal.code, signal.source) == ("unknown", "content_filter", "stream")


def test_sdk_inner_error_does_not_lose_outer_glm_role():
    body = {"contentFilter": [{"role": "user", "level": 1}], "error": {"code": "1301", "message": "unsafe"}}
    response = httpx.Response(400, json=body, request=httpx.Request("POST", "https://test.invalid"))
    error = BadRequestError("upstream error", response=response, body=body["error"])
    outer = RuntimeError("wrapped")
    outer.__cause__ = error
    signal = detect_error(outer)
    assert signal.side == "input" and signal.evidence == "content_filter_user"


def test_structured_error_wins_over_ambiguous_wrapper_text():
    error = RuntimeError("Input data may contain inappropriate content")
    error.body = {"code": "data_inspection_failed", "message": "Output data may contain inappropriate content"}
    assert detect_error(error).side == "output"


def test_conflicting_explicit_side_fields_remain_unknown():
    payload = {"contentFilter": [{"role": "user"}], "error": {
        "code": "1301", "message": "Output data may contain inappropriate content",
    }}
    signal = detect_response(payload)
    assert signal.side == "unknown" and signal.evidence == "conflicting_side_fields"
    assert moderation_mask.moderation_error_side(
        "Input data may contain inappropriate; Output data may contain inappropriate"
    ) == ""


def test_exception_chain_cycle_and_context():
    first = RuntimeError("wrapper")
    second = RuntimeError("Input text data may contain inappropriate content")
    first.__context__ = second
    second.__cause__ = first
    assert detect_error(first).side == "input"


@pytest.mark.parametrize("value", [
    {"content": "data_inspection_failed", "finish_reason": "stop"},
    {"choices": [{"finish_reason": "stop", "message": {"content": "I cannot help with this request."}}]},
    {"finish_reason": "length"},
    {"error": {"code": "1301"}},
    {"error": {"code": "1026", "message": "other failure"}},
    {"error": {"message": "input new_sensitive (1026)"}},
    {"error": {"type": "other", "message": "input new_sensitive (1026)", "http_code": "422"}},
    {"error": {"code": "model_not_found", "message": "model not found"}},
    {"error": {"code": "invalid_argument", "message": "invalid top_p"}},
    {"error": {"code": "invalid_api_key", "message": "invalid key"}},
    None,
])
def test_non_moderation_payloads(value):
    assert detect_response(value) is None


@pytest.mark.parametrize("message", [
    "1301", "1026", "SensitiveContentDetected", "context length exceeded",
    "rate limit exceeded", "invalid API key", "timeout", "I cannot help with this request.",
])
def test_no_bare_numeric_codes_or_refusal_guessing(message):
    assert detect_error(RuntimeError(message)) is None


def test_unread_response_falls_back_to_error_body():
    response = httpx.Response(400, stream=httpx.ByteStream(b"not read"))
    error = RuntimeError("wrapper")
    error.response = response
    error.body = {"code": "data_inspection_failed"}
    assert detect_error(error).side == "unknown"


def test_code_only_body_can_use_explicit_error_message_for_side():
    error = RuntimeError("Output data may contain inappropriate content")
    error.body = {"code": "data_inspection_failed"}
    assert detect_error(error).side == "output"


@pytest.mark.parametrize("text", ["data_inspection_failed", "Input data may contain inappropriate content"])
def test_validation_error_quoting_generated_content_is_not_moderation(text):
    class Result(BaseModel):
        value: int

    with pytest.raises(ValidationError) as caught:
        Result.model_validate({"value": text})
    wrapper = RuntimeError(str(caught.value))
    wrapper.__cause__ = caught.value
    assert detect_error(wrapper) is None


def test_json_parse_error_quoting_error_code_is_not_moderation():
    error = json.JSONDecodeError("data_inspection_failed", "invalid JSON", 0)
    assert detect_error(error) is None
