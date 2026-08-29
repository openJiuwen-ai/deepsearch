from unittest.mock import AsyncMock, Mock, patch

import pytest
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from pydantic import ValidationError

from openjiuwen_deepsearch.config.config import (
    AgentConfig,
    ScholarlySearchConfig,
    WebSearchEngineConfig,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder import (
    GenerateQueryNode,
    SearchQueryItem,
    SearchQueryList,
    build_target_paper_locator_items,
    normalize_search_query_item,
    route_secondary_search_engine_for_query,
    route_secondary_search_engines_for_query,
    build_retrieval_queries,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import (
    EvidenceLedger,
    build_ledger_brief,
    ensure_ledger,
    merge_ledger_update,
    target_paper_key,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import (
    DirectSearchRequest,
    InfoRetrievalNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import RetrievalQuery
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
    _initialize_web_search_context_from_agent_config,
    _zero_scholarly_search_secrets,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search import (
    ArxivSearchAPIWrapper,
    PubMedSearchAPIWrapper,
    SemanticScholarSearchAPIWrapper,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import search_engine_mapping
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import web_search_context


def _agent_input() -> dict:
    return {
        "messages": [],
        "web_page_search_record": [],
        "local_text_search_record": [],
        "other_tool_record": [],
        "research_intent": {},
    }


def test_scholarly_config_defaults_are_independent_from_web_search():
    config = AgentConfig()
    scholarly = config.scholarly_search_config

    assert config.scholarly_search_enabled is False
    assert scholarly.max_full_text_results_per_query == 1
    assert scholarly.fetch_full_text is True
    assert scholarly.pubmed.max_search_results == 1
    assert scholarly.pubmed.requests_per_second == pytest.approx(1 / 3)
    assert scholarly.arxiv.requests_per_second == pytest.approx(1 / 3)
    assert scholarly.semantic_scholar.requests_per_second == 0.5


def test_scholarly_provider_and_shared_overrides_are_typed():
    config = AgentConfig(
        scholarly_search_config={
            "max_full_text_results_per_query": 3,
            "semantic_scholar": {
                "max_search_results": 4,
                "requests_per_second": 0.75,
            },
            "pubmed": {"email": "research@example.com", "tool": "deepsearch"},
        }
    )

    assert config.scholarly_search_config.max_full_text_results_per_query == 3
    assert config.scholarly_search_config.semantic_scholar.max_search_results == 4
    assert config.scholarly_search_config.semantic_scholar.requests_per_second == 0.75
    assert config.scholarly_search_config.pubmed.email == "research@example.com"
    assert config.scholarly_search_config.pubmed.tool == "deepsearch"


@pytest.mark.parametrize(
    "override",
    [
        {"max_full_text_results_per_query": -1},
        {"full_text_timeout_seconds": 7},
        {"max_full_text_length": 1234},
        {"max_full_text_download_bytes": 5678},
        {"minimum_full_text_length": 321},
        {"max_pdf_pages": 42},
        {"parse_timeout_seconds": 8},
        {"max_redirects": 2},
        {"semantic_scholar": {"max_search_results": 0}},
        {"semantic_scholar": {"requests_per_second": 0}},
        {"semantic_scholar": {"search_url": "ftp://example.com/works"}},
        {"unknown_option": True},
    ],
)
def test_scholarly_config_rejects_invalid_values(override):
    with pytest.raises(ValidationError):
        ScholarlySearchConfig.model_validate(override)


def test_scholarly_provider_urls_default_to_empty_config_values():
    config = ScholarlySearchConfig()

    assert config.pubmed.search_url == ""
    assert config.arxiv.search_url == ""
    assert config.semantic_scholar.search_url == ""


def test_scholarly_config_rejects_retired_provider_dictionary():
    with pytest.raises(ValidationError, match="scholarly_search_engine_configs"):
        AgentConfig(scholarly_search_engine_configs={"semantic_scholar": {}})


def test_scholarly_provider_secrets_are_zeroed_for_model_and_dump():
    config = AgentConfig(
        scholarly_search_config={
            "pubmed": {"search_api_key": bytearray(b"pubmed")},
            "semantic_scholar": {"search_api_key": bytearray(b"semantic")},
        }
    )
    dumped = config.model_dump()

    _zero_scholarly_search_secrets(config)
    _zero_scholarly_search_secrets(dumped)

    assert config.scholarly_search_config.pubmed.search_api_key == bytearray(b"\0" * 6)
    assert config.scholarly_search_config.semantic_scholar.search_api_key == bytearray(b"\0" * 8)
    assert dumped["scholarly_search_config"]["pubmed"]["search_api_key"] == bytearray(b"\0" * 6)
    assert dumped["scholarly_search_config"]["semantic_scholar"]["search_api_key"] == bytearray(b"\0" * 8)


def test_vertical_search_engines_are_registered_but_not_primary_configurable():
    assert search_engine_mapping["pubmed"] is PubMedSearchAPIWrapper
    assert search_engine_mapping["arxiv"] is ArxivSearchAPIWrapper
    assert search_engine_mapping["semantic_scholar"] is SemanticScholarSearchAPIWrapper
    with pytest.raises(ValidationError):
        WebSearchEngineConfig(search_engine_name="pubmed")
    with pytest.raises(ValidationError):
        WebSearchEngineConfig(search_engine_name="arxiv")


def test_web_search_context_registers_academic_engines_for_research_only():
    config = AgentConfig(
        scholarly_search_enabled=True,
        web_search_engine_config={
            "search_engine_name": "jina",
            "max_web_search_results": 3,
        },
    )

    research_token = _initialize_web_search_context_from_agent_config(config)
    try:
        engines = web_search_context.get()
        assert set(engines) == {"jina", "pubmed", "arxiv", "semantic_scholar"}
        assert engines["jina"].max_web_search_results == 3
        assert engines["pubmed"].max_web_search_results == 1
        assert engines["arxiv"].max_web_search_results == 1
        assert engines["semantic_scholar"].max_web_search_results == 1
    finally:
        web_search_context.reset(research_token)

    disabled_config = AgentConfig(web_search_engine_config={"search_engine_name": "jina"})
    deepsearch_token = _initialize_web_search_context_from_agent_config(disabled_config)
    try:
        assert set(web_search_context.get()) == {"jina"}
    finally:
        web_search_context.reset(deepsearch_token)


def test_disabled_scholarly_search_removes_query_level_vertical_route():
    assert normalize_search_query_item(
        SearchQueryItem(query="glioblastoma trial", search_engine_name="pubmed"),
        enable_scholarly_search=False,
    ).search_engine_names == []
    assert normalize_search_query_item(
        "LLM RAG benchmark",
        enable_scholarly_search=False,
    ).search_engine_names == []


@pytest.mark.parametrize(
    "query",
    [
        "https://pubmed.ncbi.nlm.nih.gov/38132429/",
        "https://arxiv.org/abs/1706.03762v7",
    ],
)
def test_disabled_scholarly_search_preserves_academic_url_for_default_web_search(query):
    item = normalize_search_query_item(
        SearchQueryItem(query=query, search_engine_name=""),
        enable_scholarly_search=False,
    )

    assert item == SearchQueryItem(query=query, search_engine_name="")


def test_legacy_engine_extension_cannot_enable_scholarly_search():
    config = AgentConfig(
        web_search_engine_config={
            "search_engine_name": "jina",
            "extension": {"scholarly_search_enabled": True},
        }
    )

    token = _initialize_web_search_context_from_agent_config(config)
    try:
        assert set(web_search_context.get()) == {"jina"}
    finally:
        web_search_context.reset(token)


def test_academic_engine_configs_are_applied_independently():
    config = AgentConfig(
        scholarly_search_enabled=True,
        web_search_engine_config={
            "search_engine_name": "jina",
            "search_api_key": bytearray(b"jina-secret"),
            "max_web_search_results": 3,
        },
        scholarly_search_config={
            "semantic_scholar": {
                "search_api_key": bytearray(b"semantic-secret"),
                "max_search_results": 2,
                "requests_per_second": 0.75,
            },
        },
    )

    token = _initialize_web_search_context_from_agent_config(config)
    try:
        engines = web_search_context.get()
        assert engines["semantic_scholar"]._headers() == {"x-api-key": "semantic-secret"}
        assert engines["semantic_scholar"].max_web_search_results == 2
        assert engines["semantic_scholar"].requests_per_second == 0.75
        assert engines["pubmed"].max_web_search_results == 1
        assert engines["jina"].search_api_key == bytearray(b"jina-secret")
    finally:
        web_search_context.reset(token)


def test_semantic_scholar_key_is_optional_in_academic_config():
    config = AgentConfig(
        scholarly_search_enabled=True,
        scholarly_search_config={
            "semantic_scholar": {"max_search_results": 2},
        },
    )

    token = _initialize_web_search_context_from_agent_config(config)
    try:
        wrapper = web_search_context.get()["semantic_scholar"]
        assert wrapper._headers() == {}
        assert wrapper.requests_per_second == 0.5
    finally:
        web_search_context.reset(token)


def test_scholarly_wrappers_use_only_scholarly_provider_config():
    config = AgentConfig(
        scholarly_search_enabled=True,
        web_search_engine_config={
            "search_engine_name": "jina",
            "search_api_key": bytearray(b"primary-secret"),
            "search_url": "https://primary.example/search",
            "max_web_search_results": 9,
            "extension": {"include_domains": ["primary.example"]},
        },
        scholarly_search_config={"semantic_scholar": {"max_search_results": 3}},
    )

    token = _initialize_web_search_context_from_agent_config(config)
    try:
        engines = web_search_context.get()
        assert engines["jina"].search_api_key == bytearray(b"primary-secret")
        assert engines["semantic_scholar"].max_web_search_results == 3
        assert engines["semantic_scholar"].search_api_key == bytearray()
        assert engines["semantic_scholar"]._url() != "https://primary.example/search"
        assert engines["semantic_scholar"].extension in (None, {})
    finally:
        web_search_context.reset(token)


def test_retrieval_query_stores_aggregate_engine_plan():
    query_list = SearchQueryList(
        missing_evidence=["evidence"],
        queries=[SearchQueryItem(query="glioblastoma trial", search_engine_name="pubmed")],
    )
    item = normalize_search_query_item(query_list.queries[0])
    retrieval_query = RetrievalQuery(
        query=item.query,
        primary_engine="jina",
        secondary_engines=item.search_engine_names or [],
    )

    assert retrieval_query.query == "glioblastoma trial"
    assert retrieval_query.primary_engine == "jina"
    assert retrieval_query.secondary_engines == ["pubmed"]


def test_retrieval_query_defaults_to_primary_only_plan():
    query = RetrievalQuery(query="policy evidence")
    assert query.primary_engine == ""
    assert query.secondary_engines == []
    assert query.scholarly_full_text_config == {}


def test_explicit_empty_secondary_engine_is_preserved():
    item = normalize_search_query_item(
        SearchQueryItem(query="LLM RAG benchmark", search_engine_names=[])
    )

    assert item.search_engine_names == []


def test_missing_secondary_engine_uses_heuristic_routing():
    item = normalize_search_query_item(SearchQueryItem(query="LLM RAG benchmark"))

    assert item.search_engine_names == ["arxiv", "semantic_scholar"]


def test_legacy_string_query_uses_heuristic_routing():
    item = normalize_search_query_item("glioblastoma clinical trial")

    assert item.search_engine_names == ["pubmed", "semantic_scholar"]


def test_legacy_singular_query_input_is_converted_at_validation_boundary():
    item = SearchQueryItem.model_validate({
        "query": "glioblastoma clinical trial",
        "search_engine_name": "pubmed",
    })

    assert item.search_engine_names == ["pubmed"]
    assert "search_engine_name" not in SearchQueryItem.model_json_schema()["properties"]


@pytest.mark.parametrize(
    ("query", "expected_query", "expected_engine"),
    [
        ("https://pubmed.ncbi.nlm.nih.gov/38132429/", "38132429", "pubmed"),
        ("https://arxiv.org/abs/1706.03762v7", "1706.03762", "arxiv"),
    ],
)
def test_academic_paper_url_is_deterministically_routed_by_identifier(
    query, expected_query, expected_engine
):
    item = normalize_search_query_item(SearchQueryItem(query=query, search_engine_name=""))

    assert item.query == expected_query
    assert item.search_engine_names == [expected_engine]


@pytest.mark.parametrize(
    ("target_paper", "expected_query", "expected_engine"),
    [
        ({"pmid": "38132429"}, "38132429", "pubmed"),
        ({"arxiv_id": "1706.03762v7"}, "1706.03762", "arxiv"),
        (
            {"url": "https://arxiv.org/abs/1706.03762v7"},
            "1706.03762",
            "arxiv",
        ),
        ({"title": "Attention Is All You Need"}, "Attention Is All You Need", ""),
    ],
)
def test_target_paper_constraint_injects_exact_locator(
    target_paper, expected_query, expected_engine
):
    items = build_target_paper_locator_items(
        {"target_papers": [target_paper]},
        EvidenceLedger(),
    )

    assert items == [
        SearchQueryItem(query=expected_query, search_engine_name=expected_engine)
    ]


def test_confirmed_target_paper_does_not_inject_locator():
    target = {"arxiv_id": "1706.03762v7"}
    ledger = EvidenceLedger(confirmed_target_papers=[target_paper_key(target)])

    assert build_target_paper_locator_items(
        {"target_papers": [target]},
        ledger,
    ) == []


def test_confirmed_target_paper_drops_llm_generated_exact_locator():
    target = {
        "title": "Attention Is All You Need",
        "arxiv_id": "1706.03762",
        "url": "https://arxiv.org/abs/1706.03762v7",
    }
    state = {
        "collector_context.evidence_ledger": EvidenceLedger(
            confirmed_target_papers=[target_paper_key(target)]
        ).model_dump(),
        "collector_context.research_intent": {"target_papers": [target]},
        "collector_context.max_search_query_count": 5,
        "collector_context.section_idx": 1,
    }
    session = Mock()
    session.get_global_state.side_effect = state.get
    session.update_global_state.side_effect = lambda values: state.update(values)

    GenerateQueryNode()._post_handle(
        {},
        SearchQueryList(queries=[SearchQueryItem(query="1706.03762", search_engine_name="arxiv")]),
        session,
        Mock(),
    )

    assert state["collector_context.search_queries"] == []


def test_disabled_scholarly_search_routes_target_paper_locator_to_default_web():
    state = {
        "collector_context.evidence_ledger": EvidenceLedger().model_dump(),
        "collector_context.research_intent": {"target_papers": [{"pmid": "38132429"}]},
        "collector_context.max_search_query_count": 5,
        "collector_context.section_idx": 1,
    }
    session = Mock()
    session.get_global_state.side_effect = state.get
    session.update_global_state.side_effect = lambda values: state.update(values)
    token = web_search_context.set({"jina": Mock()})
    try:
        GenerateQueryNode()._post_handle(
            {},
            SearchQueryList(queries=[]),
            session,
            Mock(),
        )
    finally:
        web_search_context.reset(token)

    assert state["collector_context.search_queries"] == [
        RetrievalQuery(query="38132429", primary_engine="petal")
    ]


def test_fallback_secondary_engine_routing():
    assert route_secondary_search_engine_for_query("glioblastoma clinical trial") == "pubmed"
    assert route_secondary_search_engine_for_query("gene expression analysis") == "pubmed"
    assert route_secondary_search_engine_for_query("genes associated with cancer") == "pubmed"
    assert route_secondary_search_engine_for_query("proteins in disease pathways") == "pubmed"
    assert route_secondary_search_engine_for_query("patients in clinical trials") == "pubmed"
    assert route_secondary_search_engine_for_query("drug discovery pipeline") == "pubmed"
    assert route_secondary_search_engine_for_query("LLM RAG benchmark") == "arxiv"
    assert route_secondary_search_engine_for_query("generative AI models") == "arxiv"
    assert route_secondary_search_engine_for_query("physics simulation benchmark") == "arxiv"
    assert route_secondary_search_engine_for_query("Apple annual revenue") == ""
    assert route_secondary_search_engine_for_query("metaphysics philosophy") == ""
    assert route_secondary_search_engine_for_query("general productivity software") == ""
    assert route_secondary_search_engine_for_query("generation planning methods") == ""
    assert route_secondary_search_engine_for_query("generic database indexing") == ""
    assert route_secondary_search_engine_for_query("Japan pension reform social security statistics") == ""
    assert route_secondary_search_engine_for_query("public policy benchmark pension reform") == ""


def test_multi_engine_academic_routing_builds_one_aggregate_query():
    assert route_secondary_search_engines_for_query("peer reviewed climate attribution study") == [
        "semantic_scholar"
    ]
    assert route_secondary_search_engines_for_query("LLM clinical diagnosis trial") == [
        "pubmed", "arxiv", "semantic_scholar"
    ]
    assert route_secondary_search_engines_for_query("search Semantic Scholar for transformers") == [
        "semantic_scholar"
    ]
    item = normalize_search_query_item(SearchQueryItem(
        query="q", search_engine_names=["semantic_scholar", "semantic_scholar"]
    ))
    assert item.search_engine_names == ["semantic_scholar"]
    queries = build_retrieval_queries([item], primary_engine="jina", max_queries=4)
    assert len(queries) == 1
    assert queries[0].query == "q"
    assert queries[0].primary_engine == "jina"
    assert queries[0].secondary_engines == ["semantic_scholar"]


def test_aggregate_query_carries_shared_full_text_policy_from_scholarly_config():
    queries = build_retrieval_queries(
        [SearchQueryItem(query="paper", search_engine_names=["semantic_scholar"])],
        primary_engine="jina",
        max_queries=4,
        scholarly_config=ScholarlySearchConfig(
            fetch_full_text=False,
            max_full_text_results_per_query=2,
        ),
    )

    assert queries[0].scholarly_full_text_config == {
        "enabled": False,
        "timeout_seconds": 30.0,
        "max_text_length": 10_000,
        "max_download_bytes": 25 * 1024 * 1024,
        "minimum_text_length": 200,
        "max_pdf_pages": 200,
        "parse_timeout_seconds": 30.0,
        "max_redirects": 5,
    }
    assert queries[0].max_full_text_results == 2


def test_engine_list_keeps_primary_and_deduplicates_secondaries():
    node = InfoRetrievalNode()

    query = RetrievalQuery(
        query="q",
        primary_engine="tavily",
        secondary_engines=["pubmed", "arxiv", "pubmed", "tavily"],
    )
    assert node._engine_names_for_query(query) == ["tavily", "pubmed", "arxiv"]


@pytest.mark.asyncio
async def test_direct_path_applies_shared_tool_call_budget_in_route_order():
    node = InfoRetrievalNode()
    retrieval_query = RetrievalQuery(
        query="LLM clinical diagnosis trial",
        primary_engine="jina",
        secondary_engines=["pubmed", "arxiv", "semantic_scholar"],
    )
    state = {
        "section_idx": 0,
        "step_title": "cross-domain evidence",
        "search_query": retrieval_query.query,
        "max_tool_call_turns_per_query": 2,
        "search_method": "web",
        "web_search_engine_name": "jina",
        "retrieval_query": retrieval_query,
        "api_tools_config": {"collector_tools": []},
    }
    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=lambda args: {
        "search_engine": args["search_engine_name"],
        "search_results": [],
    })

    with patch.object(node, "_prepare_collector_tool", return_value=([], {"web_search_tool": web_tool})), \
            patch.object(node, "_structure_result", AsyncMock(return_value=([], {}))):
        await node._collector_main(state)

    assert [call.args[0]["search_engine_name"] for call in web_tool.invoke.await_args_list] == [
        "jina", "pubmed",
    ]


@pytest.mark.asyncio
async def test_llm_path_gives_secondary_engines_only_the_remaining_budget():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "search_query": "clinical trial",
        "web_search_engine_name": "tavily",
        "max_tool_call_turns_per_query": 2,
        "retrieval_query": RetrievalQuery(
            query="clinical trial",
            primary_engine="tavily",
            secondary_engines=["pubmed", "semantic_scholar"],
        ),
    }
    agent_input = {
        "messages": [{"name": "local_search_tool"}],
        "web_page_search_record": [],
        "local_text_search_record": [],
        "other_tool_record": [],
        "tool_calls_used": 1,
    }
    web_tool = Mock()
    web_tool.invoke = AsyncMock(return_value={
        "search_engine": "pubmed",
        "search_results": [],
    })

    await node._run_secondary_web_search_if_needed(
        state, agent_input, {"web_search_tool": web_tool}
    )

    web_tool.invoke.assert_awaited_once_with({
        "query": "clinical trial",
        "search_engine_name": "tavily",
    })


@pytest.mark.asyncio
async def test_non_web_llm_tool_call_does_not_consume_web_search_budget():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "search_query": "clinical trial",
        "max_tool_call_turns_per_query": 2,
    }
    agent_input = _agent_input()
    responses = iter([
        {"tool_calls": [{"name": "local_search_tool", "args": {}}]},
        {"tool_calls": []},
    ])

    with patch.object(
        node,
        "_invoke_llm_with_retry",
        AsyncMock(side_effect=lambda *_: next(responses)),
    ), patch.object(node, "_process_llm_response", AsyncMock(side_effect=lambda _, value, *__: value)):
        await node._collector_llm(state, agent_input, [], {})

    assert agent_input.get("tool_calls_used", 0) == 0


@pytest.mark.asyncio
async def test_llm_repeated_web_requests_execute_each_requested_query_within_budget():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "search_query": "clinical trial",
        "web_search_engine_name": "tavily",
        "max_tool_call_turns_per_query": 2,
        "retrieval_query": RetrievalQuery(
            query="clinical trial",
            primary_engine="tavily",
            secondary_engines=["pubmed", "semantic_scholar"],
        ),
    }
    agent_input = _agent_input()
    responses = iter([
        {"tool_calls": [{"id": "web-1", "name": "web_search_tool", "args": {"query": "first"}}]},
        {"tool_calls": [{"id": "web-2", "name": "web_search_tool", "args": {"query": "second"}}]},
    ])
    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=lambda args: {
        "search_engine": args["search_engine_name"],
        "search_results": [],
    })

    with patch.object(
        node,
        "_invoke_llm_with_retry",
        AsyncMock(side_effect=lambda *_: next(responses)),
    ):
        await node._collector_llm(state, agent_input, [], {"web_search_tool": web_tool})
        await node._run_secondary_web_search_if_needed(
            state,
            agent_input,
            {"web_search_tool": web_tool},
        )

    assert [call.args[0] for call in web_tool.invoke.await_args_list] == [
        {"query": "first", "search_engine_name": "tavily"},
        {"query": "second", "search_engine_name": "tavily"},
    ]
    assert agent_input["tool_calls_used"] == 2


@pytest.mark.asyncio
async def test_full_text_resolution_uses_post_fusion_top_n():
    query = RetrievalQuery(
        query="paper",
        primary_engine="jina",
        secondary_engines=["semantic_scholar"],
        max_full_text_results=2,
        scholarly_full_text_config={
            "enabled": True,
            "timeout_seconds": 7.0,
            "max_text_length": 1234,
            "max_download_bytes": 5678,
        },
    )
    documents = [
        {"title": title, "full_text_candidates": [{"url": f"https://example.org/{title}"}]}
        for title in ("first", "second", "third")
    ]

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector."
        "resolve_scholarly_full_text",
        new_callable=AsyncMock,
    ) as resolve:
        await InfoRetrievalNode._resolve_query_full_text(query, {"doc_infos": documents})

    assert [call.args[0]["title"] for call in resolve.await_args_list] == ["first", "second"]


@pytest.mark.asyncio
async def test_full_text_resolution_uses_explicit_query_budget_not_largest_provider_budget():
    query = RetrievalQuery(
        query="paper",
        primary_engine="jina",
        secondary_engines=["semantic_scholar", "pubmed"],
        max_full_text_results=2,
        scholarly_full_text_config={"enabled": True},
    )
    documents = [
        {
            "title": f"paper-{index}",
            "full_text_candidates": [{"url": f"https://example.org/{index}", "source": "pubmed"}],
        }
        for index in range(3)
    ]

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector."
        "resolve_scholarly_full_text",
        new_callable=AsyncMock,
    ) as resolve:
        await InfoRetrievalNode._resolve_query_full_text(query, {"doc_infos": documents})

    assert [call.args[0]["title"] for call in resolve.await_args_list] == ["paper-0", "paper-1"]


@pytest.mark.asyncio
async def test_full_text_resolution_preserves_fused_input_order():
    node = InfoRetrievalNode()
    query = RetrievalQuery(
        query="paper",
        primary_engine="jina",
        secondary_engines=["semantic_scholar"],
        scholarly_full_text_config={"enabled": True},
    )
    result = {"doc_infos": [
        {
            "title": "low", "scores": {"relevance": 0.1},
            "full_text_candidates": [{"url": "https://example.org/low"}],
        },
        {
            "title": "high", "scores": {"relevance": 0.9},
            "full_text_candidates": [{"url": "https://example.org/high"}],
        },
    ]}

    with patch.object(node, "_collector_main", AsyncMock(return_value=result)), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector."
        "resolve_scholarly_full_text",
        new_callable=AsyncMock,
    ) as resolve:
        output = await node._run_retrieval_query({}, query)

    resolve.assert_awaited_once()
    assert resolve.await_args.args[0]["title"] == "low"
    assert [document["title"] for document in output["doc_infos"]] == ["low", "high"]


@pytest.mark.asyncio
async def test_full_text_rebuilds_source_store_and_evidence_references():
    node = InfoRetrievalNode()
    query = RetrievalQuery(
        query="paper evidence",
        primary_engine="jina",
        secondary_engines=["semantic_scholar"],
        scholarly_full_text_config={"enabled": True},
    )
    original = {
        "title": "Paper",
        "url": "https://www.semanticscholar.org/paper/S1",
        "content": "short abstract",
        "original_content": "short abstract",
        "academic_source": "semantic_scholar",
        "academic_source_id": "W1",
        "scores": {"relevance": 0.9},
        "full_text_candidates": [{"url": "https://example.org/paper", "source": "semantic_scholar"}],
    }
    result = {
        "doc_infos": [original],
        "source_store": {"old-source": "short abstract"},
    }

    async def resolve(document, **_kwargs):
        document.update({
            "full_text": "paper evidence appears throughout the complete article",
            "full_text_status": "available",
            "content_type": "full_text",
            "full_text_url": "https://example.org/paper",
            "full_text_format": "html",
            "full_text_truncated": False,
        })

    with patch.object(node, "_collector_main", AsyncMock(return_value=result)), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector."
        "resolve_scholarly_full_text",
        new=AsyncMock(side_effect=resolve),
    ):
        output = await node._run_retrieval_query({}, query)

    document = output["doc_infos"][0]
    source_id = document["content_ref"]["source_id"]
    assert output["source_store"][source_id] == document["original_content"]
    assert "complete article" in output["source_store"][source_id]
    assert document["evidence_content_type"] == "full_text"
    assert document["evidence_content_chars"] == len(document["original_content"])
    assert any("paper evidence" in passage.lower() for passage in document["key_passages"])


@pytest.mark.asyncio
@pytest.mark.parametrize(("retryable", "expected_secondary_calls"), [(True, 3), (False, 1)])
async def test_direct_parallel_path_honors_secondary_retryability(
        retryable,
        expected_secondary_calls,
):
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "search_query": "glioblastoma clinical trial",
        "max_tool_call_turns_per_query": 2,
        "search_method": "web",
        "web_search_engine_name": "tavily",
        "retrieval_query": RetrievalQuery(
            query="glioblastoma clinical trial", primary_engine="tavily", secondary_engines=["pubmed"]
        ),
        "api_tools_config": {"collector_tools": []},
    }
    calls = []

    async def invoke(args):
        calls.append(args)
        if args["search_engine_name"] == "tavily":
            return {"search_engine": "tavily", "search_results": []}
        return {
            "search_engine": "pubmed",
            "search_results": [],
            "error": "search failed",
            "retryable": retryable,
        }

    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=invoke)

    with patch.object(node, "_prepare_collector_tool", return_value=([], {"web_search_tool": web_tool})), \
            patch.object(node, "_structure_result", AsyncMock(return_value=([], {}))):
        await node._collector_main(state)

    assert [item["search_engine_name"] for item in calls].count("tavily") == 1
    assert [item["search_engine_name"] for item in calls].count("pubmed") == expected_secondary_calls


def test_agent_called_tool_ignores_message_objects_without_tool_calls():
    agent_input = {
        "messages": [UserMessage(content="Now deal with the Query:\n[Query]: test")],
    }

    assert InfoRetrievalNode._agent_called_tool(agent_input, "web_search_tool") is False


@pytest.mark.asyncio
async def test_llm_path_calls_primary_before_secondary_group():
    node = InfoRetrievalNode()
    state = {
        "search_query": "paper",
        "web_search_engine_name": "tavily",
        "retrieval_query": RetrievalQuery(
            query="paper", primary_engine="tavily", secondary_engines=["semantic_scholar"]
        ),
    }
    agent_input = _agent_input()
    agent_input["messages"].append({"role": "assistant", "tool_calls": [{"name": "local_search_tool"}]})
    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=lambda args: {
        "search_engine": args["search_engine_name"], "search_results": [],
    })

    await node._run_secondary_web_search_if_needed(state, agent_input, {"web_search_tool": web_tool})

    assert [call.args[0]["search_engine_name"] for call in web_tool.invoke.await_args_list] == [
        "tavily", "semantic_scholar",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("search_method", ["all", "web"])
async def test_llm_tool_calling_path_runs_query_secondary_engine(search_method):
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "search_query": "glioblastoma clinical trial",
        "max_tool_call_turns_per_query": 2,
        "search_method": search_method,
        "web_search_engine_name": "tavily",
        "retrieval_query": RetrievalQuery(
            query="glioblastoma clinical trial", primary_engine="tavily", secondary_engines=["pubmed"]
        ),
        "api_tools_config": {"collector_tools": [{"name": "custom_tool"}]},
    }
    agent_input = _agent_input()
    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=lambda args: {
        "search_engine": args["search_engine_name"],
        "search_results": [
            {
                "title": "PubMed result",
                "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
                "content": "clinical trial summary",
            }
        ] if args["search_engine_name"] == "pubmed" else [],
    })

    with patch.object(node, "_collector_llm", AsyncMock(return_value=(state, agent_input))), \
            patch.object(node, "_prepare_collector_tool", return_value=([], {"web_search_tool": web_tool})), \
            patch.object(node, "_structure_result", AsyncMock(return_value=([], {}))):
        await node._collector_main(state)

    assert [call.args[0]["search_engine_name"] for call in web_tool.invoke.await_args_list] == [
        "tavily", "pubmed",
    ]


@pytest.mark.asyncio
async def test_llm_path_uses_primary_when_no_secondary_exists_and_web_not_called():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "arxiv evidence",
        "search_query": "LLM RAG benchmark",
        "max_tool_call_turns_per_query": 2,
        "search_method": "all",
        "web_search_engine_name": "arxiv",
        "retrieval_query": RetrievalQuery(query="LLM RAG benchmark", primary_engine="arxiv"),
        "api_tools_config": {"collector_tools": [{"name": "custom_tool"}]},
    }
    agent_input = _agent_input()
    web_tool = Mock()
    web_tool.invoke = AsyncMock(return_value={"search_engine": "arxiv", "search_results": []})

    with patch.object(node, "_collector_llm", AsyncMock(return_value=(state, agent_input))), \
            patch.object(node, "_prepare_collector_tool", return_value=([], {"web_search_tool": web_tool})), \
            patch.object(node, "_structure_result", AsyncMock(return_value=([], {}))):
        await node._collector_main(state)

    web_tool.invoke.assert_awaited_once_with({
        "query": "LLM RAG benchmark",
        "search_engine_name": "arxiv",
    })


@pytest.mark.asyncio
async def test_multi_secondary_partial_success_does_not_fall_back_to_primary():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "academic evidence",
        "search_query": "retrieval augmented generation paper",
        "web_search_engine_name": "tavily",
        "retrieval_query": RetrievalQuery(
            query="retrieval augmented generation paper",
            primary_engine="tavily",
            secondary_engines=["arxiv", "semantic_scholar"],
        ),
    }
    agent_input = _agent_input()
    web_tool = Mock()

    async def invoke(args):
        if args["search_engine_name"] == "tavily":
            return {"search_engine": "tavily", "search_results": []}
        if args["search_engine_name"] == "arxiv":
            return {
                "search_engine": "arxiv",
                "search_results": [{
                    "title": "Useful paper",
                    "url": "https://arxiv.org/abs/1706.03762",
                    "content": "Relevant abstract",
                    "source": "arxiv",
                    "source_id": "W1",
                }],
            }
        return {
            "search_engine": "semantic_scholar",
            "search_results": [],
            "error": "provider unavailable",
            "retryable": False,
        }

    web_tool.invoke = AsyncMock(side_effect=invoke)

    await node._run_secondary_web_search_if_needed(
        state, agent_input, {"web_search_tool": web_tool}
    )

    assert [call.args[0]["search_engine_name"] for call in web_tool.invoke.await_args_list] == [
        "tavily", "arxiv",
    ]
    assert [record["title"] for record in agent_input["web_page_search_record"]] == ["Useful paper"]


@pytest.mark.asyncio
async def test_all_secondaries_empty_or_failed_falls_back_to_primary_once():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "academic evidence",
        "search_query": "retrieval augmented generation paper",
        "web_search_engine_name": "tavily",
        "retrieval_query": RetrievalQuery(
            query="retrieval augmented generation paper",
            primary_engine="tavily",
            secondary_engines=["arxiv", "semantic_scholar"],
        ),
    }
    agent_input = _agent_input()
    web_tool = Mock()

    async def invoke(args):
        engine = args["search_engine_name"]
        if engine == "arxiv":
            return {"search_engine": engine, "search_results": []}
        if engine == "semantic_scholar":
            return {
                "search_engine": engine,
                "search_results": [],
                "error": "provider unavailable",
                "retryable": False,
            }
        return {
            "search_engine": "tavily",
            "search_results": [{
                "title": "Primary result",
                "url": "https://example.com/primary",
                "content": "Primary summary",
            }],
        }

    web_tool.invoke = AsyncMock(side_effect=invoke)

    await node._run_secondary_web_search_if_needed(
        state, agent_input, {"web_search_tool": web_tool}
    )

    assert [call.args[0]["search_engine_name"] for call in web_tool.invoke.await_args_list] == [
        "tavily", "arxiv",
    ]
    assert [record["title"] for record in agent_input["web_page_search_record"]] == ["Primary result"]


@pytest.mark.asyncio
async def test_all_secondaries_empty_does_not_repeat_primary_after_llm_web_call():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "academic evidence",
        "search_query": "retrieval augmented generation paper",
        "web_search_engine_name": "tavily",
        "retrieval_query": RetrievalQuery(
            query="retrieval augmented generation paper",
            primary_engine="tavily",
            secondary_engines=["arxiv", "semantic_scholar"],
        ),
    }
    agent_input = _agent_input()
    agent_input["messages"].append({
        "role": "assistant",
        "tool_calls": [{"name": "web_search_tool"}],
    })
    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=lambda args: {
        "search_engine": args["search_engine_name"],
        "search_results": [],
    })

    await node._run_secondary_web_search_if_needed(
        state, agent_input, {"web_search_tool": web_tool}
    )

    assert [call.args[0]["search_engine_name"] for call in web_tool.invoke.await_args_list] == [
        "arxiv", "semantic_scholar",
    ]


@pytest.mark.asyncio
async def test_llm_local_path_secondary_error_falls_back_to_primary_web_once():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "search_query": "glioblastoma clinical trial",
        "web_search_engine_name": "tavily",
        "retrieval_query": RetrievalQuery(
            query="glioblastoma clinical trial", primary_engine="tavily", secondary_engines=["pubmed"]
        ),
    }
    agent_input = _agent_input()
    agent_input["messages"].append({
        "role": "assistant",
        "tool_calls": [{"name": "local_search_tool"}],
    })
    agent_input["tool_calls_used"] = 1
    web_tool = Mock()
    async def invoke(args):
        if args["search_engine_name"] == "tavily":
            return {
                "search_engine": "tavily",
                "search_results": [
                {
                    "title": "Primary result",
                    "url": "https://example.com/primary",
                    "content": "Primary summary",
                }
                ],
            }
        return {
            "search_engine": "pubmed",
            "search_results": [],
            "error": "PubMed ESearch returned error: Invalid term",
            "retryable": False,
        },
    web_tool.invoke = AsyncMock(side_effect=invoke)

    await node._run_secondary_web_search_if_needed(
        state,
        agent_input,
        {"web_search_tool": web_tool},
    )

    invoked_engines = [item.args[0]["search_engine_name"] for item in web_tool.invoke.await_args_list]
    assert invoked_engines == ["tavily"]
    assert agent_input["web_page_search_record"] == [
        {
            "type": "page",
            "title": "Primary result",
            "url": "https://example.com/primary",
            "content": "Primary summary",
            "retrieval_source": "tavily",
            "matched_sources": ["tavily"],
            "source_ids": {},
        }
    ]


@pytest.mark.asyncio
async def test_llm_web_path_secondary_error_does_not_repeat_primary_web():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "search_query": "glioblastoma clinical trial",
        "web_search_engine_name": "tavily",
        "retrieval_query": RetrievalQuery(
            query="glioblastoma clinical trial", primary_engine="tavily", secondary_engines=["pubmed"]
        ),
    }
    agent_input = _agent_input()
    agent_input["messages"].append({
        "role": "assistant",
        "tool_calls": [{"name": "web_search_tool"}],
    })
    web_tool = Mock()
    web_tool.invoke = AsyncMock(return_value={
        "search_engine": "pubmed",
        "search_results": [],
        "error": "PubMed ESearch returned error: Invalid term",
        "retryable": False,
    })

    await node._run_secondary_web_search_if_needed(
        state,
        agent_input,
        {"web_search_tool": web_tool},
    )

    web_tool.invoke.assert_awaited_once_with({
        "query": "glioblastoma clinical trial",
        "search_engine_name": "pubmed",
    })
    assert agent_input["web_page_search_record"] == []


@pytest.mark.asyncio
async def test_llm_secondary_transient_error_is_retried_by_collector_only():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "search_query": "glioblastoma clinical trial",
        "web_search_engine_name": "tavily",
        "retrieval_query": RetrievalQuery(
            query="glioblastoma clinical trial", primary_engine="tavily", secondary_engines=["pubmed"]
        ),
    }
    agent_input = _agent_input()
    agent_input["messages"].append({
        "role": "assistant",
        "tool_calls": [{"name": "web_search_tool"}],
    })
    web_tool = Mock()
    web_tool.invoke = AsyncMock(return_value={
        "search_engine": "pubmed",
        "search_results": [],
        "error": "503 Service Unavailable",
        "retryable": True,
    })

    await node._run_secondary_web_search_if_needed(
        state,
        agent_input,
        {"web_search_tool": web_tool},
    )

    assert web_tool.invoke.await_count == 3
    assert all(
        item.args[0] == {
            "query": "glioblastoma clinical trial",
            "search_engine_name": "pubmed",
        }
        for item in web_tool.invoke.await_args_list
    )
    assert agent_input["web_page_search_record"] == []


@pytest.mark.asyncio
async def test_direct_search_respects_explicitly_disabled_retry_for_returned_error(caplog):
    caplog.set_level("INFO")
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "arxiv evidence",
        "web_search_engine_name": "tavily",
    }
    web_tool = Mock()
    web_tool.invoke = AsyncMock(return_value={
        "search_engine": "arxiv",
        "search_results": [],
        "error": "429 Too Many Requests",
    })

    result = await node._direct_search_with_retry(
        DirectSearchRequest(
            tool=web_tool,
            tool_name="web_search_tool",
            query="LLM RAG benchmark",
            search_engine_name="arxiv",
            retry_on_error=False,
        ),
        state,
    )

    assert result is None
    web_tool.invoke.assert_awaited_once_with({
        "query": "LLM RAG benchmark",
        "search_engine_name": "arxiv",
    })
    assert "Vertical search failed fast" in caplog.text
    assert "engine=arxiv" in caplog.text
    assert "query=LLM RAG benchmark" in caplog.text
    assert not any(record.levelname == "ERROR" for record in caplog.records)


@pytest.mark.asyncio
async def test_direct_search_respects_explicitly_disabled_retry_for_exception(caplog):
    caplog.set_level("INFO")
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "arxiv evidence",
        "web_search_engine_name": "tavily",
    }
    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=RuntimeError("429 Too Many Requests"))

    result = await node._direct_search_with_retry(
        DirectSearchRequest(
            tool=web_tool,
            tool_name="web_search_tool",
            query="LLM RAG benchmark",
            search_engine_name="arxiv",
            retry_on_error=False,
        ),
        state,
    )

    assert result is None
    web_tool.invoke.assert_awaited_once_with({
        "query": "LLM RAG benchmark",
        "search_engine_name": "arxiv",
    })
    assert "Vertical search failed fast" in caplog.text
    assert "engine=arxiv" in caplog.text
    assert "reason=exception" in caplog.text
    assert "query=LLM RAG benchmark" in caplog.text
    assert not any(record.levelname == "ERROR" for record in caplog.records)


@pytest.mark.asyncio
async def test_direct_primary_error_keeps_retry_behavior():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "general evidence",
        "web_search_engine_name": "tavily",
    }
    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=[
        {
            "search_engine": "tavily",
            "search_results": [],
            "error": "temporary failure",
        },
        {
            "search_engine": "tavily",
            "search_results": [{"title": "Recovered"}],
        },
    ])

    result = await node._direct_search_with_retry(
        DirectSearchRequest(
            tool=web_tool,
            tool_name="web_search_tool",
            query="general query",
            search_engine_name="tavily",
            retry_on_error=True,
        ),
        state,
    )

    assert result["search_results"] == [{"title": "Recovered"}]
    assert web_tool.invoke.await_count == 2
    assert web_tool.invoke.await_args_list[0].kwargs == {}
    assert web_tool.invoke.await_args_list[0].args[0] == {
        "query": "general query",
        "search_engine_name": "tavily",
    }


@pytest.mark.asyncio
async def test_direct_primary_pubmed_non_retryable_error_is_not_replayed():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "web_search_engine_name": "pubmed",
    }
    web_tool = Mock()
    web_tool.invoke = AsyncMock(return_value={
        "search_engine": "pubmed",
        "search_results": [],
        "error": "429 Too Many Requests",
        "retryable": False,
    })

    result = await node._direct_search_with_retry(
        DirectSearchRequest(
            tool=web_tool,
            tool_name="web_search_tool",
            query="medical LLM calibration",
            search_engine_name="pubmed",
            retry_on_error=True,
        ),
        state,
    )

    assert result is None
    web_tool.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_secondary_error_does_not_implicitly_fall_back_to_primary(caplog):
    caplog.set_level("INFO")
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "web_search_engine_name": "tavily",
    }
    web_tool = Mock()
    web_tool.invoke = AsyncMock(return_value={
        "search_engine": "pubmed",
        "search_results": [],
        "error": "PubMed ESearch returned error: Invalid term",
        "retryable": False,
    })

    result = await node._direct_search_with_retry(
        DirectSearchRequest(
            tool=web_tool,
            tool_name="web_search_tool",
            query="glioblastoma clinical trial",
            search_engine_name="pubmed",
        ),
        state,
    )

    assert result is None
    web_tool.invoke.assert_awaited_once_with({
        "query": "glioblastoma clinical trial",
        "search_engine_name": "pubmed",
    })
    assert "fallback to default" not in caplog.text
