# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import os

from pydantic import BaseModel, Field

from openjiuwen_codesearch.config.agent import SearchAgentConfig
from openjiuwen_codesearch.config.index import (  # noqa: F401
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
        """环境变量：OPENROUTER_API_KEY（LLM/embedding）、
        MILVUS_HOST / MILVUS_PORT / MILVUS_TOKEN（与 e2e 测试及 deepsearch 惯例一致）。
        """
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        return cls(
            llm=LLMSuite(
                main=LLMConfig(model_name="openai/gpt-5", api_key=api_key),
                filter=LLMConfig(
                    model_name="openai/gpt-5-mini", api_key=api_key, max_tokens=2048
                ),
            ),
            embed=EmbedConfig(api_key=api_key),
            milvus=MilvusConfig(
                host=os.getenv("MILVUS_HOST", "localhost"),
                port=os.getenv("MILVUS_PORT", "19530"),
                token=os.getenv("MILVUS_TOKEN", ""),
            ),
        )
