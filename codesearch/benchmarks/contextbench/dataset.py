# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ContextBench 数据集与 checkout 适配。

contextbench 不是 pip 包（git submodule），其 import 路径注入收敛在本模块的
`ensure_contextbench_importable`——benchmark 层唯一允许的 path 注入点；
核心 SDK（openjiuwen_codesearch）不依赖本模块。
"""

import logging
import os
import shutil
import sys
import tempfile

logger = logging.getLogger(__name__)

DEFAULT_CONTEXTBENCH_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "third_party",
    "contextbench",
)
DEFAULT_PARQUET = os.path.join(DEFAULT_CONTEXTBENCH_DIR, "data", "contextbench_verified.parquet")


def ensure_contextbench_importable(contextbench_dir: str = DEFAULT_CONTEXTBENCH_DIR) -> None:
    if not os.path.isdir(contextbench_dir):
        raise FileNotFoundError(
            f"ContextBench directory not found: {contextbench_dir}. "
            "Run `git submodule update --init --recursive` first."
        )
    if contextbench_dir not in sys.path:
        sys.path.insert(0, contextbench_dir)


def load_context_bench_data(path: str = DEFAULT_PARQUET):
    import pandas as pd

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Parquet file not found: {path}. Download the dataset into "
            "third_party/contextbench/data/ first."
        )
    df = pd.read_parquet(path)
    logger.info("Loaded %d ContextBench instances. Columns: %s", len(df), df.columns.tolist())
    return df


def collection_id_for(row) -> str:
    """按仓库名生成 collection 基名（同仓多 commit 共享索引）。"""
    return str(row["repo"]).replace("/", "_").replace("-", "_").replace(".", "_")


def _worktree_root() -> str:
    tmp_root = os.environ.get("CONTEXTBENCH_TMP_ROOT") or tempfile.gettempdir()
    return os.path.join(tmp_root, "contextbench_worktrees")


def clean_worktrees() -> None:
    """清理 contextbench 的临时 worktree（长跑前置清理；
    残留的坏 worktree 会让同 commit 的 checkout 永久失败）。"""
    shutil.rmtree(_worktree_root(), ignore_errors=True)


def checkout_instance(repo_url: str, commit: str, cache_dir: str = "./repos") -> str:
    ensure_contextbench_importable()
    from contextbench.core import checkout
    from contextbench.core.repo import _normalize_url

    repo_dir = checkout(repo_url, commit, cache_dir=cache_dir, verbose=False)
    if not repo_dir:
        # 疑似残留坏 worktree：清掉该 commit 的目录后重试一次
        stale = os.path.join(_worktree_root(), _normalize_url(repo_url), commit)
        logger.warning("Checkout failed; removing stale worktree %s and retrying...", stale)
        shutil.rmtree(stale, ignore_errors=True)
        repo_dir = checkout(repo_url, commit, cache_dir=cache_dir, verbose=False)
    if not repo_dir or not os.path.exists(repo_dir):
        raise RuntimeError(f"Checkout failed for {repo_url}@{commit}")
    return repo_dir
