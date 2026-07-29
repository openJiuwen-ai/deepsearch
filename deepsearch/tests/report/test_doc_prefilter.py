from types import SimpleNamespace

import pytest

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import build_content_dedup_hash
from openjiuwen_deepsearch.algorithm.report.doc_prefilter import (
    build_normalized_content_key,
    build_balanced_doc_batches,
    deduplicate_doc_infos,
    extract_doc_score,
    normalize_url_for_dedup,
    prefilter_doc_infos_for_classification,
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


def test_deduplicate_doc_infos_keeps_high_score_for_same_url_and_same_content():
    low_score = _doc(1, url="https://example.com/a?utm_source=x", content="同一 正文", relevance=0)
    high_score = _doc(2, url="https://www.example.com/a#frag", content="同一　正文", relevance=9)

    result = deduplicate_doc_infos([low_score, high_score])

    assert len(result) == 1
    assert result[0]["title"] == "doc-2"


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


def test_extract_doc_score_prefers_scores_and_normalizes_legacy_fields():
    doc = {
        "scores": {"relevance": 8, "answerability": 6, "authority": 4, "data_density": 2},
        "evaluation_scores": {"relevance": 1, "answerability": 1, "authority": 1, "data_density": 1},
        "task_relevance": "该篇文章的内容与当前任务的相关性得分：1",
    }

    score = extract_doc_score(doc)

    assert score.relevance == 0.8
    assert score.answerability == 0.6
    assert score.authority == 0.4
    assert score.data_density == 0.2

    decimal_ten_point_score = extract_doc_score({"scores": {"relevance": 0.8}})

    assert decimal_ten_point_score.relevance == 0.08


def test_extract_doc_score_ignores_legacy_text_score_fields():
    score = extract_doc_score({
        "source_authority": "该篇文章的信息来源权威性和可信度得分：10",
        "task_relevance": "该篇文章的内容与当前任务的相关性得分：10",
        "information_richness": "该篇文章的信息丰富程度与可答性得分：10",
        "data_density": "该篇文章的数据丰富和密集程度得分：10",
    })

    assert score.relevance == 0
    assert score.answerability == 0
    assert score.authority == 0
    assert score.data_density == 0


def test_prefilter_limits_to_topk_multiplier_and_preserves_step_coverage():
    docs = []
    for idx in range(80):
        doc = _doc(idx, step_idx=idx % 3, relevance=idx % 10)
        doc["plan_idx"] = idx % 2
        doc["step_id"] = str(idx % 3)
        docs.append(doc)

    result = prefilter_doc_infos_for_classification(
        docs,
        result_top_k=10,
        prefilter_multiplier=5,
    )

    assert len(result.doc_infos) == 50
    assert {(doc["plan_idx"], doc["step_idx"]) for doc in result.doc_infos} == {
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 1),
        (1, 2),
    }
    assert result.candidate_limit == 50
    assert result.step_bucket_count == 6


def test_build_balanced_doc_batches_splits_evenly():
    docs = [{"idx": idx} for idx in range(70)]

    batches = build_balanced_doc_batches(docs, 60)

    assert [len(batch) for batch in batches] == [35, 35]


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
