# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
from typing import Literal, Optional

from pydantic import BaseModel

# Local default so importing this module does not pull bm25s/numpy.
DEFAULT_TOKENIZE_WORKERS = max(1, (os.cpu_count() or 4) - 1)

# Short names for Feat_* fields / FEAT_* env vars (defaults live on the model).
FEATURE_FLAGS = (
    "ban_tests",
    "anti_early_finish",
    "same_file_expand",
    "second_file_probe",
    "inherits_expand",
    "expand_imports",
    "delete_snippets",
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
    (``MAX_*``, ``FEAT_*``, ``RETRIEVER``, …). LLM credentials live on
    ``CodeSearchConfig.llm``, not here.
    """

    retriever: str = "bm25"
    # On-disk dump of KG + BM25 (per collection). Empty disables persistence.
    index_dir: str = "./output/retropus"
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

    # Feature flags (post-ablation defaults; FEAT_ALL / FEAT_* override via from_env)
    feat_ban_tests: bool = False
    feat_anti_early_finish: bool = False
    feat_same_file_expand: bool = False
    feat_second_file_probe: bool = False
    feat_inherits_expand: bool = True
    # Suggest-only IMPORTS expand tool (does not block finish).
    feat_expand_imports: bool = False
    # Register CodeSearch ``delete_snippets`` to drop bad ``add_context`` spans by id.
    feat_delete_snippets: bool = False

    def feature_flags(self) -> dict[str, bool]:
        """Map each ``FEATURE_FLAGS`` name to its current boolean value."""
        return {name: getattr(self, f"feat_{name}") for name in FEATURE_FLAGS}

    def enabled_features(self) -> list[str]:
        """Names of feature flags that are currently on."""
        return [name for name, on in self.feature_flags().items() if on]

    @classmethod
    def from_env(cls) -> "RetropusSearchAgentConfig":
        """Build from process env (and ``.env`` if present)."""
        from openjiuwen_codesearch.config.env_file import (  # noqa: PLC0415
            ensure_dotenv_loaded,
        )

        ensure_dotenv_loaded()

        defaults = cls()
        retriever = (_first_env("RETRIEVER", default=defaults.retriever) or "bm25").lower()

        # Allow FEAT_ALL=0|1 to force every feature off/on, then
        # individual FEAT_<NAME> overrides. Per-flag defaults come from the model.
        all_override = _first_env("FEAT_ALL")
        all_default: Optional[bool] = None
        if all_override is not None:
            all_default = all_override.strip().lower() in ("1", "true", "yes", "on")

        feat_flags = {
            name: _bool_env(
                f"FEAT_{name.upper()}",
                default=(
                    all_default
                    if all_default is not None
                    else getattr(defaults, f"feat_{name}")
                ),
            )
            for name in FEATURE_FLAGS
        }

        return cls(
            retriever=retriever,
            index_dir=(
                _first_env("RETROPUS_INDEX_DIR", default=defaults.index_dir)
                or defaults.index_dir
            ),
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
            feat_ban_tests=feat_flags["ban_tests"],
            feat_anti_early_finish=feat_flags["anti_early_finish"],
            feat_same_file_expand=feat_flags["same_file_expand"],
            feat_second_file_probe=feat_flags["second_file_probe"],
            feat_inherits_expand=feat_flags["inherits_expand"],
            feat_expand_imports=feat_flags["expand_imports"],
            feat_delete_snippets=feat_flags["delete_snippets"],
        )
