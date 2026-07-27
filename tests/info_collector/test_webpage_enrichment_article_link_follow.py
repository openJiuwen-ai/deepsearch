import threading
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen_deepsearch.config.config import AgentConfig, ServiceConfig
from openjiuwen_deepsearch.algorithm.research_collector.article_link_follow import (
    ARTICLE_LINK_SOURCE_FIELD,
    ArticleLinkCandidate,
    ArticleLinkEvidence,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment import (
    FollowedArticle,
    WebPageEnrichmentNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import (
    ensure_ledger,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import RetrievalQuery
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


@pytest.fixture(autouse=True)
def _article_link_llm_context():
    """为融合节点的预处理提供测试 LLM 上下文。"""
    token = llm_context.set({"model": Mock()})
    try:
        yield
    finally:
        llm_context.reset(token)


def test_article_link_follow_defaults_are_opt_in_and_bounded():
    agent_config = AgentConfig()
    service_config = ServiceConfig()

    assert agent_config.info_collector_article_link_follow_enable is False
    assert service_config.info_collector_article_link_follow_max_urls == 3


def test_article_link_follow_keeps_only_compression_llm_identifier():
    assert "COLLECTOR_ARTICLE_LINK_FOLLOW_SELECTION" not in AgentLlmName.__members__
    assert AgentLlmName.COLLECTOR_ARTICLE_LINK_FOLLOW_COMPRESSION.value == (
        "collector_article_link_follow_compression"
    )


@pytest.mark.asyncio
async def test_article_link_follow_node_disabled_is_a_noop():
    state = {
        "config.info_collector_article_link_follow_enable": False,
        "collector_context.doc_infos": [{"doc_id": "a"}],
    }
    session = Mock()
    session.get_global_state = Mock(side_effect=lambda key: state.get(key))
    session.update_global_state = Mock()

    result = await WebPageEnrichmentNode().invoke({}, session, Mock())

    assert result == {}
    session.update_global_state.assert_not_called()


def _enabled_state(parent_content: str) -> dict:
    parent = {
        "doc_id": "web_parent",
        "source_id": "web_parent_source",
        "title": "Parent",
        "url": "https://example.com/a",
        "query": "official evidence",
        "original_content": parent_content,
    }
    return {
        "config.info_collector_article_link_follow_enable": True,
        "config.info_collector_article_link_follow_max_urls": 3,
        "config.info_collector_webpage_enrich_fetch_timeout_seconds": 45,
        "config.llm_config": {"general": {"model_name": "model"}},
        "config.llm_config.general.model_name": "model",
        "collector_context.section_idx": 0,
        "collector_context.plan_title": "Plan",
        "collector_context.plan_thought": "Find primary evidence",
        "collector_context.step_title": "Official evidence",
        "collector_context.step_description": "Collect the original report",
        "collector_context.new_doc_infos_current_loop": [dict(parent)],
        "collector_context.doc_infos": [dict(parent)],
        "collector_context.history_queries": [
            RetrievalQuery(query="official evidence", doc_infos=[dict(parent)])
        ],
        "collector_context.source_store": {"web_parent_source": parent_content},
        "collector_context.evidence_ledger": {},
    }


@pytest.mark.asyncio
async def test_rule_selection_does_not_invoke_structured_llm():
    node = WebPageEnrichmentNode()
    state = node._pre_handle({}, Mock(
        get_global_state=Mock(side_effect=lambda key: _enabled_state(
            "See [official report](https://agency.gov/report)"
        ).get(key))
    ), Mock())
    candidate = ArticleLinkCandidate(
        candidate_index=0,
        url="https://agency.gov/report",
        canonical_url="https://agency.gov/report",
        anchor_text="official report",
        context_before="",
        context_after="",
        parent_doc_id="web_parent",
        parent_title="Parent",
        parent_url="https://example.com/a",
        query="official evidence",
    )
    node._safe_article_link_candidates = AsyncMock(return_value=[candidate])
    node._invoke_structured_llm = AsyncMock(return_value=None)
    node._follow_article_candidate = AsyncMock(return_value=None)

    await node._run_article_link_follow(state, Mock())

    node._invoke_structured_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_candidates_emit_article_link_follow_summary(caplog):
    state_values = _enabled_state("Parent content without links")
    node = WebPageEnrichmentNode()
    state = node._pre_handle(
        {},
        Mock(get_global_state=Mock(side_effect=lambda key: state_values.get(key))),
        Mock(),
    )
    node._safe_article_link_candidates = AsyncMock(return_value=[])
    caplog.set_level(
        "INFO",
        logger=(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
            "webpage_enrichment"
        ),
    )

    await node._run_article_link_follow(state, Mock())

    assert "[ArticleLinkFollow] phase=start" in caplog.text
    assert "[ArticleLinkFollow] phase=summary" in caplog.text
    assert "raw_candidate_count=0" in caplog.text
    assert "writeback_count=0" in caplog.text


@pytest.mark.asyncio
async def test_article_link_follow_cleans_temporary_source_on_early_return():
    state_values = _enabled_state("Compressed parent content")
    for key in (
        "collector_context.new_doc_infos_current_loop",
        "collector_context.doc_infos",
    ):
        state_values[key][0][ARTICLE_LINK_SOURCE_FIELD] = (
            "[official report](https://agency.gov/report)"
        )
    state_values["collector_context.history_queries"][0].doc_infos[0][
        ARTICLE_LINK_SOURCE_FIELD
    ] = "[official report](https://agency.gov/report)"
    session = Mock()
    session.get_global_state = Mock(side_effect=lambda key: state_values.get(key))
    session.update_global_state = Mock(
        side_effect=lambda payload: state_values.update(payload)
    )
    node = WebPageEnrichmentNode()
    state = node._pre_handle({}, session, Mock())
    node._safe_article_link_candidates = AsyncMock(return_value=[])

    await node._run_article_link_follow(state, session)

    assert ARTICLE_LINK_SOURCE_FIELD not in state_values[
        "collector_context.new_doc_infos_current_loop"
    ][0]
    assert ARTICLE_LINK_SOURCE_FIELD not in state_values[
        "collector_context.doc_infos"
    ][0]
    assert ARTICLE_LINK_SOURCE_FIELD not in state_values[
        "collector_context.history_queries"
    ][0].doc_infos[0]


@pytest.mark.asyncio
async def test_cleanup_queues_clean_state_before_delayed_session_commit():
    committed = _enabled_state("Committed content without sidecar")
    state = WebPageEnrichmentNode()._pre_handle(
        {},
        Mock(get_global_state=Mock(side_effect=lambda key: committed.get(key))),
        Mock(),
    )
    parent = dict(state["new_doc_infos_current_loop"][0])
    parent[ARTICLE_LINK_SOURCE_FIELD] = (
        "[official report](https://agency.gov/report)"
    )
    state["new_doc_infos_current_loop"] = [parent]
    state["doc_infos"] = [parent]
    pending_updates: list[dict] = []
    session = Mock()
    session.get_global_state = Mock(side_effect=lambda key: committed.get(key))
    session.update_global_state = Mock(side_effect=pending_updates.append)

    WebPageEnrichmentNode._cleanup_article_link_sources(state, session)

    queued_docs = pending_updates[-1][
        "collector_context.new_doc_infos_current_loop"
    ]
    assert ARTICLE_LINK_SOURCE_FIELD not in queued_docs[0]


@pytest.mark.asyncio
async def test_article_link_cleanup_preserves_successfully_written_child():
    state_values = _enabled_state("Compressed parent content")
    for key in (
        "collector_context.new_doc_infos_current_loop",
        "collector_context.doc_infos",
    ):
        state_values[key][0][ARTICLE_LINK_SOURCE_FIELD] = (
            "[official report](https://agency.gov/report)"
        )
    state_values["collector_context.history_queries"][0].doc_infos[0][
        ARTICLE_LINK_SOURCE_FIELD
    ] = "[official report](https://agency.gov/report)"
    session = Mock()
    session.get_global_state = Mock(side_effect=lambda key: state_values.get(key))
    session.update_global_state = Mock(
        side_effect=lambda payload: state_values.update(payload)
    )
    node = WebPageEnrichmentNode()
    state = node._pre_handle({}, session, Mock())
    candidate = ArticleLinkCandidate(
        candidate_index=0,
        url="https://agency.gov/report",
        canonical_url="https://agency.gov/report",
        anchor_text="official report",
        context_before="",
        context_after="",
        parent_doc_id="web_parent",
        parent_title="Parent",
        parent_url="https://example.com/a",
        query="official evidence",
    )
    child = {
        "doc_id": "web-child",
        "source_id": "web-child-source",
        "url": candidate.url,
        "query": candidate.query,
        "original_content": "Followed evidence",
    }
    node._safe_article_link_candidates = AsyncMock(return_value=[candidate])
    node._follow_article_candidate = AsyncMock(return_value=FollowedArticle(
        candidate_index=0,
        canonical_url=candidate.canonical_url,
        doc_info=child,
        source_content=child["original_content"],
    ))

    await node._run_article_link_follow(state, session)

    assert [doc["doc_id"] for doc in state_values[
        "collector_context.new_doc_infos_current_loop"
    ]] == ["web_parent", "web-child"]
    assert all(
        ARTICLE_LINK_SOURCE_FIELD not in doc
        for doc in state_values["collector_context.doc_infos"]
    )
    assert state_values["collector_context.history_queries"][0].doc_infos[-1] == child


@pytest.mark.asyncio
async def test_candidate_funnel_log_explains_existing_and_self_links(caplog):
    state_values = _enabled_state(
        "https://example.com/a https://example.com/existing"
    )
    state_values["collector_context.doc_infos"].append({
        "doc_id": "existing",
        "url": "https://example.com/existing",
        "original_content": "existing",
    })
    node = WebPageEnrichmentNode()
    state = node._pre_handle(
        {},
        Mock(get_global_state=Mock(side_effect=lambda key: state_values.get(key))),
        Mock(),
    )
    caplog.set_level(
        "INFO",
        logger=(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
            "webpage_enrichment"
        ),
    )

    await node._run_article_link_follow(state, Mock())

    assert "phase=candidate_funnel" in caplog.text
    assert "sidecar_doc_count=0" in caplog.text
    assert "sidecar_link_count=0" in caplog.text
    assert "raw_extracted_link_count=2" in caplog.text
    assert "self_link_filtered_count=1" in caplog.text
    assert "existing_url_filtered_count=1" in caplog.text
    assert "final_candidate_count=0" in caplog.text


@pytest.mark.asyncio
async def test_enabled_node_writes_independent_b_to_all_collector_state(caplog):
    state = _enabled_state("See [official report](https://agency.gov/report)")
    session = Mock()
    session.get_global_state = Mock(side_effect=lambda key: state.get(key))
    session.update_global_state = Mock()
    node = WebPageEnrichmentNode()
    caplog.set_level(
        "INFO",
        logger=(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
            "webpage_enrichment"
        ),
    )

    followed = FollowedArticle(
        candidate_index=0,
        canonical_url="https://agency.gov/report",
        doc_info={
            "doc_id": "web_b",
            "source_id": "web_b_source",
            "title": "Official report",
            "url": "https://agency.gov/report",
            "query": "official evidence",
            "original_content": "Primary evidence from B",
            "discovery": {
                "method": "article_link_follow",
                "depth": 1,
                "parent_doc_id": "web_parent",
            },
        },
        source_content="Primary evidence from B",
    )

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment.validate_public_web_url"
    ), patch.object(
        node,
        "_follow_article_candidate",
        new=AsyncMock(return_value=followed),
    ):
        result = await node.invoke({}, session, Mock())

    assert result == {}
    session.update_global_state.assert_called_once()
    payload = session.update_global_state.call_args.args[0]
    updated_docs = payload["collector_context.doc_infos"]
    updated_loop = payload["collector_context.new_doc_infos_current_loop"]
    updated_history = payload["collector_context.history_queries"]
    updated_store = payload["collector_context.source_store"]
    updated_ledger = payload["collector_context.evidence_ledger"]

    assert [doc["doc_id"] for doc in updated_docs] == ["web_parent", "web_b"]
    assert [doc["doc_id"] for doc in updated_loop] == ["web_parent", "web_b"]
    assert updated_history[0].doc_infos[-1]["doc_id"] == "web_b"
    assert updated_store["web_b_source"] == "Primary evidence from B"
    assert updated_ledger["attempted_links"] == ["https://agency.gov/report"]
    assert updated_ledger["successful_links"] == ["https://agency.gov/report"]
    assert "[ArticleLinkFollow] phase=selection" in caplog.text
    assert "selected_count=1" in caplog.text
    assert "[ArticleLinkFollow] phase=writeback" in caplog.text
    assert "successful_count=1" in caplog.text
    assert "[ArticleLinkFollow] phase=summary" in caplog.text
    assert "writeback_count=1" in caplog.text


@pytest.mark.asyncio
async def test_fixed_search_result_a_is_followed_to_b_as_independent_evidence():
    """固定搜索阶段只有 A，验证真实链接管线发现、抓取并写回 B。"""
    parent_url = "https://peps.python.org/pep-0000/"
    child_url = "https://peps.python.org/pep-0008/"
    canonical_child_url = "https://peps.python.org/pep-0008"
    parent = {
        "doc_id": "pep_0",
        "source_id": "pep_0_source",
        "title": "PEP 0 – Index of Python Enhancement Proposals",
        "url": parent_url,
        "query": "Python style guide official evidence",
        "original_content": (
            "Process and Meta-PEPs\n"
            "[PEP 8 – Style Guide for Python Code]"
            f"({child_url})"
        ),
    }
    state = {
        "config.info_collector_article_link_follow_enable": True,
        "config.info_collector_article_link_follow_max_urls": 3,
        "config.info_collector_webpage_enrich_fetch_timeout_seconds": 45,
        "config.llm_config": {"general": {"model_name": "model"}},
        "config.llm_config.general.model_name": "model",
        "collector_context.section_idx": 0,
        "collector_context.plan_title": "Python style guide",
        "collector_context.plan_thought": "Follow the official index to primary evidence",
        "collector_context.step_title": "Python style guide official evidence",
        "collector_context.step_description": "Read the linked PEP Code Lay-out rules",
        "collector_context.new_doc_infos_current_loop": [dict(parent)],
        "collector_context.doc_infos": [dict(parent)],
        "collector_context.history_queries": [
            RetrievalQuery(query=parent["query"], doc_infos=[dict(parent)])
        ],
        "collector_context.source_store": {parent["source_id"]: parent["original_content"]},
        "collector_context.evidence_ledger": {},
    }
    session = Mock()
    session.get_global_state = Mock(side_effect=lambda key: state.get(key))
    session.update_global_state = Mock()
    node = WebPageEnrichmentNode()
    fetched_content = (
        "# PEP 8 – Style Guide for Python Code\n"
        "## Code Lay-out\n"
        "Use 4 spaces per indentation level."
    )
    compressed = ArticleLinkEvidence(
        title="PEP 8 – Style Guide for Python Code",
        original_content=fetched_content,
        key_passages=["Use 4 spaces per indentation level."],
    )

    assert [doc["url"] for doc in state["collector_context.doc_infos"]] == [parent_url]

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment.validate_public_web_url"
    ), patch.object(
        node,
        "_fetch_webpage",
        new=AsyncMock(return_value={
            "url": child_url,
            "title": compressed.title,
            "content": fetched_content,
        }),
    ) as fetch, patch.object(
        node,
        "_invoke_structured_llm",
        new=AsyncMock(return_value=compressed),
    ) as compress, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment.run_doc_evaluation",
        new=AsyncMock(return_value=[{"scores": {"relevance": 10}}]),
    ):
        result = await node.invoke({}, session, Mock())

    assert result == {}
    fetch.assert_awaited_once_with(canonical_child_url, 45)
    assert compress.await_args.kwargs["agent_name"] == (
        AgentLlmName.COLLECTOR_ARTICLE_LINK_FOLLOW_COMPRESSION.value
    )
    session.update_global_state.assert_called_once()
    payload = session.update_global_state.call_args.args[0]
    updated_docs = payload["collector_context.doc_infos"]
    assert [doc["url"] for doc in updated_docs] == [parent_url, child_url]

    child = updated_docs[-1]
    assert child["discovery"]["method"] == "article_link_follow"
    assert child["discovery"]["depth"] == 1
    assert child["discovery"]["parent_doc_id"] == parent["doc_id"]
    assert child["discovery"]["parent_url"] == parent_url
    assert child["discovery"]["anchor_text"] == "PEP 8 – Style Guide for Python Code"

    ledger = payload["collector_context.evidence_ledger"]
    assert ledger["attempted_links"] == [canonical_child_url]
    assert ledger["successful_links"] == [canonical_child_url]
    assert ledger["failed_links"] == []
    assert payload["collector_context.new_doc_infos_current_loop"][-1] == child
    assert payload["collector_context.history_queries"][0].doc_infos[-1] == child
    assert payload["collector_context.source_store"][child["source_id"]] == fetched_content


@pytest.mark.asyncio
async def test_enabled_node_caps_fetches_at_three_per_loop():
    links = " ".join(
        f"[report {index}](https://example.org/report/{index})"
        for index in range(5)
    )
    state = _enabled_state(links)
    session = Mock()
    session.get_global_state = Mock(side_effect=lambda key: state.get(key))
    session.update_global_state = Mock()
    node = WebPageEnrichmentNode()
    follow = AsyncMock(return_value=None)

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment.validate_public_web_url"
    ), patch.object(node, "_follow_article_candidate", new=follow):
        await node.invoke({}, session, Mock())

    assert follow.await_count == 3


@pytest.mark.asyncio
async def test_candidate_dns_validation_runs_outside_event_loop_thread():
    node = WebPageEnrichmentNode()
    state = node._pre_handle({}, Mock(
        get_global_state=Mock(side_effect=lambda key: _enabled_state(
            "[report](https://agency.gov/report)"
        ).get(key))
    ), Mock())
    event_loop_thread = threading.get_ident()
    validation_threads: list[int] = []

    def record_validation(url: str) -> None:
        validation_threads.append(threading.get_ident())

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment.validate_public_web_url",
        side_effect=record_validation,
    ):
        candidates = await node._safe_article_link_candidates(state)

    assert len(candidates) == 1
    assert validation_threads
    assert all(thread_id != event_loop_thread for thread_id in validation_threads)


@pytest.mark.asyncio
async def test_sensitive_safety_filter_log_redacts_rejected_url(caplog):
    node = WebPageEnrichmentNode()
    secret_url = "https://secret.example/private-report"
    state = node._pre_handle({}, Mock(
        get_global_state=Mock(side_effect=lambda key: _enabled_state(
            f"[private report]({secret_url})"
        ).get(key))
    ), Mock())
    caplog.set_level(
        "INFO",
        logger=(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
            "webpage_enrichment"
        ),
    )

    with patch.object(LogManager, "is_sensitive", return_value=True), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
        "webpage_enrichment.validate_public_web_url",
        side_effect=ValueError("private diagnostic detail"),
    ):
        candidates = await node._safe_article_link_candidates(state)

    assert candidates == []
    assert "phase=safety_filter" in caplog.text
    assert "unsafe_count=1" in caplog.text
    assert secret_url not in caplog.text
    assert "private diagnostic detail" not in caplog.text


@pytest.mark.asyncio
async def test_follow_candidate_reuses_webpage_fetch_pipeline():
    node = WebPageEnrichmentNode()
    node.llm = Mock()
    state = {
        "fetch_timeout_seconds": 45,
        "step_title": "Official evidence",
        "step_description": "Collect primary data",
        "section_idx": 0,
        "session": Mock(),
    }
    candidate = ArticleLinkCandidate(
        candidate_index=0,
        url="https://agency.gov/report",
        canonical_url="https://agency.gov/report",
        anchor_text="official report",
        context_before="",
        context_after="",
        parent_doc_id="web_parent",
        parent_title="Parent",
        parent_url="https://example.com/a",
        query="official evidence",
    )
    evidence = ArticleLinkEvidence(
        title="Official report",
        original_content="Primary evidence",
        key_passages=["Primary evidence"],
    )
    event_loop_thread = threading.get_ident()
    final_url_validation_threads: list[int] = []

    def record_final_url_validation(url: str) -> None:
        final_url_validation_threads.append(threading.get_ident())

    with patch.object(
        node,
        "_fetch_webpage",
        new=AsyncMock(return_value={
            "url": "https://agency.gov/report",
            "status_code": 200,
            "title": "Official report",
            "content": "Fetched body",
        }),
    ) as shared_fetch, patch.object(
        node,
        "_compress_article_link_content",
        new=AsyncMock(return_value=evidence),
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment.run_doc_evaluation",
        new=AsyncMock(return_value=[{"scores": {"relevance": 8}}]),
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment.validate_public_web_url",
        side_effect=record_final_url_validation,
    ):
        followed = await node._follow_article_candidate(state, candidate, "primary source")

    shared_fetch.assert_awaited_once_with("https://agency.gov/report", 45)
    assert final_url_validation_threads
    assert all(thread_id != event_loop_thread for thread_id in final_url_validation_threads)
    assert followed is not None
    assert followed.doc_info["url"] == "https://agency.gov/report"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("structured_result", "expected_outcome", "expected_reason"),
    [
        (None, "empty", "structured_result_none"),
        (
            ArticleLinkEvidence(original_content=""),
            "empty",
            "empty_original_content",
        ),
        (
            ArticleLinkEvidence(
                original_content="Primary evidence",
                key_passages=["Primary evidence"],
            ),
            "success",
            "none",
        ),
    ],
)
async def test_article_link_compression_logs_bounded_result_diagnostics(
    caplog,
    structured_result,
    expected_outcome,
    expected_reason,
):
    node = WebPageEnrichmentNode()
    state = {
        "step_title": "Official evidence",
        "step_description": "Collect primary data",
        "section_idx": 4,
        "session": Mock(),
    }
    candidate = ArticleLinkCandidate(
        candidate_index=0,
        url="https://agency.gov/report",
        canonical_url="https://agency.gov/report",
        anchor_text="official report",
        context_before="",
        context_after="",
        parent_doc_id="web_parent",
        parent_title="Parent",
        parent_url="https://example.com/a",
        query="official evidence",
    )
    fetched = {
        "url": "https://agency.gov/final-report",
        "title": "Official report",
        "fetch_method": "jina_reader",
        "content": "Fetched body",
    }
    node._invoke_structured_llm = AsyncMock(return_value=structured_result)
    caplog.set_level(
        "INFO",
        logger=(
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
            "webpage_enrichment"
        ),
    )

    await node._compress_article_link_content(
        state, candidate, "primary source", fetched
    )

    assert "phase=compression_diagnostic" in caplog.text
    assert "outcome=start" in caplog.text
    assert f"outcome={expected_outcome}" in caplog.text
    assert f"empty_reason={expected_reason}" in caplog.text
    assert "fetched_content_length=12" in caplog.text
    assert "payload_content_length=12" in caplog.text
    assert "fetch_method=jina_reader" in caplog.text
    assert "redirected=true" in caplog.text
    assert "title_present=true" in caplog.text


@pytest.mark.asyncio
async def test_follow_candidate_rejects_empty_evaluation_result():
    """文档评价失败或无有效输出时不得接纳 B。"""
    node = WebPageEnrichmentNode()
    node.llm = Mock()
    state = {
        "fetch_timeout_seconds": 45,
        "step_title": "Official evidence",
        "step_description": "Collect primary data",
        "section_idx": 0,
    }
    candidate = ArticleLinkCandidate(
        candidate_index=0,
        url="https://agency.gov/report",
        canonical_url="https://agency.gov/report",
        anchor_text="official report",
        context_before="",
        context_after="",
        parent_doc_id="web_parent",
        parent_title="Parent",
        parent_url="https://example.com/a",
        query="official evidence",
    )
    evidence = ArticleLinkEvidence(
        title="Official report",
        original_content="Primary evidence",
        key_passages=["Primary evidence"],
    )

    with patch.object(
        node,
        "_fetch_webpage",
        new=AsyncMock(return_value={
            "url": candidate.url,
            "title": "Official report",
            "content": "Fetched body",
        }),
    ), patch.object(
        node,
        "_compress_article_link_content",
        new=AsyncMock(return_value=evidence),
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment.run_doc_evaluation",
        new=AsyncMock(return_value=[]),
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment.validate_public_web_url"
    ):
        followed = await node._follow_article_candidate(state, candidate, "primary source")

    assert followed is None


def test_redirect_to_existing_document_is_recorded_as_failed_not_successful():
    """最终 URL 已存在时不得写入 B 或把候选 URL 记为成功。"""
    state = {
        "doc_infos": [{"url": "https://agency.gov/final", "doc_id": "existing"}],
        "new_doc_infos_current_loop": [],
        "history_queries": [],
        "source_store": {},
        "evidence_ledger": {},
    }
    state["evidence_ledger"] = ensure_ledger(state["evidence_ledger"])
    candidate = ArticleLinkCandidate(
        candidate_index=0,
        url="https://agency.gov/start",
        canonical_url="https://agency.gov/start",
        anchor_text="report",
        context_before="",
        context_after="",
        parent_doc_id="parent",
        parent_title="Parent",
        parent_url="https://example.com/a",
        query="evidence",
    )
    followed = FollowedArticle(
        candidate_index=0,
        canonical_url=candidate.canonical_url,
        doc_info={
            "url": "https://agency.gov/final",
            "doc_id": "duplicate",
            "source_id": "duplicate-source",
            "query": "evidence",
        },
        source_content="duplicate",
    )
    session = Mock()

    WebPageEnrichmentNode._write_article_link_results(
        session,
        state,
        [(0, "primary")],
        [candidate],
        [followed],
    )

    payload = session.update_global_state.call_args.args[0]
    assert [doc["doc_id"] for doc in payload["collector_context.doc_infos"]] == ["existing"]
    ledger = payload["collector_context.evidence_ledger"]
    assert ledger["successful_links"] == []
    assert ledger["failed_links"] == ["https://agency.gov/start"]


def test_article_link_writeback_does_not_mutate_input_history_objects():
    """集中写回应产生新 history 对象，不得原地修改输入 state。"""
    parent_doc = {"doc_id": "parent", "source_id": "parent-source"}
    history_item = RetrievalQuery(query="evidence", doc_infos=[parent_doc])
    state = {
        "doc_infos": [parent_doc],
        "new_doc_infos_current_loop": [parent_doc],
        "history_queries": [history_item],
        "source_store": {},
        "evidence_ledger": {},
    }
    state["evidence_ledger"] = ensure_ledger(state["evidence_ledger"])
    candidate = ArticleLinkCandidate(
        candidate_index=0,
        url="https://agency.gov/report",
        canonical_url="https://agency.gov/report",
        anchor_text="report",
        context_before="",
        context_after="",
        parent_doc_id="parent",
        parent_title="Parent",
        parent_url="https://example.com/a",
        query="evidence",
    )
    followed = FollowedArticle(
        candidate_index=0,
        canonical_url=candidate.canonical_url,
        doc_info={
            "url": candidate.url,
            "doc_id": "child",
            "source_id": "child-source",
            "query": "evidence",
        },
        source_content="child evidence",
    )
    session = Mock()

    WebPageEnrichmentNode._write_article_link_results(
        session,
        state,
        [(0, "primary")],
        [candidate],
        [followed],
    )

    payload_history = session.update_global_state.call_args.args[0][
        "collector_context.history_queries"
    ]
    assert [doc["doc_id"] for doc in history_item.doc_infos] == ["parent"]
    assert [doc["doc_id"] for doc in payload_history[0].doc_infos] == ["parent", "child"]
