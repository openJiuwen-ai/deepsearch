import logging

from openjiuwen_deepsearch.algorithm.report.article_link_follow_diagnostics import (
    followed_documents,
    log_report_candidates,
    log_report_classification,
    log_report_final_references,
    log_report_prefilter,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


LOGGER = logging.getLogger("test.article_link_follow.report_diagnostics")


def _followed(url: str, source_id: str = "followed-source") -> dict:
    return {
        "url": url,
        "source_id": source_id,
        "discovery": {"method": "article_link_follow", "depth": 1},
    }


def test_followed_documents_ignores_malformed_and_ordinary_documents():
    followed = _followed("https://example.com/followed")

    assert followed_documents([
        None,
        "bad",
        {"url": "https://example.com/search"},
        {"url": "https://example.com/other", "discovery": "bad"},
        followed,
    ]) == [followed]


def test_report_candidate_log_is_bounded_and_suppresses_ordinary_docs(caplog):
    docs = [_followed(f"https://example.com/{index}", f"source-{index}") for index in range(12)]
    docs.append({"url": "https://example.com/search"})

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        log_report_candidates(LOGGER, 2, docs)

    message = caplog.messages[-1]
    assert "phase=report_candidate" in message
    assert "section_idx=2" in message
    assert "followed_count=12" in message
    assert "https://example.com/9" in message
    assert "https://example.com/10" not in message

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        log_report_candidates(LOGGER, 2, [{"url": "https://example.com/search"}])
    assert not caplog.messages


def test_report_prefilter_logs_included_and_excluded_outcomes(caplog):
    included = _followed("https://example.com/included", "source-included")
    excluded = _followed("https://example.com/excluded", "source-excluded")

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        log_report_prefilter(LOGGER, 3, [included, excluded], [dict(included)])

    assert any(
        "url=https://example.com/included" in message
        and "outcome=included" in message
        and "prefilter_index=0" in message
        for message in caplog.messages
    )
    assert any(
        "url=https://example.com/excluded" in message
        and "outcome=excluded" in message
        and "prefilter_index=-1" in message
        for message in caplog.messages
    )


def test_report_classification_logs_selected_and_rejected_outcomes(caplog):
    selected = _followed("https://example.com/Selected?a=1&utm_source=test", "selected")
    rejected = _followed("https://example.com/rejected", "rejected")

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        log_report_classification(
            LOGGER,
            1,
            [selected, rejected],
            ["https://example.com/Selected?a=1"],
        )

    assert any(
        "url=https://example.com/Selected?a=1&utm_source=test" in message
        and "outcome=selected" in message
        for message in caplog.messages
    )
    assert any(
        "url=https://example.com/rejected" in message
        and "outcome=rejected" in message
        for message in caplog.messages
    )


def test_report_classification_distinguishes_same_url_source_variants(caplog):
    selected = _followed("https://example.com/shared", "selected-source")
    rejected = _followed("https://example.com/shared", "rejected-source")

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        log_report_classification(
            LOGGER,
            1,
            [selected, rejected],
            [selected],
        )

    assert any(
        "source_id=selected-source" in message
        and "outcome=selected" in message
        for message in caplog.messages
    )
    assert any(
        "source_id=rejected-source" in message
        and "outcome=rejected" in message
        for message in caplog.messages
    )


def test_report_final_reference_uses_trace_data_or_final_text(caplog):
    traced = _followed("https://example.com/traced", "trace-source")
    text_matched = _followed("https://example.com/text", "text-source")
    missing = _followed("https://example.com/missing", "missing-source")

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        log_report_final_references(
            LOGGER,
            2,
            [traced, text_matched, missing],
            "Final [text source](https://example.com/text).",
            [{"source_id": "trace-source", "url": "https://unrelated.example"}],
        )

    assert any(
        "url=https://example.com/traced" in message
        and "outcome=cited" in message
        and "trace_source_match=true" in message
        for message in caplog.messages
    )
    assert any(
        "url=https://example.com/text" in message
        and "outcome=cited" in message
        and "report_text_match=true" in message
        for message in caplog.messages
    )
    assert any(
        "url=https://example.com/missing" in message
        and "outcome=not_cited" in message
        for message in caplog.messages
    )


def test_report_final_reference_ignores_unrelated_nested_values_and_plain_text(caplog):
    followed = _followed("https://example.com/not-a-citation", "followed-source")

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        log_report_final_references(
            LOGGER,
            2,
            [followed],
            "The URL https://example.com/not-a-citation is discussed as plain prose.",
            [{"metadata": {"error_detail": "followed-source"}, "content": followed["url"]}],
        )

    assert any(
        "url=https://example.com/not-a-citation" in message
        and "outcome=not_cited" in message
        and "trace_source_match=false" in message
        and "report_text_match=false" in message
        for message in caplog.messages
    )


def test_report_diagnostics_hide_document_identifiers_in_sensitive_mode(
    caplog, monkeypatch
):
    secret_url = "https://example.com/private/report?token=secret-token"
    secret_source_id = "secret-source-id"
    followed = _followed(secret_url, secret_source_id)
    monkeypatch.setattr(LogManager, "is_sensitive", lambda: True)

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        log_report_candidates(LOGGER, 4, [followed])
        log_report_prefilter(LOGGER, 4, [followed], [followed])
        log_report_classification(LOGGER, 4, [followed], [secret_url])
        log_report_final_references(
            LOGGER,
            4,
            [followed],
            f"Final reference: {secret_url}",
            [{"source_id": secret_source_id, "url": secret_url}],
        )

    messages = "\n".join(caplog.messages)
    assert secret_url not in messages
    assert "secret-token" not in messages
    assert secret_source_id not in messages
    assert "phase=report_candidate" in messages
    assert "followed_count=1" in messages
    assert "phase=report_prefilter" in messages
    assert "outcome=included" in messages
    assert "phase=report_classification" in messages
    assert "outcome=selected" in messages
    assert "phase=report_final_reference" in messages
    assert "outcome=cited" in messages
