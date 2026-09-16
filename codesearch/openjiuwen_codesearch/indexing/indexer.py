# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""索引构建：文件发现 → 哈希查重 → 切块 → 记录构造 →（可选）嵌入 → 入库。

纯逻辑（reconcile_existing / build_chunk_records）与存储 I/O 分离；
存储以 `ChunkStore` 协议注入，单测用 fake，生产用 MilvusStore。
"""

import logging
import os
from typing import Any, Optional, Protocol

from openjiuwen_codesearch.api.models import IndexReport
from openjiuwen_codesearch.config.index import IndexConfig
from openjiuwen_codesearch.indexing.chunkers.base import Chunk, Chunker
from openjiuwen_codesearch.indexing.embedder import APIEmbedModel
from openjiuwen_codesearch.indexing.hashing import deterministic_chunk_id, file_content_hash
from openjiuwen_codesearch.retrieval.tokenizer import generate_char_trigrams, tokenise_code_string

logger = logging.getLogger(__name__)

TRUNCATION_MARK = "\n... [TRUNCATED DUE TO SIZE] ..."
MAX_CALL_LENGTH = 2048  # calls 数组单条上限（schema max_length 对齐）


class ChunkStore(Protocol):
    """索引写入侧的最小存储协议。"""

    async def fetch_records_by_hashes(self, file_hashes: list[str]) -> list[dict]:
        ...

    async def upsert_records(self, records: list[dict]) -> None:
        ...

    async def insert_records(self, records: list[dict]) -> None:
        ...

    async def flush(self) -> None:
        ...


def discover_python_files(
    repo_dir: str,
    max_files: Optional[int] = None,
    max_file_size_bytes: int = 5 * 1024 * 1024,
) -> list[str]:
    """遍历仓库收集 .py 文件。

    安全防护：不跟随目录符号链接（防符号链接环导致无限遍历、防越界索引仓库外
    内容）；跳过隐藏目录（.venv/.git 等）；超大文件（默认 >5MB，多为生成物）
    跳过并告警。
    """
    files: list[str] = []
    for root, dirs, names in os.walk(repo_dir, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(names):
            if not name.endswith(".py") or name.startswith("."):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.islink(path):
                    continue  # 文件级符号链接同样不跟随
                if os.stat(path).st_size > max_file_size_bytes:
                    logger.warning("Skipping oversized file (>%dB): %s",
                                   max_file_size_bytes, path)
                    continue
            except OSError:
                continue
            files.append(path)
    files.sort()
    if max_files is not None:
        files = files[:max_files]
    return files


def hash_files(code_files: list[str], repo_dir: str) -> dict[str, str]:
    """file_hash -> 绝对路径。读失败的文件跳过并告警。"""
    result: dict[str, str] = {}
    for file_path in code_files:
        rel_path = os.path.relpath(file_path, repo_dir)
        try:
            with open(file_path, "rb") as f:
                content = f.read()
        except OSError as e:
            logger.warning("Failed to read/hash %s: %s", file_path, e)
            continue
        result[file_content_hash(rel_path, content)] = file_path
    return result


def reconcile_existing(
    existing_records: list[dict],
    all_hashes: list[str],
    revision: str,
    instance_id: str,
) -> tuple[list[dict], list[str]]:
    """按**文件内容哈希**决定复用已有 chunk 还是重新切块/嵌入。

    调用方先用 ``fetch_records_by_hashes(all_hashes)`` 从 collection 拉回
    ``existing_records``（命中条件：记录的 ``file_hash`` 落在本次仓库文件哈希集合里）。
    粒度是**整文件**内容哈希，不是单条 snippet id。

    对每个 ``file_hash``：

    * **哈希已在 collection 中**：该文件内容未变，对应 chunk 记录可复用。
      若记录的 ``commits`` 尚无本次 ``revision``，则追加；``instance_ids``
      同理追加 ``instance_id``。有变更的记录进入 ``records_to_upsert``，
      **不**进入 ``hashes_to_embed``（不再切块/嵌入）。
    * **哈希不在 collection 中**：视为新文件（或内容已改导致哈希变了），
      将该 hash 放入 ``hashes_to_embed``，后续切块并构造新记录插入。

    返回 ``(records_to_upsert, hashes_to_embed)``。
    """
    by_hash: dict[str, list[dict]] = {}
    for record in existing_records:
        by_hash.setdefault(record.get("file_hash"), []).append(record)

    records_to_upsert: list[dict] = []
    hashes_to_embed: list[str] = []
    for h in all_hashes:
        if h in by_hash:
            for record in by_hash[h]:
                updated = False
                commits = record.setdefault("commits", [])
                instances = record.setdefault("instance_ids", [])
                if revision not in commits:
                    commits.append(revision)
                    updated = True
                if instance_id not in instances:
                    instances.append(instance_id)
                    updated = True
                if updated:
                    records_to_upsert.append(record)
        else:
            hashes_to_embed.append(h)
    return records_to_upsert, hashes_to_embed


def build_chunk_records(
    chunks: list[Chunk],
    rel_path: str,
    file_hash: str,
    index_cfg: IndexConfig,
    **meta,
) -> list[dict[str, Any]]:
    """chunk → Milvus 插入记录。含路径头注入、字节级截断、确定性 ID。纯函数。"""
    instance_id = meta["instance_id"]
    repo_name = meta["repo_name"]
    revision = meta["revision"]
    records = []
    for chunk in chunks:
        text = f"File: {rel_path} (L{chunk.start_line}-L{chunk.end_line})\n\n" + chunk.text
        text_bytes = text.encode("utf-8")
        if len(text_bytes) > index_cfg.max_char_limit:
            text = (
                text_bytes[: index_cfg.max_char_limit - 64].decode("utf-8", errors="ignore")
                + TRUNCATION_MARK
            )
        calls = [c[:MAX_CALL_LENGTH] for c in chunk.calls[: index_cfg.max_num_calls]]
        records.append(
            {
                "id": deterministic_chunk_id(
                    file_hash, chunk.start_line, chunk.end_line, chunk.name
                ),
                "file_hash": file_hash,
                "instance_ids": [instance_id],
                "repo": repo_name,
                "commits": [revision],
                "file_path": rel_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "kind": chunk.kind,
                "name": tokenise_code_string(chunk.name),
                "original_name": chunk.name,
                "text": text,
                "text_trigram": (
                    generate_char_trigrams(text, index_cfg.max_char_limit)
                    if index_cfg.enable_trigram
                    else ""
                ),
                "calls": calls,
            }
        )
    return records


async def index_repository(
    store: ChunkStore,
    chunker: Chunker,
    repo_dir: str,
    index_cfg: IndexConfig,
    **ctx,
) -> IndexReport:
    """索引一个仓库目录。已存在文件 upsert 标记。"""
    instance_id = ctx["instance_id"]
    repo_name = ctx["repo_name"]
    revision = ctx["revision"]
    embedder = ctx.get("embedder")
    embed_batch_size = ctx.get("embed_batch_size", 64)
    code_files = discover_python_files(
        repo_dir,
        max_files=index_cfg.max_num_files_per_repo,
        max_file_size_bytes=index_cfg.max_file_size_bytes,
    )
    logger.info("Found %d python files to process.", len(code_files))

    hash2path = hash_files(code_files, repo_dir)
    all_hashes = list(hash2path.keys())
    existing = await store.fetch_records_by_hashes(all_hashes)
    to_upsert, hashes_to_embed = reconcile_existing(existing, all_hashes, revision, instance_id)

    if to_upsert:
        logger.info("Upserting %d existing chunks with new revision/instance tags...", len(to_upsert))
        await store.upsert_records(to_upsert)

    new_records: list[dict] = []
    for file_hash in hashes_to_embed:
        file_path = hash2path[file_hash]
        rel_path = os.path.relpath(file_path, repo_dir)
        chunks = chunker.chunk_file(file_path)
        new_records.extend(
            build_chunk_records(
                chunks,
                rel_path,
                file_hash,
                index_cfg,
                instance_id=instance_id,
                repo_name=repo_name,
                revision=revision,
            )
        )

    if index_cfg.use_dense_embeddings and new_records:
        if embedder is None:
            raise ValueError("use_dense_embeddings=True but no embedder provided")
        for start in range(0, len(new_records), embed_batch_size):
            batch = new_records[start:start + embed_batch_size]
            vectors = await embedder.async_encode([r["text"].strip() for r in batch])
            for record, vec in zip(batch, vectors):
                record["dense_vector"] = vec

    if new_records:
        logger.info("Inserting %d new chunk records...", len(new_records))
        await store.insert_records(new_records)
    await store.flush()

    reused = len(all_hashes) - len(hashes_to_embed)
    logger.info(
        "Indexing complete: %d files (%d reused, %d new), %d chunks inserted.",
        len(all_hashes), reused, len(hashes_to_embed), len(new_records),
    )
    return IndexReport(
        files_total=len(all_hashes),
        files_new=len(hashes_to_embed),
        files_reused=reused,
        chunks_inserted=len(new_records),
    )
