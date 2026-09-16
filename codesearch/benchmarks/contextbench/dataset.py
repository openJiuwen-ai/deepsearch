# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ContextBench 数据集与 checkout 适配。

contextbench 不是 pip 包，也不是 git submodule。按需 clone 到
``third_party/contextbench``（或设 ``CONTEXTBENCH_DIR``）后，其 import 路径
注入收敛在本模块的 ``ensure_contextbench_importable``——benchmark 层唯一允许
的 path 注入点；核心 SDK（openjiuwen_codesearch）不依赖本模块。
"""

import logging
import os
import shutil
import sys
import tempfile

logger = logging.getLogger(__name__)

_BUILTIN_CONTEXTBENCH_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "third_party",
    "contextbench",
)

DEFAULT_CONTEXTBENCH_DIR = _BUILTIN_CONTEXTBENCH_DIR
DEFAULT_PARQUET = os.path.join(DEFAULT_CONTEXTBENCH_DIR, "data", "contextbench_verified.parquet")

_MISSING_HINT = (
    "ContextBench is optional and is not fetched by clone/CI. "
    "From codesearch/: bash scripts/fetch_contextbench.sh "
    "(or git clone <url> third_party/contextbench), "
    "or set CONTEXTBENCH_DIR to an existing checkout. "
    "See third_party/README.md."
)


def resolve_contextbench_dir(explicit: str | None = None) -> str:
    """Resolve the ContextBench checkout: explicit path, then CONTEXTBENCH_DIR, then builtin."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    env = os.environ.get("CONTEXTBENCH_DIR", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return _BUILTIN_CONTEXTBENCH_DIR


def resolve_parquet_path(
    explicit: str | None = None,
    contextbench_dir: str | None = None,
) -> str:
    """Resolve the gold parquet: explicit path, then CONTEXTBENCH_PARQUET, then ``<dir>/data/…``."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    env = os.environ.get("CONTEXTBENCH_PARQUET", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(
        resolve_contextbench_dir(contextbench_dir),
        "data",
        "contextbench_verified.parquet",
    )


def ensure_contextbench_importable(contextbench_dir: str | None = None) -> str:
    path = resolve_contextbench_dir(contextbench_dir)
    marker = os.path.join(path, "contextbench", "__init__.py")
    if not os.path.isfile(marker):
        raise FileNotFoundError(
            f"ContextBench directory not found: {path}. {_MISSING_HINT}"
        )
    if path not in sys.path:
        sys.path.append(path)
    return path


def load_context_bench_data(path: str | None = None):
    import pandas as pd

    resolved = resolve_parquet_path(path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(
            f"Parquet file not found: {resolved}. "
            "Place the dataset under <contextbench>/data/ or set CONTEXTBENCH_PARQUET. "
            f"{_MISSING_HINT}"
        )
    df = pd.read_parquet(resolved)
    logger.info("Loaded %d ContextBench instances. Columns: %s", len(df), df.columns.tolist())
    return df


def collection_id_for(row) -> str:
    """按仓库名生成 collection 基名（同仓多 commit 共享索引）。"""
    return str(row["repo"]).replace("/", "_").replace("-", "_").replace(".", "_")


def _worktree_root() -> str:
    tmp_root = os.environ.get("CONTEXTBENCH_TMP_ROOT") or tempfile.gettempdir()
    return os.path.join(tmp_root, "contextbench_worktrees")


def clear_repo_cache(repo_url: str, cache_dir: str = "./repos") -> None:
    """清理远程仓库 clone 到本地的 bare cache 目录。"""
    ensure_contextbench_importable()
    from contextbench.core.repo import _normalize_url

    normalized_url = _normalize_url(repo_url)
    repo_cache_path = os.path.join(cache_dir, normalized_url)
    logger.info("Cleaning up base repo cache for %s before starting run...", repo_url)
    shutil.rmtree(repo_cache_path, ignore_errors=True)


def clean_worktrees() -> None:
    """清理 contextbench 临时 worktree（残留坏 worktree 会使同 commit checkout 失败）。"""
    shutil.rmtree(_worktree_root(), ignore_errors=True)


def checkout_instance(repo_url: str, commit: str, cache_dir: str = "./repos") -> str:
    ensure_contextbench_importable()
    from contextbench.core import checkout
    from contextbench.core.repo import _normalize_url

    normalized_url = _normalize_url(repo_url)
    repo_cache_path = os.path.join(cache_dir, normalized_url)
    worktree_path = os.path.join(_worktree_root(), normalized_url, commit)

    logger.info("Cleaning up worktree for %s to ensure a fresh clone...", repo_url)
    shutil.rmtree(worktree_path, ignore_errors=True)

    repo_dir = checkout(repo_url, commit, cache_dir=cache_dir, verbose=False)
    if not repo_dir or not os.path.exists(repo_dir):
        raise RuntimeError(f"Checkout failed for {repo_url}@{commit}")
    return repo_dir
