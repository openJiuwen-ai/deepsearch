"""Tests for ``KnowledgeBaseRetriever``."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.retrieval.common.config import RetrievalConfig
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult

from openjiuwen_deepsearch.algorithm.search_tools.retrieval.base_retriever import (
    MilvusBaseRetriever,
    RetrieveConfig,
)
from openjiuwen_deepsearch.algorithm.search_tools.retrieval.retriever import (
    KnowledgeBaseRetriever,
)
from openjiuwen_deepsearch.algorithm.search_tools.retrieval.embedder import (
    AbstractEmbedder,
)


@pytest.fixture(autouse=True)
def _allow_localhost_embedding_url(monkeypatch):
    """Bypass embedding-service SSRF validation; the stub embedder never makes HTTP calls."""
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.search_tools.retrieval.embedder.validate_embedding_service_url",
        lambda url: None,
    )


class _StubEmbedder(AbstractEmbedder):
    """Minimal embedder for constructing ``KnowledgeBaseRetriever`` in tests (Milvus not connected)."""

    def __init__(self):
        super().__init__(
            pretrained_model="stub",
            api_token=bytearray(b"x"),
            api_url="http://localhost",
            model_dim=4,
        )

    def get_query_instruction(self, query: str) -> str:
        return ""

    def encode(self, input_texts: list[str], is_query: bool = False):
        return [[0.0] * self.embed_dim for _ in input_texts]


def _kb_retriever(kb):
    with patch.object(
        KnowledgeBaseRetriever,
        "_create_knowledge_base",
        return_value=kb,
    ):
        return KnowledgeBaseRetriever(
            "localhost",
            "19530",
            "default",
            "",
            _StubEmbedder(),
        )


def _make_kb(results_per_query):
    """``results_per_query`` is a list of lists of ``RetrievalResult`` (one list per retrieve call)."""
    kb = MagicMock()
    kb.retrieve = AsyncMock(side_effect=results_per_query)
    return kb


def test_knowledge_base_retriever_is_milvus_base_and_skips_client():
    kb = _make_kb([[RetrievalResult(text="x", score=1.0, chunk_id="id0")]])
    r = _kb_retriever(kb)
    assert isinstance(r, MilvusBaseRetriever)
    assert r.client is None
    assert r.embedder is not None
    assert r.collection_name == ""


def test_retrieve_single_query_returns_json_and_ids():
    kb = _make_kb(
        [
            [
                RetrievalResult(text="chunk a", score=0.9, chunk_id="c-1", metadata={}),
                RetrievalResult(text="chunk b", score=0.8, doc_id="d-2", chunk_id=None, metadata={}),
            ]
        ]
    )
    retriever = _kb_retriever(kb)
    text, id_list = retriever.retrieve(
        RetrieveConfig(query=["what is X?"], top_k=5)
    )

    payload = json.loads(text)
    assert payload["query"] == "what is X?"
    assert payload["results"] == ["chunk a", "chunk b"]
    assert id_list == ["c-1", "d-2"]

    kb.retrieve.assert_awaited_once()
    assert kb.retrieve.await_args.args[0] == "what is X?"
    assert kb.retrieve.await_args.kwargs["config"] == RetrievalConfig(top_k=5)


def test_retrieve_multiple_queries_joined_with_blank_line():
    kb = _make_kb(
        [
            [RetrievalResult(text="one", score=1.0, chunk_id="1")],
            [RetrievalResult(text="two", score=1.0, chunk_id="2")],
        ]
    )
    retriever = _kb_retriever(kb)
    text, id_list = retriever.retrieve(
        RetrieveConfig(query=["first", "second"], top_k=3)
    )

    parts = text.split("\n\n")
    assert len(parts) == 2
    assert json.loads(parts[0]) == {"query": "first", "results": ["one"]}
    assert json.loads(parts[1]) == {"query": "second", "results": ["two"]}
    assert id_list == ["1", "2"]
    assert kb.retrieve.await_count == 2


def test_top_k_multiplied_by_factor():
    kb = _make_kb([[RetrievalResult(text="t", score=1.0, chunk_id="x")]])
    retriever = _kb_retriever(kb)
    retriever.retrieve(
        RetrieveConfig(query=["q"], top_k=4, top_k_multiply_factor=3)
    )
    assert kb.retrieve.await_args.kwargs["config"].top_k == 12


@pytest.mark.parametrize(
    "result,expected_id",
    [
        (RetrievalResult(text="a", score=0.0, chunk_id="cid"), "cid"),
        (RetrievalResult(text="a", score=0.0, doc_id="did", chunk_id=None), "did"),
        (
            RetrievalResult(
                text="a",
                score=0.0,
                metadata={"id": "mid"},
            ),
            "mid",
        ),
        (
            RetrievalResult(
                text="a",
                score=0.0,
                metadata={"chunk_id": "mc"},
            ),
            "mc",
        ),
        (RetrievalResult(text="a", score=0.0, metadata={}), ""),
    ],
)
def test_result_id_resolution(result, expected_id):
    assert KnowledgeBaseRetriever._result_id(result) == expected_id


def test_save_as_writes_json_with_raw_results(tmp_path):
    out = tmp_path / "out.json"
    kb = _make_kb(
        [
            [
                RetrievalResult(
                    text="body",
                    score=0.5,
                    chunk_id="ch-0",
                    metadata={"k": "v"},
                )
            ]
        ]
    )
    retriever = _kb_retriever(kb)
    retriever.retrieve(
        RetrieveConfig(query=["q"], top_k=2, save_as=str(out))
    )

    assert out.is_file()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["query"] == "q"
    assert saved[0]["results"] == ["body"]
    assert "raw_results" in saved[0]
    assert saved[0]["raw_results"][0]["text"] == "body"
    assert saved[0]["raw_results"][0]["chunk_id"] == "ch-0"
