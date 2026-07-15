from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen_deepsearch.config.config import WebSearchEngineConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder import (
    SearchQueryItem,
    SearchQueryList,
    normalize_search_query_item,
    route_secondary_search_engine_for_query,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import InfoRetrievalNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import RetrievalQuery
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search import (
    ArxivSearchAPIWrapper,
    PubMedSearchAPIWrapper,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import search_engine_mapping


def _agent_input() -> dict:
    return {
        "messages": [],
        "web_page_search_record": [],
        "local_text_search_record": [],
        "other_tool_record": [],
        "research_intent": {},
    }


def test_vertical_search_engines_are_registered_and_configurable():
    assert search_engine_mapping["pubmed"] is PubMedSearchAPIWrapper
    assert search_engine_mapping["arxiv"] is ArxivSearchAPIWrapper
    assert WebSearchEngineConfig(search_engine_name="pubmed").search_engine_name == "pubmed"
    assert WebSearchEngineConfig(search_engine_name="arxiv").search_engine_name == "arxiv"


def test_query_object_and_retrieval_query_carry_secondary_engine():
    query_list = SearchQueryList(
        missing_evidence=["evidence"],
        queries=[SearchQueryItem(query="glioblastoma trial", search_engine_name="pubmed")],
    )
    item = normalize_search_query_item(query_list.queries[0])
    retrieval_query = RetrievalQuery(query=item.query, search_engine_name=item.search_engine_name)

    assert retrieval_query.query == "glioblastoma trial"
    assert retrieval_query.search_engine_name == "pubmed"


def test_explicit_empty_secondary_engine_is_preserved():
    item = normalize_search_query_item(
        SearchQueryItem(query="LLM RAG benchmark", search_engine_name="")
    )

    assert item.search_engine_name == ""


def test_missing_secondary_engine_uses_heuristic_routing():
    item = normalize_search_query_item(SearchQueryItem(query="LLM RAG benchmark"))

    assert item.search_engine_name == "arxiv"


def test_legacy_string_query_uses_heuristic_routing():
    item = normalize_search_query_item("glioblastoma clinical trial")

    assert item.search_engine_name == "pubmed"


def test_fallback_secondary_engine_routing():
    assert route_secondary_search_engine_for_query("glioblastoma clinical trial") == "pubmed"
    assert route_secondary_search_engine_for_query("gene expression analysis") == "pubmed"
    assert route_secondary_search_engine_for_query("drug discovery pipeline") == "pubmed"
    assert route_secondary_search_engine_for_query("LLM RAG benchmark") == "arxiv"
    assert route_secondary_search_engine_for_query("generative AI models") == "arxiv"
    assert route_secondary_search_engine_for_query("Apple annual revenue") == ""
    assert route_secondary_search_engine_for_query("general productivity software") == ""
    assert route_secondary_search_engine_for_query("generation planning methods") == ""
    assert route_secondary_search_engine_for_query("generic database indexing") == ""
    assert route_secondary_search_engine_for_query("Japan pension reform social security statistics") == ""
    assert route_secondary_search_engine_for_query("public policy benchmark pension reform") == ""


def test_web_search_engine_list_keeps_primary_and_adds_one_secondary():
    node = InfoRetrievalNode()

    assert node._web_search_engines_for_query({
        "web_search_engine_name": "tavily",
        "secondary_web_search_engine_name": "pubmed",
    }) == ["tavily", "pubmed"]
    assert node._web_search_engines_for_query({
        "web_search_engine_name": "arxiv",
        "secondary_web_search_engine_name": "arxiv",
    }) == ["arxiv"]
    assert node._web_search_engines_for_query({
        "web_search_engine_name": "tavily",
        "secondary_web_search_engine_name": "",
    }) == ["tavily"]


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
        "secondary_web_search_engine_name": "pubmed",
        "api_tools_config": {"collector_tools": [{"name": "custom_tool"}]},
    }
    agent_input = _agent_input()
    web_tool = Mock()
    web_tool.invoke = AsyncMock(return_value={
        "search_engine": "pubmed",
        "search_results": [
            {
                "title": "PubMed result",
                "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
                "content": "clinical trial summary",
            }
        ],
    })

    with patch.object(node, "_collector_llm", AsyncMock(return_value=(state, agent_input))), \
            patch.object(node, "_prepare_collector_tool", return_value=([], {"web_search_tool": web_tool})), \
            patch.object(node, "_structure_result", AsyncMock(return_value=([], [], {}))), \
            patch.object(node, "_process_post_process_result", return_value=[]):
        await node._collector_main(state)

    web_tool.invoke.assert_awaited_once_with({
        "query": "glioblastoma clinical trial",
        "search_engine_name": "pubmed",
    })


@pytest.mark.asyncio
async def test_llm_tool_calling_path_skips_duplicate_secondary_engine():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "arxiv evidence",
        "search_query": "LLM RAG benchmark",
        "max_tool_call_turns_per_query": 2,
        "search_method": "all",
        "web_search_engine_name": "arxiv",
        "secondary_web_search_engine_name": "arxiv",
        "api_tools_config": {"collector_tools": [{"name": "custom_tool"}]},
    }
    agent_input = _agent_input()
    web_tool = Mock()
    web_tool.invoke = AsyncMock(return_value={"search_engine": "arxiv", "search_results": []})

    with patch.object(node, "_collector_llm", AsyncMock(return_value=(state, agent_input))), \
            patch.object(node, "_prepare_collector_tool", return_value=([], {"web_search_tool": web_tool})), \
            patch.object(node, "_structure_result", AsyncMock(return_value=([], [], {}))), \
            patch.object(node, "_process_post_process_result", return_value=[]):
        await node._collector_main(state)

    web_tool.invoke.assert_not_awaited()
