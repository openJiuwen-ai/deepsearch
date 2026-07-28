# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM 客户端抽象。

上层（algorithm/framework）只依赖 `LLMClient` 协议与规范化的
`LLMResponse`/`ChatMessage`；openJiuwen 的具体 API 面被隔离在
`OpenJiuwenLLMClient` 内（guarded import），版本 spike 的结论只影响这一个文件。
"""

import json
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen_codesearch.config.llm import LLMConfig
from openjiuwen_codesearch.domain.models import ToolCall


class ChatMessage(BaseModel):
    """规范化会话消息。assistant 消息可携带 provider 原生对象（raw）以保持历史保真。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: str  # user | assistant | tool
    content: str = ""
    tool_call_id: str = ""
    raw: Any = None


class LLMResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: Optional[str] = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    cost: float = 0.0
    raw: Any = None


@runtime_checkable
class LLMClient(Protocol):
    async def invoke(
        self, messages: list[ChatMessage], tools: Optional[list[dict]] = None
    ) -> LLMResponse: ...


def normalize_tool_calls(raw_tool_calls: Any) -> list[ToolCall]:
    """兼容两种 provider 形态：call.name/arguments 或 call.function.name/arguments。
    与旧实现的 getattr 回退逻辑一致；arguments 解析为 dict。
    """
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
    """从 usage_metadata 提取 total_cost（OpenRouter 特有字段），与旧实现一致。"""
    usage = getattr(response, "usage_metadata", {})
    if isinstance(usage, dict):
        return float(usage.get("total_cost", 0.0) or 0.0)
    return float(getattr(usage, "total_cost", 0.0) or 0.0)


class OpenJiuwenLLMClient:
    """基于 openjiuwen `core.foundation.llm.Model` 的实现（jiuwenCoder 所用 API 面）。

    注意：deepsearch 锁定的 openjiuwen 0.1.10.post3 使用不同 API 面
    （core.workflow/Runner/Session）。两者的兼容性验证见 plan Phase 1 spike；
    本类构造时才 import，环境缺依赖不影响其余功能。
    """

    def __init__(self, config: LLMConfig, client_id: str = "codesearch") -> None:
        from openjiuwen.core.foundation.llm import (  # noqa: PLC0415  guarded import
            Model,
            ModelClientConfig,
            ModelRequestConfig,
        )

        client_kwargs: dict = {}
        if config.verify_ssl:
            # openjiuwen 强制 verify_ssl=True 时提供证书路径，且证书必须位于
            # SAFE_CERT_DIR 白名单目录内；默认用 certifi CA 包并把其目录注册为
            # SAFE_CERT_DIR（仅在未设置时），避免旧实现"为绕校验而关闭 SSL 验证"。
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
                client_provider="OpenAI",  # OpenRouter 走 OpenAI 兼容 API
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
        response = await self._model.invoke(
            self._to_provider_messages(messages), tools=tools or []
        )
        return LLMResponse(
            content=getattr(response, "content", None),
            tool_calls=normalize_tool_calls(getattr(response, "tool_calls", None)),
            cost=extract_cost(response),
            raw=response,
        )


def create_llm_client(config: LLMConfig, client_id: str = "codesearch") -> LLMClient:
    return OpenJiuwenLLMClient(config, client_id=client_id)
