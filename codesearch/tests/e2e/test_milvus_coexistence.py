# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""共用 Milvus 实例的隔离验证（e2e）：

模拟同一实例上存在其他产品（如 deepsearch）的无前缀 collection，
验证 codesearch 的建库/写入/重建全流程只触碰 `cs_` 前缀命名空间。
"""

import os

import pytest

pytest.importorskip("pymilvus", reason="requires pymilvus (install extras: milvus)")

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

from openjiuwen_codesearch.config.index import IndexConfig, MilvusConfig
from openjiuwen_codesearch.retrieval.milvus.store import MilvusStore

from tests.conftest import run

pytestmark = pytest.mark.e2e

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")

FOREIGN = "coexist_foreign_collection"  # 模拟他产品的无前缀 collection


@pytest.fixture(scope="module")
def foreign_collection():
    try:
        connections.connect("coexist_check", host=MILVUS_HOST, port=MILVUS_PORT)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Milvus not reachable at {MILVUS_HOST}:{MILVUS_PORT}: {e}")
    # 预清理：上一轮被强杀的残留（FOREIGN 与 cs_test_coexist*）会导致断言假失败
    for name in utility.list_collections(using="coexist_check"):
        if name == FOREIGN or (name.startswith("cs_test_coexist") and "__v" in name):
            utility.drop_collection(name, using="coexist_check")
    schema = CollectionSchema(
        [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True),
            FieldSchema(name="vec", dtype=DataType.FLOAT_VECTOR, dim=4),
        ],
        description="simulated foreign product collection",
    )
    coll = Collection(name=FOREIGN, schema=schema, using="coexist_check")
    coll.insert([[1, 2], [[0.1] * 4, [0.2] * 4]])
    coll.flush()
    yield coll
    # 清理放 fixture 收尾：断言失败也不遗留任何 collection 污染共用实例
    for name in utility.list_collections(using="coexist_check"):
        if name == FOREIGN or (name.startswith("cs_test_coexist") and "__v" in name):
            utility.drop_collection(name, using="coexist_check")


def test_codesearch_only_touches_prefixed_namespace(foreign_collection):
    before = set(utility.list_collections(using="coexist_check"))

    store = MilvusStore(
        milvus_cfg=MilvusConfig(host=MILVUS_HOST, port=MILVUS_PORT),
        index_cfg=IndexConfig(),
        collection_name="test_coexist",
        reset=True,
    )
    run(store.insert_records([{
        "id": 1, "file_hash": "h", "instance_ids": ["i"], "repo": "r",
        "commits": ["c1"], "file_path": "a.py", "start_line": 1, "end_line": 2,
        "kind": "function", "original_name": "f", "name": "f",
        "text": "def f(): pass", "text_trigram": "", "calls": [],
    }]))
    run(store.flush())

    after = set(utility.list_collections(using="coexist_check"))
    created = after - before
    # 我们创建的 collection 全部带 cs_ 前缀与版本后缀
    assert created, "expected codesearch to create its collection"
    assert all(name.startswith("cs_") and "__v" in name for name in created), created

    # 他产品的 collection 原封不动（存在且行数未变）
    assert FOREIGN in after
    foreign_collection.flush()
    assert foreign_collection.num_entities == 2

    # 重建（reset）也只 drop 自己的实名 collection
    MilvusStore(
        milvus_cfg=MilvusConfig(host=MILVUS_HOST, port=MILVUS_PORT),
        index_cfg=IndexConfig(),
        collection_name="test_coexist",
        reset=True,
    )
    assert FOREIGN in set(utility.list_collections(using="coexist_check"))
