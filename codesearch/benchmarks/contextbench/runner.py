# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ContextBench 全量评测流程（对应旧 build_index.py 的 benchmark 半部 + run_contextbench.py）。

用法：
    python -m benchmarks.contextbench.runner --num-repos 4
"""

import argparse
import asyncio
import logging
import os

from openjiuwen_codesearch import CodeSearchConfig, CodeSearchRetriever

from benchmarks.contextbench.dataset import (
    checkout_instance,
    clean_worktrees,
    collection_id_for,
    load_context_bench_data,
)
from benchmarks.contextbench.exporter import append_prediction, run_eval, write_predictions

logger = logging.getLogger(__name__)


async def run_benchmark(
    config: CodeSearchConfig,
    num_instances: int = 4,
    results_dir: str = "./results",
    reset_indices: bool = False,
) -> str:
    # worktree 根默认收敛到本工作目录：系统 /tmp 是共享命名空间，
    # 同机他人跑 contextbench 时会发生同 commit worktree 冲突（且 sticky bit
    # 导致互相无法清理）。显式设置过 CONTEXTBENCH_TMP_ROOT 则尊重之。
    os.environ.setdefault("CONTEXTBENCH_TMP_ROOT", os.path.join(os.getcwd(), "tmp"))
    clean_worktrees()  # 清理自己根下的残留 worktree（坏残留会让 checkout 永久失败）
    df = load_context_bench_data()
    rows = [df.iloc[i] for i in range(min(num_instances, len(df)))]

    # 按仓库分组：索引→检索→release 逐仓进行，把 Milvus 加载态内存
    # 从"全部仓库索引之和"压到"单个最大仓库"（内存受限服务器的关键约束）。
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(collection_id_for(row), []).append(row)
    logger.info("%d instances across %d repos", len(rows), len(groups))

    preds = []
    failures: list[tuple[str, str]] = []
    done = 0
    partial_path = os.path.join(results_dir, "partial_predictions.jsonl")
    for collection, group_rows in groups.items():
        retriever = CodeSearchRetriever(config=config, collection_name=collection)
        logger.info("=" * 50)
        logger.info("Repo group '%s': %d instances", collection, len(group_rows))

        indexed_rows = []
        for row in group_rows:
            try:
                logger.info("Indexing instance: %s", row["instance_id"])
                repo_dir = checkout_instance(row["repo_url"], row["base_commit"])
                await retriever.index_repository(
                    repo_dir,
                    revision=row["base_commit"],
                    instance_id=row["instance_id"],
                    reset=(reset_indices and row is group_rows[0]),
                )
                indexed_rows.append(row)
            except Exception as e:  # 单实例失败不终止长跑
                logger.error("Index failed for %s: %s", row["instance_id"], e)
                failures.append((row["instance_id"], f"index: {e}"))

        for row in indexed_rows:
            try:
                result = await retriever.search(
                    row["problem_statement"],
                    revision=row["base_commit"],
                    top_k=config.agent.retrieve_topk,
                )
            except Exception as e:
                logger.error("Search failed for %s: %s", row["instance_id"], e)
                failures.append((row["instance_id"], f"search: {e}"))
                continue
            pred_files: set[str] = set()
            pred_spans: dict[str, list[dict]] = {}
            for hit in result.hits:
                pred_files.add(hit.file_path)
                pred_spans.setdefault(hit.file_path, []).append(
                    {"start": hit.start_line, "end": hit.end_line}
                )
            pred = {
                "instance_id": row["instance_id"],
                "traj_data": {"pred_files": list(pred_files), "pred_spans": pred_spans},
            }
            preds.append(pred)
            append_prediction(partial_path, pred)
            done += 1
            logger.info(
                "[%d/%d] %s: %d hits, termination=%s, tokens=%din/%dout",
                done, len(rows), row["instance_id"], len(result.hits),
                result.termination.value,
                result.total_input_tokens, result.total_output_tokens,
            )

    if failures:
        logger.warning("%d instances failed: %s", len(failures), failures)

        # 本仓全部实例完成，卸载 collection 释放查询内存
        store = retriever._store
        if store is not None and hasattr(store, "release"):
            await store.release()
            logger.info("Released collection '%s' from memory.", collection)

    # 3) 写预测（单文件）并调官方评测
    pred_file = write_predictions(
        preds, results_dir, mode="agent", topk=config.agent.retrieve_topk,
        num_instances=len(rows),
    )
    run_eval(pred_file)
    return pred_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ContextBench benchmark.")
    parser.add_argument(
        "--num-instances", "--num-repos", dest="num_instances", type=int, default=4,
        help="取数据集前 N 个实例（行）。--num-repos 为历史别名（旧名有误导：语义一直是实例数而非仓库数）",
    )
    parser.add_argument("--results-dir", default="./results")
    parser.add_argument("--reset-indices", action="store_true")
    parser.add_argument("--milvus-host", default="")
    parser.add_argument("--milvus-port", default="", help="Milvus port (default 19530)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    config = CodeSearchConfig.from_env()
    if args.milvus_host:
        config.milvus.host = args.milvus_host
    if args.milvus_port:
        config.milvus.port = args.milvus_port
    asyncio.run(
        run_benchmark(
            config,
            num_instances=args.num_instances,
            results_dir=args.results_dir,
            reset_indices=args.reset_indices,
        )
    )


if __name__ == "__main__":
    main()
