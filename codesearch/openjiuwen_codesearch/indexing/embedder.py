# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Embedding API 客户端（SQLite 缓存）。

对旧 embed_utils 的修复（notes #10/#11/#12）：
- URL/headers 为实例属性、构造注入（旧类属性在 import 时绑定导致动态配置失效）；
- 重试有上限 + 指数退避（旧 while True 会永久挂起）；
- 删除被 async 版覆盖的死代码同步 `__call__`；
- HTTP 传输可注入（单测无需网络）。
"""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
from typing import Awaitable, Callable, Optional

from openjiuwen_codesearch.common.exception import CodeSearchException
from openjiuwen_codesearch.common.status_code import StatusCode
from openjiuwen_codesearch.config.index import EmbedConfig

logger = logging.getLogger(__name__)

# transport(payload) -> (status_code, parsed_json_or_text)
Transport = Callable[[dict], Awaitable[tuple[int, object]]]


class APIEmbedModel:
    def __init__(
        self,
        config: EmbedConfig,
        max_chars: int = 65535,
        transport: Optional[Transport] = None,
    ) -> None:
        self._config = config
        self._max_chars = max_chars
        self._transport = transport or self._aiohttp_transport
        safe_model_name = config.model.replace("/", "_")
        self._cache_db_path = os.path.join(config.cache_dir, f"_emb_{safe_model_name}.db")
        with sqlite3.connect(self._cache_db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache (text_hash TEXT PRIMARY KEY, embedding TEXT)"
            )

    # --- cache ---
    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def _fetch_cached(self, chunks: list[str]) -> tuple[list, list[int], list[str]]:
        embeddings: list = [None] * len(chunks)
        missing_indices: list[int] = []
        missing_chunks: list[str] = []
        with sqlite3.connect(self._cache_db_path) as conn:
            cursor = conn.cursor()
            for i, chunk in enumerate(chunks):
                cursor.execute(
                    "SELECT embedding FROM cache WHERE text_hash = ?", (self._text_hash(chunk),)
                )
                row = cursor.fetchone()
                if row:
                    embeddings[i] = json.loads(row[0])
                else:
                    missing_indices.append(i)
                    missing_chunks.append(chunk)
        return embeddings, missing_indices, missing_chunks

    def _save_cached(self, chunks: list[str], indices: list[int], vectors: list) -> None:
        with sqlite3.connect(self._cache_db_path) as conn:
            for idx, emb in zip(indices, vectors):
                conn.execute(
                    "INSERT OR IGNORE INTO cache (text_hash, embedding) VALUES (?, ?)",
                    (self._text_hash(chunks[idx]), json.dumps(emb)),
                )

    # --- request ---
    def _prepare_input(self, chunks: list[str], is_query: bool) -> list[str]:
        if is_query:
            chunks = [self._config.query_prefix + c for c in chunks]
        return [c[: self._max_chars] for c in chunks]

    async def _aiohttp_transport(self, payload: dict) -> tuple[int, object]:
        import aiohttp  # noqa: PLC0415  guarded import：核心安装不强制 aiohttp

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self._config.url, headers=headers, json=payload) as resp:
                status = resp.status
                try:
                    body = await resp.json()
                except Exception:  # noqa: BLE001
                    body = await resp.text()
                return status, body

    async def async_encode(
        self, chunks: list[str], is_query: bool = False
    ) -> list[list[float]]:
        embeddings, missing_indices, missing_chunks = self._fetch_cached(chunks)
        if not missing_chunks:
            return embeddings

        payload = {
            "model": self._config.model,
            "input": self._prepare_input(missing_chunks, is_query),
            "encoding_format": "float",
        }

        last_error: object = None
        for attempt in range(self._config.max_retries):
            try:
                status, body = await self._transport(payload)
            except Exception as e:  # noqa: BLE001  网络异常与非 200 同路径重试
                status, body = -1, str(e)
            if status == 200 and isinstance(body, dict) and "data" in body:
                vectors = [item["embedding"] for item in body["data"]]
                if len(vectors) == len(missing_chunks):
                    self._save_cached(chunks, missing_indices, vectors)
                    for idx, emb in zip(missing_indices, vectors):
                        embeddings[idx] = emb
                    return embeddings
                last_error = f"vector count mismatch: {len(vectors)} != {len(missing_chunks)}"
            else:
                last_error = f"status={status} body={str(body)[:500]}"
            backoff = self._config.retry_backoff_seconds * (2**attempt)
            logger.warning("Embedding attempt %d failed (%s); retrying in %.1fs",
                           attempt + 1, last_error, backoff)
            await asyncio.sleep(backoff)

        raise CodeSearchException(
            StatusCode.EMBEDDING_ERROR, retries=self._config.max_retries, e=last_error
        )
