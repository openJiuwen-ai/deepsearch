# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OpenAI 兼容 embedding 客户端：SQLite 缓存 + 有限重试指数退避 + 可注入传输层。"""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
from typing import Awaitable, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openjiuwen_search_base.security import reveal_secret

logger = logging.getLogger(__name__)

# transport(payload) -> (status_code, parsed_json_or_text)
Transport = Callable[[dict], Awaitable[tuple[int, object]]]


class EmbeddingRetryError(Exception):
    """重试耗尽。携带 retries 与最后错误信息，消费方可包装为自己的异常体系。"""

    def __init__(self, retries: int, last_error: object) -> None:
        self.retries = retries
        self.last_error = last_error
        super().__init__(f"Embedding failed after {retries} retries: {last_error}")


class EmbedderSettings(BaseModel):
    """密钥以 bytearray 存储，构造时可传字符串（自动规范化）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str = "https://openrouter.ai/api/v1/embeddings"
    model: str = ""
    api_key: bytearray = Field(default_factory=bytearray)
    query_prefix: str = ""
    cache_dir: str = "."
    max_retries: int = 8
    retry_backoff_seconds: float = 2.0
    max_chars: int = 65535
    # 单次请求总超时（防服务端 accept 后挂住导致重试永不触发）
    timeout_seconds: float = 60.0

    @field_validator("url")
    @classmethod
    def _http_scheme_only(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"embedding url must be http(s), got: {v[:32]}")
        return v


class ApiEmbedder:
    def __init__(self, settings: EmbedderSettings, transport: Optional[Transport] = None) -> None:
        self._settings = settings
        self._transport = transport or self._aiohttp_transport
        self._session = None  # 惰性创建、跨请求复用的 aiohttp session（连接池/免重复握手）
        os.makedirs(settings.cache_dir, exist_ok=True)
        safe_model_name = settings.model.replace("/", "_")
        self._cache_db_path = os.path.join(settings.cache_dir, f"_emb_{safe_model_name}.db")
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache (text_hash TEXT PRIMARY KEY, embedding TEXT)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._cache_db_path)
        # WAL + busy_timeout：多任务并发索引时避免 "database is locked"
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    async def close(self) -> None:
        """释放持久连接（服务化/长驻进程的生命周期收口）。"""
        if self._session is not None:
            await self._session.close()
            self._session = None

    # --- cache ---
    # 缓存键 = **实际发送文本**（含 query 前缀与截断）的 sha256：
    # 同一文本作为文档与作为查询（instruct 前缀不同）产出不同向量，必须分键缓存。
    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _fetch_cached(self, chunks: list[str]) -> tuple[list, list[int], list[str]]:
        embeddings: list = [None] * len(chunks)
        missing_indices: list[int] = []
        missing_chunks: list[str] = []
        with self._connect() as conn:
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
        with self._connect() as conn:
            for idx, emb in zip(indices, vectors):
                conn.execute(
                    "INSERT OR IGNORE INTO cache (text_hash, embedding) VALUES (?, ?)",
                    (self._text_hash(chunks[idx]), json.dumps(emb)),
                )

    # --- request ---
    def _prepare_input(self, chunks: list[str], is_query: bool) -> list[str]:
        if is_query and self._settings.query_prefix:
            chunks = [self._settings.query_prefix + c for c in chunks]
        return [c[: self._settings.max_chars] for c in chunks]

    async def _aiohttp_transport(self, payload: dict) -> tuple[int, object]:
        import aiohttp  # guarded import

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._settings.timeout_seconds)
            )
        headers = {
            "Authorization": f"Bearer {reveal_secret(self._settings.api_key)}",
            "Content-Type": "application/json",
        }
        async with self._session.post(self._settings.url, headers=headers, json=payload) as resp:
            status = resp.status
            try:
                body = await resp.json()
            except Exception:
                body = await resp.text()
            return status, body

    async def async_encode(self, chunks: list[str], is_query: bool = False) -> list[list[float]]:
        # 先构造实际发送文本（前缀+截断），缓存查/存一律以它为键；
        # sqlite 读写走线程，不占事件循环
        prepared = self._prepare_input(chunks, is_query)
        embeddings, missing_indices, missing_prepared = await asyncio.to_thread(
            self._fetch_cached, prepared
        )
        if not missing_prepared:
            return embeddings

        payload = {
            "model": self._settings.model,
            "input": missing_prepared,
            "encoding_format": "float",
        }

        last_error: object = None
        for attempt in range(self._settings.max_retries):
            try:
                status, body = await self._transport(payload)
            except Exception as e:  # 网络异常与非 200 同路径重试
                status, body = -1, str(e)
            if status == 200 and isinstance(body, dict) and "data" in body:
                vectors = [item["embedding"] for item in body["data"]]
                if len(vectors) == len(missing_prepared):
                    await asyncio.to_thread(self._save_cached, prepared, missing_indices, vectors)
                    for idx, emb in zip(missing_indices, vectors):
                        embeddings[idx] = emb
                    return embeddings
                last_error = f"vector count mismatch: {len(vectors)} != {len(missing_prepared)}"
            else:
                last_error = f"status={status} body={str(body)[:500]}"
            if attempt + 1 >= self._settings.max_retries:
                break  # 最后一轮失败直接抛，不再空等退避
            backoff = self._settings.retry_backoff_seconds * (2**attempt)
            logger.warning(
                "Embedding attempt %d failed (%s); retrying in %.1fs",
                attempt + 1, last_error, backoff,
            )
            await asyncio.sleep(backoff)

        raise EmbeddingRetryError(self._settings.max_retries, last_error)
