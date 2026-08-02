# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""预测文件生成与官方评测调用。

时间戳与输出文件在**全部实例完成后**生成一次，不再每实例写一个累积文件。
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime

from benchmarks.contextbench.dataset import DEFAULT_CONTEXTBENCH_DIR, DEFAULT_PARQUET

logger = logging.getLogger(__name__)


def append_prediction(partial_path: str, pred: dict) -> None:
    """逐实例增量落盘（长跑中途可评分/可恢复；最终文件仍由 write_predictions 输出）。"""
    os.makedirs(os.path.dirname(partial_path), exist_ok=True)
    with open(partial_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(pred, ensure_ascii=False) + "\n")


def write_predictions(
    preds: list[dict], results_dir: str, mode: str, topk: int, num_instances: int
) -> str:
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d__%H%M%S")
    output_path = os.path.join(
        results_dir, f"[{num_instances}]{timestamp}__{mode}_topk={topk}.jsonl"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        for p in preds:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    logger.info("Saved predictions at: %s", output_path)
    return output_path


def _load_metrics_rows(metrics_path: str) -> list[dict]:
    rows: list[dict] = []
    if not os.path.isfile(metrics_path):
        return rows
    with open(metrics_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_eval_summary(
    metrics_path: str,
    summary_path: str | None = None,
    contextbench_dir: str = DEFAULT_CONTEXTBENCH_DIR,
) -> str | None:
    """Persist the aggregate EVALUATION summary next to the per-instance metrics JSONL."""
    rows = _load_metrics_rows(metrics_path)
    if not rows:
        logger.warning("No metrics rows to summarize at %s", metrics_path)
        return None

    # Reuse official micro-average aggregation so the JSON matches the printed banner.
    if contextbench_dir not in sys.path:
        sys.path.insert(0, contextbench_dir)
    from contextbench.evaluate import aggregate_results

    summary = aggregate_results(rows)
    error_counts: dict[str, int] = {}
    n_empty_scored = 0
    for row in rows:
        err = row.get("error")
        if err:
            error_counts[err] = error_counts.get(err, 0) + 1
        if row.get("empty_retrieval"):
            n_empty_scored += 1
    if error_counts:
        summary["errors"] = error_counts
    if n_empty_scored:
        summary["empty_retrieval_scored"] = n_empty_scored

    if summary_path is None:
        if metrics_path.endswith("_metrics.jsonl"):
            summary_path = metrics_path[: -len("_metrics.jsonl")] + "_summary.json"
        else:
            summary_path = f"{metrics_path}_summary.json"

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")
    logger.info("Saved evaluation summary at: %s", summary_path)
    return summary_path


def run_eval(
    pred_file: str,
    gold_path: str = DEFAULT_PARQUET,
    contextbench_dir: str = DEFAULT_CONTEXTBENCH_DIR,
) -> int:
    metrics_path = f"{pred_file}_metrics.jsonl"
    cmd = [
        sys.executable, "-m", "contextbench.evaluate",
        "--gold", gold_path,
        "--pred", pred_file,
        "--out", metrics_path,
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = contextbench_dir + os.pathsep + env.get("PYTHONPATH", "")
    logger.info("Starting eval for %s", pred_file)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    # EVALUATION summary is on evaluate's stdout; reprint it last.
    print(result.stdout or "", flush=True)
    if os.path.isfile(metrics_path):
        summary_path = write_eval_summary(metrics_path, contextbench_dir=contextbench_dir)
        if summary_path:
            print(f"Summary written to {summary_path}", flush=True)
    if result.returncode != 0:
        logger.error("Eval failed with return code %s", result.returncode)
    return result.returncode
