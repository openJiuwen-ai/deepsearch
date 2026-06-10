# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError


class EvidenceLedger(BaseModel):
    """Collector-internal runtime state for evidence-oriented research loops."""

    known_facts: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    attempted_queries: list[str] = Field(default_factory=list)


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
    )


def append_attempted_queries(current: EvidenceLedger, queries: list[str]) -> EvidenceLedger:
    """Append executed query strings to the ledger with stable deduplication."""
    current = ensure_ledger(current)
    return EvidenceLedger(
        known_facts=current.known_facts,
        missing_evidence=current.missing_evidence,
        attempted_queries=_clean_items(current.attempted_queries + queries),
    )


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
