"""Structured audit events for the academic full-text writing funnel."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote, urlsplit

from openjiuwen_deepsearch.utils.log_utils.log_common import session_id_ctx


logger = logging.getLogger(__name__)
AUDIT_MARKER = "ACADEMIC_FULL_TEXT_AUDIT "
ACADEMIC_ENGINES = ("pubmed", "arxiv")
EVENT_METRICS = {
    "returned": "full_text_return_events",
    "entered": "entered_collector",
    "selected": "selected_for_writing",
    "cited": "cited_in_final_report",
}


def _normalized_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/").casefold()


def _pmcid_from_url(value: Any) -> str:
    match = re.search(r"/articles/(PMC\d+)(?:[/?#]|$)", str(value or ""), re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _arxiv_id_from_url(value: Any) -> str:
    path = unquote(urlsplit(str(value or "").strip()).path)
    match = re.search(r"/(?:abs|html|pdf)/(.+?)/*$", path, re.IGNORECASE)
    if not match:
        return ""
    return re.sub(r"\.pdf$", "", match.group(1), flags=re.IGNORECASE).casefold()


def _citation_identities(item: dict[str, Any]) -> set[tuple[str, str]]:
    """Build stable identities used to join academic documents to citations."""
    identities: set[tuple[str, str]] = set()
    url = item.get("url") or item.get("link") or item.get("source_url")
    normalized_url = _normalized_url(url)
    if normalized_url:
        identities.add(("url", normalized_url))
    pmcid = str(item.get("pmcid") or "").strip().upper() or _pmcid_from_url(url)
    if pmcid:
        identities.add(("pmcid", pmcid))
    arxiv_id = ""
    if str(item.get("academic_source") or item.get("source") or "").casefold() == "arxiv":
        arxiv_id = str(item.get("academic_source_id") or item.get("source_id") or "").strip().casefold()
    arxiv_id = arxiv_id or _arxiv_id_from_url(url)
    if arxiv_id:
        identities.add(("arxiv", arxiv_id))
    return identities


def _identity(item: dict[str, Any]) -> tuple[str, str]:
    engine = str(item.get("academic_source") or item.get("source") or "").strip().casefold()
    paper_id = str(item.get("academic_source_id") or item.get("source_id") or "").strip()
    return engine, paper_id


def _current_conversation_id() -> str:
    conversation_id = str(session_id_ctx.get() or "").strip()
    return "" if conversation_id == "-" else conversation_id


def emit_academic_full_text_event(
    event_logger: logging.Logger,
    event: str,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    """Emit one content-free JSON audit event and return its payload."""
    engine, paper_id = _identity(item)
    if engine not in ACADEMIC_ENGINES or not paper_id or event not in EVENT_METRICS:
        return None
    conversation_id = _current_conversation_id()
    if not conversation_id:
        event_logger.debug(
            "Academic full-text audit event skipped because conversation_id is unavailable. "
            "event=%s engine=%s paper_id=%s",
            event,
            engine,
            paper_id,
        )
        return None
    evidence_content_type = str(
        item.get("evidence_content_type") or item.get("content_type") or ""
    )
    if event == "returned":
        if item.get("full_text_status") != "available" or not str(item.get("full_text") or ""):
            return None
    elif evidence_content_type != "full_text":
        return None
    if event == "returned":
        full_text_chars = len(str(item.get("full_text") or ""))
    elif "evidence_content_chars" not in item:
        full_text_chars = len(str(item.get("full_text") or item.get("original_content") or ""))
    else:
        try:
            full_text_chars = max(0, int(item.get("evidence_content_chars") or 0))
        except (TypeError, ValueError):
            full_text_chars = 0
    payload = {
        "conversation_id": conversation_id,
        "event": event,
        "engine": engine,
        "paper_id": paper_id,
        "url": str(item.get("url") or ""),
        "full_text_status": str(item.get("full_text_status") or ""),
        "evidence_content_type": evidence_content_type,
        "full_text_chars": full_text_chars,
        "full_text_truncated": bool(item.get("full_text_truncated", False)),
    }
    event_logger.info("%s%s", AUDIT_MARKER, json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


def _iter_documents(value: Any):
    if isinstance(value, dict):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_documents(item)


def emit_selected_academic_full_text_events(
    event_logger: logging.Logger,
    documents: Any,
) -> None:
    """Emit one selected event for every academic document in writing context."""
    for document in _iter_documents(documents):
        emit_academic_full_text_event(event_logger, "selected", document)


def emit_cited_academic_full_text_events(
    event_logger: logging.Logger,
    citations: Any,
    documents: Any,
) -> None:
    """Join final citations to writing documents by URL or stable academic identity."""
    cited_identities: set[tuple[str, str]] = set()
    for citation in _iter_documents(citations):
        cited_identities.update(_citation_identities(citation))
    for document in _iter_documents(documents):
        if _citation_identities(document) & cited_identities:
            emit_academic_full_text_event(event_logger, "cited", document)


def summarize_academic_full_text_events(
    lines: Iterable[str],
    conversation_id: str,
) -> dict[str, dict[str, int]]:
    """Summarize audit log lines into per-engine event and unique-paper counts."""
    requested_conversation_id = str(conversation_id or "").strip()
    if not requested_conversation_id:
        raise ValueError("conversation_id is required for academic audit summaries")
    event_ids = {
        engine: {event: set() for event in EVENT_METRICS}
        for engine in ACADEMIC_ENGINES
    }
    event_counts = {
        engine: {event: 0 for event in EVENT_METRICS}
        for engine in ACADEMIC_ENGINES
    }
    for raw_line in lines:
        line = str(raw_line)
        if AUDIT_MARKER not in line:
            continue
        try:
            payload = json.loads(line.split(AUDIT_MARKER, 1)[1])
        except (json.JSONDecodeError, TypeError):
            continue
        engine = str(payload.get("engine") or "").casefold()
        event = str(payload.get("event") or "")
        paper_id = str(payload.get("paper_id") or "")
        if str(payload.get("conversation_id") or "") != requested_conversation_id:
            continue
        if engine not in event_counts or event not in EVENT_METRICS or not paper_id:
            continue
        event_counts[engine][event] += 1
        event_ids[engine][event].add(paper_id)

    summary = {}
    for engine in ACADEMIC_ENGINES:
        summary[engine] = {
            "full_text_return_events": event_counts[engine]["returned"],
            "unique_full_text_papers": len(event_ids[engine]["returned"]),
            "entered_collector": len(event_ids[engine]["entered"]),
            "selected_for_writing": len(event_ids[engine]["selected"]),
            "cited_in_final_report": len(event_ids[engine]["cited"]),
        }
    return summary


def summarize_academic_full_text_log(
    path: str | Path,
    conversation_id: str,
) -> dict[str, dict[str, int]]:
    """Read a UTF-8 common.log file and summarize its academic audit events."""
    with Path(path).open("r", encoding="utf-8", errors="replace") as log_file:
        return summarize_academic_full_text_events(log_file, conversation_id)


def main() -> None:
    """CLI entry point used after an isolated end-to-end experiment."""
    import argparse

    parser = argparse.ArgumentParser(description="Summarize academic full-text audit events.")
    parser.add_argument("log_path", help="Path to the experiment's common/common.log")
    parser.add_argument("--conversation-id", required=True, help="Conversation ID to summarize")
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()
    summary = summarize_academic_full_text_log(args.log_path, args.conversation_id)
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    logger.info("%s", rendered)


if __name__ == "__main__":
    main()
