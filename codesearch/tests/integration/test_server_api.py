# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""服务层冒烟测试：健康检查与作业状态（不触发真实检索/索引）。"""

import pytest

pytest.importorskip("fastapi", reason="requires the server extra")

from fastapi.testclient import TestClient

from openjiuwen_codesearch.server.main import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["version"]


def test_unknown_job_returns_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404


def test_search_requires_fields(client):
    assert client.post("/api/v1/search", json={"query": "x"}).status_code == 422


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
        from openjiuwen_codesearch.server.routers import api

        api.jobs.clear()
        for i in range(api.MAX_JOBS + 20):
            api.remember_job(api.JobResponse(job_id=f"j{i}", status="running"))
        assert len(api.jobs) == api.MAX_JOBS
        assert "j0" not in api.jobs          # 最早的被淘汰
        assert f"j{api.MAX_JOBS + 19}" in api.jobs
        api.jobs.clear()
