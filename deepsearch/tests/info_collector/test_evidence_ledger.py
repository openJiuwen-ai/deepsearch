# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import (
    EvidenceLedger,
    append_link_attempts,
    append_attempted_queries,
    build_ledger_brief,
    ensure_ledger,
    merge_ledger_update,
)


def test_ensure_ledger_returns_empty_ledger_for_none_and_invalid_values():
    """Invalid ledger payloads should be treated as an empty runtime ledger."""
    assert ensure_ledger(None) == EvidenceLedger()
    assert ensure_ledger({"known_facts": "not-a-list"}) == EvidenceLedger()
    assert ensure_ledger("bad value") == EvidenceLedger()


def test_ensure_ledger_converts_dict_and_filters_blank_items():
    """Dict payloads should become ledgers with blank string items removed."""
    ledger = ensure_ledger(
        {
            "known_facts": ["fact A", " ", "", "fact B"],
            "missing_evidence": ["evidence A", "\t"],
            "attempted_queries": ["query A", ""],
            "extra": "ignored",
        }
    )

    assert ledger.known_facts == ["fact A", "fact B"]
    assert ledger.missing_evidence == ["evidence A"]
    assert ledger.attempted_queries == ["query A"]


def test_merge_ledger_update_deduplicates_and_replaces_missing_evidence():
    """Ledger updates should deduplicate stable fields and replace non-empty missing evidence."""
    current = EvidenceLedger(
        known_facts=["fact A"],
        missing_evidence=["old missing"],
        attempted_queries=["query A"],
    )
    update = EvidenceLedger(
        known_facts=["fact A", "fact B"],
        missing_evidence=["new missing"],
        attempted_queries=["query A", "query B"],
    )

    merged = merge_ledger_update(current, update)

    assert merged.known_facts == ["fact A", "fact B"]
    assert merged.missing_evidence == ["new missing"]
    assert merged.attempted_queries == ["query A", "query B"]


def test_merge_ledger_update_keeps_or_clears_missing_evidence_by_policy():
    """Empty update missing evidence should either keep current gaps or explicitly clear them."""
    current = EvidenceLedger(missing_evidence=["old missing"])

    kept = merge_ledger_update(current, EvidenceLedger())
    cleared = merge_ledger_update(current, EvidenceLedger(), clear_missing_evidence=True)

    assert kept.missing_evidence == ["old missing"]
    assert cleared.missing_evidence == []


def test_append_attempted_queries_records_executed_queries_with_stable_deduplication():
    """Executed queries should be appended once without treating empty items as attempts."""
    ledger = EvidenceLedger(attempted_queries=["query A"])

    updated = append_attempted_queries(ledger, ["query B", "query A", " ", "query C"])

    assert updated.attempted_queries == ["query A", "query B", "query C"]


def test_build_ledger_brief_includes_non_empty_sections():
    """Prompt brief should expose only non-empty ledger sections."""
    ledger = EvidenceLedger(
        known_facts=["fact A"],
        missing_evidence=["missing A"],
        attempted_queries=["query A"],
    )

    brief = build_ledger_brief(ledger)

    assert "Known facts:" in brief
    assert "- fact A" in brief
    assert "Missing evidence:" in brief
    assert "- missing A" in brief
    assert "Attempted queries:" in brief
    assert "- query A" in brief


def test_build_ledger_brief_returns_empty_string_for_empty_ledger():
    """Empty ledgers should not add prompt noise."""
    assert build_ledger_brief(EvidenceLedger()) == ""


def test_legacy_ledger_defaults_link_lists_to_empty():
    ledger = ensure_ledger({"attempted_queries": ["q"]})

    assert ledger.attempted_links == []
    assert ledger.successful_links == []
    assert ledger.failed_links == []


def test_append_link_attempts_preserves_order_and_deduplicates():
    ledger = ensure_ledger(None)

    updated = append_link_attempts(
        ledger,
        attempted=["https://example.com/b", "https://example.com/b"],
        successful=["https://example.com/b"],
        failed=[],
    )

    assert updated.attempted_links == ["https://example.com/b"]
    assert updated.successful_links == ["https://example.com/b"]
    assert updated.failed_links == []
