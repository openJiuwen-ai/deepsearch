# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""每次运行的可变状态（对齐 deepsearch `DeepSearchRunContext` 模式）。

规则：
- 运行态一律挂在本对象上，禁止挂 Agent 实例的 `self`（重叠运行隔离）；
- 含锁/连接对象（retriever）只在这里持有引用，禁止进入可被复制的 workflow state；
- config 为深拷贝副本，运行期只读。
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from openjiuwen_search_base.runtime import RunRegistry

from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.domain.memory import SnippetMemory
from openjiuwen_codesearch.domain.models import ToolCall
from openjiuwen_codesearch.domain.result import Termination
from openjiuwen_codesearch.llm.factory import ChatMessage, LLMClient
from openjiuwen_codesearch.retrieval.base import CodeRetriever

if TYPE_CHECKING:
    from openjiuwen_codesearch.domain.result import CodeSearchResult


@dataclass
class CodeSearchRunContext:
    config: CodeSearchConfig
    query: str
    revision: str
    top_k: int
    retriever: CodeRetriever
    main_llm: LLMClient
    filter_llm: LLMClient
    memory: SnippetMemory = field(default_factory=SnippetMemory)
    trace_path: Optional[str] = None

    # 运行态
    turn: int = 0
    empty_search_rounds: int = 0
    termination: Optional[Termination] = None
    error: str = ""
    tokens_by_stage: dict[str, tuple[int, int]] = field(default_factory=dict)

    # 循环中间态（react 与图形态共用；大对象不进 workflow session state）
    base_prompt: str = ""
    history: list[ChatMessage] = field(default_factory=list)
    pending_calls: list[ToolCall] = field(default_factory=list)
    submitted_ids: list[int] = field(default_factory=list)
    pending_termination: Optional[Termination] = None
    result: Optional["CodeSearchResult"] = None

    # --- ToolEnv 协议所需的派生属性 ---
    @property
    def search_topk(self) -> int:
        return self.config.agent.search_topk

    @property
    def filter_concurrency(self) -> int:
        return self.config.agent.filter_concurrency

    @property
    def total_input_tokens(self) -> int:
        return sum(i for i, _ in self.tokens_by_stage.values())

    @property
    def total_output_tokens(self) -> int:
        return sum(o for _, o in self.tokens_by_stage.values())

    def add_tokens(self, stage: str, input_tokens: int, output_tokens: int) -> None:
        prev_in, prev_out = self.tokens_by_stage.get(stage, (0, 0))
        self.tokens_by_stage[stage] = (prev_in + input_tokens, prev_out + output_tokens)

    def write_trace(self, record: dict[str, Any]) -> None:
        if not self.trace_path:
            return
        os.makedirs(os.path.dirname(self.trace_path), exist_ok=True)
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# --- 运行注册表（泛型实现由 base 包提供）：workflow session 只携带可序列化的
#     run_id，含连接/锁的活对象经此注册表在节点内取回。---
_RUN_REGISTRY: RunRegistry[CodeSearchRunContext] = RunRegistry()


def register_run_context(ctx: CodeSearchRunContext) -> str:
    return _RUN_REGISTRY.register(ctx)


def run_session(ctx: CodeSearchRunContext):
    """结构化注册（推荐）：`with run_session(ctx) as run_id:`，退出自动注销。"""
    return _RUN_REGISTRY.session(ctx)


def get_run_context(run_id: str) -> CodeSearchRunContext:
    return _RUN_REGISTRY.get(run_id)


def unregister_run_context(run_id: str) -> None:
    _RUN_REGISTRY.unregister(run_id)


def build_run_context(
    config: CodeSearchConfig,
    query: str,
    revision: str,
    top_k: int,
    retriever: CodeRetriever,
    main_llm: LLMClient,
    filter_llm: LLMClient,
) -> CodeSearchRunContext:
    trace_path = None
    if config.agent.trace_dir:
        stamp = datetime.now().strftime("%Y%m%d__%H%M%S_%f")
        safe_rev = revision.replace("/", "_")[:64]
        trace_path = os.path.join(config.agent.trace_dir, stamp, f"{safe_rev}.jsonl")
    return CodeSearchRunContext(
        config=config.model_copy(deep=True),
        query=query,
        revision=revision,
        top_k=top_k,
        retriever=retriever,
        main_llm=main_llm,
        filter_llm=filter_llm,
        trace_path=trace_path,
    )
