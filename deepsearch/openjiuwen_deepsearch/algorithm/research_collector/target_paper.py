"""Minimal deterministic verification for user-targeted academic papers."""

from __future__ import annotations

import re
import string
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import canonicalize_url


_WHITESPACE_RE = re.compile(r"\s+")
_PMID_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)(?:/|$)", re.IGNORECASE)
_MODERN_ARXIV_ID_RE = re.compile(r"\d{4}\.\d{4,5}", re.IGNORECASE)
_LEGACY_ARXIV_ID_RE = re.compile(r"[a-z][a-z0-9.-]*/\d{7}", re.IGNORECASE)
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", re.IGNORECASE)


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def normalize_pmid(value: Any) -> str:
    """Return a numeric PMID or an empty string."""
    text = _text(value)
    url_match = _PMID_URL_RE.search(text)
    if url_match:
        return url_match.group(1)
    text = re.sub(r"^pmid\s*:\s*", "", text, flags=re.IGNORECASE)
    return text if text.isdigit() else ""


def normalize_doi(value: Any) -> str:
    """Return a case-folded DOI without a label or resolver URL."""
    text = _DOI_PREFIX_RE.sub("", _text(value)).strip().rstrip("./")
    return text.casefold() if text.startswith("10.") and "/" in text else ""


def normalize_arxiv_id(value: Any) -> str:
    """Return a canonical arXiv ID without a version suffix."""
    text = _text(value)
    parse_target = (
        f"https://{text}"
        if re.match(r"^(?:(?:www|export)\.)?arxiv\.org/", text, re.IGNORECASE)
        else text
    )
    parsed = urlsplit(parse_target)
    if parsed.scheme.casefold() in {"http", "https"}:
        if parsed.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            return ""
        path_match = re.fullmatch(r"/(?:abs|pdf|html)/(.+?)/?", unquote(parsed.path), re.IGNORECASE)
        if not path_match:
            return ""
        text = path_match.group(1)
    else:
        text = re.sub(r"^arxiv\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE)
    identifier = _ARXIV_VERSION_RE.sub("", text)
    if not (_MODERN_ARXIV_ID_RE.fullmatch(identifier) or _LEGACY_ARXIV_ID_RE.fullmatch(identifier)):
        return ""
    return identifier.casefold()


def normalize_title(value: Any) -> str:
    """Normalize a complete title for strict equality."""
    text = _WHITESPACE_RE.sub(" ", _text(value)).casefold()
    return text.rstrip(string.punctuation + "。！？").strip()


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    return model_dump() if callable(model_dump) else {}


def _document_pmid(document: dict) -> str:
    if str(document.get("academic_source") or "").casefold() == "pubmed":
        pmid = normalize_pmid(document.get("academic_source_id"))
        if pmid:
            return pmid
    return normalize_pmid(document.get("url"))


def _document_arxiv_id(document: dict) -> str:
    if str(document.get("academic_source") or "").casefold() == "arxiv":
        identifier = normalize_arxiv_id(document.get("academic_source_id"))
        if identifier:
            return identifier
    return normalize_arxiv_id(document.get("url"))


def _match_fact(target: dict, document: dict) -> str:
    title = _text(document.get("title")) or "Untitled"
    pmid = normalize_pmid(target.get("pmid"))
    doi = normalize_doi(target.get("doi"))
    arxiv_id = normalize_arxiv_id(target.get("arxiv_id"))
    target_url = canonicalize_url(_text(target.get("url")))
    target_title = normalize_title(target.get("title"))

    if pmid:
        return f"Target paper located: PMID {pmid}, {title}." if pmid == _document_pmid(document) else ""
    if doi:
        document_doi = normalize_doi(document.get("doi")) or normalize_doi(document.get("url"))
        return f"Target paper located: DOI {doi}, {title}." if doi == document_doi else ""
    if arxiv_id:
        return (
            f"Target paper located: arXiv {arxiv_id}, {title}."
            if arxiv_id == _document_arxiv_id(document)
            else ""
        )
    if target_url:
        return (
            f"Target paper located: URL, {title}."
            if target_url == canonicalize_url(_text(document.get("url")))
            else ""
        )
    if target_title and target_title == normalize_title(document.get("title")):
        return f"Target paper located: exact title, {title}."
    return ""


def find_exact_target_paper_facts(
    target_papers: list[dict] | None,
    documents: list[dict] | None,
) -> list[str]:
    """Return stable ledger facts for deterministic exact matches only."""
    valid_documents = [_as_dict(document) for document in documents or []]
    valid_documents = [document for document in valid_documents if document]
    facts: list[str] = []
    seen: set[str] = set()
    for raw_target in target_papers or []:
        target = _as_dict(raw_target)
        if not target:
            continue
        for document in valid_documents:
            fact = _match_fact(target, document)
            if not fact:
                continue
            if fact not in seen:
                seen.add(fact)
                facts.append(fact)
            break
    return facts
