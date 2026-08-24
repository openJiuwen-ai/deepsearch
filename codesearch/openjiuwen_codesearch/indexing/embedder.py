# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Embedding 客户端 —— 核心实现由 openjiuwen-search-base 提供。

本壳负责：EmbedConfig → base EmbedderSettings 的适配，以及把 base 的重试耗尽
异常包装为 codesearch 错误码体系。原 import 路径与构造签名保持不变。
"""

from typing import Optional

from openjiuwen_search_base.embedding import ApiEmbedder, EmbedderSettings, EmbeddingRetryError
from openjiuwen_search_base.embedding.api_embedder import Transport

from openjiuwen_codesearch.common.exception import CodeSearchException
from openjiuwen_codesearch.common.status_code import StatusCode
from openjiuwen_codesearch.config.index import EmbedConfig


class APIEmbedModel(ApiEmbedder):
    def __init__(
        self,
        config: EmbedConfig,
        max_chars: int = 65535,
        transport: Optional[Transport] = None,
    ) -> None:
        super().__init__(
            EmbedderSettings(
                url=config.url,
                model=config.model,
                api_key=config.api_key,
                query_prefix=config.query_prefix,
                cache_dir=config.cache_dir,
                max_retries=config.max_retries,
                retry_backoff_seconds=config.retry_backoff_seconds,
                max_chars=max_chars,
                timeout_seconds=config.timeout_seconds,
            ),
            transport=transport,
        )

    async def async_encode(self, chunks: list[str], is_query: bool = False) -> list[list[float]]:
        try:
            return await super().async_encode(chunks, is_query=is_query)
        except EmbeddingRetryError as e:
            raise CodeSearchException(
                StatusCode.EMBEDDING_ERROR, retries=e.retries, e=e.last_error
            ) from e
