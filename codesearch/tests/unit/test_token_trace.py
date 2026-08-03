# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared token/trace helpers used by CodeSearch and Retropus run contexts."""

from __future__ import annotations

import json
from pathlib import Path

from openjiuwen_codesearch.framework.openjiuwen.token_trace import (
    add_tokens,
    build_trace_path,
    total_input_tokens,
    total_output_tokens,
    write_trace,
)


def test_add_and_sum_tokens():
    stage: dict[str, tuple[int, int]] = {}
    add_tokens(stage, "main_llm", 10, 3)
    add_tokens(stage, "main_llm", 5, 1)
    add_tokens(stage, "filter_llm", 2, 4)
    assert total_input_tokens(stage) == 17
    assert total_output_tokens(stage) == 8


def test_write_trace_and_build_path(tmp_path: Path):
    assert build_trace_path(None, "rev") is None
    path = build_trace_path(str(tmp_path), "repo/path", stem_prefix="retropus_")
    assert path is not None
    assert path.endswith("retropus_repo_path.jsonl")
    write_trace(path, {"a": 1})
    write_trace(None, {"ignored": True})
    lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"a": 1}
