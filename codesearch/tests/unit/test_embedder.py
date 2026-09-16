# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import pytest

from openjiuwen_codesearch.common.exception import CodeSearchException
from openjiuwen_codesearch.config.index import EmbedConfig
from openjiuwen_codesearch.indexing.embedder import APIEmbedModel

from tests.conftest import run


def _config(tmp_path, retries=2):
    return EmbedConfig(
        cache_dir=str(tmp_path), max_retries=retries, retry_backoff_seconds=0.0
    )


class CountingTransport:
    def __init__(self, dim=3, status=200):
        self.count = 0
        self.dim = dim
        self.status = status
        self.last_payload = None

    async def __call__(self, payload):
        self.count += 1
        self.last_payload = payload
        if self.status != 200:
            return self.status, "server error"
        vectors = [{"embedding": [0.1] * self.dim} for _ in payload["input"]]
        return 200, {"data": vectors}


def test_encode_and_sqlite_cache_hit(tmp_path):
    transport = CountingTransport()
    model = APIEmbedModel(_config(tmp_path), transport=transport)

    first = run(model.async_encode(["hello", "world"]))
    assert len(first) == 2 and len(first[0]) == 3
    assert transport.count == 1

    # 新实例（同缓存目录）：全部命中缓存，不再发请求
    transport2 = CountingTransport()
    model2 = APIEmbedModel(_config(tmp_path), transport=transport2)
    second = run(model2.async_encode(["hello", "world"]))
    assert second == first
    assert transport2.count == 0


def test_partial_cache_only_fetches_missing(tmp_path):
    transport = CountingTransport()
    model = APIEmbedModel(_config(tmp_path), transport=transport)
    run(model.async_encode(["a"]))
    run(model.async_encode(["a", "b"]))
    assert transport.last_payload["input"] == ["b"]


def test_query_prefix_applied(tmp_path):
    transport = CountingTransport()
    model = APIEmbedModel(_config(tmp_path), transport=transport)
    run(model.async_encode(["find this"], is_query=True))
    assert transport.last_payload["input"][0].startswith("Instruct:")


def test_doc_and_query_vectors_cached_separately(tmp_path):
    """回归：同文本作为文档与查询须分别请求/缓存，查询不得命中文档向量。"""
    transport = CountingTransport()
    model = APIEmbedModel(_config(tmp_path), transport=transport)
    run(model.async_encode(["same text"], is_query=False))
    assert transport.count == 1
    run(model.async_encode(["same text"], is_query=True))
    assert transport.count == 2, "查询不应命中文档向量的缓存"
    # 各自二次调用均命中各自缓存
    run(model.async_encode(["same text"], is_query=False))
    run(model.async_encode(["same text"], is_query=True))
    assert transport.count == 2


def test_retry_exhaustion_raises(tmp_path):
    transport = CountingTransport(status=500)
    model = APIEmbedModel(_config(tmp_path, retries=3), transport=transport)
    with pytest.raises(CodeSearchException):
        run(model.async_encode(["x"]))
    assert transport.count == 3  # 有限重试（旧实现 while True 会挂死）
