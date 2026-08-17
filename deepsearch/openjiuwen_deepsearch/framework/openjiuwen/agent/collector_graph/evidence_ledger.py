# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field, ValidationError


MAX_TARGET_PAPER_ATTEMPTS = 3


def target_paper_key(paper: dict) -> str:
    """Return a canonical key containing no session path separators."""
    from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import canonicalize_url
    from openjiuwen_deepsearch.algorithm.research_collector.target_paper import (
        normalize_arxiv_id,
        normalize_doi,
        normalize_pmid,
        normalize_title,
    )

    payload = {
        "pmid": normalize_pmid(paper.get("pmid") or paper.get("url")),
        "doi": normalize_doi(paper.get("doi") or paper.get("url")),
        "arxiv_id": normalize_arxiv_id(paper.get("arxiv_id") or paper.get("url")),
        "url": canonicalize_url(str(paper.get("url") or "")),
        "title": normalize_title(paper.get("title")),
        "dataset": str(paper.get("dataset") or "").strip().casefold(),
        "data_year": str(paper.get("data_year") or "").strip().casefold(),
        "topic": str(paper.get("topic") or "").strip().casefold(),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "tp_" + hashlib.sha256(encoded).hexdigest()


class EvidenceLedger(BaseModel):
    """Collector-internal runtime state for evidence-oriented research loops."""

    known_facts: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    attempted_queries: list[str] = Field(default_factory=list)
    target_paper_attempts: dict[str, int] = Field(default_factory=dict)
    confirmed_target_papers: list[str] = Field(default_factory=list)


def _clean_items(items: list[str]) -> list[str]:
    """Filter blank strings and remove duplicates while preserving order."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = item.strip() if isinstance(item, str) else ""
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
    return cleaned


def ensure_ledger(value: dict | EvidenceLedger | None) -> EvidenceLedger:
    """Convert a dict, model, or empty value into a usable EvidenceLedger.

    Invalid payloads are treated as an empty ledger to preserve collector fallback behavior.
    """
    if value is None:
        return EvidenceLedger()
    if isinstance(value, EvidenceLedger):
        ledger = value
    elif isinstance(value, dict):
        try:
            ledger = EvidenceLedger.model_validate(value)
        except ValidationError:
            return EvidenceLedger()
    else:
        return EvidenceLedger()

    return EvidenceLedger(
        known_facts=_clean_items(ledger.known_facts),
        missing_evidence=_clean_items(ledger.missing_evidence),
        attempted_queries=_clean_items(ledger.attempted_queries),
        target_paper_attempts={
            str(key): max(0, int(value))
            for key, value in ledger.target_paper_attempts.items()
            if isinstance(value, int) or (isinstance(value, str) and value.isdigit())
        },
        confirmed_target_papers=_clean_items(ledger.confirmed_target_papers),
    )


def merge_ledger_update(
    current: EvidenceLedger,
    update: EvidenceLedger,
    clear_missing_evidence: bool = False,
) -> EvidenceLedger:
    """Merge ledger updates with stable deduplication.

    Args:
        current: Existing runtime ledger.
        update: New ledger values from an LLM or node update.
        clear_missing_evidence: Whether to explicitly clear remaining gaps.

    Returns:
        EvidenceLedger: Merged ledger.
    """
    current = ensure_ledger(current)
    update = ensure_ledger(update)
    if clear_missing_evidence:
        missing_evidence = []
    elif update.missing_evidence:
        missing_evidence = update.missing_evidence
    else:
        missing_evidence = current.missing_evidence

    return EvidenceLedger(
        known_facts=_clean_items(current.known_facts + update.known_facts),
        missing_evidence=_clean_items(missing_evidence),
        attempted_queries=_clean_items(current.attempted_queries + update.attempted_queries),
        target_paper_attempts={**current.target_paper_attempts, **update.target_paper_attempts},
        confirmed_target_papers=_clean_items(
            current.confirmed_target_papers + update.confirmed_target_papers
        ),
    )


def append_attempted_queries(current: EvidenceLedger, queries: list[str]) -> EvidenceLedger:
    """Append executed query strings to the ledger with stable deduplication."""
    current = ensure_ledger(current)
    return EvidenceLedger(
        known_facts=current.known_facts,
        missing_evidence=current.missing_evidence,
        attempted_queries=_clean_items(current.attempted_queries + queries),
        target_paper_attempts=current.target_paper_attempts,
        confirmed_target_papers=current.confirmed_target_papers,
    )


def target_papers_still_searchable(
    papers: list[dict] | None, ledger: EvidenceLedger | dict | None
) -> list[dict]:
    """Exclude confirmed and exhausted target-paper constraints from locator prompts."""
    ledger = ensure_ledger(ledger)
    return [
        paper for paper in papers or []
        if isinstance(paper, dict)
        and target_paper_key(paper) not in ledger.confirmed_target_papers
        and ledger.target_paper_attempts.get(target_paper_key(paper), 0) < MAX_TARGET_PAPER_ATTEMPTS
    ]


def build_ledger_brief(ledger: EvidenceLedger | dict | None) -> str:
    """Build a compact prompt brief for the collector query and supervisor prompts."""
    ledger = ensure_ledger(ledger)
    sections: list[str] = []
    if ledger.known_facts:
        sections.append("Known facts:\n" + "\n".join(f"- {item}" for item in ledger.known_facts))
    if ledger.missing_evidence:
        sections.append("Missing evidence:\n" + "\n".join(f"- {item}" for item in ledger.missing_evidence))
    if ledger.attempted_queries:
        sections.append("Attempted queries:\n" + "\n".join(f"- {item}" for item in ledger.attempted_queries))
    return "\n\n".join(sections)
