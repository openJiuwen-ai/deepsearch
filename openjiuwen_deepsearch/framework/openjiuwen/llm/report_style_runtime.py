# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Provide temporary LLM runtime setup for report styling."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import ValidationError

from openjiuwen_deepsearch.algorithm.report_style.exceptions import ReportStyleValidationError
from openjiuwen_deepsearch.config.config import LLMConfig
from openjiuwen_deepsearch.llm.llm_wrapper import create_llm_obj
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context


def _select_report_style_config(raw_config: dict) -> LLMConfig:
    """选择报告样式化调用所需的单一 LLM 配置。

    Args:
        raw_config: 直接模型配置，或以模型类别为键的 DeepSearch LLM 配置。

    Returns:
        LLMConfig: 校验后的样式化模型配置。

    Raises:
        ReportStyleValidationError: 缺少可用模型配置或配置格式非法时抛出。
    """
    if not isinstance(raw_config, dict):
        raise ReportStyleValidationError("llm_config must be a dictionary")

    candidate = raw_config
    if "model_name" not in raw_config:
        candidate = raw_config.get("writing_checking") or raw_config.get("general")
    if not isinstance(candidate, dict):
        raise ReportStyleValidationError("llm_config requires writing_checking or general")

    try:
        return LLMConfig.model_validate(candidate)
    except ValidationError as exc:
        raise ReportStyleValidationError("invalid llm_config") from exc


@asynccontextmanager
async def report_style_llm_context(llm_config: dict) -> AsyncIterator[dict]:
    """为一次报告样式化请求建立并复位 LLM 上下文。

    Args:
        llm_config: 直接模型配置，或以模型类别为键的 DeepSearch LLM 配置。

    Yields:
        dict: 可直接传入 `ainvoke_llm_with_stats` 的 LLM 运行时对象。

    Raises:
        ReportStyleValidationError: 模型配置非法或模型实例创建失败时抛出。
    """
    config = _select_report_style_config(llm_config)
    try:
        runtime_llm = create_llm_obj(config)
    except Exception as exc:
        raise ReportStyleValidationError("unable to create report style LLM") from exc

    # ContextVar token guarantees nested workflow contexts are restored exactly.
    token = llm_context.set({config.model_name: runtime_llm})
    try:
        yield runtime_llm
    finally:
        llm_context.reset(token)
