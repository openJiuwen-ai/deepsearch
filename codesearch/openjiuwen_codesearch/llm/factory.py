# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM 客户端 —— 由 openjiuwen-search-base 提供（2026-07-29 提取，见 base/README.md）。

本模块保留原 import 路径作为薄壳；codesearch 侧仅定制默认 client_id。
"""

from openjiuwen_search_base.llm import (  # noqa: F401  re-export
    ChatMessage,
    LLMClient,
    LLMResponse,
    OpenJiuwenLLMClient,
    extract_cost,
    normalize_tool_calls,
)
from openjiuwen_search_base.llm import create_llm_client as _base_create_llm_client

from openjiuwen_codesearch.config.llm import LLMConfig


def create_llm_client(config: LLMConfig, client_id: str = "codesearch") -> LLMClient:
    return _base_create_llm_client(config, client_id=client_id)
