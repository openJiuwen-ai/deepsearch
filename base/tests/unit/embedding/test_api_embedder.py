"""Tests for :class:`ApiEmbedder` cache lifecycle and vector ordering."""

import asyncio
import sqlite3

from openjiuwen_search_base.embedding.api_embedder import ApiEmbedder, EmbedderSettings


def _settings(cache_dir) -> EmbedderSettings:
    return EmbedderSettings(
        url="https://example.com/v1/embeddings",
        model="test-model",
        api_key=bytearray(),
        cache_dir=str(cache_dir),
    )


class _ConnectionProxy:
    """Records ``close()`` calls made through the embedder's connections."""

    def __init__(self, conn: sqlite3.Connection, closed: list) -> None:
        self._conn = conn
        self._closed = closed

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._closed.append(True)
        self._conn.close()


def test_cache_connections_are_closed(tmp_path, monkeypatch):
    """``with conn`` 只提交不关闭；每次缓存读写都必须真正 close。"""
    closed: list = []
    real_connect = ApiEmbedder._connect

    def _tracked_connect(self):
        return _ConnectionProxy(real_connect(self), closed)

    monkeypatch.setattr(ApiEmbedder, "_connect", _tracked_connect)

    embedder = ApiEmbedder(_settings(tmp_path))
    embedder._fetch_cached(["a", "b"])
    embedder._save_cached(["a"], [0], [[1.0]])

    # __init__ 建表 1 次 + _fetch_cached 1 次 + _save_cached 1 次
    assert len(closed) == 3


def test_out_of_order_response_is_reordered_by_index(tmp_path):
    """响应乱序返回时，向量必须按 index 对齐到请求文本。"""

    async def transport(payload):
        return 200, {
            "data": [
                {"index": 2, "embedding": [3.0]},
                {"index": 0, "embedding": [1.0]},
                {"index": 1, "embedding": [2.0]},
            ]
        }

    embedder = ApiEmbedder(_settings(tmp_path), transport=transport)
    vectors = asyncio.run(embedder.async_encode(["a", "b", "c"]))

    assert vectors == [[1.0], [2.0], [3.0]]
    # 缓存里也必须按原文本存对，否则二次调用会读到错配向量
    cached, missing, _ = embedder._fetch_cached(["a", "b", "c"])
    assert cached == [[1.0], [2.0], [3.0]]
    assert missing == []


def test_missing_index_keeps_response_order(tmp_path):
    """没有 index 字段时保持原有数组顺序，行为与旧实现一致。"""

    async def transport(payload):
        return 200, {"data": [{"embedding": [1.0]}, {"embedding": [2.0]}]}

    embedder = ApiEmbedder(_settings(tmp_path), transport=transport)

    assert asyncio.run(embedder.async_encode(["a", "b"])) == [[1.0], [2.0]]


def test_ordered_embeddings_handles_non_int_index():
    items = [{"index": "x", "embedding": [1.0]}, {"index": "y", "embedding": [2.0]}]
    assert ApiEmbedder._ordered_embeddings(items) == [[1.0], [2.0]]
