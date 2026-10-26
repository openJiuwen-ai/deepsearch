# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
from typing import Any

from openjiuwen_deepsearch.utils.common_utils.llm_utils import safe_float


logger = logging.getLogger(__name__)


def normalize_key_passages(value: object) -> list[str]:
    """Normalize key_passages into a clean string list."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []

    passages = []
    for item in value:
        text = str(item or "").strip()
        if text:
            passages.append(text)
    return passages


def _format_key_passages(passages: list[str]) -> list[str]:
    if not passages:
        return ["[]"]
    return [f"- {passage}" for passage in passages]


def format_key_passage_block(passage_info: dict[str, Any], index: int) -> str:
    """Format one selected document as a key-passages-only block for outline."""
    passages = normalize_key_passages(passage_info.get("key_passages"))
    # Passage-level fallback: use passage_text if key_passages is empty
    if not passages:
        passage_text = passage_info.get("passage_text", "")
        if passage_text:
            passages = [passage_text]
    return "\n".join(
        [
            f"Document {index} key passages:",
            *_format_key_passages(passages),
        ]
    )


def build_structured_evidence_guide(
    selected_passages: list[dict[str, Any]],
    rationales: list[dict[str, Any]],
    coverage_result: dict[str, Any],
    *,
    selected_passage_keys: list[str],
    requirements: list[dict[str, Any]] | None = None,
    requirement_evidence_diagnostics: dict[str, Any] | None = None,
) -> str:
    """Build compact writing guidance from existing document-selection results."""
    coverage_matrix = coverage_result.get("coverage_matrix", {})
    if not isinstance(coverage_matrix, dict):
        coverage_matrix = {}
    if not selected_passages or not rationales or not coverage_matrix:
        if not requirements:
            return ""
        diagnostics = requirement_evidence_diagnostics
        if diagnostics is None:
            diagnostics = build_requirement_evidence_diagnostics(
                rationales, requirements, coverage_result, selected_passage_keys
            )
        lines = [
            "Structured evidence guidance:",
            f"Requirement evidence coverage: {diagnostics['requirements_with_evidence']}/"
            f"{diagnostics['requirement_count']} requirements have selected evidence "
            f"(coverage >= 0.30); unable to determine: "
            f"{diagnostics['requirements_undetermined']}.",
        ]
        for item in diagnostics["items"]:
            rationale_ids = ", ".join(item["rationale_ids"]) or "unmapped"
            status_labels = {
                "evidence_found": "evidence found",
                "evidence_gap": "evidence gap",
                "undetermined": "unable to determine",
            }
            status = status_labels.get(item.get("status"), "unknown status")
            coverage_text = (
                f"{item['best_coverage']:.2f}"
                if item["best_coverage"] is not None
                else "n/a"
            )
            lines.append(
                f"- [requirement:{item['requirement_id']}, {status}, "
                f"best_coverage:{coverage_text}, rationales:{rationale_ids}] "
                f"{item['description']}"
            )
        return "\n".join(lines)
    if len(selected_passages) != len(selected_passage_keys):
        logger.warning(
            "Cannot build structured evidence guide: selected passages and stable keys are misaligned"
        )
        return ""
    if any(not isinstance(passage_key, str) for passage_key in selected_passage_keys):
        logger.warning(
            "Cannot build structured evidence guide: selected stable key is invalid"
        )
        return ""
    if any(passage_key not in coverage_matrix for passage_key in selected_passage_keys):
        logger.warning(
            "Cannot build structured evidence guide: selected stable key is missing "
            "from coverage matrix"
        )
        return ""

    lines = ["Structured evidence guidance:"]
    for rationale in rationales:
        rationale_id = str(rationale.get("id", "") or "")
        if not rationale_id:
            continue
        evidence = []
        max_coverage = 0.0
        for passage, passage_key in zip(selected_passages, selected_passage_keys, strict=True):
            cov_entry = coverage_matrix.get(passage_key, {})
            if not isinstance(cov_entry, dict):
                continue
            score = safe_float(cov_entry.get(rationale_id, 0.0))
            max_coverage = max(max_coverage, score)
            if score >= 0.3:
                evidence.append((score, passage, passage_key))

        status = "covered" if max_coverage >= 0.6 else "weak" if max_coverage >= 0.3 else "uncovered"
        priority = str(rationale.get("priority", "supplementary") or "supplementary")
        description = str(rationale.get("description", "") or "")
        lines.append(f"- {rationale_id} [{priority}, {status}]: {description}")
        for score, passage, _ in sorted(
            evidence, key=lambda item: item[0], reverse=True
        )[:3]:
            citation_index = passage.get("index", "")
            title = str(passage.get("doc_title", "") or passage.get("title", "") or "")
            lines.append(
                f"  - [citation:{citation_index}] {title} (coverage: {score:.2f})"
            )

    if requirements:
        diagnostics = requirement_evidence_diagnostics
        if diagnostics is None:
            diagnostics = build_requirement_evidence_diagnostics(
                rationales,
                requirements,
                coverage_result,
                selected_passage_keys,
            )
        lines.append(
            f"Requirement evidence coverage: {diagnostics['requirements_with_evidence']}/"
            f"{diagnostics['requirement_count']} "
            "requirements have selected evidence (coverage >= 0.30); "
            f"unable to determine: {diagnostics['requirements_undetermined']}."
        )
        for item in diagnostics["items"]:
            rationale_ids = ", ".join(item["rationale_ids"]) or "unmapped"
            status_labels = {
                "evidence_found": "evidence found",
                "evidence_gap": "evidence gap",
                "undetermined": "unable to determine",
            }
            status = status_labels.get(item.get("status"), "unknown status")
            coverage_text = (
                f"{item['best_coverage']:.2f}"
                if item["best_coverage"] is not None
                else "n/a"
            )
            lines.append(
                f"- [requirement:{item['requirement_id']}, {status}, "
                f"best_coverage:{coverage_text}, rationales:{rationale_ids}] "
                f"{item['description']}"
            )

    return "\n".join(lines)


def build_requirement_evidence_diagnostics(
    rationales: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    coverage_result: dict[str, Any],
    selected_passage_keys: list[str],
    *,
    evidence_threshold: float = 0.3,
) -> dict[str, Any]:
    """Report whether each checklist requirement has selected supporting evidence."""
    coverage_matrix = coverage_result.get("coverage_matrix", {})
    if not isinstance(coverage_matrix, dict):
        coverage_matrix = {}
    valid_coverage_rows = {}
    unavailable_keys = []
    for passage_key in selected_passage_keys:
        if not isinstance(passage_key, str):
            unavailable_keys.append(passage_key)
            continue
        coverage = coverage_matrix.get(passage_key)
        if not isinstance(coverage, dict):
            unavailable_keys.append(passage_key)
            continue
        valid_coverage_rows[passage_key] = coverage
    if unavailable_keys:
        logger.warning(
            "Requirement evidence diagnostics cannot evaluate %s selected passage keys",
            len(unavailable_keys),
        )
    items = []
    for requirement in requirements:
        requirement_id = str(requirement.get("id", "") or "")
        rationale_ids = []
        for rationale in rationales:
            if not isinstance(rationale, dict):
                continue
            if requirement_id in rationale.get("covers", []):
                rationale_ids.append(str(rationale.get("id", "")))

        coverage_scores = []
        unavailable_key = False
        for rationale_id in rationale_ids:
            for passage_key in selected_passage_keys:
                if passage_key in unavailable_keys:
                    unavailable_key = True
                    continue
                coverage = valid_coverage_rows[passage_key]
                coverage_scores.append(safe_float(coverage.get(rationale_id, 0.0)))
        best_coverage = max(coverage_scores) if coverage_scores else None
        if best_coverage is not None and best_coverage >= evidence_threshold:
            status = "evidence_found"
        elif unavailable_key:
            status = "undetermined"
        else:
            best_coverage = best_coverage if best_coverage is not None else 0.0
            status = "evidence_gap"
        items.append({
            "requirement_id": requirement_id,
            "description": str(requirement.get("description", "") or ""),
            "rationale_ids": rationale_ids,
            "best_coverage": best_coverage,
            "status": status,
        })
    found = sum(item["status"] == "evidence_found" for item in items)
    undetermined = sum(item["status"] == "undetermined" for item in items)
    total = len(items)
    evaluable = total - undetermined
    return {
        "requirement_count": total,
        "requirements_with_evidence": found,
        "requirements_undetermined": undetermined,
        "requirements_evaluable": evaluable,
        "coverage_rate": (found / evaluable) if evaluable else 0.0,
        "evidence_threshold": evidence_threshold,
        "items": items,
    }


def build_coverage_passage_block(coverage_sections: list[tuple[int, list[str]]]) -> str:
    """Format the aggregated coverage-passages block appended after key blocks.

    Args:
        coverage_sections: ``(document index, coverage passage text list)`` pairs.
            The document index must align with the ``Document N key passages``
            block numbering so the two channels stay source-traceable.

    Returns:
        The aggregated coverage block; empty string when there is no content.
    """
    if not coverage_sections or not any(passages for _, passages in coverage_sections):
        return ""
    lines = ["===== COVERAGE PASSAGES ====="]
    for index, passages in coverage_sections:
        if not passages:
            continue
        lines.append(f"Document {index} coverage passages:")
        lines.extend(f"- {passage}" for passage in passages)
    return "\n".join(lines)
