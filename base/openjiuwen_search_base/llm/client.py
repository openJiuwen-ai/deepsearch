# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM 客户端抽象与 openJiuwen 适配。

消费方只依赖 `LLMClient` 协议与规范化类型；openJiuwen 的具体 API 面隔离在
`OpenJiuwenLLMClient` 内（guarded import），框架版本变化只影响本文件。
"""

import json
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openjiuwen_search_base.llm.types import ChatMessage, LLMResponse, ToolCall
from openjiuwen_search_base.security import SecretInput, reveal_secret, to_secret


class LLMConfig(BaseModel):
    """OpenAI 兼容端点的通用客户端配置。

    密钥以 `bytearray` 存储（与 openJiuwen 系列产品一致），仅在发起调用时解码，
    避免不可变字符串在进程内长期驻留且无法擦除；构造时可直接传字符串。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str
    api_key: bytearray = Field(default_factory=bytearray)
    api_base: str = "https://openrouter.ai/api/v1"
    temperature: float = 0.0
    max_tokens: int = 8192
    # verify_ssl=True 时底层客户端要求显式提供 CA 证书路径；
    # ssl_cert 留空则运行时自动使用 certifi 提供的 CA 包。
    # 保持 verify_ssl=True 是推荐配置，仅在受控内网且证书不可得时才关闭。
    verify_ssl: bool = True
    ssl_cert: str = ""
    # 单次 invoke 的外层兜底超时（openjiuwen 客户端内部另有 60s×3 重试；
    # 本值为整次调用的硬上界，防极端挂死）
    timeout_seconds: float = 600.0

    @field_validator("api_key", mode="before")
    @classmethod
    def _normalize_api_key(cls, v: SecretInput) -> bytearray:
        return to_secret(v)


@runtime_checkable
class LLMClient(Protocol):
    async def invoke(
        self, messages: list[ChatMessage], tools: Optional[list[dict]] = None
    ) -> LLMResponse:
        ...


def normalize_tool_calls(raw_tool_calls: Any) -> list[ToolCall]:
    """兼容两种 provider 形态：call.name/arguments 或 call.function.name/arguments。"""
    normalized: list[ToolCall] = []
    for call in raw_tool_calls or []:
        name = getattr(call, "name", None) or getattr(
            getattr(call, "function", None), "name", None
        )
        arguments_raw = getattr(call, "arguments", None) or getattr(
            getattr(call, "function", None), "arguments", None
        )
        call_id = getattr(call, "id", "") or ""
        if not name:
            continue
        if isinstance(arguments_raw, dict):
            arguments = arguments_raw
        else:
            try:
                arguments = json.loads(arguments_raw) if arguments_raw else {}
            except (TypeError, ValueError):
                arguments = {}
        normalized.append(ToolCall(name=name, arguments=arguments, call_id=call_id))
    return normalized


def extract_usage(response: Any) -> tuple[int, int]:
    """从 usage_metadata 提取 (input_tokens, output_tokens)。

    token 是 OpenAI 兼容端点的通用字段，换端点仍有效；金额字段各家不一，
    故本层只上报 token，不上报费用。
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    return int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)


class OpenJiuwenLLMClient:
    """基于 openjiuwen `core.foundation.llm.Model` 的实现（构造时才 import）。"""

    def __init__(self, config: LLMConfig, client_id: str = "search") -> None:
        from openjiuwen.core.foundation.llm import (  # guarded import
            Model,
            ModelClientConfig,
            ModelRequestConfig,
        )

        client_kwargs: dict = {}
        if config.verify_ssl:
            # 底层客户端要求证书位于环境变量 SAFE_CERT_DIR 指向的目录内。
            # 未设置时以 certifi 的 CA 目录填充（setdefault：不覆盖调用方已有配置）。
            # 该变量是进程级的：同进程内使用不同证书目录时，以最先设置的为准。
            # 使用自定义证书请在进程启动前设置 SAFE_CERT_DIR。
            import os

            ssl_cert = config.ssl_cert
            if not ssl_cert:
                import certifi

                ssl_cert = certifi.where()
            os.environ.setdefault("SAFE_CERT_DIR", os.path.dirname(ssl_cert))
            client_kwargs["ssl_cert"] = ssl_cert

        self._config = config
        self._model = Model(
            model_client_config=ModelClientConfig(
                client_id=client_id,
                client_provider="OpenAI",  # OpenRouter 等走 OpenAI 兼容 API
                api_key=reveal_secret(config.api_key),
                api_base=config.api_base,
                verify_ssl=config.verify_ssl,
                **client_kwargs,
            ),
            model_config=ModelRequestConfig(
                model_name=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            ),
        )

    @staticmethod
    def _to_provider_messages(messages: list[ChatMessage]) -> list[Any]:
        from openjiuwen.core.foundation.llm import ToolMessage, UserMessage

        provider_messages: list[Any] = []
        for msg in messages:
            if msg.role == "assistant" and msg.raw is not None:
                provider_messages.append(msg.raw)
            elif msg.role == "tool":
                provider_messages.append(
                    ToolMessage(tool_call_id=msg.tool_call_id, content=msg.content)
                )
            else:
                provider_messages.append(UserMessage(content=msg.content))
        return provider_messages

    async def invoke(
        self, messages: list[ChatMessage], tools: Optional[list[dict]] = None
    ) -> LLMResponse:
        import asyncio

        response = await asyncio.wait_for(
            self._model.invoke(self._to_provider_messages(messages), tools=tools or []),
            timeout=self._config.timeout_seconds,
        )
        input_tokens, output_tokens = extract_usage(response)
        return LLMResponse(
            content=getattr(response, "content", None),
            tool_calls=normalize_tool_calls(getattr(response, "tool_calls", None)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=response,
        )


def create_llm_client(config: LLMConfig, client_id: str = "search") -> LLMClient:
    return OpenJiuwenLLMClient(config, client_id=client_id)
