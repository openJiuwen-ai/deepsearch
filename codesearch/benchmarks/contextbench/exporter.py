# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""预测文件生成与官方评测调用。

修复旧实现 bug（notes #14）：时间戳与输出文件在**全部实例完成后**生成一次，
不再每实例写一个累积文件。
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


def run_eval(
    pred_file: str,
    gold_path: str = DEFAULT_PARQUET,
    contextbench_dir: str = DEFAULT_CONTEXTBENCH_DIR,
) -> int:
    cmd = [
        sys.executable, "-m", "contextbench.evaluate",
        "--gold", gold_path,
        "--pred", pred_file,
        "--out", f"{pred_file}_metrics.jsonl",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = contextbench_dir + os.pathsep + env.get("PYTHONPATH", "")
    logger.info("Starting eval for %s", pred_file)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    logger.info("Return code: %s\nSTDOUT: %s\nSTDERR: %s",
                result.returncode, result.stdout, result.stderr)
    return result.returncode
