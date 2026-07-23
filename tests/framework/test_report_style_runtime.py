"""Test temporary LLM runtime support for report styling."""

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.llm import report_style_runtime as runtime
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context


@pytest.mark.asyncio
async def test_report_style_runtime_uses_general_model_and_restores_context(monkeypatch):
    """Use general and restore the caller's LLM context.

    Args:
        monkeypatch: pytest 的动态替换夹具。
    """
    created_configs = []
    styled_llm = {"model_name": "styled-model", "model": object()}
    monkeypatch.setattr(
        runtime,
        "create_llm_obj",
        lambda config: created_configs.append(config) or styled_llm,
    )

    outer_registry = {"outer": {"model_name": "outer"}}
    outer_token = llm_context.set(outer_registry)
    try:
        async with runtime.report_style_llm_context(
            {
                "general": {"model_name": "general-model", "api_key": bytearray(b"general-key")},
                "writing_checking": {"model_name": "styled-model", "api_key": bytearray(b"style-key")},
            }
        ) as llm:
            assert llm is styled_llm

        assert created_configs[0].model_name == "general-model"
        assert llm_context.get() is outer_registry
    finally:
        llm_context.reset(outer_token)


@pytest.mark.asyncio
async def test_report_style_runtime_accepts_direct_model_and_rejects_missing_config(monkeypatch):
    """Accept direct model configuration and reject missing usable configuration.

    Args:
        monkeypatch: pytest 的动态替换夹具。
    """
    direct_llm = {"model_name": "direct-model", "model": object()}
    monkeypatch.setattr(runtime, "create_llm_obj", lambda config: direct_llm)

    async with runtime.report_style_llm_context(
        {"model_name": "direct-model", "api_key": bytearray(b"direct-key")}
    ) as llm:
        assert llm is direct_llm

    for invalid_config in ({}, {"writing_checking": {"model_name": "old-model"}}):
        with pytest.raises(CustomValueException, match="llm_config"):
            async with runtime.report_style_llm_context(invalid_config):
                pytest.fail("invalid config must not enter the LLM context")
