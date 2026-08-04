# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MilvusCollectionClient 的注入式单测（fake collection，不连真实 Milvus）。"""

import pytest

pytest.importorskip("pymilvus", reason="store 模块 import pymilvus（extras: milvus）")

from openjiuwen_search_base.milvus.store import MilvusCollectionClient

from tests.conftest_helpers import run


class _Field:
    def __init__(self, name, auto_id=False):
        self.name, self.auto_id = name, auto_id


class _Schema:
    def __init__(self, fields):
        self.fields = fields


class FakeCollection:
    def __init__(self):
        self.schema = _Schema(
            [_Field("id"), _Field("auto", auto_id=True), _Field("sparse_vec"), _Field("text")]
        )
        self.inserted_batches: list[list[list]] = []
        self.query_calls: list[dict] = []

    def insert(self, columns):
        self.inserted_batches.append(columns)

    def query(self, expr, output_fields, **kwargs):
        self.query_calls.append({"expr": expr, "fields": output_fields})
        if output_fields == ["id"]:
            return [{"id": i} for i in range(5)]
        return [{"id": 0, "text": "t"}]


def _client(fake):
    return MilvusCollectionClient(
        host="", port="", collection_name="c", schema_factory=lambda: None,
        indexes=[], sparse_generated_fields=("sparse_vec",), collection=fake,
    )


def test_writable_fields_excludes_auto_and_generated():
    client = _client(FakeCollection())
    assert client.writable_fields() == ["id", "text"]


def test_records_to_columns_orders_by_schema():
    client = _client(FakeCollection())
    cols = client.records_to_columns([{"text": "a", "id": 1}, {"id": 2}])
    assert cols == [[1, 2], ["a", None]]  # 按 schema 字段序构列，缺失补 None


def test_insert_records_batches():
    fake = FakeCollection()
    client = _client(fake)
    run(client.insert_records([{"id": i, "text": str(i)} for i in range(5)], batch_size=2))
    assert len(fake.inserted_batches) == 3  # 5 条 / 批 2 → 3 批


def test_query_ids_then_fields_two_phase_batching():
    fake = FakeCollection()
    client = _client(fake)
    records = run(
        client.query_ids_then_fields(
            expr="e", output_fields=["id", "text"],
            ids_filter_builder=lambda ids: f"id in {ids}", heavy_batch_size=2,
        )
    )
    # 第一段取 5 个 id，第二段按批 2 → 3 次重字段查询
    assert len(fake.query_calls) == 1 + 3
    assert len(records) == 3
