# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""公共 SDK 门面。

配置一律经 `CodeSearchConfig` 传入——禁止旧 wrapper 的"改写全局 settings"注入方式。
重依赖（pymilvus / openjiuwen / aiohttp）延迟到实际使用时 import；
测试可经构造参数注入 fake retriever / fake llm。
"""

import logging
from typing import Optional

from openjiuwen_codesearch.api.models import IndexReport
from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.domain.result import CodeSearchResult
from openjiuwen_codesearch.framework.openjiuwen.agent import CodeSearchAgent
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import build_run_context
from openjiuwen_codesearch.llm.factory import LLMClient
from openjiuwen_codesearch.retrieval.base import CodeRetriever

logger = logging.getLogger(__name__)


class CodeSearchRetriever:
    """两步用法：`await index_repository(...)` → `await search(...)`。"""

    def __init__(
        self,
        config: Optional[CodeSearchConfig] = None,
        collection_name: str = "local_repo",
        *,
        retriever: Optional[CodeRetriever] = None,
        main_llm: Optional[LLMClient] = None,
        filter_llm: Optional[LLMClient] = None,
    ) -> None:
        import asyncio  # noqa: PLC0415

        self.config = config or CodeSearchConfig.from_env()
        self.collection_name = collection_name
        self._store = retriever
        self._main_llm = main_llm
        self._filter_llm = filter_llm
        # 防并发构造竞态：两个并发请求同时看到 _store is None 会重复建连，
        # 且 reset 与并发 search 交错时可能 drop 对方刚 load 的 collection
        self._store_lock = asyncio.Lock()

    # ---------- lazy wiring ----------
    def _ensure_store(self, reset: bool = False):
        if self._store is None:
            from openjiuwen_codesearch.retrieval.milvus.store import MilvusStore  # noqa: PLC0415

            self._store = MilvusStore(
                milvus_cfg=self.config.milvus,
                index_cfg=self.config.index,
                collection_name=self.collection_name,
                reset=reset,
                strict_trigram=self.config.agent.strict_trigram,
            )
        return self._store

    def _ensure_llms(self) -> tuple[LLMClient, LLMClient]:
        if self._main_llm is None or self._filter_llm is None:
            from openjiuwen_codesearch.llm.factory import create_llm_client  # noqa: PLC0415

            if self._main_llm is None:
                self._main_llm = create_llm_client(self.config.llm.main, client_id="codesearch_main")
            if self._filter_llm is None:
                self._filter_llm = create_llm_client(
                    self.config.llm.filter, client_id="codesearch_filter"
                )
        return self._main_llm, self._filter_llm

    # ---------- public API ----------
    async def index_repository(
        self,
        repo_path: str,
        revision: str = "local",
        instance_id: Optional[str] = None,
        reset: bool = False,
    ) -> IndexReport:
        import asyncio  # noqa: PLC0415

        from openjiuwen_codesearch.indexing.chunkers.python import PythonAstChunker  # noqa: PLC0415
        from openjiuwen_codesearch.indexing.indexer import index_repository  # noqa: PLC0415

        # store 构造含阻塞网络 I/O（connect/load 可达数秒），不占事件循环；
        # 加锁防并发重复构造
        async with self._store_lock:
            store = await asyncio.to_thread(self._ensure_store, reset)
        embedder = None
        if self.config.index.use_dense_embeddings:
            from openjiuwen_codesearch.indexing.embedder import APIEmbedModel  # noqa: PLC0415

            embedder = APIEmbedModel(
                self.config.embed, max_chars=self.config.index.max_char_limit
            )
        try:
            return await index_repository(
                store=store,
                chunker=PythonAstChunker(),
                repo_dir=repo_path,
                instance_id=instance_id or self.collection_name,
                repo_name=self.collection_name,
                revision=revision,
                index_cfg=self.config.index,
                embedder=embedder,
                embed_batch_size=self.config.embed.batch_size,
            )
        finally:
            if embedder is not None:
                await embedder.close()  # 释放持久 HTTP 会话

    def _create_agent(self):
        """按配置选择引擎：graph（openjiuwen workflow 图形态）或 react 兜底。"""
        engine = self.config.agent.engine
        if engine in ("graph", "auto"):
            try:
                from openjiuwen_codesearch.framework.openjiuwen.workflow import (  # noqa: PLC0415
                    GraphCodeSearchAgent,
                )

                return GraphCodeSearchAgent()
            except ImportError as e:
                if engine == "graph":
                    raise ImportError(
                        "engine='graph' requires openjiuwen to be installed "
                        "(pip install 'openjiuwen-codesearch[llm]')"
                    ) from e
                logger.warning("openjiuwen unavailable (%s); falling back to react engine.", e)
        return CodeSearchAgent()

    async def search(
        self, query: str, revision: str = "local", top_k: int = 20
    ) -> CodeSearchResult:
        import asyncio  # noqa: PLC0415

        # store 惰性构造含阻塞网络 I/O，隔离到线程执行；加锁防并发重复构造
        async with self._store_lock:
            store = await asyncio.to_thread(self._ensure_store)
        main_llm, filter_llm = self._ensure_llms()
        ctx = build_run_context(
            config=self.config,
            query=query,
            revision=revision,
            top_k=top_k,
            retriever=store,
            main_llm=main_llm,
            filter_llm=filter_llm,
        )
        return await self._create_agent().run(ctx)


    # ---------- 生命周期 ----------
    async def close(self) -> None:
        """释放持有的连接资源（Milvus 连接别名等）。支持 async with 用法。"""
        if self._store is not None and hasattr(self._store, "close"):
            await self._store.close()
            self._store = None

    async def __aenter__(self) -> "CodeSearchRetriever":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


# 兼容别名（保留一个版本，见 plan Phase 5/6）
JiuwenRetriever = CodeSearchRetriever
