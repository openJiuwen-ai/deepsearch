"""Deterministic de-duplication for results returned by scholarly providers."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SCHOLARLY_SOURCES = {"arxiv", "pubmed", "semantic_scholar"}
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _source(record: dict[str, Any]) -> str:
    return _text(
        record.get("academic_source") or record.get("retrieval_source") or record.get("source")
    ).casefold()


def _provenance(record: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    sources: list[str] = []
    matched_sources = record.get("matched_sources")
    if isinstance(matched_sources, list):
        for value in matched_sources:
            source = _text(value).casefold()
            if source and source not in sources:
                sources.append(source)

    source = _source(record)
    if source and source not in sources:
        sources.append(source)

    source_ids: dict[str, str] = {}
    historical_ids = record.get("source_ids")
    if isinstance(historical_ids, dict):
        for name, value in historical_ids.items():
            normalized_name = _text(name).casefold()
            normalized_value = _text(value)
            if normalized_name and normalized_value:
                source_ids[normalized_name] = normalized_value

    source_id = _text(record.get("academic_source_id") or record.get("source_id"))
    if source and source_id:
        source_ids[source] = source_id
    return sources, source_ids


def _is_scholarly_record(record: dict[str, Any]) -> bool:
    sources, source_ids = _provenance(record)
    return bool(SCHOLARLY_SOURCES.intersection(sources) or SCHOLARLY_SOURCES.intersection(source_ids))


def _normalize_doi(value: Any) -> str:
    return _DOI_PREFIX.sub("", _text(value)).rstrip("./").casefold()


def _normalize_prefixed_id(value: Any, prefix: str) -> str:
    text = _text(value).casefold()
    return re.sub(rf"^{re.escape(prefix.casefold())}:?\s*", "", text)


def _year(record: dict[str, Any]) -> str:
    match = re.search(r"\b(?:19|20)\d{2}\b", _text(record.get("published") or record.get("year")))
    return match.group(0) if match else ""


def _title_key(value: Any) -> str:
    return _NON_WORD.sub(" ", _text(value).casefold()).strip()


def _author_key(record: dict[str, Any]) -> str:
    authors = record.get("authors")
    if not isinstance(authors, list) or not authors:
        return ""
    first = authors[0]
    if isinstance(first, dict):
        first = first.get("name") or first.get("display_name") or ""
    return _title_key(first)


def _canonical_url(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme.casefold() not in {"http", "https"} or not parts.netloc:
        return ""
    retained_query_items = []
    ignored_query_keys = {"fbclid", "gclid", "ref", "source", "from"}
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        normalized_key = key.casefold()
        if normalized_key.startswith("utm_") or normalized_key in ignored_query_keys:
            continue
        retained_query_items.append((key, item))
    query = urlencode(retained_query_items)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, query, ""))


def _identity_keys(record: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    doi = _normalize_doi(record.get("doi"))
    pmid = _normalize_prefixed_id(record.get("pmid"), "pmid")
    pmcid = _normalize_prefixed_id(record.get("pmcid"), "pmc")
    arxiv_id = _normalize_prefixed_id(record.get("arxiv_id"), "arxiv")
    source = _source(record)
    if source == "pubmed" and not pmid:
        pmid = _normalize_prefixed_id(record.get("academic_source_id") or record.get("source_id"), "pmid")
    if source == "arxiv" and not arxiv_id:
        arxiv_id = _normalize_prefixed_id(record.get("academic_source_id") or record.get("source_id"), "arxiv")
    semantic_scholar_id = _normalize_prefixed_id(
        record.get("semantic_scholar_id") or record.get("paper_id"), "semantic_scholar"
    )
    historical_ids = record.get("source_ids")
    if isinstance(historical_ids, dict):
        pmid = pmid or _normalize_prefixed_id(historical_ids.get("pubmed"), "pmid")
        arxiv_id = arxiv_id or _normalize_prefixed_id(historical_ids.get("arxiv"), "arxiv")
        semantic_scholar_id = semantic_scholar_id or _normalize_prefixed_id(
            historical_ids.get("semantic_scholar"), "semantic_scholar"
        )
    if source == "semantic_scholar" and not semantic_scholar_id:
        semantic_scholar_id = _normalize_prefixed_id(
            record.get("academic_source_id") or record.get("source_id"), "semantic_scholar"
        )
    for name, value in (
        ("doi", doi), ("pmid", pmid), ("pmcid", pmcid), ("arxiv", arxiv_id),
        ("semantic_scholar", semantic_scholar_id),
    ):
        if value:
            keys.append(f"{name}:{value}")
    canonical_url = _canonical_url(record.get("url"))
    if canonical_url:
        keys.append(f"url:{canonical_url}")
    title, author, year = _title_key(record.get("title")), _author_key(record), _year(record)
    if title and author and year:
        keys.append(f"title_author_year:{title}:{author}:{year}")
    return keys


def _unique_items(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = repr(value)
        if marker not in seen:
            seen.add(marker)
            result.append(deepcopy(value))
    return result


def _merge(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        current = target.get(key)
        if key in {"content", "abstract", "full_text"}:
            if len(_text(value)) > len(_text(current)):
                target[key] = deepcopy(value)
        elif key in {"authors", "full_text_candidates"}:
            target[key] = _unique_items([*(current or []), *(value or [])])
        elif current in (None, "", [], {}):
            target[key] = deepcopy(value)


def _apply_provenance(record: dict[str, Any], sources: list[str], source_ids: dict[str, str]) -> None:
    record["matched_sources"] = sources
    record["source_ids"] = source_ids
    pmid = _normalize_prefixed_id(record.get("pmid"), "pmid")
    if pmid:
        record["pmid"] = pmid


def fuse_scholarly_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate scholarly records while preserving stable input order."""
    output: list[dict[str, Any]] = []
    key_to_index: dict[str, int] = {}
    provenance: dict[int, tuple[list[str], dict[str, str]]] = {}

    for original in records:
        record = deepcopy(original)
        record_sources, record_source_ids = _provenance(record)
        keys = _identity_keys(record)
        candidate_indices = {key_to_index[key] for key in keys if key in key_to_index}
        candidate_indices = {
            index
            for index in candidate_indices
            if _is_scholarly_record(record) and _is_scholarly_record(output[index])
        }
        matched_indices = sorted(candidate_indices)
        if not matched_indices:
            matched_index = len(output)
            output.append(record)
            provenance[matched_index] = (record_sources, record_source_ids)
        else:
            matched_index = matched_indices[0]
            sources, source_ids = provenance[matched_index]
            for redundant_index in matched_indices[1:]:
                _merge(output[matched_index], output[redundant_index])
                redundant_sources, redundant_source_ids = provenance[redundant_index]
                for redundant_source in redundant_sources:
                    if redundant_source not in sources:
                        sources.append(redundant_source)
                source_ids.update(redundant_source_ids)
            removed = set(matched_indices[1:])
            for redundant_index in reversed(matched_indices[1:]):
                del output[redundant_index]
            if removed:
                provenance = {
                    index - sum(removed_index < index for removed_index in removed): value
                    for index, value in provenance.items()
                    if index not in removed
                }
                key_to_index = {
                    key: index
                    for index, item in enumerate(output)
                    for key in _identity_keys(item)
                }
            _merge(output[matched_index], record)
        sources, source_ids = provenance[matched_index]
        for record_source in record_sources:
            if record_source not in sources:
                sources.append(record_source)
        source_ids.update(record_source_ids)
        for key in {*keys, *_identity_keys(output[matched_index])}:
            key_to_index[key] = matched_index
        if sources or source_ids:
            _apply_provenance(output[matched_index], sources, source_ids)
    return output
