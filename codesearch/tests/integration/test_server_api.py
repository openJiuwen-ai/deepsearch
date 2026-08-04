# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""服务层冒烟测试：健康检查与作业状态（不触发真实检索/索引）。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi", reason="requires the server extra")

from fastapi.testclient import TestClient

from openjiuwen_codesearch.api.models import IndexReport
from openjiuwen_codesearch.domain.result import CodeSearchResult, FinalHit, Termination
from openjiuwen_codesearch.server.main import create_app
from openjiuwen_codesearch.server.routers import api as api_mod


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clear_server_runtime_state():
    """Isolate process-local caches between tests."""
    api_mod.jobs.clear()
    api_mod._retrievers.clear()
    api_mod._indexed_engines.clear()
    yield
    api_mod.jobs.clear()
    api_mod._retrievers.clear()
    api_mod._indexed_engines.clear()


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["version"]


def test_unknown_job_returns_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404


def test_search_requires_fields(client):
    assert client.post("/api/v1/search", json={"query": "x"}).status_code == 422


def test_invalid_engine_rejected(client):
    r = client.post(
        "/api/v1/search",
        json={
            "query": "x",
            "collection": "c",
            "engine": "not-a-real-engine",
        },
    )
    assert r.status_code == 422


class TestIndexPathWhitelist:
    """`/v1/index` 读的是服务端本地目录，必须限制在白名单内。"""

    @staticmethod
    def _client(monkeypatch, roots: str):
        from openjiuwen_codesearch.server.core.config import settings

        monkeypatch.setattr(settings, "index_roots", roots)
        return TestClient(create_app())

    def test_disabled_when_no_roots_configured(self, monkeypatch):
        client = self._client(monkeypatch, "")
        r = client.post("/api/v1/index", json={"repo_path": "/etc", "collection": "x"})
        assert r.status_code == 403
        assert "CODESEARCH_INDEX_ROOTS" in r.json()["detail"]

    def test_rejects_path_outside_roots(self, monkeypatch, tmp_path):
        client = self._client(monkeypatch, str(tmp_path))
        r = client.post("/api/v1/index", json={"repo_path": "/etc", "collection": "x"})
        assert r.status_code == 403

    def test_rejects_traversal_out_of_roots(self, monkeypatch, tmp_path):
        """`..` 必须在 resolve 后被展开，不能借它绕出白名单。"""
        client = self._client(monkeypatch, str(tmp_path / "allowed"))
        (tmp_path / "allowed").mkdir()
        (tmp_path / "secret").mkdir()
        r = client.post(
            "/api/v1/index",
            json={"repo_path": str(tmp_path / "allowed" / ".." / "secret"), "collection": "x"},
        )
        assert r.status_code == 403

    def test_rejects_symlink_escaping_roots(self, monkeypatch, tmp_path):
        """符号链接同样要在 resolve 后判定。"""
        allowed, secret = tmp_path / "allowed", tmp_path / "secret"
        allowed.mkdir()
        secret.mkdir()
        (allowed / "link").symlink_to(secret, target_is_directory=True)
        client = self._client(monkeypatch, str(allowed))
        r = client.post(
            "/api/v1/index", json={"repo_path": str(allowed / "link"), "collection": "x"}
        )
        assert r.status_code == 403

    def test_missing_path_reports_400(self, monkeypatch, tmp_path):
        client = self._client(monkeypatch, str(tmp_path))
        r = client.post(
            "/api/v1/index", json={"repo_path": str(tmp_path / "nope"), "collection": "x"}
        )
        assert r.status_code == 400

    def test_file_instead_of_directory_reports_400(self, monkeypatch, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        client = self._client(monkeypatch, str(tmp_path))
        r = client.post("/api/v1/index", json={"repo_path": str(f), "collection": "x"})
        assert r.status_code == 400


class TestJobTable:
    @staticmethod
    def test_job_table_is_bounded():
        api_mod.jobs.clear()
        for i in range(api_mod.MAX_JOBS + 20):
            api_mod.remember_job(api_mod.JobResponse(job_id=f"j{i}", status="running"))
        assert len(api_mod.jobs) == api_mod.MAX_JOBS
        assert "j0" not in api_mod.jobs          # 最早的被淘汰
        assert f"j{api_mod.MAX_JOBS + 19}" in api_mod.jobs
        api_mod.jobs.clear()


class TestEngineRouting:
    """Optional ``engine`` on index/search; Retropus keeps in-process cache."""

    @staticmethod
    def _client(monkeypatch, roots: str):
        from openjiuwen_codesearch.server.core.config import settings

        monkeypatch.setattr(settings, "index_roots", roots)
        return TestClient(create_app())

    @staticmethod
    def _fake_retriever_cls(instances: list, created_engines: list):
        class FakeRetriever:
            @staticmethod
            def engine_keeps_index_in_process(engine: str) -> bool:
                return engine == "retropus"

            def __init__(self, config, collection_name="local_repo", **_kw):
                self.config = config
                self.collection_name = collection_name
                created_engines.append(config.agent.engine)
                self.index_repository = AsyncMock(
                    return_value=IndexReport(
                        files_total=1, files_new=1, files_reused=0, chunks_inserted=2
                    )
                )
                self.search = AsyncMock(
                    return_value=CodeSearchResult(
                        hits=[
                            FinalHit(
                                id=1,
                                file_path="a.py",
                                start_line=1,
                                end_line=2,
                                text="x = 1",
                            )
                        ],
                        termination=Termination.SUBMITTED,
                        turns=1,
                    )
                )
                self.close = AsyncMock()
                instances.append(self)

            def keeps_index_in_process(self) -> bool:
                return self.engine_keeps_index_in_process(self.config.agent.engine)

        return FakeRetriever

    def test_retropus_index_keeps_cached_retriever(self, monkeypatch, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        client = self._client(monkeypatch, str(tmp_path))
        instances: list = []
        created_engines: list = []
        monkeypatch.setattr(
            api_mod,
            "CodeSearchRetriever",
            self._fake_retriever_cls(instances, created_engines),
        )

        idx = client.post(
            "/api/v1/index",
            json={
                "repo_path": str(repo),
                "collection": "c1",
                "engine": "retropus",
            },
        )
        assert idx.status_code == 202
        job_id = idx.json()["job_id"]
        status = client.get(f"/api/v1/jobs/{job_id}")
        assert status.json()["status"] == "succeeded"
        assert ("c1", "retropus") in api_mod._retrievers
        assert api_mod._indexed_engines["c1"] == "retropus"
        assert created_engines == ["retropus"]
        assert instances[0].close.await_count == 0

        search = client.post(
            "/api/v1/search",
            json={
                "query": "where is x",
                "collection": "c1",
                "engine": "retropus",
            },
        )
        assert search.status_code == 200
        body = search.json()
        assert body["termination"] == Termination.SUBMITTED.value
        assert body["hits"][0]["file_path"] == "a.py"
        # Same cached instance served search
        assert len(instances) == 1
        instances[0].search.assert_awaited_once()

    def test_engine_backend_mismatch_returns_409(self, monkeypatch, tmp_path):
        api_mod._indexed_engines["c1"] = "retropus"
        client = self._client(monkeypatch, str(tmp_path))
        r = client.post(
            "/api/v1/search",
            json={"query": "q", "collection": "c1", "engine": "auto"},
        )
        assert r.status_code == 409
        assert "retropus" in r.json()["detail"]

    def test_default_engine_is_auto_not_retropus(self, monkeypatch, tmp_path):
        """Omit engine → auto; milvus path still closes after index."""
        repo = tmp_path / "repo"
        repo.mkdir()
        client = self._client(monkeypatch, str(tmp_path))
        instances: list = []
        created_engines: list = []
        monkeypatch.setattr(
            api_mod,
            "CodeSearchRetriever",
            self._fake_retriever_cls(instances, created_engines),
        )

        idx = client.post(
            "/api/v1/index",
            json={"repo_path": str(repo), "collection": "c2"},
        )
        assert idx.status_code == 202
        job_id = idx.json()["job_id"]
        assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "succeeded"
        assert created_engines == ["auto"]
        assert api_mod._indexed_engines["c2"] == "auto"
        assert ("c2", "auto") not in api_mod._retrievers  # closed after milvus index
        assert instances[0].close.await_count == 1
