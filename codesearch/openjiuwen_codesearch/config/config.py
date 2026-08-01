# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import os
from pathlib import Path

from pydantic import BaseModel, Field

from openjiuwen_codesearch.config.agent import (
    RetropusSearchAgentConfig,
    SearchAgentConfig,
)
from openjiuwen_codesearch.config.index import (
    EmbedConfig,
    IndexConfig,
    MilvusConfig,
)
from openjiuwen_codesearch.config.llm import LLMConfig, LLMSuite

# codesearch/.env (this file lives in openjiuwen_codesearch/config/)
_CODESEARCH_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ENV_FILE = _CODESEARCH_ROOT / ".env"
_DOTENV_LOADED = False


def _load_dotenv(env_file: Path | None = None, *, override: bool = False) -> None:
    """Load ``codesearch/.env`` into ``os.environ`` (existing values win by default)."""
    global _DOTENV_LOADED
    path = env_file if env_file is not None else _DEFAULT_ENV_FILE
    if _DOTENV_LOADED and not override and env_file is None:
        return
    if not path.is_file():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not override and key in os.environ and os.environ[key].strip() != "":
            continue
        os.environ[key] = value

    if env_file is None:
        _DOTENV_LOADED = True


class CodeSearchConfig(BaseModel):
    """总配置。一处解析、全程只读；运行期通过 RunContext 携带副本，
    禁止任何全局可变配置（旧 wrapper 改写全局 settings 的模式不得复现）。
    """

    llm: LLMSuite
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    milvus: MilvusConfig = Field(default_factory=MilvusConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    agent: SearchAgentConfig = Field(default_factory=SearchAgentConfig)
    retropus: RetropusSearchAgentConfig = Field(
        default_factory=RetropusSearchAgentConfig
    )

    @classmethod
    def from_env(cls) -> "CodeSearchConfig":
        """从 ``codesearch/.env``（若存在）与进程环境构造配置。

        LLM：``OPENAI_API_KEY`` / ``OPENAI_BASE_URL``（默认 OpenRouter）/ ``MODEL``。
        Milvus：``MILVUS_HOST`` / ``MILVUS_PORT`` / ``MILVUS_TOKEN``。
        Retropus：``MAX_*`` / ``IMP_*`` / ``RETRIEVER`` 等 → ``retropus``
        （供 ``RetropusCodeSearchAgent`` 与 contextbench runner 使用）。
        """
        _load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY", "")
        api_base = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
        model = os.getenv("MODEL") or os.getenv("OPENAI_MODEL") or "openai/gpt-5"
        temperature = float(os.getenv("TEMPERATURE", "0") or "0")
        # RetropusSearchAgentConfig.from_env also loads dotenv (idempotent); call
        # after _load_dotenv so LLM + retropus share one env snapshot.
        return cls(
            llm=LLMSuite(
                main=LLMConfig(
                    model_name=model,
                    api_key=api_key,
                    api_base=api_base,
                    temperature=temperature,
                ),
                filter=LLMConfig(
                    model_name=model,
                    api_key=api_key,
                    api_base=api_base,
                    temperature=temperature,
                    max_tokens=2048,
                ),
            ),
            embed=EmbedConfig(api_key=api_key),
            milvus=MilvusConfig(
                host=os.getenv("MILVUS_HOST", "localhost"),
                port=os.getenv("MILVUS_PORT", "19530"),
                token=os.getenv("MILVUS_TOKEN", ""),
            ),
            retropus=RetropusSearchAgentConfig.from_env(),
        )
