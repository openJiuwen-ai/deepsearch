# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for ContextBench prediction export / eval summary helpers."""

import json
import os
import sys
import types

import pytest

from benchmarks.contextbench.exporter import write_eval_summary


def test_write_eval_summary_writes_json(tmp_path, monkeypatch):
    metrics_path = tmp_path / "run.jsonl_metrics.jsonl"
    metrics_path.write_text(
        json.dumps(
            {
                "instance_id": "demo",
                "final": {
                    "file": {
                        "coverage": 1.0,
                        "precision": 0.5,
                        "f1": 0.666,
                        "intersection": 1,
                        "gold_size": 1,
                        "pred_size": 2,
                    }
                },
            }
        )
        + "\n"
        + json.dumps({"instance_id": "broken", "error": "missing_gold"})
        + "\n",
        encoding="utf-8",
    )

    fake_agg = {
        "num_valid": 1,
        "num_total": 2,
        "final_file": {"coverage": 1.0, "precision": 0.5, "f1": 0.666},
    }
    fake_mod = types.ModuleType("contextbench.evaluate")

    def _aggregate_results(rows, **_kw):
        return dict(fake_agg)

    fake_mod.aggregate_results = _aggregate_results
    monkeypatch.setitem(sys.modules, "contextbench.evaluate", fake_mod)
    # Parent package may also be missing in a bare unit env.
    if "contextbench" not in sys.modules:
        monkeypatch.setitem(sys.modules, "contextbench", types.ModuleType("contextbench"))

    summary_path = write_eval_summary(str(metrics_path), contextbench_dir=str(tmp_path))
    assert summary_path == str(tmp_path / "run.jsonl_summary.json")
    payload = json.loads((tmp_path / "run.jsonl_summary.json").read_text(encoding="utf-8"))
    assert payload["num_valid"] == 1
    assert payload["num_total"] == 2
    assert payload["final_file"]["f1"] == 0.666
    assert payload["errors"] == {"missing_gold": 1}


def test_resolve_contextbench_dir_honors_env(tmp_path, monkeypatch):
    from benchmarks.contextbench.dataset import (
        DEFAULT_CONTEXTBENCH_DIR,
        ensure_contextbench_importable,
        resolve_contextbench_dir,
        resolve_parquet_path,
    )

    monkeypatch.delenv("CONTEXTBENCH_DIR", raising=False)
    monkeypatch.delenv("CONTEXTBENCH_PARQUET", raising=False)
    assert resolve_contextbench_dir() == DEFAULT_CONTEXTBENCH_DIR

    monkeypatch.setenv("CONTEXTBENCH_DIR", str(tmp_path))
    assert resolve_contextbench_dir() == os.path.abspath(str(tmp_path))
    assert resolve_contextbench_dir("/explicit") == os.path.abspath("/explicit")

    parquet = tmp_path / "gold.parquet"
    monkeypatch.setenv("CONTEXTBENCH_PARQUET", str(parquet))
    assert resolve_parquet_path() == os.path.abspath(str(parquet))

    marker = tmp_path / "contextbench" / "__init__.py"
    marker.parent.mkdir()
    marker.write_text("", encoding="utf-8")
    assert ensure_contextbench_importable() == os.path.abspath(str(tmp_path))


def test_ensure_contextbench_importable_missing(tmp_path, monkeypatch):
    from benchmarks.contextbench.dataset import ensure_contextbench_importable

    monkeypatch.setenv("CONTEXTBENCH_DIR", str(tmp_path / "absent"))
    with pytest.raises(FileNotFoundError, match="fetch_contextbench"):
        ensure_contextbench_importable()
