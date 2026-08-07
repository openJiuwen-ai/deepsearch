# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared token accounting + JSONL trace helpers for run contexts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping, Optional


def total_input_tokens(tokens_by_stage: Mapping[str, tuple[int, int]]) -> int:
    """Sum recorded input tokens across stages."""
    return sum(input_tokens for input_tokens, _ in tokens_by_stage.values())


def total_output_tokens(tokens_by_stage: Mapping[str, tuple[int, int]]) -> int:
    """Sum recorded output tokens across stages."""
    return sum(output_tokens for _, output_tokens in tokens_by_stage.values())


def add_tokens(
    tokens_by_stage: MutableMapping[str, tuple[int, int]],
    stage: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Accumulate token usage for ``stage`` (additive across calls)."""
    prev_in, prev_out = tokens_by_stage.get(stage, (0, 0))
    tokens_by_stage[stage] = (prev_in + input_tokens, prev_out + output_tokens)


def write_trace(trace_path: Optional[str], record: dict[str, Any]) -> None:
    """Append one JSONL trace record when ``trace_path`` is configured."""
    if not trace_path:
        return
    os.makedirs(os.path.dirname(trace_path), exist_ok=True)
    with open(trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def build_trace_path(
    trace_dir: Optional[str],
    safe_label: str,
    *,
    stem_prefix: str = "",
) -> Optional[str]:
    """Build ``{trace_dir}/{stamp}/{stem_prefix}{safe_label}.jsonl`` or ``None``."""
    if not trace_dir:
        return None
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d__%H%M%S_%f")
    safe = str(safe_label).replace("/", "_")[:64]
    return os.path.join(trace_dir, stamp, f"{stem_prefix}{safe}.jsonl")
