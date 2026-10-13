from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.chart_generation import utils as utils_module
from openjiuwen_deepsearch.algorithm.chart_generation import (
    vlm_chart_generator as vlm_chart_generator_module,
)
from openjiuwen_deepsearch.algorithm.chart_generation.utils import (
    CallModelInput,
    call_model,
)
from openjiuwen_deepsearch.algorithm.chart_generation.vlm_chart_generator import (
    VLMChartGenerator,
)


pytestmark = pytest.mark.unit


def _messages_from_call(call):
    if "messages" in call.kwargs:
        return call.kwargs["messages"]
    return call.args[1]


@pytest.mark.asyncio
async def test_call_model_builds_stable_system_and_dynamic_user_messages():
    model = object()
    context = {
        "extracted_chart_json": '{"records":[["A","1","kg"]]}',
        "section_outline": "Annual output",
    }

    with (
        patch.object(
            utils_module,
            "llm_context",
            SimpleNamespace(get=lambda: {"model": model}),
        ),
        patch.object(
            utils_module,
            "ainvoke_llm_with_stats",
            new=AsyncMock(return_value={"content": '{"valid":true,"error_msg":""}'}),
        ) as mocked_llm,
    ):
        result = await call_model(
            CallModelInput(
                model_name="model",
                prompt="chart_compliance_validate",
                user_input=context,
            )
        )

    messages = _messages_from_call(mocked_llm.await_args)
    assert result == {"valid": True, "error_msg": ""}
    assert [message["role"] for message in messages] == ["system", "user"]
    assert context["extracted_chart_json"] not in messages[0]["content"]
    assert context["section_outline"] not in messages[0]["content"]
    assert context["extracted_chart_json"] in messages[1]["content"]
    assert context["section_outline"] in messages[1]["content"]


@pytest.mark.asyncio
async def test_call_model_vlm_retry_reuses_one_multimodal_message_snapshot():
    model = object()
    test_base64 = "dGVzdC1pbWFnZQ=="
    responses = [
        {"content": ""},
        {"content": '{"suggestion":"pass","score":100}'},
    ]
    user_input = {
        "chart_title": "Output",
        "chart_description": "Annual output",
        "chart_type": "bar",
        "chart_data": {"A": 1, "B": 2},
        "history_suggestion": [],
        "chart_base64": test_base64,
    }

    with (
        patch.object(
            utils_module,
            "llm_context",
            SimpleNamespace(get=lambda: {"model": model}),
        ),
        patch.object(
            utils_module,
            "ainvoke_llm_with_stats",
            new=AsyncMock(side_effect=responses),
        ) as mocked_llm,
    ):
        result = await call_model(
            CallModelInput(
                model_name="model",
                prompt="vlm_iterate_prompt",
                user_input=user_input,
            ),
            use_vlm=True,
        )

    first_messages = _messages_from_call(mocked_llm.await_args_list[0])
    second_messages = _messages_from_call(mocked_llm.await_args_list[1])
    assert result == {"suggestion": "pass", "score": 100}
    assert first_messages is second_messages
    assert [message["role"] for message in first_messages] == ["system", "user"]
    assert isinstance(first_messages[0]["content"], str)
    assert isinstance(first_messages[1]["content"], list)
    image_parts = [
        part
        for part in first_messages[1]["content"]
        if part.get("type") == "image_url"
    ]
    assert image_parts == [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{test_base64}"},
        }
    ]
    assert test_base64 not in first_messages[0]["content"]


@pytest.mark.asyncio
async def test_llm_model_capability_probe_uses_builder_multimodal_messages():
    model = object()
    test_base64 = "cHJvYmUtaW1hZ2U="
    generator = VLMChartGenerator.__new__(VLMChartGenerator)
    generator._log_prefix = "[VLMChartGenerator]"

    with (
        patch.object(
            vlm_chart_generator_module,
            "get_chart_base64",
            return_value=test_base64,
        ),
        patch.object(
            vlm_chart_generator_module,
            "llm_context",
            SimpleNamespace(get=lambda: {"model": model}),
        ),
        patch.object(
            vlm_chart_generator_module,
            "ainvoke_llm_with_stats",
            new=AsyncMock(return_value={"content": "A test chart"}),
        ) as mocked_llm,
    ):
        supported = await generator.test_llm_model("model")

    messages = _messages_from_call(mocked_llm.await_args)
    assert supported is True
    assert [message["role"] for message in messages] == ["system", "user"]
    assert isinstance(messages[0]["content"], str)
    assert isinstance(messages[1]["content"], list)
    assert messages[1]["content"][0]["type"] == "text"
    assert "not supported" in messages[1]["content"][0]["text"]
    assert messages[1]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{test_base64}"},
    }
