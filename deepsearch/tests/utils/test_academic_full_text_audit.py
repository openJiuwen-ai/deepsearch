import ast
import inspect
import json
import logging

import pytest

import openjiuwen_deepsearch.utils.academic_full_text_audit as academic_full_text_audit
from openjiuwen_deepsearch.utils.academic_full_text_audit import (
    emit_academic_full_text_event,
    emit_cited_academic_full_text_events,
    emit_selected_academic_full_text_events,
    summarize_academic_full_text_events,
    summarize_academic_full_text_log,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context
from openjiuwen_deepsearch.utils.log_utils.log_common import session_id_ctx


class _FakeSession:
    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id

    def get_session_id(self) -> str:
        return self.conversation_id


@pytest.fixture(autouse=True)
def _academic_audit_session():
    token = session_id_ctx.set("conversation-a")
    try:
        yield
    finally:
        session_id_ctx.reset(token)


def test_emit_and_summarize_academic_full_text_funnel(caplog):
    logger = logging.getLogger("academic-audit-test")
    pubmed = {
        "academic_source": "pubmed",
        "academic_source_id": "38132429",
        "url": "https://pubmed.ncbi.nlm.nih.gov/38132429/",
        "full_text_status": "available",
        "full_text": "PMC text",
        "evidence_content_type": "full_text",
    }
    arxiv = {
        "source": "arxiv",
        "source_id": "1706.03762v7",
        "url": "https://arxiv.org/abs/1706.03762v7",
        "full_text_status": "available",
        "full_text": "arXiv text",
        "evidence_content_type": "full_text",
    }

    with caplog.at_level(logging.INFO, logger=logger.name):
        emit_academic_full_text_event(logger, "returned", pubmed)
        emit_academic_full_text_event(logger, "returned", pubmed)
        emit_academic_full_text_event(logger, "entered", pubmed)
        emit_academic_full_text_event(logger, "selected", pubmed)
        emit_academic_full_text_event(logger, "cited", pubmed)
        emit_academic_full_text_event(logger, "returned", arxiv)
        emit_academic_full_text_event(logger, "entered", arxiv)

    summary = summarize_academic_full_text_events(caplog.messages, "conversation-a")

    assert summary["pubmed"] == {
        "full_text_return_events": 2,
        "unique_full_text_papers": 1,
        "entered_collector": 1,
        "selected_for_writing": 1,
        "cited_in_final_report": 1,
    }
    assert summary["arxiv"]["full_text_return_events"] == 1
    assert summary["arxiv"]["unique_full_text_papers"] == 1
    assert summary["arxiv"]["entered_collector"] == 1
    assert summary["arxiv"]["selected_for_writing"] == 0
    assert summary["arxiv"]["cited_in_final_report"] == 0
    payload = json.loads(caplog.messages[0].split("ACADEMIC_FULL_TEXT_AUDIT ", 1)[1])
    assert payload["paper_id"] == "38132429"
    assert payload["conversation_id"] == "conversation-a"
    assert payload["full_text_chars"] == len(pubmed["full_text"])
    assert all(
        item["full_text_chars"] == len(pubmed["full_text"])
        for item in (
            json.loads(message.split("ACADEMIC_FULL_TEXT_AUDIT ", 1)[1])
            for message in caplog.messages
        )
        if item["engine"] == "pubmed"
    )


def test_audit_ignores_non_academic_and_malformed_lines():
    logger = logging.getLogger("academic-audit-ignore-test")

    assert emit_academic_full_text_event(logger, "returned", {"source": "tavily"}) is None
    assert emit_academic_full_text_event(logger, "selected", {
        "academic_source": "pubmed",
        "academic_source_id": "1",
        "evidence_content_type": "abstract",
    }) is None
    summary = summarize_academic_full_text_events([
        "ordinary log line",
        "ACADEMIC_FULL_TEXT_AUDIT not-json",
    ], "conversation-a")

    assert summary["pubmed"]["full_text_return_events"] == 0
    assert summary["arxiv"]["full_text_return_events"] == 0


def test_selected_and_cited_helpers_emit_only_matching_academic_documents(caplog):
    logger = logging.getLogger("academic-audit-stage-test")
    pubmed = {
        "academic_source": "pubmed",
        "academic_source_id": "38132429",
        "url": "https://pubmed.ncbi.nlm.nih.gov/38132429/",
        "evidence_content_type": "full_text",
        "evidence_content_chars": 321,
    }
    arxiv = {
        "academic_source": "arxiv",
        "academic_source_id": "1706.03762v7",
        "url": "https://arxiv.org/abs/1706.03762v7",
        "evidence_content_type": "full_text",
        "evidence_content_chars": 654,
    }

    with caplog.at_level(logging.INFO, logger=logger.name):
        emit_selected_academic_full_text_events(logger, [pubmed, arxiv])
        emit_cited_academic_full_text_events(
            logger,
            [{"url": pubmed["url"]}, {"url": "https://example.com"}],
            [[pubmed], [arxiv]],
        )

    summary = summarize_academic_full_text_events(caplog.messages, "conversation-a")
    assert summary["pubmed"]["selected_for_writing"] == 1
    assert summary["arxiv"]["selected_for_writing"] == 1
    assert summary["pubmed"]["cited_in_final_report"] == 1
    assert summary["arxiv"]["cited_in_final_report"] == 0
    payloads = [
        json.loads(message.split("ACADEMIC_FULL_TEXT_AUDIT ", 1)[1])
        for message in caplog.messages
    ]
    assert {payload["full_text_chars"] for payload in payloads} == {321, 654}


def test_cited_helper_matches_pubmed_document_to_pmc_citation(caplog):
    logger = logging.getLogger("academic-audit-pmc-citation-test")
    pubmed = {
        "academic_source": "pubmed",
        "academic_source_id": "38132429",
        "pmcid": "PMC10740908",
        "url": "https://pubmed.ncbi.nlm.nih.gov/38132429/",
        "evidence_content_type": "full_text",
    }

    with caplog.at_level(logging.INFO, logger=logger.name):
        emit_cited_academic_full_text_events(
            logger,
            [{"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10740908/"}],
            [pubmed],
        )

    summary = summarize_academic_full_text_events(caplog.messages, "conversation-a")
    assert summary["pubmed"]["cited_in_final_report"] == 1


def test_cited_helper_matches_arxiv_url_variants(caplog):
    logger = logging.getLogger("academic-audit-arxiv-citation-test")
    arxiv = {
        "academic_source": "arxiv",
        "academic_source_id": "1706.03762v7",
        "url": "https://export.arxiv.org/abs/1706.03762v7",
        "evidence_content_type": "full_text",
    }

    with caplog.at_level(logging.INFO, logger=logger.name):
        emit_cited_academic_full_text_events(
            logger,
            [{"url": "https://arxiv.org/html/1706.03762v7"}],
            [arxiv],
        )

    summary = summarize_academic_full_text_events(caplog.messages, "conversation-a")
    assert summary["arxiv"]["cited_in_final_report"] == 1


@pytest.mark.parametrize("citation_url", [
    "https://arxiv.org/html/hep-th/9901001",
    "https://arxiv.org/pdf/hep-th/9901001.pdf?download=1",
])
def test_cited_helper_matches_legacy_arxiv_url_variants(caplog, citation_url):
    logger = logging.getLogger("academic-audit-legacy-arxiv-citation-test")
    document = {
        "academic_source": "arxiv",
        "academic_source_id": "hep-th/9901001",
        "url": "https://export.arxiv.org/abs/hep-th/9901001",
        "evidence_content_type": "full_text",
        "evidence_content_chars": 100,
    }

    with caplog.at_level(logging.INFO, logger=logger.name):
        emit_cited_academic_full_text_events(logger, [{"url": citation_url}], [document])

    summary = summarize_academic_full_text_events(caplog.messages, "conversation-a")
    assert summary["arxiv"]["cited_in_final_report"] == 1


def test_summarize_academic_full_text_log_reads_common_log(tmp_path):
    log_path = tmp_path / "common.log"
    payload = {
        "event": "returned",
        "engine": "pubmed",
        "paper_id": "38132429",
        "conversation_id": "conversation-a",
    }
    log_path.write_text(
        "prefix ACADEMIC_FULL_TEXT_AUDIT " + json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    summary = summarize_academic_full_text_log(log_path, "conversation-a")

    assert summary["pubmed"]["full_text_return_events"] == 1
    assert summary["pubmed"]["unique_full_text_papers"] == 1


def test_summary_strictly_isolates_conversations():
    lines = [
        "ACADEMIC_FULL_TEXT_AUDIT " + json.dumps({
            "event": "returned", "engine": "pubmed", "paper_id": "1",
            "conversation_id": "conversation-a",
        }),
        "ACADEMIC_FULL_TEXT_AUDIT " + json.dumps({
            "event": "returned", "engine": "pubmed", "paper_id": "2",
            "conversation_id": "conversation-b",
        }),
    ]

    summary = summarize_academic_full_text_events(lines, "conversation-a")

    assert summary["pubmed"]["full_text_return_events"] == 1
    assert summary["pubmed"]["unique_full_text_papers"] == 1


def test_emit_skips_formal_event_without_session_context(caplog):
    token = session_id_ctx.set("-")
    logger = logging.getLogger("academic-audit-no-session-test")
    try:
        with caplog.at_level(logging.DEBUG, logger=logger.name):
            payload = emit_academic_full_text_event(logger, "returned", {
                "source": "pubmed",
                "source_id": "1",
                "full_text_status": "available",
                "full_text": "text",
            })
    finally:
        session_id_ctx.reset(token)

    assert payload is None
    assert not any("ACADEMIC_FULL_TEXT_AUDIT " in message for message in caplog.messages)


def test_emit_does_not_break_search_when_session_id_lookup_fails(caplog):
    class BrokenSession:
        def get_session_id(self):
            raise RuntimeError("session unavailable")

    stale_token = session_context.set(BrokenSession())
    token = session_id_ctx.set("-")
    logger = logging.getLogger("academic-audit-broken-session-test")
    try:
        with caplog.at_level(logging.DEBUG, logger=logger.name):
            payload = emit_academic_full_text_event(logger, "returned", {
                "source": "arxiv",
                "source_id": "2401.00001",
                "full_text_status": "available",
                "full_text": "text",
            })
    finally:
        session_id_ctx.reset(token)
        session_context.reset(stale_token)

    assert payload is None
    assert not any("ACADEMIC_FULL_TEXT_AUDIT " in message for message in caplog.messages)


def test_emit_does_not_reuse_stale_session_context(caplog):
    stale_token = session_context.set(_FakeSession("stale-conversation"))
    token = session_id_ctx.set("-")
    logger = logging.getLogger("academic-audit-stale-session-test")
    try:
        with caplog.at_level(logging.DEBUG, logger=logger.name):
            payload = emit_academic_full_text_event(logger, "returned", {
                "source": "pubmed",
                "source_id": "1",
                "full_text_status": "available",
                "full_text": "text",
            })
    finally:
        session_id_ctx.reset(token)
        session_context.reset(stale_token)

    assert payload is None
    assert not any("ACADEMIC_FULL_TEXT_AUDIT " in message for message in caplog.messages)


def test_audit_cli_uses_logging_instead_of_console_output():
    tree = ast.parse(inspect.getsource(academic_full_text_audit.main))

    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "sys"
        and node.func.value.attr == "stdout"
        and node.func.attr == "write"
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
        and node.func.attr == "info"
        for node in ast.walk(tree)
    )


def test_audit_helpers_do_not_shadow_module_logger():
    helper_names = {
        "emit_academic_full_text_event",
        "emit_selected_academic_full_text_events",
        "emit_cited_academic_full_text_events",
    }
    tree = ast.parse(inspect.getsource(academic_full_text_audit))
    helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    }

    assert helpers.keys() == helper_names
    assert all("logger" not in {argument.arg for argument in helper.args.args}
               for helper in helpers.values())
