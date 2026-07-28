# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    model_name: str
    api_key: str = ""
    api_base: str = "https://openrouter.ai/api/v1"
    temperature: float = 0.0
    max_tokens: int = 8192
    # 旧实现硬编码 verify_ssl=False，属安全缺陷；新默认 True，需要时显式关闭。
    # 注意：openjiuwen 要求 verify_ssl=True 时必须提供证书路径（这正是旧实现
    # 关闭校验的原因）；留空则运行时自动使用 certifi 的系统 CA 包。
    verify_ssl: bool = True
    ssl_cert: str = ""


class LLMSuite(BaseModel):
    """主模型（决策）+ 过滤模型（逐行提取）。两者均可独立注入。"""

    main: LLMConfig
    filter: LLMConfig = Field(
        default_factory=lambda: LLMConfig(model_name="openai/gpt-5-mini", max_tokens=2048)
    )
