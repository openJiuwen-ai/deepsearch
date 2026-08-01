# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import os
from typing import Literal, Optional

from pydantic import BaseModel

# Local default so importing this module does not pull bm25s/numpy.
DEFAULT_TOKENIZE_WORKERS = max(1, (os.cpu_count() or 4) - 1)

# Short names for Imp_* fields / IMP_* env vars (defaults live on the model).
_IMPROVEMENT_FLAGS = (
    "ban_tests",
    "anti_early_finish",
    "same_file_expand",
    "second_file_probe",
    "inherits_expand",
)


def _first_env(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip() != "":
            return value
    return default


def _int_env(*names: str, default: int) -> int:
    raw = _first_env(*names)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(*names: str, default: bool) -> bool:
    raw = _first_env(*names)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class SearchAgentConfig(BaseModel):
    # 引擎：graph = openjiuwen workflow 图形态（默认，SDK 亮点，Studio/Ops 可观测）；
    # react = 纯代码循环（openjiuwen 不可用时的兜底）；auto = graph 可用则 graph。
    engine: Literal["auto", "react", "graph", "retropus"] = "auto"
    max_turns: int = 20
    warn_before_turns: int = 2        # 距上限 N 轮时注入"必须提交"警告
    # graph 引擎的 workflow 执行超时（openjiuwen 默认仅 60s，多轮检索必须放宽）
    time_limit_seconds: int = 900
    retrieve_topk: int = 20           # 最终返回的 snippet 数上限
    search_topk: int = 10             # 每次 search_codebase 检索条数
    filter_concurrency: int = 8       # 过滤 agent 并发上限（semaphore）
    stagnation_rounds: int = 3        # 连续 N 个含检索的轮次无新增 → STAGNATED
    strict_trigram: bool = True       # trigram 检索后按真实子串包含过滤
    trace_dir: str = "agent_logs"     # 轨迹 jsonl 目录；空串关闭轨迹


class RetropusSearchAgentConfig(BaseModel):
    """Retropus runtime settings on ``CodeSearchConfig.retropus``.

    Loaded from ``codesearch/.env`` / process env via ``from_env()``
    (``MAX_*``, ``IMP_*``, ``RETRIEVER``, …). LLM credentials live on
    ``CodeSearchConfig.llm``, not here.
    """

    retriever: str = "bm25"
    max_tool_calls: int = 24
    max_rounds: int = 12
    max_final_spans: int = 25
    max_obs_chars: int = 6000
    max_read_lines: int = 400
    max_ast_depth: int = 6
    chunk_size: int = 1000
    chunk_overlap: int = 200
    code_aware_tokenizer: bool = False
    tokenize_workers: int = DEFAULT_TOKENIZE_WORKERS
    min_spans_before_finish: int = 3
    min_files_before_finish: int = 1
    # Pad final pred with top retriever defs if fewer than this many spans
    # were recorded (0 = disabled; empty-only legacy fallback of 5 still runs).
    min_mandatory_return_spans: int = 0

    # Improvement flags (post-ablation defaults; IMP_ALL / IMP_* override via from_env)
    imp_ban_tests: bool = False
    imp_anti_early_finish: bool = False
    imp_same_file_expand: bool = False
    imp_second_file_probe: bool = False
    imp_inherits_expand: bool = True

    def improvement_flags(self) -> dict[str, bool]:
        return {name: getattr(self, f"imp_{name}") for name in _IMPROVEMENT_FLAGS}

    def enabled_improvements(self) -> list[str]:
        return [name for name, on in self.improvement_flags().items() if on]

    @classmethod
    def from_env(cls) -> RetropusSearchAgentConfig:
        """Build from process env (and ``codesearch/.env`` if present)."""
        # Lazy import avoids circular import with CodeSearchConfig.
        from openjiuwen_codesearch.config import config as cfg_mod  # noqa: PLC0415

        cfg_mod._load_dotenv()

        defaults = cls()
        retriever = (_first_env("RETRIEVER", default=defaults.retriever) or "bm25").lower()

        # Allow IMP_ALL=0|1 to force every improvement off/on, then
        # individual IMP_<NAME> overrides. Per-flag defaults come from the model.
        all_override = _first_env("IMP_ALL")
        all_default: Optional[bool] = None
        if all_override is not None:
            all_default = all_override.strip().lower() in ("1", "true", "yes", "on")

        imp_flags = {
            name: _bool_env(
                f"IMP_{name.upper()}",
                default=(
                    all_default
                    if all_default is not None
                    else getattr(defaults, f"imp_{name}")
                ),
            )
            for name in _IMPROVEMENT_FLAGS
        }

        return cls(
            retriever=retriever,
            max_tool_calls=_int_env(
                "MAX_TOOL_CALLS", default=defaults.max_tool_calls
            ),
            max_rounds=_int_env("MAX_ROUNDS", default=defaults.max_rounds),
            max_final_spans=_int_env(
                "MAX_FINAL_SPANS", default=defaults.max_final_spans
            ),
            max_obs_chars=_int_env(
                "MAX_OBS_CHARS", default=defaults.max_obs_chars
            ),
            max_read_lines=_int_env(
                "MAX_READ_LINES", default=defaults.max_read_lines
            ),
            max_ast_depth=_int_env("MAX_AST_DEPTH", default=defaults.max_ast_depth),
            chunk_size=_int_env("CHUNK_SIZE", default=defaults.chunk_size),
            chunk_overlap=_int_env("CHUNK_OVERLAP", default=defaults.chunk_overlap),
            code_aware_tokenizer=_bool_env(
                "CODE_AWARE_TOKENIZER", default=defaults.code_aware_tokenizer
            ),
            tokenize_workers=_int_env(
                "TOKENIZE_WORKERS", default=defaults.tokenize_workers
            ),
            min_spans_before_finish=_int_env(
                "MIN_SPANS_BEFORE_FINISH", default=defaults.min_spans_before_finish
            ),
            min_files_before_finish=_int_env(
                "MIN_FILES_BEFORE_FINISH", default=defaults.min_files_before_finish
            ),
            min_mandatory_return_spans=_int_env(
                "MIN_MANDATORY_RETURN_SPANS",
                "RETROPUS_MIN_MANDATORY_RETURN_SPANS",
                default=defaults.min_mandatory_return_spans,
            ),
            imp_ban_tests=imp_flags["ban_tests"],
            imp_anti_early_finish=imp_flags["anti_early_finish"],
            imp_same_file_expand=imp_flags["same_file_expand"],
            imp_second_file_probe=imp_flags["second_file_probe"],
            imp_inherits_expand=imp_flags["inherits_expand"],
        )
