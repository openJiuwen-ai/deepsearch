# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Milvus 通用存取基建（schema 无关，需要 pymilvus，extras: milvus）。

提供 search 场景共用的存取执行层：连接/别名管理、建库与索引、批式读写
（含防 payload 上限的两段式查询）、稀疏/稠密/混合检索执行、同步调用线程隔离。
**不含任何领域语义**——schema、字段名、结果映射由消费方传入/继承实现。
"""

import asyncio
import logging
from typing import Any, Callable

from pymilvus import AnnSearchRequest, Collection, WeightedRanker, connections, utility

logger = logging.getLogger(__name__)


class MilvusCollectionClient:
    """一个 collection 的通用客户端。

    Args:
        schema_factory: 惰性构造 CollectionSchema（仅在 collection 不存在时调用）。
        indexes: [(field_name, index_params)]，建库时创建。
        sparse_generated_fields: 由 Milvus Function 生成、写入时必须排除的字段。
        database_name: Milvus 2.2+ 的 database（比 collection 前缀更强一级的
            多产品硬隔离手段）；"default" 表示不切换。
            `using_database` 是 **per-alias 全局**的：同一 alias 下的所有
            客户端共享该切换，勿在同 alias 上混用不同 database。
        collection: 测试/高级注入口——直接提供 collection 对象时跳过
            connect/建库/load（对齐 deepsearch "构造器可注入 client" 的可测性惯例）。

    构造函数包含**阻塞网络 I/O**（connect/建库/load，大 collection 的 load
    可达数秒）：在事件循环上惰性构造时请包 `asyncio.to_thread`。
    """

    def __init__(
        self,
        host: str,
        port: str,
        collection_name: str,
        schema_factory: Callable[[], Any],
        indexes: list[tuple[str, dict]],
        connection_alias: str = "default",
        reset: bool = False,
        sparse_generated_fields: tuple[str, ...] = (),
        database_name: str = "default",
        token: str = "",
        collection: Any = None,
    ) -> None:
        self._alias = connection_alias
        self._name = collection_name
        self._sparse_generated = set(sparse_generated_fields)

        if collection is not None:
            self.collection = collection
            return

        connect_kwargs: dict = {"host": host, "port": port}
        if token:
            # Milvus 认证部署（如 deepsearch 文档的 MILVUS_TOKEN 场景）；
            # token 支持 "user:password" 形式
            connect_kwargs["token"] = token
        connections.connect(self._alias, **connect_kwargs)
        if database_name and database_name != "default":
            from pymilvus import db  # Milvus 2.2+

            db.using_database(database_name, using=self._alias)
        if reset and utility.has_collection(self._name, using=self._alias):
            logger.info("Dropping existing collection '%s' (explicit reset)...", self._name)
            utility.drop_collection(self._name, using=self._alias)

        if utility.has_collection(self._name, using=self._alias):
            logger.info("Loading existing collection '%s'...", self._name)
            self.collection = Collection(name=self._name, using=self._alias)
        else:
            logger.info("Creating collection '%s'...", self._name)
            self.collection = Collection(
                name=self._name, schema=schema_factory(), using=self._alias
            )
            for field_name, params in indexes:
                self.collection.create_index(field_name, params)
        self.collection.load()

    # ---------- 写入 ----------
    def writable_fields(self) -> list[str]:
        return [
            f.name
            for f in self.collection.schema.fields
            if not f.auto_id and f.name not in self._sparse_generated
        ]

    def _records_to_columns(self, records: list[dict]) -> list[list]:
        fields = self.writable_fields()
        return [[record.get(name) for record in records] for name in fields]

    async def insert_records(self, records: list[dict], batch_size: int = 128) -> None:
        await self._write(records, self.collection.insert, batch_size)

    async def upsert_records(self, records: list[dict], batch_size: int = 128) -> None:
        await self._write(records, self.collection.upsert, batch_size)

    async def _write(self, records: list[dict], op, batch_size: int) -> None:
        def _run():
            for i in range(0, len(records), batch_size):
                op(self._records_to_columns(records[i : i + batch_size]))

        await asyncio.to_thread(_run)

    async def flush(self) -> None:
        await asyncio.to_thread(self.collection.flush)

    async def release(self) -> None:
        """卸载 collection 释放查询内存（多集合批处理的内存控制手段）。"""
        await asyncio.to_thread(self.collection.release)

    async def close(self) -> None:
        """断开本客户端的连接别名（服务化/热重载的生命周期收口）。

        pymilvus 连接按 **alias 共享**：同进程内多个客户端若使用同一
        alias，close 任意一个会断开全部；对同 alias 换 host 重连也会报错。
        多实例长驻场景请为每个实例分配独立 alias（或参照 deepsearch server
        的做法：短命 alias 仅用于健康检查、长连接用独立 MilvusClient）。
        """
        await asyncio.to_thread(connections.disconnect, self._alias)

    # ---------- 查询 ----------
    async def query(self, expr: str, output_fields: list[str], **kwargs) -> list[dict]:
        return await asyncio.to_thread(
            lambda: self.collection.query(expr=expr, output_fields=output_fields, **kwargs)
        )

    async def query_ids_then_fields(
        self,
        expr: str,
        output_fields: list[str],
        ids_filter_builder: Callable[[list[int]], str],
        heavy_batch_size: int = 20,
        pk_field: str = "id",
        **kwargs,
    ) -> list[dict]:
        """两段式查询：先取主键再按主键小批量取重字段（防 65MB payload 上限）。"""

        def _run() -> list[dict]:
            id_res = self.collection.query(expr=expr, output_fields=[pk_field], **kwargs)
            all_ids = [r[pk_field] for r in id_res]
            records: list[dict] = []
            for j in range(0, len(all_ids), heavy_batch_size):
                records.extend(
                    self.collection.query(
                        expr=ids_filter_builder(all_ids[j : j + heavy_batch_size]),
                        output_fields=output_fields,
                        **kwargs,
                    )
                )
            return records

        return await asyncio.to_thread(_run)

    async def ann_search(
        self,
        data: list,
        anns_field: str,
        param: dict,
        limit: int,
        expr: str,
        output_fields: list[str],
    ):
        def _search():
            return self.collection.search(
                data=data, anns_field=anns_field, param=param,
                limit=limit, expr=expr, output_fields=output_fields,
            )

        return await asyncio.to_thread(_search)

    async def hybrid_ann_search(
        self,
        reqs: list[AnnSearchRequest],
        weights: tuple[float, ...],
        limit: int,
        output_fields: list[str],
    ):
        def _search():
            return self.collection.hybrid_search(
                reqs=reqs, rerank=WeightedRanker(*weights),
                limit=limit, output_fields=output_fields,
            )

        return await asyncio.to_thread(_search)
