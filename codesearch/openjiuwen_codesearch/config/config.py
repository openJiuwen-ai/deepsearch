# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import os

from pydantic import BaseModel, Field

from openjiuwen_codesearch.config.agent import SearchAgentConfig
from openjiuwen_codesearch.config.index import (
    EmbedConfig,
    IndexConfig,
    MilvusConfig,
)
from openjiuwen_codesearch.config.llm import LLMConfig, LLMSuite


class CodeSearchConfig(BaseModel):
    """总配置。一处解析、全程只读；运行期通过 RunContext 携带副本，
    禁止任何全局可变配置（旧 wrapper 改写全局 settings 的模式不得复现）。
    """

    llm: LLMSuite
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    milvus: MilvusConfig = Field(default_factory=MilvusConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    agent: SearchAgentConfig = Field(default_factory=SearchAgentConfig)

    @classmethod
    def from_env(cls) -> "CodeSearchConfig":
        """从环境变量组装配置（OpenAI 兼容端点）。

        与 deepsearch ``LLMConfig`` 一致，检索侧只认两组字段：
          ``api_key`` ← ``CODESEARCH_LLM_API_KEY``
          ``base_url`` ← ``CODESEARCH_LLM_BASE_URL``（默认空，须显式配置）

        模型名（可选）：
          ``CODESEARCH_LLM_MODEL``（主）、``CODESEARCH_FILTER_LLM_MODEL``（过滤）

        Milvus：``MILVUS_HOST`` / ``MILVUS_PORT`` / ``MILVUS_TOKEN``。
        """
        api_key = os.getenv("CODESEARCH_LLM_API_KEY", "")
        base_url = os.getenv("CODESEARCH_LLM_BASE_URL", "")
        main_model = os.getenv("CODESEARCH_LLM_MODEL", "openai/gpt-5")
        filter_model = os.getenv("CODESEARCH_FILTER_LLM_MODEL", "openai/gpt-5-mini")
        return cls(
            llm=LLMSuite(
                main=LLMConfig(
                    model_name=main_model, api_key=api_key, base_url=base_url
                ),
                filter=LLMConfig(
                    model_name=filter_model,
                    api_key=api_key,
                    base_url=base_url,
                    max_tokens=2048,
                ),
            ),
            embed=EmbedConfig(api_key=api_key),
            milvus=MilvusConfig(
                host=os.getenv("MILVUS_HOST", "localhost"),
                port=os.getenv("MILVUS_PORT", "19530"),
                token=os.getenv("MILVUS_TOKEN", ""),
            ),
        )
