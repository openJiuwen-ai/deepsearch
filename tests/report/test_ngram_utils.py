"""Tests for n-gram Pool-IDF utilities used in document coverage and redundancy."""

import math

import pytest

from openjiuwen_deepsearch.algorithm.report.ngram_utils import (
    _tokenize,
    compute_pool_idf,
    extract_doc_ngrams,
    extract_ngrams,
    ngram_jaccard_similarity,
    prefilter_by_ngram_coverage,
)


# ---------- _tokenize ----------

def test_tokenize_handles_chinese_text():
    # Chinese text without separators is treated as one token by \w+
    tokens = _tokenize("中国 新能源汽车 出口")
    assert "中国" in tokens
    assert "新能源" not in tokens  # not split mid-word
    assert len(tokens) > 0


def test_tokenize_handles_english_text():
    tokens = _tokenize("electric vehicle export 2024")
    assert "electric" in tokens
    assert "vehicle" in tokens
    assert "2024" in tokens


def test_tokenize_handles_mixed_text():
    tokens = _tokenize("2024年 中国 EV export data")
    assert "export" in tokens
    assert "data" in tokens
    assert len(tokens) >= 3


def test_tokenize_empty_string():
    assert _tokenize("") == []


def test_tokenize_none():
    assert _tokenize(None) == []


# ---------- extract_ngrams ----------

def test_extract_ngrams_unigram_bigram_trigram():
    ngrams = extract_ngrams("a b c d", max_n=3)
    # unigrams
    assert "a" in ngrams
    assert "d" in ngrams
    # bigrams
    assert "a b" in ngrams
    assert "c d" in ngrams
    # trigrams
    assert "a b c" in ngrams
    assert "b c d" in ngrams


def test_extract_ngrams_single_token():
    ngrams = extract_ngrams("hello", max_n=3)
    assert ngrams == {"hello"}


def test_extract_ngrams_empty():
    assert extract_ngrams("") == set()


def test_extract_ngrams_max_n_1():
    ngrams = extract_ngrams("a b c", max_n=1)
    assert ngrams == {"a", "b", "c"}
    assert "a b" not in ngrams


# ---------- extract_doc_ngrams ----------

def test_extract_doc_ngrams_from_title_and_passages():
    doc = {
        "title": "出口数据",
        "key_passages": ["2024年出口增长", "欧洲市场份额"],
    }
    ngrams = extract_doc_ngrams(doc)
    assert len(ngrams) > 0
    assert any("出口" in ng for ng in ngrams)


def test_extract_doc_ngrams_passages_as_dicts():
    doc = {
        "title": "test",
        "key_passages": [{"text": "passage text"}, {"content": "other content"}],
    }
    ngrams = extract_doc_ngrams(doc)
    assert any("passage" in ng for ng in ngrams)
    assert any("other" in ng for ng in ngrams)


def test_extract_doc_ngrams_empty_doc():
    assert extract_doc_ngrams({}) == set()
    assert extract_doc_ngrams({"title": "", "key_passages": []}) == set()


# ---------- compute_pool_idf ----------

def test_compute_pool_idf_rare_ngrams_get_higher_weight():
    # doc1 has "rare", doc2 doesn't
    doc1_ngrams = {"common", "rare"}
    doc2_ngrams = {"common"}

    pool_idf = compute_pool_idf([doc1_ngrams, doc2_ngrams])
    pool_size = 2

    # rare appears in 1 doc, common in 2
    rare_idf = pool_idf["rare"]
    common_idf = pool_idf["common"]

    assert rare_idf > common_idf
    # Verify formula: log((|P|+1)/(df+1))
    assert math.isclose(rare_idf, math.log((pool_size + 1) / (1 + 1)))
    assert math.isclose(common_idf, math.log((pool_size + 1) / (2 + 1)))


def test_compute_pool_idf_empty_list():
    assert compute_pool_idf([]) == {}


def test_compute_pool_idf_single_doc():
    ngrams = {"a", "b"}
    pool_idf = compute_pool_idf([ngrams])
    # df=1 for all, pool_size=1
    expected = math.log((1 + 1) / (1 + 1))
    assert math.isclose(pool_idf["a"], expected)


# ---------- ngram_jaccard_similarity ----------

def test_jaccard_identical_sets():
    assert ngram_jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets():
    assert ngram_jaccard_similarity({"a"}, {"b"}) == 0.0


def test_jaccard_partial_overlap():
    sim = ngram_jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
    assert 0.0 < sim < 1.0
    assert math.isclose(sim, 2.0 / 4.0)


def test_jaccard_empty_sets():
    assert ngram_jaccard_similarity(set(), {"a"}) == 0.0
    assert ngram_jaccard_similarity(set(), set()) == 0.0


# ---------- prefilter_by_ngram_coverage ----------

def test_prefilter_keeps_docs_with_overlap():
    docs = [
        {"title": "export data analysis", "key_passages": ["2024 export volume 1.2 million"]},
        {"title": "weather forecast", "key_passages": ["sunny day"]},
    ]
    rationales = [
        {"id": "r1", "description": "export volume data"},
    ]
    filtered = prefilter_by_ngram_coverage(docs, rationales)
    assert len(filtered) == 1
    assert filtered[0]["title"] == "export data analysis"


def test_prefilter_keeps_all_if_rationales_empty():
    docs = [{"title": "doc1"}, {"title": "doc2"}]
    assert prefilter_by_ngram_coverage(docs, []) == docs


def test_prefilter_keeps_all_if_docs_empty():
    assert prefilter_by_ngram_coverage([], [{"id": "r1", "description": "test"}]) == []


def test_prefilter_keeps_all_when_all_have_overlap():
    # Add a third unrelated doc so common n-grams have non-zero Pool-IDF
    docs = [
        {"title": "export data A", "key_passages": ["alpha detail"]},
        {"title": "export data B", "key_passages": ["beta detail"]},
        {"title": "weather forecast", "key_passages": ["sunny day"]},
    ]
    rationales = [{"id": "r1", "description": "export data"}]
    filtered = prefilter_by_ngram_coverage(docs, rationales)
    titles = [d["title"] for d in filtered]
    assert "export data A" in titles
    assert "export data B" in titles
    assert "weather forecast" not in titles


def test_prefilter_removes_zero_overlap_docs():
    docs = [
        {"title": "new energy vehicle export", "key_passages": ["2024 data"]},
        {"title": "weather forecast", "key_passages": ["sunny"]},
        {"title": "football match", "key_passages": ["goal"]},
    ]
    rationales = [
        {"id": "r1", "description": "new energy vehicle export data"},
    ]
    filtered = prefilter_by_ngram_coverage(docs, rationales)
    assert len(filtered) == 1
    assert filtered[0]["title"] == "new energy vehicle export"


def test_prefilter_multiple_rationales():
    docs = [
        {"title": "export data", "key_passages": []},
        {"title": "destination country analysis", "key_passages": []},
        {"title": "weather forecast", "key_passages": []},
    ]
    rationales = [
        {"id": "r1", "description": "export data"},
        {"id": "r2", "description": "destination country"},
    ]
    filtered = prefilter_by_ngram_coverage(docs, rationales)
    assert len(filtered) == 2
    titles = [d["title"] for d in filtered]
    assert "export data" in titles
    assert "destination country analysis" in titles
