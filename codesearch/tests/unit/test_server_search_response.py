"""HTTP /v1/search 必须把 SDK 结果里的 fail-fast error 透出给调用方。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjiuwen_codesearch.domain.result import CodeSearchResult, Termination
from openjiuwen_codesearch.server.routers import api


def _client(monkeypatch, result: CodeSearchResult) -> TestClient:
    class FakeRetriever:
        async def search(self, query, *, revision, top_k):
            return result

    async def fake_get_retriever(collection, engine):
        return FakeRetriever()

    monkeypatch.setattr(api, "_get_retriever", fake_get_retriever)
    app = FastAPI()
    app.include_router(api.api_router, prefix="/api")
    return TestClient(app)


def test_search_surfaces_index_not_ready_error(monkeypatch):
    result = CodeSearchResult(
        hits=[],
        termination=Termination.INDEX_NOT_READY,
        error="retropus index not ready; call index_repository first",
    )
    response = _client(monkeypatch, result).post(
        "/api/v1/search", json={"query": "q", "collection": "c"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["termination"] == "index_not_ready"
    assert "retropus index not ready" in body["error"]


def test_search_surfaces_llm_error(monkeypatch):
    result = CodeSearchResult(
        hits=[], termination=Termination.LLM_ERROR, error="connection reset by peer"
    )
    response = _client(monkeypatch, result).post(
        "/api/v1/search", json={"query": "q", "collection": "c"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["termination"] == "llm_error"
    assert "connection reset by peer" in body["error"]
