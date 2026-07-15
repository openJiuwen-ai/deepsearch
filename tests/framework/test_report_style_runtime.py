"""Test temporary LLM runtime support for report styling."""

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.llm import report_style_runtime as runtime
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context


@pytest.mark.asyncio
async def test_report_style_runtime_prefers_writing_model_and_restores_context(monkeypatch):
    """Use writing_checking and restore the caller's LLM context.

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
                "general": {"model_name": "general-model", "api_key": "general-key"},
                "writing_checking": {"model_name": "styled-model", "api_key": "style-key"},
            }
        ) as llm:
            assert llm is styled_llm

        assert created_configs[0].model_name == "styled-model"
        assert llm_context.get() is outer_registry
    finally:
        llm_context.reset(outer_token)


@pytest.mark.asyncio
async def test_report_style_runtime_accepts_single_model_and_rejects_empty_config(monkeypatch):
    """Treat a single model as general and reject a missing model configuration.

    Args:
        monkeypatch: pytest 的动态替换夹具。
    """
    single_llm = {"model_name": "single-model", "model": object()}
    monkeypatch.setattr(runtime, "create_llm_obj", lambda config: single_llm)

    async with runtime.report_style_llm_context(
        {"model_name": "single-model", "api_key": "single-key"}
    ) as llm:
        assert llm is single_llm

    with pytest.raises(runtime.ReportStyleValidationError, match="llm_config"):
        async with runtime.report_style_llm_context({}):
            pytest.fail("empty config must not enter the LLM context")
