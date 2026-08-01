# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-run state for RetropusCodeSearchAgent (isolated from CodeSearchRunContext)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.domain.models import ToolCall
from openjiuwen_codesearch.domain.result import Termination
from openjiuwen_codesearch.llm.factory import ChatMessage, LLMClient

if TYPE_CHECKING:
    from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig
    from openjiuwen_codesearch.domain.result import CodeSearchResult
    from openjiuwen_codesearch.retropus.graph.knowledge_graph import KnowledgeGraph
    from openjiuwen_codesearch.retropus.retrievers.base import AbstractBaseRetriever
    from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import (
        RetrievalTools,
    )


@dataclass
class RetropusRunContext:
    config: CodeSearchConfig
    retropus_config: "RetropusSearchAgentConfig"
    query: str
    top_k: int
    repo_dir: Path
    kg: Any
    retriever: Any
    main_llm: LLMClient
    tools: Any  # RetrievalTools-like
    trace_path: Optional[str] = None

    # loop state
    turn: int = 0
    tool_calls_made: int = 0
    nudges: int = 0
    finish_requested: bool = False
    finish_blocked: bool = False
    history: list[ChatMessage] = field(default_factory=list)
    pending_calls: list[ToolCall] = field(default_factory=list)
    system_prompt: str = ""
    issue_text: str = ""
    termination: Optional[Termination] = None
    error: str = ""
    tokens_by_stage: dict[str, tuple[int, int]] = field(default_factory=dict)
    result: Optional["CodeSearchResult"] = None

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


def build_retropus_run_context(
    *,
    config: CodeSearchConfig,
    query: str,
    top_k: int,
    repo_dir: Path,
    kg: Any,
    retriever: Any,
    main_llm: LLMClient,
    issue_title: str = "",
    issue_body: str = "",
) -> RetropusRunContext:
    from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import (  # noqa: PLC0415
        RetrievalTools,
    )

    retropus_config = config.retropus
    issue_text = (issue_title or "").strip()
    body = (issue_body or query or "").strip()
    if body:
        issue_text = f"{issue_text}\n\n{body}" if issue_text else body
    if not issue_text:
        issue_text = query

    tools = RetrievalTools(
        kg, retriever, Path(repo_dir), retropus_config, issue_text=issue_text
    )

    trace_path = None
    if config.agent.trace_dir:
        stamp = datetime.now().strftime("%Y%m%d__%H%M%S_%f")
        safe = str(repo_dir).replace("/", "_")[:64]
        trace_path = os.path.join(config.agent.trace_dir, stamp, f"retropus_{safe}.jsonl")

    return RetropusRunContext(
        config=config.model_copy(deep=True),
        retropus_config=retropus_config,
        query=query,
        top_k=top_k,
        repo_dir=Path(repo_dir),
        kg=kg,
        retriever=retriever,
        main_llm=main_llm,
        tools=tools,
        issue_text=issue_text,
        trace_path=trace_path,
    )
