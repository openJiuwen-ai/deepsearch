# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MilvusStore：代码检索的 Milvus 实现。

通用存取基建（连接/建库/批式读写/两段式查询/检索执行/线程隔离）由
openjiuwen-search-base 的 `MilvusCollectionClient` 提供（2026-07-29 提取）；
本类只保留**代码检索特有**的部分：chunk schema、Snippet 映射、trigram 查询
与严格子串过滤、repo map、行区间重叠查询、revision 语义、文件哈希查重。
"""

import logging
import uuid
from typing import Any

from pymilvus import AnnSearchRequest
from pymilvus.exceptions import MilvusException

from openjiuwen_search_base.milvus.store import MilvusCollectionClient

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

_SPARSE_GENERATED_FIELDS = ("sparse_vector", "sparse_vector_trigram")


class MilvusStore(MilvusCollectionClient):
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

        indexes: list[tuple[str, dict]] = []
        if index_cfg.use_dense_embeddings:
            indexes.append(("dense_vector", DENSE_INDEX_PARAMS))
        indexes.append(("sparse_vector", SPARSE_INDEX_PARAMS))
        indexes.append(("sparse_vector_trigram", SPARSE_INDEX_PARAMS))

        # 别名按实例唯一化：pymilvus 连接按 alias 共享，固定别名下任一实例
        # close() 会断掉同进程所有实例的连接（互杀）。配置值作为前缀保留可辨识性。
        instance_alias = f"{milvus_cfg.connection_alias}-{uuid.uuid4().hex[:8]}"
        super().__init__(
            host=milvus_cfg.host,
            port=milvus_cfg.port,
            token=milvus_cfg.token,
            database_name=milvus_cfg.database_name,
            collection_name=versioned_collection_name(
                collection_name, milvus_cfg.schema_version, milvus_cfg.collection_prefix
            ),
            schema_factory=lambda: build_schema(
                use_dense=index_cfg.use_dense_embeddings,
                embed_dim=embed_dim,
                max_char_limit=index_cfg.max_char_limit,
                max_num_calls=index_cfg.max_num_calls,
            ),
            indexes=indexes,
            connection_alias=instance_alias,
            reset=reset,
            sparse_generated_fields=_SPARSE_GENERATED_FIELDS,
        )

    # ---------- 结果映射（代码域） ----------
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

        results = await self.ann_search(
            data=[query_data],
            anns_field=field,
            param={"metric_type": "BM25"},
            limit=fetch_limit,
            expr=queries.revision_filter(revision),
            output_fields=OUTPUT_FIELDS,
        )
        hits = [self._hit_to_snippet(hit) for hit in results[0]]

        if use_trigram and self._strict_trigram:
            needle = query.lower()
            filtered = [h for h in hits if needle in h.text.lower()]
            return filtered[:topk]
        return hits[:topk]

    async def get_repo_map(self, revision: str) -> str:
        try:
            records = await self.query_ids_then_fields(
                expr=queries.revision_filter(revision),
                output_fields=["file_path"],
                ids_filter_builder=queries.ids_filter,
                heavy_batch_size=5000,
            )
            paths = sorted({r["file_path"] for r in records if r.get("file_path")})
        except MilvusException:
            # 面向 LLM 的工具结果允许优雅降级，但必须留完整现场；编程错误正常抛出
            logger.exception("Failed to generate repo map")
            return "Repository Map unavailable."
        if not paths:
            return "Repository Map unavailable (no files found)."
        return "\n".join(["Repository File Map:"] + [f"- {p}" for p in paths])

    async def fetch_overlapping(
        self, revision: str, file_path: str, start_line: int, end_line: int
    ) -> list[Snippet]:
        res = await self.query(
            expr=queries.overlap_filter(revision, file_path, start_line, end_line),
            output_fields=["id"] + OUTPUT_FIELDS,
        )
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
        try:
            res = await self.query(
                expr=queries.revision_filter(revision), output_fields=["id"], limit=1
            )
            return bool(res)
        except MilvusException:
            # Milvus 侧故障按"未就绪"处理（fail-fast 返回空结果，不进检索循环），
            # 完整现场入日志；编程错误正常抛出
            logger.exception("has_revision check failed")
            return False

    async def hybrid_search(
        self,
        query: str,
        query_vector: list[float],
        revision: str,
        topk: int,
        use_trigram: bool = False,
    ) -> list[Snippet]:
        """稠密+稀疏混合检索（use_dense_embeddings=True 的配套查询能力）。

        领域级 API：0.3 dense / 0.7 sparse 加权，trigram 模式下超采样后
        按真实子串包含严格过滤——与迁移前行为一致。
        通用执行由 base 的 `hybrid_ann_search` 承担。
        """
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
        results = await self.hybrid_ann_search(
            reqs=reqs, weights=(0.3, 0.7), limit=fetch_limit, output_fields=OUTPUT_FIELDS
        )
        hits = [self._hit_to_snippet(hit) for hit in results[0]]
        if use_trigram and self._strict_trigram:
            needle = query.lower()
            hits = [h for h in hits if needle in h.text.lower()]
        return hits[:topk]

    # ---------- ChunkStore（索引写入侧） ----------
    async def fetch_records_by_hashes(self, file_hashes: list[str]) -> list[dict]:
        if not file_hashes:
            return []
        fields = self.writable_fields()
        if "id" not in fields:
            fields.append("id")
        # 有意不捕获异常：此处失败若静默返回 []，去重会把已有文件当新文件重复
        # insert（同 PK 重复写入）。索引失败应当喊出来，由上层按实例记失败并继续。
        records: list[dict] = []
        for i in range(0, len(file_hashes), self._milvus_cfg.query_batch_size):
            batch = file_hashes[i : i + self._milvus_cfg.query_batch_size]
            records.extend(
                await self.query_ids_then_fields(
                    expr=queries.hashes_filter(batch),
                    output_fields=fields,
                    ids_filter_builder=queries.ids_filter,
                    heavy_batch_size=self._milvus_cfg.heavy_fetch_batch_size,
                    consistency_level="Strong",
                )
            )
        return records

    async def insert_records(self, records: list[dict], batch_size: int | None = None) -> None:
        await super().insert_records(
            records, batch_size=batch_size or self._milvus_cfg.insert_batch_size
        )

    async def upsert_records(self, records: list[dict], batch_size: int | None = None) -> None:
        await super().upsert_records(
            records, batch_size=batch_size or self._milvus_cfg.insert_batch_size
        )
