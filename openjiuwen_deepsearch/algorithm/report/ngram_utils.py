# -*- coding: UTF-8 -*-
"""N-gram Pool-IDF utilities for document coverage and redundancy computation.

Borrowed from TREC RAG 2025 submodular evidence selection:
- Use unigram + bigram + trigram n-gram sets
- Pool-IDF: log((|P|+1) / (df+1)) where |P| = total documents, df = documents containing the n-gram
- Rare n-grams (low df) get higher weights, encouraging diversity
"""

import math
import re
from typing import Dict, List, Set, Tuple


def _tokenize(text: str) -> List[str]:
    """Simple tokenization for Chinese + English mixed text."""
    if not text:
        return []
    # Split on non-alphanumeric (works for both Chinese and English)
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    return [t for t in tokens if len(t) > 0]


def extract_ngrams(text: str, max_n: int = 3) -> Set[str]:
    """Extract unigram, bigram, and trigram from text.

    Args:
        text: Input text (key_passages, title, etc.)
        max_n: Maximum n-gram size (default 3 for unigram+bigram+trigram).

    Returns:
        Set of n-gram strings, joined by space for multi-word n-grams.
    """
    tokens = _tokenize(text)
    ngrams: Set[str] = set()
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            ngram = " ".join(tokens[i:i + n])
            ngrams.add(ngram)
    return ngrams


def extract_doc_ngrams(doc_info: dict) -> Set[str]:
    """Extract n-grams from a doc_info's key content fields."""
    parts = []
    title = doc_info.get("title", "")
    if title:
        parts.append(str(title))
    # key_passages is a list of strings or dicts
    key_passages = doc_info.get("key_passages", [])
    if isinstance(key_passages, list):
        for passage in key_passages:
            if isinstance(passage, str):
                parts.append(passage)
            elif isinstance(passage, dict):
                parts.append(str(passage.get("text", passage.get("content", ""))))
    if parts:
        return extract_ngrams(" ".join(parts))
    return set()


def compute_pool_idf(doc_ngrams_list: List[Set[str]]) -> Dict[str, float]:
    """Compute Pool-IDF weights for all n-grams in the pool.

    Pool-IDF(ngram) = log((|P| + 1) / (df(ngram) + 1))
    where |P| = total documents, df = documents containing the n-gram.

    Args:
        doc_ngrams_list: List of n-gram sets, one per document.

    Returns:
        Dict mapping n-gram string to Pool-IDF weight.
    """
    pool_size = len(doc_ngrams_list)
    if pool_size == 0:
        return {}

    # Document frequency for each n-gram
    df: Dict[str, int] = {}
    for ngrams in doc_ngrams_list:
        for ng in ngrams:
            df[ng] = df.get(ng, 0) + 1

    # Pool-IDF: rare n-grams get higher weights
    pool_idf: Dict[str, float] = {}
    for ng, freq in df.items():
        pool_idf[ng] = math.log((pool_size + 1.0) / (freq + 1.0))

    return pool_idf


def ngram_jaccard_similarity(ngrams_a: Set[str], ngrams_b: Set[str]) -> float:
    """Compute Jaccard similarity between two n-gram sets.

    Used for redundancy penalty in greedy selection.
    """
    if not ngrams_a or not ngrams_b:
        return 0.0
    intersection = ngrams_a & ngrams_b
    union = ngrams_a | ngrams_b
    return len(intersection) / len(union) if union else 0.0


def prefilter_by_ngram_coverage(
    doc_infos: List[dict],
    rationales: List[dict],
    min_gain: float = 0.01,
) -> List[dict]:
    """Coarse filter: remove documents with zero n-gram coverage for all rationales.

    This is a fast algorithmic pre-filter (0 LLM calls) before sending
    documents to LLM for coverage matrix evaluation.

    Args:
        doc_infos: List of doc_info dicts.
        rationales: List of rationale dicts with "id" and "description".
        min_gain: Minimum coverage gain threshold.

    Returns:
        Filtered list of doc_infos that have non-zero n-gram overlap
        with at least one rationale.
    """
    if not doc_infos or not rationales:
        return doc_infos

    # Pre-compute n-grams for all documents
    doc_ngrams_list = [extract_doc_ngrams(d) for d in doc_infos]
    pool_idf = compute_pool_idf(doc_ngrams_list)

    # Pre-compute n-grams for all rationales
    rationale_ngrams = {
        r.get("id", ""): extract_ngrams(str(r.get("description", "")))
        for r in rationales
    }

    # Filter: keep documents with non-zero coverage for at least one rationale
    filtered = []
    for idx, doc in enumerate(doc_infos):
        doc_ng = doc_ngrams_list[idx]
        has_coverage = False
        for r_id, r_ng in rationale_ngrams.items():
            overlap = doc_ng & r_ng
            if overlap:
                gain = sum(pool_idf.get(ng, 0.0) for ng in overlap)
                if gain > min_gain:
                    has_coverage = True
                    break
        if has_coverage:
            filtered.append(doc)

    return filtered
