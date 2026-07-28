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
    model2 = APIEmbedModel(_config(tmp_path), transport=CountingTransport())
    second = run(model2.async_encode(["hello", "world"]))
    assert second == first
    assert model2._transport.count == 0  # type: ignore[attr-defined]


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


def test_retry_exhaustion_raises(tmp_path):
    transport = CountingTransport(status=500)
    model = APIEmbedModel(_config(tmp_path, retries=3), transport=transport)
    with pytest.raises(CodeSearchException):
        run(model.async_encode(["x"]))
    assert transport.count == 3  # 有限重试（旧实现 while True 会挂死）
