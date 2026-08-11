# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import traceback

import pytest

from openjiuwen_deepsearch.config.config import LLMConfig
from openjiuwen_deepsearch.llm import llm_wrapper
from openjiuwen_deepsearch.llm import llm_request_adapter
from openjiuwen_deepsearch.utils.common_utils import llm_utils
from openjiuwen_deepsearch.llm.llm_request_adapter import (
    build_thinking_fallback_extension,
    merge_thinking_extension,
    resolve_llm_thinking_enabled,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats


def _llm_config(
    *,
    base_url: str,
    model_name: str = "test-model",
    model_type: str = "openai",
    extension: dict | None = None,
) -> LLMConfig:
    return LLMConfig(
        model_name=model_name,
        model_type=model_type,
        base_url=base_url,
        api_key=bytearray(b"test-key"),
        extension=extension or {},
    )


class _FakeSession:
    def __init__(self):
        self.state = {}

    def get_global_state(self, key):
        value = self.state
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value

    def update_global_state(self, values):
        for key, value in values.items():
            target = self.state
            parts = key.split(".")
            for part in parts[:-1]:
                next_target = target.get(part)
                if not isinstance(next_target, dict):
                    next_target = {}
                    target[part] = next_target
                target = next_target
            target[parts[-1]] = value


@pytest.mark.parametrize(
    "base_url, model_name",
    [
        ("https://api.deepseek.com", "deepseek-reasoner"),
        ("https://open.bigmodel.cn/api/paas/v4", "glm-4.7"),
        ("https://api.moonshot.cn/v1", "kimi-k2"),
        ("https://ark.cn-beijing.volces.com/api/v3", "doubao-seed"),
        ("https://api.modelarts-maas.com/v1/chat/completion", "huawei-maas-model"),
        ("https://api.modelarts-maas.com/v2/chat/completions", "huawei-maas-model"),
    ],
)
def test_merge_thinking_extension_uses_thinking_type(base_url, model_name):
    config = _llm_config(base_url=base_url, model_name=model_name)

    result = merge_thinking_extension(config, thinking_enabled=False)

    assert result["extra_body"]["thinking"] == {"type": "disabled"}


def test_merge_thinking_extension_uses_dashscope_enable_thinking(caplog):
    config = _llm_config(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        extension={"extra_body": {"thinking": {"type": "enabled"}, "other": 1}},
    )
    caplog.set_level(logging.WARNING, logger=llm_request_adapter.__name__)

    result = merge_thinking_extension(config, thinking_enabled=False)

    assert result["extra_body"]["enable_thinking"] is False
    assert result["extra_body"]["other"] == 1
    assert "thinking" not in result["extra_body"]
    assert "Existing thinking fields in LLMConfig.extension are overridden" in caplog.text
    assert "extra_body.thinking" in caplog.text


def test_merge_thinking_extension_uses_siliconflow_top_level_field():
    config = _llm_config(
        base_url="https://api.siliconflow.cn/v1",
        model_type="siliconflow",
        extension={"extra_body": {"enable_thinking": True}},
    )

    result = merge_thinking_extension(config, thinking_enabled=False)

    assert result["enable_thinking"] is False
    assert "extra_body" in result
    assert "enable_thinking" not in result["extra_body"]


def test_merge_thinking_extension_uses_huawei_openai_compatible_chat_template_kwargs():
    config = _llm_config(
        base_url="https://api.modelarts-maas.com/openai/v1",
    )

    result = merge_thinking_extension(config, thinking_enabled=False)

    assert result["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "thinking" not in result["extra_body"]


def test_build_thinking_fallback_extension_removes_all_thinking_switch_fields():
    config = _llm_config(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    extension = {
        "enable_thinking": False,
        "extra_body": {
            "enable_thinking": False,
            "thinking": {"type": "disabled"},
            "chat_template_kwargs": {
                "enable_thinking": False,
                "thinking": {"type": "disabled"},
            },
            "other": 1,
        },
    }

    fallback_extension, removed_fields = build_thinking_fallback_extension(config, extension)

    assert fallback_extension == {"extra_body": {"other": 1}}
    assert removed_fields == [
        "enable_thinking",
        "extra_body.thinking",
        "extra_body.enable_thinking",
        "extra_body.chat_template_kwargs.thinking",
        "extra_body.chat_template_kwargs.enable_thinking",
    ]


def test_merge_thinking_extension_keeps_original_extension_unchanged():
    original_extension = {"extra_body": {"thinking": {"type": "enabled"}}}
    config = _llm_config(base_url="https://api.deepseek.com", extension=original_extension)

    result = merge_thinking_extension(config, thinking_enabled=False)

    assert result["extra_body"]["thinking"] == {"type": "disabled"}
    assert original_extension == {"extra_body": {"thinking": {"type": "enabled"}}}


def test_merge_thinking_extension_warns_and_keeps_minimax_extension(caplog):
    extension = {"extra_body": {"enable_thinking": True}}
    config = _llm_config(base_url="https://api.minimax.io/v1", extension=extension)
    caplog.set_level(logging.WARNING, logger=llm_request_adapter.__name__)

    result = merge_thinking_extension(config, thinking_enabled=False)

    assert result == extension
    assert "does not support thinking switch" in caplog.text
    assert "minimax" in caplog.text


def test_resolve_llm_thinking_enabled_accepts_runtime_service_config():
    assert resolve_llm_thinking_enabled({"llm_thinking_enabled": "true"}) is True
    assert resolve_llm_thinking_enabled({"llm_thinking_enabled": "false"}) is False


def test_build_thinking_fallback_key_normalizes_base_url():
    config = _llm_config(
        base_url="HTTPS://DASHSCOPE.ALIYUNCS.COM/compatible-mode/v1/",
        model_name="qwen-plus",
    )
    same_config = _llm_config(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen-plus",
    )

    fallback_key = llm_wrapper._build_thinking_fallback_key(config)

    assert fallback_key == llm_wrapper._build_thinking_fallback_key(same_config)
    assert fallback_key.startswith("thinking_fallback:v1:")
    assert "|" not in fallback_key


def test_create_llm_obj_passes_merged_extension_to_factory(monkeypatch):
    captured = {"params": []}

    class DummyFactory:
        def get_model(self, params):
            captured["params"].append(params)
            return object()

    monkeypatch.setattr(llm_wrapper, "LLMModelFactory", lambda: DummyFactory())
    config = _llm_config(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen-plus",
    )

    result = llm_wrapper.create_llm_obj(config, thinking_enabled=False)

    assert result["model_name"] == "qwen-plus"
    assert captured["params"][0].extension["extra_body"]["enable_thinking"] is False
    assert captured["params"][1].extension == {}
    assert result["thinking_fallback_model"] is not None
    assert result["thinking_fallback_key"] == llm_wrapper._build_thinking_fallback_key(config)
    assert result["thinking_fallback_removed_fields"] == ["extra_body.enable_thinking"]


def test_create_llm_obj_fallback_uses_original_extension_without_merged_leakage(monkeypatch):
    captured = {"params": []}

    class DummyFactory:
        def get_model(self, params):
            captured["params"].append(params)
            return object()

    def fake_merge_thinking_extension(*_):
        return {
            "extra_body": {
                "original": True,
                "enable_thinking": False,
                "main_only": "do-not-leak",
            }
        }

    monkeypatch.setattr(llm_wrapper, "LLMModelFactory", lambda: DummyFactory())
    monkeypatch.setattr(llm_wrapper, "merge_thinking_extension", fake_merge_thinking_extension)
    config = _llm_config(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        extension={"extra_body": {"original": True}},
    )

    result = llm_wrapper.create_llm_obj(config, thinking_enabled=False)

    assert captured["params"][0].extension["extra_body"]["main_only"] == "do-not-leak"
    assert captured["params"][1].extension == {"extra_body": {"original": True}}
    assert result["thinking_fallback_model"] is not None
    assert result["thinking_fallback_removed_fields"] == ["extra_body.enable_thinking"]


def test_create_llm_obj_huawei_fallback_does_not_keep_empty_containers(monkeypatch):
    captured = {"params": []}

    class DummyFactory:
        def get_model(self, params):
            captured["params"].append(params)
            return object()

    monkeypatch.setattr(llm_wrapper, "LLMModelFactory", lambda: DummyFactory())
    config = _llm_config(base_url="https://api.modelarts-maas.com/openai/v1")

    result = llm_wrapper.create_llm_obj(config, thinking_enabled=False)

    assert captured["params"][0].extension == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
    }
    assert captured["params"][1].extension == {}
    assert result["thinking_fallback_model"] is not None
    assert result["thinking_fallback_removed_fields"] == [
        "extra_body.chat_template_kwargs.enable_thinking"
    ]


def test_create_llm_obj_skips_thinking_switch_by_default(monkeypatch):
    captured = {}

    class DummyFactory:
        def get_model(self, params):
            captured["params"] = params
            return object()

    monkeypatch.setattr(llm_wrapper, "LLMModelFactory", lambda: DummyFactory())
    config = _llm_config(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen-plus",
        extension={"extra_body": {"original": True}},
    )

    llm_wrapper.create_llm_obj(config)

    assert captured["params"].extension == {"extra_body": {"original": True}}


class _DummyResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = {}
        self.tool_calls = []

    def model_dump(self):
        return {
            "content": self.content,
            "usage_metadata": self.usage_metadata,
            "tool_calls": self.tool_calls,
        }


class _ThinkingErrorStreamModel:
    def __init__(self):
        self.calls = 0

    async def stream(self, **_):
        self.calls += 1
        raise RuntimeError(
            "<400> InvalidParameter: The value of the thinking parameter is unsupported."
        )
        yield _DummyResponse("")


class _SuccessStreamModel:
    def __init__(self):
        self.calls = 0

    async def stream(self, **_):
        self.calls += 1
        yield _DummyResponse("stream-ok")


class _FailingStreamModel:
    def __init__(self):
        self.calls = 0

    async def stream(self, **_):
        self.calls += 1
        raise RuntimeError("fallback stream failed")
        yield _DummyResponse("")


@pytest.mark.asyncio
async def test_ainvoke_llm_with_stats_retries_without_thinking_switch_and_activates_fallback(caplog):
    primary_model = _ThinkingErrorStreamModel()
    fallback_model = _SuccessStreamModel()
    llm = {
        "model": primary_model,
        "model_name": "qwen3-235b-a22b-thinking-2507",
        "thinking_fallback_model": fallback_model,
        "thinking_fallback_removed_fields": ["extra_body.thinking"],
        "thinking_fallback_active": False,
    }
    caplog.set_level(logging.WARNING)

    result = await ainvoke_llm_with_stats(
        llm=llm,
        messages=[{"role": "user", "content": "hi"}],
    )
    second_result = await ainvoke_llm_with_stats(
        llm=llm,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result["content"] == "stream-ok"
    assert second_result["content"] == "stream-ok"
    assert llm["thinking_fallback_active"] is True
    assert primary_model.calls == 1
    assert fallback_model.calls == 2
    assert "retry without thinking switch" in caplog.text
    assert "subsequent calls will use model without thinking switch" in caplog.text


@pytest.mark.asyncio
async def test_ainvoke_llm_with_stats_reuses_session_thinking_fallback_across_llm_dicts():
    fallback_key = "thinking_fallback:v1:test-model"
    session = _FakeSession()
    token = llm_utils.session_context.set(session)
    primary_model = _ThinkingErrorStreamModel()
    fallback_model = _SuccessStreamModel()
    next_primary_model = _ThinkingErrorStreamModel()
    next_fallback_model = _SuccessStreamModel()
    llm = {
        "model": primary_model,
        "model_name": "test-model",
        "thinking_fallback_model": fallback_model,
        "thinking_fallback_key": fallback_key,
        "thinking_fallback_removed_fields": ["extra_body.thinking"],
        "thinking_fallback_active": False,
    }
    next_llm = {
        "model": next_primary_model,
        "model_name": "test-model",
        "thinking_fallback_model": next_fallback_model,
        "thinking_fallback_key": fallback_key,
        "thinking_fallback_removed_fields": ["extra_body.thinking"],
        "thinking_fallback_active": False,
    }

    try:
        await ainvoke_llm_with_stats(
            llm=llm,
            messages=[{"role": "user", "content": "hi"}],
        )
        result = await ainvoke_llm_with_stats(
            llm=next_llm,
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        llm_utils.session_context.reset(token)

    assert result["content"] == "stream-ok"
    assert primary_model.calls == 1
    assert fallback_model.calls == 1
    assert next_primary_model.calls == 0
    assert next_fallback_model.calls == 1
    assert next_llm["thinking_fallback_active"] is True
    assert session.get_global_state(llm_utils._THINKING_FALLBACK_SESSION_KEY) == {fallback_key: True}


@pytest.mark.asyncio
async def test_ainvoke_llm_with_stats_stream_path_retries_without_thinking_switch():
    primary_model = _ThinkingErrorStreamModel()
    fallback_model = _SuccessStreamModel()
    llm = {
        "model": primary_model,
        "model_name": "test-model",
        "thinking_fallback_model": fallback_model,
        "thinking_fallback_removed_fields": ["extra_body.thinking"],
        "thinking_fallback_active": False,
    }

    result = await ainvoke_llm_with_stats(
        llm=llm,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result["content"] == "stream-ok"
    assert llm["thinking_fallback_active"] is True
    assert primary_model.calls == 1
    assert fallback_model.calls == 1


@pytest.mark.asyncio
async def test_ainvoke_llm_with_stats_active_stream_fallback_raises_without_formatting(monkeypatch):
    fallback_model = _FailingStreamModel()
    llm = {
        "model": object(),
        "model_name": "test-model",
        "thinking_fallback_model": fallback_model,
        "thinking_fallback_removed_fields": ["extra_body.thinking"],
        "thinking_fallback_active": True,
    }

    def fail_format(_):
        raise AssertionError("active fallback failures should not be formatted for retry checks")

    monkeypatch.setattr(llm_utils, "_format_llm_invoke_exception", fail_format)

    with pytest.raises(RuntimeError, match="fallback stream failed"):
        await ainvoke_llm_with_stats(
            llm=llm,
            messages=[{"role": "user", "content": "hi"}],
        )

    assert fallback_model.calls == 1


@pytest.mark.asyncio
async def test_ainvoke_llm_with_stats_stream_fallback_failure_preserves_traceback():
    primary_model = _ThinkingErrorStreamModel()
    fallback_model = _FailingStreamModel()
    llm = {
        "model": primary_model,
        "model_name": "test-model",
        "thinking_fallback_model": fallback_model,
        "thinking_fallback_removed_fields": ["extra_body.thinking"],
        "thinking_fallback_active": False,
    }

    with pytest.raises(RuntimeError, match="fallback stream failed") as exc_info:
        await ainvoke_llm_with_stats(
            llm=llm,
            messages=[{"role": "user", "content": "hi"}],
        )

    frames = traceback.extract_tb(exc_info.value.__traceback__)

    assert any(
        frame.name == "stream" and frame.line and "fallback stream failed" in frame.line
        for frame in frames
    )
    assert not any(
        frame.filename.endswith("llm_utils.py") and frame.line == "raise e"
        for frame in frames
    )
    assert exc_info.value.__cause__ is not None
    assert "unsupported" in str(exc_info.value.__cause__).lower()
    assert fallback_model.calls == 1
    assert llm["thinking_fallback_active"] is True

    with pytest.raises(RuntimeError, match="fallback stream failed"):
        await ainvoke_llm_with_stats(
            llm=llm,
            messages=[{"role": "user", "content": "hi"}],
        )

    assert primary_model.calls == 1
    assert fallback_model.calls == 2
