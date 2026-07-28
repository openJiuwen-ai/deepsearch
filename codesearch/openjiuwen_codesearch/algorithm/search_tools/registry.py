# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""工具注册表：name → (schema, executor)。替代旧实现的 if/elif 大分发。

执行环境以 `ToolEnv` Protocol 定义（结构化子类型）：framework 层的
RunContext 满足该协议即可，algorithm 层不 import framework——依赖方向纪律。
"""

from typing import Any, Awaitable, Callable, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen_codesearch.domain.memory import SnippetMemory
from openjiuwen_codesearch.domain.models import Snippet
from openjiuwen_codesearch.llm.factory import LLMClient


class ToolEnv(Protocol):
    """工具执行所需的最小环境（RunContext 结构化满足）。"""

    query: str
    revision: str
    memory: SnippetMemory
    filter_llm: LLMClient
    search_topk: int
    filter_concurrency: int
    retriever: "RetrieverLike"


class RetrieverLike(Protocol):
    async def search(
        self, query: str, revision: str, topk: int, use_trigram: bool
    ) -> list[Snippet]: ...

    async def get_repo_map(self, revision: str) -> str: ...

    async def fetch_overlapping(
        self, revision: str, file_path: str, start_line: int, end_line: int
    ) -> list[Snippet]: ...

    async def has_revision(self, revision: str) -> bool: ...


class ToolOutcome(BaseModel):
    """单次工具执行的结果。orchestrator 依据它更新停滞计数、成本归账与终止判断。"""

    message: str = ""
    added_snippets: int = 0
    searched: bool = False          # 是否属于"检索类"调用（参与停滞计数）
    filter_cost: float = 0.0        # 本次调用产生的过滤 agent 成本（stage: filter_llm）
    submitted_ids: Optional[list[int]] = None  # 非 None 即为最终提交


ToolExecutor = Callable[[Any, dict], Awaitable[ToolOutcome]]


class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    name: str
    schema_: dict = Field(alias="schema")
    executor: ToolExecutor


def build_default_registry() -> dict[str, ToolSpec]:
    """默认 5 工具注册表。import 放在函数内避免循环依赖。"""
    from openjiuwen_codesearch.algorithm.search_tools import (  # noqa: PLC0415
        expand_context,
        memory_tools,
        repo_map,
        search_codebase,
    )

    specs = [
        repo_map.SPEC,
        expand_context.SPEC,
        search_codebase.SPEC,
        memory_tools.DELETE_SPEC,
        memory_tools.SUBMIT_SPEC,
    ]
    return {spec.name: spec for spec in specs}


def registry_schemas(registry: dict[str, ToolSpec]) -> list[dict]:
    return [spec.schema_ for spec in registry.values()]
