# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""真实 Milvus 的端到端检索测试。

前置：本地有运行中的 Milvus。连接地址经环境变量覆盖（隔离部署场景）：
    MILVUS_HOST=localhost MILVUS_PORT=19530 pytest -m e2e -W ignore
"""

import os

import pytest

pytest.importorskip("pymilvus", reason="requires pymilvus (install extras: milvus)")

from openjiuwen_codesearch.config.index import IndexConfig, MilvusConfig
from openjiuwen_codesearch.retrieval.milvus.store import MilvusStore
from openjiuwen_codesearch.retrieval.tokenizer import generate_char_trigrams, tokenise_code_string

from tests.conftest import run

pytestmark = pytest.mark.e2e

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")


def _record(rid, path, start, end, **fields):
    name = fields["name"]
    text = fields["text"]
    return {
        "id": rid,
        "file_hash": f"hash{rid}",
        "instance_ids": [f"inst{rid}"],
        "repo": "test_repo",
        "commits": ["commit1"],
        "file_path": path,
        "start_line": start,
        "end_line": end,
        "kind": "function",
        "original_name": name,
        "name": tokenise_code_string(name),
        "text": text,
        "text_trigram": generate_char_trigrams(text),
        "calls": [],
    }


@pytest.fixture(scope="module")
def store():
    try:
        store = MilvusStore(
            milvus_cfg=MilvusConfig(host=MILVUS_HOST, port=MILVUS_PORT),
            index_cfg=IndexConfig(),
            collection_name="test_e2e_sparse",
            reset=True,
            strict_trigram=False,  # 测 raw 排序行为
        )
    except Exception as e:
        pytest.skip(f"Milvus not reachable at {MILVUS_HOST}:{MILVUS_PORT}: {e}")

    data = [
        _record(1, "app.py", 1, 5, name="setup_app",
                text="def setup_app():\n    print('Initializing application')\n    return True"),
        _record(2, "utils.py", 10, 15, name="calculate_metrics",
                text="def calculate_metrics(data):\n    # some complex logic here\n    return data.sum()"),
        # 干扰项 1：词都在但分散，无 data.sum()
        _record(3, "eval.py", 20, 25, name="evaluate_logic",
                text="def evaluate_logic(metrics):\n    # calculate the final score\n    return sum(metrics)"),
        # 干扰项 2：trigram 目标词但空格打散
        _record(4, "spaced.py", 30, 35, name="spaced_sum",
                text="def spaced_sum(data):\n    return data . sum ()"),
    ]
    run(store.insert_records(data))
    run(store.flush())
    yield store
    store.collection.drop()


def test_token_bm25_matches_dispersed_keywords(store):
    results = run(store.search("calculate metrics logic", revision="commit1",
                               topk=5, use_trigram=False))
    ids = [r.id for r in results]
    assert 2 in ids and 3 in ids
    assert results[0].id in (2, 3)


def test_trigram_bm25_exact_substring_ranks_first(store):
    results = run(store.search("return data.sum()", revision="commit1",
                               topk=5, use_trigram=True))
    assert results, "no results for trigram query"
    assert results[0].id == 2, "trigram should rank the tightly-packed 'data.sum()' first"
    assert "data.sum()" in results[0].text


def test_revision_filter_isolates(store):
    results = run(store.search("calculate metrics", revision="nonexistent-commit",
                               topk=5, use_trigram=False))
    assert results == []


def test_has_revision_and_repo_map(store):
    assert run(store.has_revision("commit1")) is True
    assert run(store.has_revision("nope")) is False
    repo_map = run(store.get_repo_map("commit1"))
    assert "app.py" in repo_map and "utils.py" in repo_map


def test_fetch_overlapping(store):
    chunks = run(store.fetch_overlapping("commit1", "utils.py", 12, 30))
    assert [c.id for c in chunks] == [2]
