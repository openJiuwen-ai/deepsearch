# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from pydantic import BaseModel, Field

# LLMConfig（通用 OpenAI 兼容客户端配置，含 SSL/证书处理）由 base 包提供
from openjiuwen_search_base.llm import LLMConfig


class LLMSuite(BaseModel):
    """codesearch 产品概念：主模型（决策）+ 过滤模型（逐行提取）。"""

    main: LLMConfig
    filter: LLMConfig = Field(
        default_factory=lambda: LLMConfig(model_name="openai/gpt-5-mini", max_tokens=2048)
    )
