# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MilvusStore：同时实现检索协议（CodeRetriever）与索引写入协议（ChunkStore）。

对旧 MilvusCollection 的修正：
- 所有同步 pymilvus 调用经 `asyncio.to_thread` 隔离（不阻塞 event loop，notes #8）；
- 所有 expr 经 queries.py 构造（转义，notes #20）；
- collection 实名带 schema_version（notes #23），重建需显式 reset=True。
"""

import asyncio
import logging
from typing import Any, Optional

from pymilvus import AnnSearchRequest, Collection, WeightedRanker, connections, utility

from openjiuwen_codesearch.config.index import IndexConfig, MilvusConfig
from openjiuwen_codesearch.domain.models import Snippet
from openjiuwen_codesearch.retrieval.milvus import queries
from openjiuwen_codesearch.retrieval.milvus.schema import (
    DENSE_INDEX_PARAMS,
    OUTPUT_FIELDS,
    SPARSE_INDEX_PARAMS,
    build_schema,
    versioned_collection_name,
)
from openjiuwen_codesearch.retrieval.tokenizer import generate_char_trigrams

logger = logging.getLogger(__name__)


class MilvusStore:
    def __init__(
        self,
        milvus_cfg: MilvusConfig,
        index_cfg: IndexConfig,
        collection_name: str,
        embed_dim: int = 0,
        reset: bool = False,
        strict_trigram: bool = True,
    ) -> None:
        self._milvus_cfg = milvus_cfg
        self._index_cfg = index_cfg
        self._strict_trigram = strict_trigram
        self._alias = milvus_cfg.connection_alias
        self._name = versioned_collection_name(
            collection_name, milvus_cfg.schema_version, milvus_cfg.collection_prefix
        )

        connections.connect(self._alias, host=milvus_cfg.host, port=milvus_cfg.port)
        if reset and utility.has_collection(self._name, using=self._alias):
            logger.info("Dropping existing collection '%s' (explicit reset)...", self._name)
            utility.drop_collection(self._name, using=self._alias)

        if utility.has_collection(self._name, using=self._alias):
            logger.info("Loading existing collection '%s'...", self._name)
            self.collection = Collection(name=self._name, using=self._alias)
        else:
            logger.info("Creating collection '%s'...", self._name)
            schema = build_schema(
                use_dense=index_cfg.use_dense_embeddings,
                embed_dim=embed_dim,
                max_char_limit=index_cfg.max_char_limit,
                max_num_calls=index_cfg.max_num_calls,
            )
            self.collection = Collection(name=self._name, schema=schema, using=self._alias)
            if index_cfg.use_dense_embeddings:
                self.collection.create_index("dense_vector", DENSE_INDEX_PARAMS)
            self.collection.create_index("sparse_vector", SPARSE_INDEX_PARAMS)
            self.collection.create_index("sparse_vector_trigram", SPARSE_INDEX_PARAMS)
        self.collection.load()

    # ---------- 内部工具 ----------
    @staticmethod
    def _hit_to_snippet(hit: Any) -> Snippet:
        return Snippet(
            id=hit.id,
            score=hit.distance,
            text=hit.entity.get("text") or "",
            file_path=hit.entity.get("file_path") or "",
            start_line=hit.entity.get("start_line") or 0,
            end_line=hit.entity.get("end_line") or 0,
            kind=hit.entity.get("kind") or "",
            original_name=hit.entity.get("original_name") or "",
        )

    def _writable_fields(self) -> list[str]:
        return [
            f.name
            for f in self.collection.schema.fields
            if not f.auto_id and f.name not in ("sparse_vector", "sparse_vector_trigram")
        ]

    def _records_to_columns(self, records: list[dict]) -> list[list]:
        fields = self._writable_fields()
        return [[record.get(name) for record in records] for name in fields]

    # ---------- CodeRetriever ----------
    async def search(
        self, query: str, revision: str, topk: int, use_trigram: bool
    ) -> list[Snippet]:
        if use_trigram:
            query_data = generate_char_trigrams(query, self._index_cfg.max_char_limit)
            field = "sparse_vector_trigram"
            fetch_limit = topk * 10
        else:
            query_data = query
            field = "sparse_vector"
            fetch_limit = topk

        def _search():
            return self.collection.search(
                data=[query_data],
                anns_field=field,
                param={"metric_type": "BM25"},
                limit=fetch_limit,
                expr=queries.revision_filter(revision),
                output_fields=OUTPUT_FIELDS,
            )

        results = await asyncio.to_thread(_search)
        hits = [self._hit_to_snippet(hit) for hit in results[0]]

        if use_trigram and self._strict_trigram:
            needle = query.lower()
            filtered = [h for h in hits if needle in h.text.lower()]
            return filtered[:topk]
        return hits[:topk]

    async def get_repo_map(self, revision: str) -> str:
        def _collect() -> list[str]:
            id_res = self.collection.query(
                expr=queries.revision_filter(revision), output_fields=["id"]
            )
            if not id_res:
                return []
            all_ids = [r["id"] for r in id_res]
            unique_paths: set[str] = set()
            batch = 5000
            for j in range(0, len(all_ids), batch):
                res = self.collection.query(
                    expr=queries.ids_filter(all_ids[j : j + batch]),
                    output_fields=["file_path"],
                )
                unique_paths.update(r["file_path"] for r in res if r.get("file_path"))
            return sorted(unique_paths)

        try:
            paths = await asyncio.to_thread(_collect)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to generate repo map: %s", e)
            return "Repository Map unavailable."
        if not paths:
            return "Repository Map unavailable (no files found)."
        return "\n".join(["Repository File Map:"] + [f"- {p}" for p in paths])

    async def fetch_overlapping(
        self, revision: str, file_path: str, start_line: int, end_line: int
    ) -> list[Snippet]:
        def _query():
            return self.collection.query(
                expr=queries.overlap_filter(revision, file_path, start_line, end_line),
                output_fields=["id"] + OUTPUT_FIELDS,
            )

        res = await asyncio.to_thread(_query)
        return [
            Snippet(
                id=r["id"],
                text=r.get("text") or "",
                file_path=r.get("file_path") or "",
                start_line=r.get("start_line") or 0,
                end_line=r.get("end_line") or 0,
                kind=r.get("kind") or "",
                original_name=r.get("original_name") or "",
            )
            for r in res
        ]

    async def has_revision(self, revision: str) -> bool:
        def _query():
            return self.collection.query(
                expr=queries.revision_filter(revision), output_fields=["id"], limit=1
            )

        try:
            return bool(await asyncio.to_thread(_query))
        except Exception as e:  # noqa: BLE001
            logger.warning("has_revision check failed: %s", e)
            return False

    # ---------- ChunkStore ----------
    async def fetch_records_by_hashes(self, file_hashes: list[str]) -> list[dict]:
        if not file_hashes:
            return []
        fields = self._writable_fields()
        if "id" not in fields:
            fields.append("id")
        heavy_batch = self._milvus_cfg.heavy_fetch_batch_size

        def _fetch() -> list[dict]:
            records: list[dict] = []
            for i in range(0, len(file_hashes), self._milvus_cfg.query_batch_size):
                batch_hashes = file_hashes[i : i + self._milvus_cfg.query_batch_size]
                id_res = self.collection.query(
                    expr=queries.hashes_filter(batch_hashes),
                    output_fields=["id"],
                    consistency_level="Strong",
                )
                all_ids = [r["id"] for r in id_res]
                for j in range(0, len(all_ids), heavy_batch):
                    records.extend(
                        self.collection.query(
                            expr=queries.ids_filter(all_ids[j : j + heavy_batch]),
                            output_fields=fields,
                            consistency_level="Strong",
                        )
                    )
            return records

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to query existing hashes from Milvus: %s", e)
            return []

    async def upsert_records(self, records: list[dict]) -> None:
        await self._write(records, self.collection.upsert)

    async def insert_records(self, records: list[dict]) -> None:
        await self._write(records, self.collection.insert)

    async def _write(self, records: list[dict], op) -> None:
        batch_size = self._milvus_cfg.insert_batch_size

        def _run():
            for i in range(0, len(records), batch_size):
                op(self._records_to_columns(records[i : i + batch_size]))

        await asyncio.to_thread(_run)

    async def flush(self) -> None:
        await asyncio.to_thread(self.collection.flush)

    async def release(self) -> None:
        """卸载 collection 释放查询内存（多仓 benchmark 按仓分批时使用）。"""
        await asyncio.to_thread(self.collection.release)

    # ---------- 可选：dense / hybrid（use_dense_embeddings=True 时可用） ----------
    async def hybrid_search(
        self,
        query: str,
        query_vector: list[float],
        revision: str,
        topk: int,
        use_trigram: bool = False,
    ) -> list[Snippet]:
        fetch_limit = topk * 10 if use_trigram else topk
        sparse_field = "sparse_vector_trigram" if use_trigram else "sparse_vector"
        sparse_data = (
            generate_char_trigrams(query, self._index_cfg.max_char_limit)
            if use_trigram
            else query
        )
        expr = queries.revision_filter(revision)
        reqs = [
            AnnSearchRequest(
                data=[query_vector],
                anns_field="dense_vector",
                param={"metric_type": "COSINE", "params": {"ef": 1024}},
                limit=fetch_limit,
                expr=expr,
            ),
            AnnSearchRequest(
                data=[sparse_data],
                anns_field=sparse_field,
                param={"metric_type": "BM25"},
                limit=fetch_limit,
                expr=expr,
            ),
        ]

        def _search():
            return self.collection.hybrid_search(
                reqs=reqs,
                rerank=WeightedRanker(0.3, 0.7),
                limit=fetch_limit,
                output_fields=OUTPUT_FIELDS,
            )

        results = await asyncio.to_thread(_search)
        hits = [self._hit_to_snippet(hit) for hit in results[0]]
        if use_trigram and self._strict_trigram:
            needle = query.lower()
            hits = [h for h in hits if needle in h.text.lower()]
        return hits[:topk]
