# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM 客户端抽象与 openJiuwen 适配。

消费方只依赖 `LLMClient` 协议与规范化类型；openJiuwen 的具体 API 面隔离在
`OpenJiuwenLLMClient` 内（guarded import），框架版本变化只影响本文件。
"""

import json
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from openjiuwen_search_base.llm.types import ChatMessage, LLMResponse, ToolCall


class LLMConfig(BaseModel):
    """OpenAI 兼容端点的通用客户端配置。"""

    model_name: str
    api_key: str = ""
    api_base: str = "https://openrouter.ai/api/v1"
    temperature: float = 0.0
    max_tokens: int = 8192
    # openjiuwen 要求 verify_ssl=True 时必须提供证书路径（历史实现常为绕过校验
    # 而关闭 SSL 验证——不安全）；留空则运行时自动使用 certifi 的系统 CA 包。
    verify_ssl: bool = True
    ssl_cert: str = ""
    # 单次 invoke 的外层兜底超时（openjiuwen 客户端内部另有 60s×3 重试；
    # 本值为整次调用的硬上界，防极端挂死）
    timeout_seconds: float = 600.0


@runtime_checkable
class LLMClient(Protocol):
    async def invoke(
        self, messages: list[ChatMessage], tools: Optional[list[dict]] = None
    ) -> LLMResponse: ...


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


def extract_cost(response: Any) -> float:
    """从 usage_metadata 提取 total_cost（OpenRouter 特有字段）。"""
    usage = getattr(response, "usage_metadata", {})
    if isinstance(usage, dict):
        return float(usage.get("total_cost", 0.0) or 0.0)
    return float(getattr(usage, "total_cost", 0.0) or 0.0)


class OpenJiuwenLLMClient:
    """基于 openjiuwen `core.foundation.llm.Model` 的实现（构造时才 import）。"""

    def __init__(self, config: LLMConfig, client_id: str = "search") -> None:
        from openjiuwen.core.foundation.llm import (  # noqa: PLC0415  guarded import
            Model,
            ModelClientConfig,
            ModelRequestConfig,
        )

        client_kwargs: dict = {}
        if config.verify_ssl:
            # openjiuwen 强制 verify_ssl=True 时提供证书路径，且证书必须位于
            # SAFE_CERT_DIR 白名单目录内；默认用 certifi CA 包并注册其目录（仅在未设置时）。
            # ⚠️ 已知限制（openjiuwen 以进程级 env 承载该白名单，无法按客户端传参）：
            # SAFE_CERT_DIR 是全局副作用——同进程多产品若使用**不同**证书目录，
            # 先初始化者生效；均用默认 certifi 时目录相同，无冲突。自定义证书场景
            # 请在进程启动前显式设置 SAFE_CERT_DIR。
            import os  # noqa: PLC0415

            ssl_cert = config.ssl_cert
            if not ssl_cert:
                import certifi  # noqa: PLC0415

                ssl_cert = certifi.where()
            os.environ.setdefault("SAFE_CERT_DIR", os.path.dirname(ssl_cert))
            client_kwargs["ssl_cert"] = ssl_cert

        self._config = config
        self._model = Model(
            model_client_config=ModelClientConfig(
                client_id=client_id,
                client_provider="OpenAI",  # OpenRouter 等走 OpenAI 兼容 API
                api_key=config.api_key,
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

    def _to_provider_messages(self, messages: list[ChatMessage]) -> list[Any]:
        from openjiuwen.core.foundation.llm import ToolMessage, UserMessage  # noqa: PLC0415

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
        import asyncio  # noqa: PLC0415

        response = await asyncio.wait_for(
            self._model.invoke(self._to_provider_messages(messages), tools=tools or []),
            timeout=self._config.timeout_seconds,
        )
        return LLMResponse(
            content=getattr(response, "content", None),
            tool_calls=normalize_tool_calls(getattr(response, "tool_calls", None)),
            cost=extract_cost(response),
            raw=response,
        )


def create_llm_client(config: LLMConfig, client_id: str = "search") -> LLMClient:
    return OpenJiuwenLLMClient(config, client_id=client_id)
