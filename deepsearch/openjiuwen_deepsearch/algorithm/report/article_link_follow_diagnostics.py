"""Temporary downstream diagnostics for article-link-follow report lineage."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from openjiuwen_deepsearch.algorithm.research_collector.article_link_follow import (
    canonicalize_url,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


_METHOD = "article_link_follow"
_SUMMARY_URL_LIMIT = 10
_MARKDOWN_LINK_RE = re.compile(
    r"\[[^\]]*\]\(((?:[^()\s]+|\([^()\s]*\))+?)\)"
)


def _is_followed_document(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    discovery = value.get("discovery")
    return isinstance(discovery, dict) and discovery.get("method") == _METHOD


def followed_documents(doc_infos: Any) -> list[dict]:
    if not isinstance(doc_infos, (list, tuple)):
        return []
    return [doc for doc in doc_infos if _is_followed_document(doc)]


def _canonical_url(doc_or_url: Any) -> str:
    value = doc_or_url.get("url") if isinstance(doc_or_url, dict) else doc_or_url
    return canonicalize_url(str(value or ""))


def _same_document(left: dict, right: Any) -> bool:
    if not isinstance(right, dict):
        return False
    left_source_id = str(left.get("source_id") or "")
    right_source_id = str(right.get("source_id") or "")
    if left_source_id and right_source_id:
        return left_source_id == right_source_id
    left_url = _canonical_url(left)
    return bool(left_url and left_url == _canonical_url(right))


def _display_url(doc: dict) -> str:
    return str(doc.get("url") or "<missing>")


def _lower_bool(value: bool) -> str:
    return "true" if value else "false"


def log_report_candidates(logger, section_idx: Any, doc_infos: Any) -> None:
    followed = followed_documents(doc_infos)
    if not followed:
        return
    if LogManager.is_sensitive():
        logger.info(
            "[ArticleLinkFollow] phase=report_candidate section_idx=%s followed_count=%s",
            section_idx,
            len(followed),
        )
        return
    urls = ",".join(_display_url(doc) for doc in followed[:_SUMMARY_URL_LIMIT])
    logger.info(
        "[ArticleLinkFollow] phase=report_candidate section_idx=%s followed_count=%s urls=%s",
        section_idx,
        len(followed),
        urls,
    )


def log_report_prefilter(
    logger,
    section_idx: Any,
    raw_doc_infos: Any,
    filtered_doc_infos: Any,
) -> None:
    raw_docs = raw_doc_infos if isinstance(raw_doc_infos, (list, tuple)) else []
    filtered_docs = filtered_doc_infos if isinstance(filtered_doc_infos, (list, tuple)) else []
    for raw_index, doc in enumerate(raw_docs):
        if not _is_followed_document(doc):
            continue
        prefilter_index = next(
            (index for index, candidate in enumerate(filtered_docs) if _same_document(doc, candidate)),
            -1,
        )
        outcome = "included" if prefilter_index >= 0 else "excluded"
        if LogManager.is_sensitive():
            logger.info(
                "[ArticleLinkFollow] phase=report_prefilter section_idx=%s "
                "outcome=%s raw_index=%s prefilter_index=%s",
                section_idx,
                outcome,
                raw_index,
                prefilter_index,
            )
        else:
            logger.info(
                "[ArticleLinkFollow] phase=report_prefilter section_idx=%s url=%s "
                "outcome=%s raw_index=%s prefilter_index=%s",
                section_idx,
                _display_url(doc),
                outcome,
                raw_index,
                prefilter_index,
            )


def log_report_classification(
    logger,
    section_idx: Any,
    candidate_doc_infos: Any,
    selected_doc_infos: Iterable[Any] | None,
    *,
    terminal_reason: str | None = None,
) -> None:
    selected = list(selected_doc_infos or [])
    for doc in followed_documents(candidate_doc_infos):
        if terminal_reason:
            if LogManager.is_sensitive():
                logger.info(
                    "[ArticleLinkFollow] phase=report_classification section_idx=%s "
                    "outcome=not_selected reason=%s",
                    section_idx,
                    terminal_reason,
                )
            else:
                logger.info(
                    "[ArticleLinkFollow] phase=report_classification section_idx=%s "
                    "url=%s outcome=not_selected reason=%s",
                    section_idx,
                    _display_url(doc),
                    terminal_reason,
                )
            continue
        is_selected = any(
            _same_document(doc, candidate)
            if isinstance(candidate, dict)
            else bool(_canonical_url(doc) == _canonical_url(candidate))
            for candidate in selected
        )
        outcome = "selected" if is_selected else "rejected"
        if LogManager.is_sensitive():
            logger.info(
                "[ArticleLinkFollow] phase=report_classification section_idx=%s outcome=%s",
                section_idx,
                outcome,
            )
        else:
            logger.info(
                "[ArticleLinkFollow] phase=report_classification section_idx=%s "
                "url=%s source_id=%s outcome=%s",
                section_idx,
                _display_url(doc),
                str(doc.get("source_id") or "<missing>"),
                outcome,
            )


def _trace_reference_matches(value: Any, source_id: str, canonical_url: str) -> bool:
    if not isinstance(value, (list, tuple)):
        return False
    for item in value:
        if not isinstance(item, dict):
            continue
        item_source_id = str(item.get("source_id") or "")
        if source_id and item_source_id == source_id:
            return True
        item_url = _canonical_url(item.get("url"))
        if canonical_url and item_url == canonical_url:
            return True
    return False


def _markdown_reference_urls(report_text: str) -> set[str]:
    urls = {_canonical_url(match.group(1)) for match in _MARKDOWN_LINK_RE.finditer(report_text)}
    urls.discard("")
    return urls


def log_report_final_references(
    logger,
    section_idx: Any,
    classified_doc_infos: Any,
    modified_report: Any,
    trace_source_datas: Any,
) -> None:
    report_text = str(modified_report or "")
    report_reference_urls = _markdown_reference_urls(report_text)
    for doc in followed_documents(classified_doc_infos):
        source_id = str(doc.get("source_id") or "")
        canonical_url = _canonical_url(doc)
        trace_match = _trace_reference_matches(
            trace_source_datas, source_id, canonical_url
        )
        text_match = bool(canonical_url and canonical_url in report_reference_urls)
        outcome = "cited" if trace_match or text_match else "not_cited"
        if LogManager.is_sensitive():
            logger.info(
                "[ArticleLinkFollow] phase=report_final_reference section_idx=%s "
                "outcome=%s trace_source_match=%s report_text_match=%s",
                section_idx,
                outcome,
                _lower_bool(trace_match),
                _lower_bool(text_match),
            )
        else:
            logger.info(
                "[ArticleLinkFollow] phase=report_final_reference section_idx=%s url=%s "
                "outcome=%s trace_source_match=%s report_text_match=%s",
                section_idx,
                _display_url(doc),
                outcome,
                _lower_bool(trace_match),
                _lower_bool(text_match),
            )
