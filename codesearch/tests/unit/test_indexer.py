# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import os

from openjiuwen_codesearch.config.index import IndexConfig
from openjiuwen_codesearch.indexing.chunkers.base import Chunk
from openjiuwen_codesearch.indexing.chunkers.python import PythonAstChunker
from openjiuwen_codesearch.indexing.indexer import (
    TRUNCATION_MARK,
    build_chunk_records,
    index_repository,
    reconcile_existing,
)

from tests.conftest import run


class FakeStore:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.upserted, self.inserted = [], []
        self.flushed = 0

    async def fetch_records_by_hashes(self, hashes):
        return [r for r in self.existing if r.get("file_hash") in hashes]

    async def upsert_records(self, records):
        self.upserted.extend(records)

    async def insert_records(self, records):
        self.inserted.extend(records)

    async def flush(self):
        self.flushed += 1


class TestReconcileExisting:
    def test_new_hash_goes_to_embed_list(self):
        upserts, to_embed = reconcile_existing([], ["h1"], "rev1", "inst1")
        assert upserts == [] and to_embed == ["h1"]

    def test_existing_gets_revision_and_instance_appended(self):
        record = {"file_hash": "h1", "commits": ["old"], "instance_ids": ["i0"]}
        upserts, to_embed = reconcile_existing([record], ["h1"], "rev1", "inst1")
        assert to_embed == []
        assert upserts[0]["commits"] == ["old", "rev1"]
        assert upserts[0]["instance_ids"] == ["i0", "inst1"]

    def test_already_tagged_record_not_upserted(self):
        record = {"file_hash": "h1", "commits": ["rev1"], "instance_ids": ["inst1"]}
        upserts, to_embed = reconcile_existing([record], ["h1"], "rev1", "inst1")
        assert upserts == [] and to_embed == []


class TestBuildChunkRecords:
    CFG = IndexConfig()

    def _one(self, **overrides):
        chunk = Chunk(
            text=overrides.pop("text", "def f():\n    pass"),
            start_line=3,
            end_line=4,
            kind="function_definition",
            name=overrides.pop("name", "MyFunc"),
            calls=overrides.pop("calls", ["helperCall"]),
        )
        return build_chunk_records(
            [chunk], "pkg/a.py", "hash1", "inst", "repo", "rev", self.CFG
        )[0]

    def test_header_injected_with_line_span(self):
        record = self._one()
        assert record["text"].startswith("File: pkg/a.py (L3-L4)\n\n")

    def test_name_tokenized_original_kept(self):
        record = self._one()
        assert record["name"] == "my func"
        assert record["original_name"] == "MyFunc"

    def test_deterministic_id(self):
        assert self._one()["id"] == self._one()["id"]

    def test_oversized_text_truncated_with_mark(self):
        cfg = IndexConfig(max_char_limit=200)
        chunk = Chunk(text="x" * 1000, start_line=1, end_line=1)
        record = build_chunk_records([chunk], "a.py", "h", "i", "r", "v", cfg)[0]
        assert record["text"].endswith(TRUNCATION_MARK)
        assert len(record["text"].encode()) <= 200 + len(TRUNCATION_MARK)

    def test_trigram_field_populated(self):
        assert self._one()["text_trigram"]

    def test_trigram_disabled_writes_empty_field(self):
        cfg = IndexConfig(enable_trigram=False)
        chunk = Chunk(text="def f():\n    pass", start_line=1, end_line=2)
        record = build_chunk_records([chunk], "a.py", "h", "i", "r", "v", cfg)[0]
        assert record["text_trigram"] == ""


class TestIndexRepository:
    def _make_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "mod.py").write_text("def f():\n    return 1\n")
        return str(repo)

    def test_new_repo_inserts_chunks(self, tmp_path):
        store = FakeStore()
        report = run(
            index_repository(
                store, PythonAstChunker(), self._make_repo(tmp_path),
                "inst", "repo", "rev1", IndexConfig(),
            )
        )
        assert report.files_new == 1 and report.chunks_inserted >= 1
        assert store.inserted and store.flushed == 1
        assert store.inserted[0]["commits"] == ["rev1"]

    def test_reindex_same_content_new_revision_upserts(self, tmp_path):
        repo_dir = self._make_repo(tmp_path)
        store = FakeStore()
        run(index_repository(store, PythonAstChunker(), repo_dir,
                             "inst", "repo", "rev1", IndexConfig()))
        # 模拟已入库：把插入的记录作为存量，换 revision 重建
        store2 = FakeStore(existing=store.inserted)
        report = run(index_repository(store2, PythonAstChunker(), repo_dir,
                                      "inst2", "repo", "rev2", IndexConfig()))
        # 旧 wrapper 丢 upsert 的 bug（notes #13）的回归测试：必须发生 upsert
        assert report.files_new == 0 and report.files_reused == 1
        assert store2.upserted, "existing files must be re-tagged via upsert"
        assert "rev2" in store2.upserted[0]["commits"]
        assert store2.inserted == []
