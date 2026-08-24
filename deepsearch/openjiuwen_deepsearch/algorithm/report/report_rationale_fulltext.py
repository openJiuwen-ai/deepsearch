# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Full-text selection and writing input builder for report writing.

Selects top-frequency URLs from rationale-selected passages, uses their
``original_content`` from the info_collector phase as full-text evidence,
and builds unified writing inputs combining full-text evidence with
remaining passage-level evidence.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from openjiuwen_deepsearch.algorithm.report.compact_doc_info import (
    build_structured_evidence_guide,
    format_key_passage_block,
    normalize_key_passages,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import safe_float


logger = logging.getLogger(__name__)


def get_required_document_content(document: dict) -> str:
    """Return the best available content for a required report document."""
    key_passages = normalize_key_passages(document.get("key_passages"))
    return str(
        document.get("original_content", "")
        or document.get("content", "")
        or "\n".join(key_passages)
    ).strip()


@dataclass
class FullTextEvidence:
    """Evidence from a full-text article selected by URL frequency.

    Carries aggregated reliability (max) and data_density (max) from
    passage-level scores for visualization selection.
    """

    url: str = ""
    doc_title: str = ""
    doc_time: str = ""
    original_content: str = ""
    key_passages: list[str] = field(default_factory=list)  # Always empty — retained for backward compat
    reliability: float = 0.0
    data_density: float = 0.0
    coverage_scores: dict = field(default_factory=dict)  # Always empty — fulltext has no per-rationale coverage scores
    citation_index: int = 0
    fetch_success: bool = False  # Always True — retained for backward compat


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _text_ngrams(text: str, n: int = 3) -> frozenset[str]:
    """Extract n-grams from text: character-level for CJK, word-level for Latin.

    For CJK text: character-level n-grams (default 3-gram).
    For Latin text: punctuation-stripped word-level uni+bi+tri-grams.
    The 0.70 similarity threshold was calibrated for CJK 3-grams; Latin
    multi-grams reduce false-positive dedup of same-domain English passages.
    """
    text = text.strip().lower()
    if not text:
        return frozenset()
    grams: set[str] = set()
    # CJK: character-level n-gram (scan each whitespace-split segment)
    for segment in text.split():
        if _CJK_RE.search(segment):
            for i in range(len(segment) - n + 1):
                grams.add(segment[i:i + n])
    # Latin: extract clean alphanumeric tokens, generate uni+bi+tri-grams
    latin_tokens = _LATIN_TOKEN_RE.findall(text)
    for token in latin_tokens:
        grams.add(token)
    for i in range(len(latin_tokens) - 1):
        grams.add(f"{latin_tokens[i]} {latin_tokens[i+1]}")
    for i in range(len(latin_tokens) - 2):
        grams.add(f"{latin_tokens[i]} {latin_tokens[i+1]} {latin_tokens[i+2]}")
    return frozenset(grams)


def filter_passages_by_coverage(
    selected_passages: list[dict],
    rationales: list[dict],
    coverage_result: dict | None,
    threshold: float = 0.15,
) -> list[dict]:
    """Layer 1: remove passages whose max coverage across all rationales < threshold.

    Args:
        selected_passages: matrix-selected passage variants.
        rationales: rationale list.
        coverage_result: coverage matrix evaluation result (may be ``None``).
        threshold: minimum coverage score to keep a passage.

    Returns:
        Filtered passage list. If coverage data is unavailable, returns the
        original list unchanged.
    """
    if not coverage_result or not rationales:
        return selected_passages

    coverage_matrix: dict = coverage_result.get("coverage_matrix", {}) or {}
    if not coverage_matrix:
        return selected_passages

    filtered_passages: list = coverage_result.get("filtered_passages", []) or []
    for fp_idx, fp in enumerate(filtered_passages):
        if isinstance(fp, dict):
            fp["_passage_key"] = f"passage_{fp_idx}"

    if selected_passages and not any(p.get("_passage_key") for p in selected_passages if isinstance(p, dict)):
        logger.warning(
            "[report_rationale_fulltext] coverage filter: no passage in selected_passages "
            "matches filtered_passages by passage_key; key-based filtering will be skipped"
        )

    rationale_ids = [
        str(r.get("id", "") or "")
        for r in rationales
        if isinstance(r, dict) and r.get("id")
    ]

    kept: list[dict] = []
    removed_count = 0
    # Track all scored passages so we can fall back when threshold filters
    # everything out (e.g. LLM returned all-zero or missing scores).
    scored_passages: list[tuple[float, dict]] = []
    for passage in selected_passages:
        if not isinstance(passage, dict):
            continue
        passage_key = passage.get("_passage_key", "")
        if not passage_key:
            kept.append(passage)
            continue
        passage_cov = coverage_matrix.get(passage_key, {})
        if not isinstance(passage_cov, dict):
            passage_cov = {}
        max_score = 0.0
        for rid in rationale_ids:
            score = float(passage_cov.get(rid, 0.0) or 0.0)
            if score > max_score:
                max_score = score
        scored_passages.append((max_score, passage))
        if max_score >= threshold:
            kept.append(passage)
        else:
            removed_count += 1

    # Fallback: if threshold filtering removed ALL scored passages, keep the
    # top-5 by max coverage so downstream pipeline has material to work with.
    # This handles the common case where LLM scores are uniformly low/zero.
    if not kept and scored_passages:
        scored_passages.sort(key=lambda x: x[0], reverse=True)
        fallback_count = min(5, len(scored_passages))
        kept = [p for _, p in scored_passages[:fallback_count]]
        removed_count -= fallback_count
        logger.warning(
            "[report_rationale_fulltext] coverage filter: all %s passages below "
            "threshold %.2f, keeping top-%s by max coverage as fallback",
            len(scored_passages), threshold, fallback_count,
        )

    logger.info(
        "[report_rationale_fulltext] coverage filter: before=%s after=%s removed=%s (threshold=%.2f)",
        len(selected_passages), len(kept), removed_count, threshold,
    )
    return kept


def dedup_passages_by_rationale(
    selected_passages: list[dict],
    rationales: list[dict],
    coverage_result: dict | None,
    similarity_threshold: float = 0.70,
    top_k_per_rationale: int | None = None,
) -> list[dict]:
    """Layer 2: cross-source dedup per rationale.

    For each rationale, passages with text similarity > ``similarity_threshold``
    are considered duplicates; only the higher-scored one is kept.

    Args:
        selected_passages: passages surviving Layer 1.
        rationales: rationale list.
        coverage_result: coverage matrix evaluation result.
        similarity_threshold: n-gram Jaccard similarity above which two passages
            are considered duplicates.
        top_k_per_rationale: maximum passages to keep per rationale.
            If ``None``, no limit is applied (only dedup).

    Returns:
        Deduplicated passage list.
    """
    if not selected_passages or not rationales:
        return selected_passages

    coverage_matrix: dict = {}
    if isinstance(coverage_result, dict):
        coverage_matrix = coverage_result.get("coverage_matrix", {}) or {}
    filtered_passages: list = (
        coverage_result.get("filtered_passages", []) if isinstance(coverage_result, dict) else []
    ) or []
    for fp_idx, fp in enumerate(filtered_passages):
        if isinstance(fp, dict):
            fp["_passage_key"] = f"passage_{fp_idx}"

    rationale_ids = [
        str(r.get("id", "") or "")
        for r in rationales
        if isinstance(r, dict) and r.get("id")
    ]

    def _get_score(passage: dict, rid: str) -> float:
        pkey = passage.get("_passage_key", "")
        if not pkey:
            return 0.0
        cov = coverage_matrix.get(pkey, {})
        if not isinstance(cov, dict):
            return 0.0
        try:
            return float(cov.get(rid, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _get_text(passage: dict) -> str:
        return str(
            passage.get("passage_text", "")
            or passage.get("original_content", "")
            or ""
        )

    ids_kept: set[int] = set()

    for rid in rationale_ids:
        scored: list[tuple[float, dict]] = []
        for passage in selected_passages:
            if not isinstance(passage, dict):
                continue
            score = _get_score(passage, rid)
            if score > 0:
                scored.append((score, passage))

        scored.sort(key=lambda entry: entry[0], reverse=True)

        kept_for_rid: list[tuple[frozenset, dict]] = []
        for score, passage in scored:
            # scored 已按分数降序，且 kept_for_rid 只增不减：一旦达到
            # top_k 上限，剩余（分数更低）的段落不会再被保留，直接跳出。
            if top_k_per_rationale is not None and len(kept_for_rid) >= top_k_per_rationale:
                break
            grams_new = _text_ngrams(_get_text(passage))
            is_dup = False
            for kept_grams, _ in kept_for_rid:
                if not grams_new or not kept_grams:
                    continue
                intersection = len(grams_new & kept_grams)
                union = len(grams_new | kept_grams)
                sim = intersection / union if union > 0 else 0.0
                if sim >= similarity_threshold:
                    is_dup = True
                    break
            if not is_dup:
                kept_for_rid.append((grams_new, passage))
                ids_kept.add(id(passage))

    # Fallback: when no passage is kept (e.g. all coverage batches failed,
    # coverage_matrix is empty), return the original passages as-is.
    if not ids_kept:
        result = [p for p in selected_passages if isinstance(p, dict)]
    else:
        result = [p for p in selected_passages if id(p) in ids_kept]

    # Global near-duplicate scan: near-duplicate passages that each won in
    # a different rationale can survive per-rationale dedup. Since each
    # passage carries scores for ALL rationales, removing a near-duplicate
    # does not lose coverage information — the survivor's scores cover all
    # rationales. Keep the passage with higher total coverage.
    if len(result) > 1:
        def _total_coverage(passage: dict) -> float:
            pkey = passage.get("_passage_key", "")
            cov = coverage_matrix.get(pkey, {})
            if not isinstance(cov, dict):
                return 0.0
            return sum(
                safe_float(v) for v in cov.values()
            )

        global_kept: list[dict] = []
        global_grams: list[frozenset] = []
        for passage in result:
            grams_new = _text_ngrams(_get_text(passage))
            is_dup = False
            for gi, kept_g in enumerate(global_grams):
                if grams_new and kept_g:
                    intersection = len(grams_new & kept_g)
                    union = len(grams_new | kept_g)
                    if union > 0 and intersection / union >= similarity_threshold:
                        # Near-duplicate found: keep the one with higher total coverage
                        if _total_coverage(passage) > _total_coverage(global_kept[gi]):
                            global_kept[gi] = passage
                            global_grams[gi] = grams_new
                        is_dup = True
                        break
            if not is_dup:
                global_kept.append(passage)
                global_grams.append(grams_new)
        result = global_kept

    logger.info(
        "[report_rationale_fulltext] dedup filter: before=%s after=%s removed=%s "
        "(sim_threshold=%.2f, top_k=%s)",
        len(selected_passages), len(result), len(selected_passages) - len(result),
        similarity_threshold, top_k_per_rationale,
    )
    return result


def select_top_urls_by_frequency(
    selected_passages: list[dict], top_n: int = 10
) -> list[dict]:
    """Count URL frequency across all selected passages and return the top_n URLs.

    Args:
        selected_passages: matrix-selected passage variants.
        top_n: maximum number of URLs to return.

    Returns:
        List of dicts ``{"url", "doc_title", "doc_time", "frequency"}`` for the
        most frequently cited URLs. ``doc_title``/``doc_time`` come from the
        first passage carrying that URL. Empty URLs are skipped.
    """
    frequency: dict[str, int] = {}
    meta: dict[str, dict[str, str]] = {}
    for passage in selected_passages or []:
        if not isinstance(passage, dict):
            continue
        url = str(passage.get("doc_url") or "").strip()
        if not url:
            continue
        frequency[url] = frequency.get(url, 0) + 1
        if url not in meta:
            meta[url] = {
                "url": url,
                "doc_title": str(passage.get("doc_title", "") or ""),
                "doc_time": str(passage.get("doc_time", "") or ""),
            }

    ranked = sorted(frequency.items(), key=lambda item: item[1], reverse=True)
    limit = max(0, int(top_n))
    result: list[dict] = []
    for url, freq in ranked[:limit]:
        info = dict(meta.get(url, {"url": url, "doc_title": "", "doc_time": ""}))
        info["frequency"] = freq
        result.append(info)
    return result


def split_passages_by_url(
    selected_passages: list[dict],
    fetched_urls_set: set[str],
) -> tuple[list[dict], list[dict]]:
    """Split selected passages into (removed, remaining) by fetched URLs.

    Args:
        selected_passages: matrix-selected passage variants.
        fetched_urls_set: URLs that were successfully fetched and compressed.

    Returns:
        ``(removed_passages, remaining_passages)``. Passages whose ``doc_url``
        is in ``fetched_urls_set`` are removed; the rest remain.
    """
    removed: list[dict] = []
    remaining: list[dict] = []
    for passage in selected_passages or []:
        if not isinstance(passage, dict):
            continue
        url = str(passage.get("doc_url") or "").strip()
        if url and url in fetched_urls_set:
            removed.append(passage)
        else:
            remaining.append(passage)
    return removed, remaining


def build_classified_content(
    fulltext_evidences: list[FullTextEvidence],
    remaining_passages: list[dict],
) -> list[dict]:
    """Build the unified classified content list for the writing prompt.

    Full-text items come first (indices 1..N); remaining passages follow
    (indices N+1..) without ``query``. Full-text items carry no per-rationale
    scores (coverage_scores is always empty); only top-level ``reliability``
    and ``data_density`` are set.

    Args:
        fulltext_evidences: successfully selected full-text evidences.
        remaining_passages: passage-level evidences not replaced by full text.

    Returns:
        Unified list of dicts with ``index``, ``doc_time``, ``title``,
        ``passage_text`` (passage items only), ``original_content``,
        ``is_fulltext``, ``url`` and ``content_time``. Top-level
        ``data_density``/``reliability`` are document-level values assessed
        once per document/passage, not per rationale, so visualization
        selection reads them directly. ``content_time`` is the passage's
        fact-level time window (``{start, end}``) under a ``content_date``
        temporal scope and ``None`` otherwise (including full-text items).
    """
    classified: list[dict] = []
    fulltext_count = len(fulltext_evidences or [])
    for idx, evidence in enumerate(fulltext_evidences or []):
        if not isinstance(evidence, FullTextEvidence):
            continue
        classified.append(
            {
                "index": idx + 1,
                "doc_time": evidence.doc_time,
                "title": evidence.doc_title,
                "original_content": evidence.original_content,
                "scores": evidence.coverage_scores,
                "reliability": evidence.reliability,
                "data_density": evidence.data_density,
                "is_fulltext": True,
                "url": evidence.url,
                # Full-text items describe an entire document; there is no
                # single fact-level content_time, so leave it unset (None).
                "content_time": None,
            }
        )
    for pos, passage in enumerate(remaining_passages or []):
        if not isinstance(passage, dict):
            continue
        passage_scores = passage.get("scores", {})
        passage_reliability = safe_float(passage.get("reliability"))
        passage_data_density = safe_float(passage.get("data_density"))
        classified.append(
            {
                "index": fulltext_count + pos + 1,
                "doc_time": str(passage.get("doc_time", "") or ""),
                "title": str(passage.get("doc_title", "") or ""),
                "passage_text": str(passage.get("passage_text", "") or ""),
                "original_content": str(passage.get("original_content", "") or ""),
                "scores": passage_scores,
                "reliability": float(passage_reliability),
                "data_density": float(passage_data_density),
                "is_fulltext": False,
                "url": str(passage.get("doc_url", "") or ""),
                # Propagate the passage's fact-level time (content_date scope);
                # None for source_date scope / full-text fallback.
                "content_time": passage.get("content_time"),
            }
        )
    return classified


def build_core_content_list(
    fulltext_evidences: list[FullTextEvidence],
    remaining_passages: list[dict],
) -> list[str]:
    """Build key-passage blocks for full-text evidences and remaining passages.

    Args:
        fulltext_evidences: successfully compressed full-text evidences.
        remaining_passages: passage-level evidences not replaced by full text.

    Returns:
        List of formatted key-passage block strings, indexed 1..N for full-text
        evidences then N+1.. for remaining passages.
    """
    blocks: list[str] = []
    idx = 1
    for evidence in fulltext_evidences or []:
        if not isinstance(evidence, FullTextEvidence):
            continue
        # Truncate full-text content for the outline prompt; the full
        # content is available separately in classified_content for the
        # writing prompt.  Outline only needs topic-level context.
        outline_text = evidence.original_content
        if len(outline_text) > 500:
            outline_text = outline_text[:500] + "..."
        blocks.append(
            format_key_passage_block(
                {"key_passages": evidence.key_passages, "passage_text": outline_text},
                idx,
            )
        )
        idx += 1
    for passage in remaining_passages or []:
        if not isinstance(passage, dict):
            continue
        blocks.append(format_key_passage_block(passage, idx))
        idx += 1
    return blocks


def _escape_markdown_text(value: object) -> str:
    """Escape markdown special characters in reference text."""
    text = str(value or "")
    text = re.sub(r"[\r\n\t]+", " ", text)
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>])", r"\\\1", text)


def _format_reference_link(title_value: object, url_value: object) -> str:
    """Format a markdown reference link, escaping title and URL."""
    title = _escape_markdown_text(title_value)
    url = str(url_value or "").strip()
    if not url or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        escaped_url = _escape_markdown_text(url)
        if title and escaped_url:
            return f"{title} ({escaped_url})"
        return title or escaped_url

    parsed_url = urlparse(url)
    scheme = parsed_url.scheme.lower()
    is_allowed_url = scheme in {"http", "https", "localdataset"} and (
        bool(parsed_url.netloc) if scheme in {"http", "https"} else bool(parsed_url.netloc or parsed_url.path)
    )
    if not is_allowed_url:
        escaped_url = _escape_markdown_text(url)
        if title and escaped_url:
            return f"{title} ({escaped_url})"
        return title or escaped_url

    escaped_url = url.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"[{title}]({escaped_url})"


def build_references(
    fulltext_evidences: list[FullTextEvidence],
    remaining_passages: list[dict],
) -> list[str]:
    """Build a reference list that mirrors classified_content indices.

    Each item in classified_content (full-text first, then passages) gets
    its own reference entry, maintaining a 1:1 correspondence between
    ``[citation:X]`` in the report text and reference ``[X]``. Deduplication
    of repeated URLs is handled downstream by ``_deduplicate_and_renumber_ref``.

    Args:
        fulltext_evidences: compressed full-text evidences.
        remaining_passages: passage-level evidences.

    Returns:
        List of formatted ``[escaped_title](escaped_url)`` strings, one per
        evidence item, in the same order as classified_content.
    """
    references: list[str] = []
    for evidence in fulltext_evidences or []:
        if not isinstance(evidence, FullTextEvidence):
            continue
        url = str(evidence.url or "").strip()
        if url:
            references.append(_format_reference_link(evidence.doc_title, url))
    for passage in remaining_passages or []:
        if not isinstance(passage, dict):
            continue
        url = str(passage.get("doc_url") or "").strip()
        if url:
            references.append(
                _format_reference_link(passage.get("doc_title", ""), url)
            )
    return references


def enrich_fulltext_for_section(
    passages: dict[str, list[dict] | None],
    context: dict,
    section_idx: int,
    top_n: int = 10,
) -> dict:
    """Select top-frequency URLs and build unified writing inputs.

    After L1/L2 passage filtering, counts URL frequency across all filtered
    passages, picks the top-N URLs, and uses their ``original_content`` from
    the info_collector phase as full-text evidence (no fetching, no LLM
    compression, no coverage assessment). Passages from those URLs are
    removed; remaining passages keep their passage-level
    ``reliability``/``data_density``. Builds classified content, core content,
    references, and a passage-only structured evidence guide (top-3 per
    rationale by coverage). Full-text items carry no scores.

    Args:
        passages: dict with ``"selected"`` (matrix-selected passage variants)
            and ``"raw"`` (original doc_infos from info_collector phase) keys.
            Raw passages' ``original_content`` is used as full-text content.
        context: dict with ``"rationales"`` (rationale list for L1/L2
            filtering and guide generation) and ``"coverage_result"``
            (coverage matrix evaluation result, may be ``None``).
        section_idx: current section index for logging.
        top_n: maximum number of top-frequency URLs to select.

    Returns:
        Dict with keys ``classified_content``, ``sub_section_core_content``,
        ``sub_section_references``, ``structured_evidence_guide`` (passage-only),
        ``fulltext_count`` and ``remaining_count``.
    """
    selected_passages = passages.get("selected") or []
    raw_passages = passages.get("raw") or []
    rationales = context.get("rationales") or []
    coverage_result = context.get("coverage_result")
    required_documents = context.get("required_documents") or []

    # Layer 1: coverage filter — drop passages with max coverage < 0.15.
    filtered_passages = filter_passages_by_coverage(
        selected_passages, rationales, coverage_result, threshold=0.15,
    )
    # Layer 2: cross-source dedup + top-15 per rationale (similarity > 0.70 removed).
    filtered_passages = dedup_passages_by_rationale(
        filtered_passages, rationales, coverage_result,
        similarity_threshold=0.70, top_k_per_rationale=15,
    )

    top_urls = select_top_urls_by_frequency(filtered_passages, top_n=top_n)

    # Build URL → original_content lookup from raw_passages collected
    # during the info_collector phase.
    existing_content_by_url: dict[str, str] = {}
    for doc in raw_passages or []:
        if not isinstance(doc, dict):
            continue
        url = str(doc.get("url", "") or doc.get("doc_url", "") or "").strip()
        content = str(doc.get("original_content", "") or "").strip()
        if url and content:
            existing_content_by_url.setdefault(url, content)

    # Aggregate data_density per URL from filtered_passages (max value).
    # Full-text documents inherit the highest data_density from their passages,
    # enabling visualization selection (which requires data_density >= 0.8).
    url_data_density: dict[str, float] = {}
    url_reliability: dict[str, float] = {}
    for passage in filtered_passages or []:
        if not isinstance(passage, dict):
            continue
        url = str(passage.get("doc_url") or "").strip()
        if not url:
            continue
        dd = safe_float(passage.get("data_density"))
        if dd >= 0:
            url_data_density[url] = max(url_data_density.get(url, 0.0), dd)
        rel = safe_float(passage.get("reliability"))
        if rel >= 0:
            url_reliability[url] = max(url_reliability.get(url, 0.0), rel)

    # Build FullTextEvidence directly from existing content (no fetch/compress).
    fulltext_evidences: list[FullTextEvidence] = []
    required_urls: set[str] = set()
    for doc in required_documents:
        if not isinstance(doc, dict):
            continue
        url = str(doc.get("url", "") or doc.get("doc_url", "") or "").strip()
        content = get_required_document_content(doc)
        if not url or not content or url in required_urls:
            continue
        required_urls.add(url)
        fulltext_evidences.append(FullTextEvidence(
            url=url,
            doc_title=str(doc.get("title", "") or doc.get("doc_title", "") or ""),
            doc_time=str(doc.get("time", "") or doc.get("doc_time", "") or ""),
            original_content=content,
            citation_index=len(fulltext_evidences) + 1,
            fetch_success=True,
        ))
    required_target_citation_indexes = [
        evidence.citation_index for evidence in fulltext_evidences
    ]
    for url_info in top_urls:
        url = str(url_info.get("url", "") or "")
        if not url or url in required_urls:
            continue
        content = existing_content_by_url.get(url, "")
        if not content:
            continue
        fulltext_evidences.append(FullTextEvidence(
            url=url,
            doc_title=str(url_info.get("doc_title", "") or ""),
            doc_time=str(url_info.get("doc_time", "") or ""),
            original_content=content,
            citation_index=len(fulltext_evidences) + 1,
            fetch_success=True,
            reliability=url_reliability.get(url, 0.0),
            data_density=url_data_density.get(url, 0.0),
        ))

    fetched_urls_set = {ev.url for ev in fulltext_evidences}
    removed_passages, remaining_passages = split_passages_by_url(
        filtered_passages, fetched_urls_set
    )

    fulltext_index_by_url = {
        evidence.url: evidence.citation_index for evidence in fulltext_evidences
    }
    indexed_removed_passages = []
    for passage in removed_passages:
        indexed_passage = dict(passage)
        indexed_passage["index"] = fulltext_index_by_url.get(
            str(passage.get("doc_url") or "").strip(), ""
        )
        indexed_removed_passages.append(indexed_passage)
    removed_passages = indexed_removed_passages

    # Index remaining passages and drop the internal ``query`` field.
    fulltext_count = len(fulltext_evidences)
    for i, passage in enumerate(remaining_passages):
        passage = dict(passage)  # shallow copy to avoid mutating shared dict
        passage["index"] = fulltext_count + i + 1
        passage.pop("query", None)
        remaining_passages[i] = passage

    classified_content = build_classified_content(
        fulltext_evidences, remaining_passages
    )
    sub_section_core_content = build_core_content_list(
        fulltext_evidences, remaining_passages
    )
    sub_section_references = build_references(
        fulltext_evidences, remaining_passages
    )

    # Map remaining and removed passages back to coverage_result passage keys
    # using the _passage_key field (set in filter_passages_by_coverage), so
    # build_structured_evidence_guide can look up coverage_matrix. Removed
    # passages (those whose URLs were promoted to fulltext) are included so
    # their coverage scores are accounted for in the guide; otherwise a
    # rationale whose best evidence was promoted to fulltext would show
    # max_coverage=0 and be misleadingly marked "uncovered".
    remaining_passage_keys: list[str] = []
    for passage in remaining_passages:
        remaining_passage_keys.append(passage.get("_passage_key", ""))
    removed_passage_keys: list[str] = []
    for passage in removed_passages:
        removed_passage_keys.append(passage.get("_passage_key", ""))

    structured_evidence_guide = build_structured_evidence_guide(
        selected_passages=remaining_passages + removed_passages,
        rationales=rationales,
        coverage_result=coverage_result or {},
        selected_passage_keys=remaining_passage_keys + removed_passage_keys,
    )

    logger.info(
        "[report_rationale_fulltext] section_idx=%s fulltext_count=%s "
        "remaining_count=%s removed_count=%s",
        section_idx,
        fulltext_count,
        len(remaining_passages),
        len(selected_passages or []) - len(remaining_passages),
    )

    return {
        "classified_content": classified_content,
        "sub_section_core_content": sub_section_core_content,
        "sub_section_references": sub_section_references,
        "structured_evidence_guide": structured_evidence_guide,
        "fulltext_count": fulltext_count,
        "remaining_count": len(remaining_passages),
        "fulltext_evidences": fulltext_evidences,
        "remaining_passages": remaining_passages,
        "remaining_passage_keys": remaining_passage_keys,
        "required_target_citation_indexes": required_target_citation_indexes,
    }
