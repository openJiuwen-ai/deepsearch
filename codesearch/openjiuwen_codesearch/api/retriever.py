# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""公共 SDK 门面。

配置一律经 `CodeSearchConfig` 传入——禁止旧 wrapper 的"改写全局 settings"注入方式。
重依赖（pymilvus / openjiuwen / aiohttp）延迟到实际使用时 import；
测试可经构造参数注入 fake retriever / fake llm。
"""

import logging
from pathlib import Path
from typing import Any, Optional

from openjiuwen_codesearch.api.models import IndexReport
from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.domain.result import CodeSearchResult, Termination
from openjiuwen_codesearch.framework.openjiuwen.agent import (
    CodeSearchAgent,
    RetropusCodeSearchAgent,
)
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
        import asyncio

        self.config = config or CodeSearchConfig.from_env()
        self.collection_name = collection_name
        self._store = retriever
        self._main_llm = main_llm
        self._filter_llm = filter_llm
        # 防并发构造竞态：两个并发请求同时看到 _store is None 会重复建连，
        # 且 reset 与并发 search 交错时可能 drop 对方刚 load 的 collection
        self._store_lock = asyncio.Lock()
        # Retropus-owned index cache (KG + BM25); never shared with Milvus path
        self._retropus_repo_dir: Optional[Path] = None
        self._retropus_kg: Any = None
        self._retropus_retriever: Any = None

    def _is_retropus(self) -> bool:
        return self.config.agent.engine == "retropus"

    @staticmethod
    def engine_keeps_index_in_process(engine: str) -> bool:
        """True when that engine's index lives on the retriever instance.

        Retropus KG/BM25 is in-memory; Milvus is durable and can reconnect
        after ``close()``. Used by the HTTP server to decide whether to keep
        the cached retriever after ``/v1/index``.
        """
        return engine == "retropus"

    def keeps_index_in_process(self) -> bool:
        """See :meth:`engine_keeps_index_in_process`."""
        return self.engine_keeps_index_in_process(self.config.agent.engine)

    def has_retropus_index(self) -> bool:
        """True when an in-memory Retropus KG has been built or loaded."""
        return self._retropus_kg is not None

    def set_retropus_index(
        self,
        kg: Any,
        retriever: Any,
        repo_dir: Optional[Path | str],
    ) -> None:
        """Install an in-memory Retropus index (tests / advanced wiring)."""
        self._retropus_kg = kg
        self._retropus_retriever = retriever
        self._retropus_repo_dir = Path(repo_dir) if repo_dir is not None else None

    # ---------- lazy wiring ----------
    def _ensure_store(self, reset: bool = False):
        if self._store is None:
            from openjiuwen_codesearch.retrieval.milvus.store import MilvusStore

            self._store = MilvusStore(
                milvus_cfg=self.config.milvus,
                index_cfg=self.config.index,
                collection_name=self.collection_name,
                reset=reset,
                strict_trigram=self.config.agent.strict_trigram,
            )
        return self._store

    def _ensure_llms(self) -> tuple[LLMClient, LLMClient]:
        """``search`` 专用：返回非空的 (main, filter)。缺则按 config.llm 创建。"""
        if self._main_llm is None or self._filter_llm is None:
            from openjiuwen_codesearch.llm.factory import create_llm_client

            if self._main_llm is None:
                self._main_llm = create_llm_client(self.config.llm.main, client_id="codesearch_main")
            if self._filter_llm is None:
                self._filter_llm = create_llm_client(
                    self.config.llm.filter, client_id="codesearch_filter"
                )
        return self._main_llm, self._filter_llm

    def _retropus_cache_dir(self) -> Optional[Path]:
        """Per-collection on-disk cache path, or ``None`` when persistence is off."""
        index_dir = (self.config.retropus.index_dir or "").strip()
        if not index_dir:
            return None
        from openjiuwen_codesearch.retropus.persist import (  # noqa: PLC0415
            collection_index_dir,
        )

        return collection_index_dir(index_dir, self.collection_name)

    def _load_retropus_cache(
        self, repo_dir: Optional[Path] = None
    ) -> bool:
        """Populate in-memory Retropus state from disk. Returns True on hit."""
        cache_dir = self._retropus_cache_dir()
        if cache_dir is None:
            return False
        from openjiuwen_codesearch.retropus.persist import (  # noqa: PLC0415
            load_retropus_index,
        )

        loaded = load_retropus_index(
            cache_dir, config=self.config.retropus, repo_dir=repo_dir
        )
        if loaded is None:
            return False
        kg, retriever, cached_repo = loaded
        self._retropus_kg = kg
        self._retropus_retriever = retriever
        self._retropus_repo_dir = cached_repo
        return True

    def _dump_retropus_cache(self) -> None:
        """Persist current in-memory Retropus index when ``index_dir`` is set."""
        cache_dir = self._retropus_cache_dir()
        if cache_dir is None:
            return
        missing_state = (
            self._retropus_kg is None
            or self._retropus_retriever is None
            or self._retropus_repo_dir is None
        )
        if missing_state:
            return
        from openjiuwen_codesearch.retropus.persist import (  # noqa: PLC0415
            dump_retropus_index,
        )

        dump_retropus_index(
            cache_dir,
            kg=self._retropus_kg,
            retriever=self._retropus_retriever,
            repo_dir=self._retropus_repo_dir,
            collection=self.collection_name,
            config=self.config.retropus,
        )

    def _build_retropus_index(self, repo_path: str, reset: bool = False) -> IndexReport:
        try:
            from openjiuwen_codesearch.retropus.index import (  # noqa: PLC0415
                build_index,
                build_retriever,
            )
        except ImportError as e:
            raise ImportError(
                "engine='retropus' requires retropus extras "
                "(pip install 'openjiuwen-codesearch[retropus]')"
            ) from e

        repo_dir = Path(repo_path).resolve()
        retropus_cfg = self.config.retropus
        cache_dir = self._retropus_cache_dir()

        if reset:
            self._retropus_kg = None
            self._retropus_retriever = None
            self._retropus_repo_dir = None
            if cache_dir is not None:
                from openjiuwen_codesearch.retropus.persist import (  # noqa: PLC0415
                    clear_retropus_index,
                )

                clear_retropus_index(cache_dir)
        elif self._retropus_repo_dir is not None and self._retropus_repo_dir != repo_dir:
            self._retropus_kg = None
            self._retropus_retriever = None
            self._retropus_repo_dir = None

        if self._retropus_kg is None and not reset:
            if self._load_retropus_cache(repo_dir=repo_dir):
                kg = self._retropus_kg
                return IndexReport(
                    files_total=len(kg.get_file_nodes()),
                    files_new=0,
                    files_reused=len(kg.get_file_nodes()),
                    chunks_inserted=0,
                )

        if self._retropus_kg is None:
            self._retropus_kg = build_index(repo_dir, retropus_cfg)
            self._retropus_retriever = build_retriever(self._retropus_kg, retropus_cfg)
            self._retropus_repo_dir = repo_dir
            self._dump_retropus_cache()

        kg = self._retropus_kg
        return IndexReport(
            files_total=len(kg.get_file_nodes()),
            files_new=len(kg.get_file_nodes()),
            files_reused=0,
            chunks_inserted=len(kg.get_ast_nodes()) + len(kg.get_text_nodes()),
        )

    # ---------- public API ----------
    async def index_repository(
        self,
        repo_path: str,
        revision: str = "local",
        instance_id: Optional[str] = None,
        reset: bool = False,
    ) -> IndexReport:
        import asyncio

        if self._is_retropus():
            async with self._store_lock:
                return await asyncio.to_thread(self._build_retropus_index, repo_path, reset)

        from openjiuwen_codesearch.indexing.chunkers.python import PythonAstChunker
        from openjiuwen_codesearch.indexing.indexer import index_repository


        # store 构造含阻塞网络 I/O（connect/load 可达数秒），不占事件循环；
        # 加锁防并发重复构造
        async with self._store_lock:
            store = await asyncio.to_thread(self._ensure_store, reset)
        embedder = None
        if self.config.index.use_dense_embeddings:
            from openjiuwen_codesearch.indexing.embedder import APIEmbedModel

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
        """按配置选择引擎：retropus / graph / react。"""
        engine = self.config.agent.engine
        if engine == "retropus":
            return RetropusCodeSearchAgent()
        if engine == "graph":
            try:
                from openjiuwen_codesearch.framework.openjiuwen.workflow import (
                    GraphCodeSearchAgent,
                )

                return GraphCodeSearchAgent()
            except ImportError as e:
                raise ImportError(
                    "engine='graph' requires openjiuwen to be installed "
                    "(pip install 'openjiuwen-codesearch[llm]')"
                ) from e
        return CodeSearchAgent()

    async def search(
        self, query: str, revision: str = "local", top_k: int = 20
    ) -> CodeSearchResult:
        import asyncio

        if self._is_retropus():
            return await self._search_retropus(query, top_k=top_k)

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

    async def _search_retropus(self, query: str, top_k: int) -> CodeSearchResult:
        from openjiuwen_codesearch.framework.openjiuwen.retropus_context import (  # noqa: PLC0415
            build_retropus_run_context,
        )

        if self._retropus_kg is None or self._retropus_retriever is None:
            # Separate CLI/process invocations reload from the on-disk dump.
            self._load_retropus_cache(repo_dir=None)

        if self._retropus_kg is None or self._retropus_retriever is None:
            return CodeSearchResult(
                hits=[],
                termination=Termination.INDEX_NOT_READY,
                turns=0,
                error=(
                    "retropus index not ready; call index_repository first "
                    "(or ensure a dump exists under retropus.index_dir)"
                ),
            )

        main_llm, _filter_llm = self._ensure_llms()
        ctx = build_retropus_run_context(
            config=self.config,
            query=query,
            top_k=top_k,
            repo_dir=self._retropus_repo_dir or Path("."),
            kg=self._retropus_kg,
            retriever=self._retropus_retriever,
            main_llm=main_llm,
            issue_body=query,
        )
        return await RetropusCodeSearchAgent().run(ctx)

    def get_store(self):
        """返回当前底层 store（可能尚未惰性创建）。"""
        return self._store

    # ---------- 生命周期 ----------

    async def close(self) -> None:
        """释放持有的连接资源（Milvus 连接别名等）。支持 async with 用法。"""
        if self._store is not None and hasattr(self._store, "close"):
            await self._store.close()
            self._store = None
        self._retropus_kg = None
        self._retropus_retriever = None
        self._retropus_repo_dir = None

    async def __aenter__(self) -> "CodeSearchRetriever":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


# 兼容别名
JiuwenRetriever = CodeSearchRetriever
