# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""索引构建：文件发现 → 哈希查重 → 切块 → 记录构造 →（可选）嵌入 → 入库。

纯逻辑（reconcile_existing / build_chunk_records）与存储 I/O 分离；
存储以 `ChunkStore` 协议注入，单测用 fake，生产用 MilvusStore。
"""

import glob
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

    async def fetch_records_by_hashes(self, file_hashes: list[str]) -> list[dict]: ...

    async def upsert_records(self, records: list[dict]) -> None: ...

    async def insert_records(self, records: list[dict]) -> None: ...

    async def flush(self) -> None: ...


def discover_python_files(repo_dir: str, max_files: Optional[int] = None) -> list[str]:
    files = glob.glob(os.path.join(repo_dir, "**/*.py"), recursive=True)
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
    """已入库文件追加 revision/instance 标记；未入库文件进入待嵌入列表。

    返回 (records_to_upsert, hashes_to_embed)。纯函数，行为与旧
    `find_files_in_collection` 的核心循环一致。
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
    instance_id: str,
    repo_name: str,
    revision: str,
    index_cfg: IndexConfig,
) -> list[dict[str, Any]]:
    """chunk → Milvus 插入记录。含路径头注入、字节级截断、确定性 ID。纯函数。"""
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
    instance_id: str,
    repo_name: str,
    revision: str,
    index_cfg: IndexConfig,
    embedder: Optional[APIEmbedModel] = None,
    embed_batch_size: int = 64,
) -> IndexReport:
    """索引一个仓库目录。已存在文件 upsert 标记（修复旧 wrapper 丢 upsert 的 bug #13）。"""
    code_files = discover_python_files(repo_dir, index_cfg.max_num_files_per_repo)
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
                chunks, rel_path, file_hash, instance_id, repo_name, revision, index_cfg
            )
        )

    if index_cfg.use_dense_embeddings and new_records:
        if embedder is None:
            raise ValueError("use_dense_embeddings=True but no embedder provided")
        for start in range(0, len(new_records), embed_batch_size):
            batch = new_records[start : start + embed_batch_size]
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
