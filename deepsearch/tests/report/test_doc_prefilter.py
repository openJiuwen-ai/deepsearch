from types import SimpleNamespace

import pytest

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import build_content_dedup_hash
from openjiuwen_deepsearch.algorithm.report.doc_prefilter import (
    build_normalized_content_key,
    deduplicate_doc_infos,
    normalize_url_for_dedup,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import _collect_doc_infos


def _doc(
    idx: int,
    *,
    url: str | None = None,
    content: str | None = None,
    step_idx: int = 0,
    relevance: float = 1,
    answerability: float = 1,
    authority: float = 1,
    data_density: float = 1,
) -> dict:
    return {
        "title": f"doc-{idx}",
        "url": url or f"https://example.com/news/{idx}",
        "original_content": content if content is not None else f"content-{idx}",
        "plan_idx": 0,
        "step_idx": step_idx,
        "step_task": f"step-{step_idx}",
        "scores": {
            "relevance": relevance,
            "answerability": answerability,
            "authority": authority,
            "data_density": data_density,
        },
    }


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        ("HTTPS://www.Example.com/a//b/?utm_source=x#frag", "https://example.com/a/b"),
        ("https://m.example.com/a/b/", "https://example.com/a/b"),
        ("https://example.com/a/index.html", "https://example.com/a"),
        ("https://example.com/?b=2&a=1", "https://example.com/?a=1&b=2"),
        ("https://example.com/?UTM_SOURCE=x&id=1", "https://example.com/?id=1"),
    ],
)
def test_normalize_url_for_dedup_handles_common_variants(raw_url, expected):
    assert normalize_url_for_dedup(raw_url) == expected


def test_normalize_url_for_dedup_keeps_meaningful_root_query_params():
    assert normalize_url_for_dedup("https://example.com/?id=1") != normalize_url_for_dedup("https://example.com/?id=2")


def test_deduplicate_doc_infos_keeps_first_seen_for_same_url_and_same_content():
    first = _doc(1, url="https://example.com/a?utm_source=x", content="同一 正文")
    second = _doc(2, url="https://www.example.com/a#frag", content="同一　正文")

    result = deduplicate_doc_infos([first, second])

    assert len(result) == 1
    assert result[0]["title"] == "doc-1"


def test_deduplicate_doc_infos_does_not_add_empty_provenance_to_ordinary_docs():
    first = {"title": "Document", "url": "https://example.com/a"}
    duplicate = {"title": "Document", "url": "https://example.com/a", "extra": "later"}

    result = deduplicate_doc_infos([first, duplicate])

    assert result == [first]
    assert "matched_sources" not in result[0]
    assert "source_ids" not in result[0]


def test_deduplicate_doc_infos_preserves_academic_provenance_from_discarded_duplicate():
    academic = _doc(1, url="https://example.com/paper", content="same content", relevance=1)
    academic.update({
        "academic_source": "pubmed",
        "academic_source_id": "38132429",
        "pmcid": "PMC10740908",
        "evidence_content_type": "full_text",
    })
    tavily = _doc(2, url="https://example.com/paper", content="same content", relevance=9)
    tavily["evidence_content_type"] = "abstract"

    result = deduplicate_doc_infos([academic, tavily])

    assert len(result) == 1
    assert result[0]["title"] == "doc-1"
    assert result[0]["academic_source"] == "pubmed"
    assert result[0]["academic_source_id"] == "38132429"
    assert result[0]["pmcid"] == "PMC10740908"
    assert result[0]["evidence_content_type"] == "full_text"


def test_deduplicate_doc_infos_unions_multi_source_provenance():
    first = _doc(1, url="https://doi.org/10.1000/example", content="same", relevance=9)
    first.update({
        "academic_source": "semantic_scholar",
        "academic_source_id": "S1",
        "matched_sources": ["semantic_scholar"],
        "source_ids": {"semantic_scholar": "S1"},
        "doi": "10.1000/example",
    })
    second = _doc(2, url=first["url"], content="same", relevance=1)
    second.update({
        "academic_source": "pubmed",
        "academic_source_id": "123",
        "matched_sources": ["pubmed", "arxiv"],
        "source_ids": {"pubmed": "123", "arxiv": "2401.00001"},
        "pmid": "123",
        "pmcid": "PMC123",
        "arxiv_id": "2401.00001",
    })

    result = deduplicate_doc_infos([first, second])

    assert result[0]["matched_sources"] == [
        "semantic_scholar", "pubmed", "arxiv"
    ]
    assert result[0]["source_ids"] == {
        "semantic_scholar": "S1", "pubmed": "123", "arxiv": "2401.00001"
    }
    assert result[0]["pmid"] == "123"
    assert result[0]["pmcid"] == "PMC123"
    assert result[0]["arxiv_id"] == "2401.00001"


def test_deduplicate_doc_infos_keeps_same_url_with_different_content():
    first = _doc(1, url="https://example.com/a", content="正文 A")
    second = _doc(2, url="https://www.example.com/a#frag", content="正文 B")

    result = deduplicate_doc_infos([first, second])

    assert [doc["title"] for doc in result] == ["doc-1", "doc-2"]


def test_deduplicate_doc_infos_uses_source_id_as_content_variant_key():
    first = _doc(1, url="https://example.com/a", content="same content")
    second = _doc(2, url="https://www.example.com/a#frag", content="same\ncontent")
    first["source_id"] = "source-a"
    second["source_id"] = "source-b"

    result = deduplicate_doc_infos([first, second])

    assert [doc["source_id"] for doc in result] == ["source-a", "source-b"]


def test_normalized_content_key_reuses_collector_content_hash():
    doc = _doc(1, content="Ａ  B\r\nC")

    assert build_normalized_content_key(doc) == build_content_dedup_hash("A B C")


def test_collect_doc_infos_adds_step_metadata_and_keeps_same_url_different_content():
    plans = [
        SimpleNamespace(
            steps=[
                SimpleNamespace(
                    id="step-a",
                    title="Step A",
                    retrieval_queries=[
                        SimpleNamespace(
                            doc_infos=[
                                {"title": "A", "url": "https://example.com/a", "original_content": "content A"},
                                {
                                    "title": "A",
                                    "url": "https://example.com/a",
                                    "original_content": "content B",
                                    "plan_idx": 99,
                                    "step_idx": 99,
                                    "step_task": "stale step",
                                    "step_id": "stale-id",
                                },
                                {"title": "A", "url": "https://example.com/a", "original_content": "content B"},
                                {
                                    "title": "A",
                                    "url": "https://example.com/a",
                                    "original_content": "content\nB",
                                    "source_id": "source-b-2",
                                },
                            ]
                        )
                    ],
                )
            ]
        )
    ]

    result = _collect_doc_infos(plans)

    assert [doc.get("source_id") for doc in result] == [None, None, "source-b-2"]
    assert [doc["original_content"] for doc in result] == ["content A", "content B", "content\nB"]
    assert all(doc["plan_idx"] == 0 for doc in result)
    assert all(doc["step_idx"] == 0 for doc in result)
    assert all(doc["step_task"] == "Step A" for doc in result)
    assert all(doc["step_id"] == "step-a" for doc in result)


def test_collect_doc_infos_deduplicates_same_source_across_steps():
    shared_doc = {
        "title": "A",
        "url": "https://example.com/a",
        "source_id": "source-a",
        "original_content": "same content",
    }
    plans = [
        SimpleNamespace(
            steps=[
                SimpleNamespace(
                    id="step-a",
                    title="Step A",
                    retrieval_queries=[SimpleNamespace(doc_infos=[shared_doc])],
                ),
                SimpleNamespace(
                    id="step-b",
                    title="Step B",
                    retrieval_queries=[SimpleNamespace(doc_infos=[shared_doc])],
                ),
            ]
        )
    ]

    result = _collect_doc_infos(plans)

    assert len(result) == 1
    assert result[0]["step_id"] == "step-a"
