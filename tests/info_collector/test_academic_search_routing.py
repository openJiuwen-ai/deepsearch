from unittest.mock import AsyncMock, Mock, patch

import pytest
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from pydantic import ValidationError

from openjiuwen_deepsearch.config.config import WebSearchEngineConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder import (
    SearchQueryItem,
    SearchQueryList,
    normalize_search_query_item,
    route_secondary_search_engine_for_query,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import (
    DirectSearchRequest,
    InfoRetrievalNode,
)
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


def test_vertical_search_engines_are_registered_but_not_primary_configurable():
    assert search_engine_mapping["pubmed"] is PubMedSearchAPIWrapper
    assert search_engine_mapping["arxiv"] is ArxivSearchAPIWrapper
    with pytest.raises(ValidationError):
        WebSearchEngineConfig(search_engine_name="pubmed")
    with pytest.raises(ValidationError):
        WebSearchEngineConfig(search_engine_name="arxiv")


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


def test_agent_called_tool_ignores_message_objects_without_tool_calls():
    agent_input = {
        "messages": [UserMessage(content="Now deal with the Query:\n[Query]: test")],
    }

    assert InfoRetrievalNode._agent_called_tool(agent_input, "web_search_tool") is False


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


@pytest.mark.asyncio
async def test_llm_local_path_secondary_error_falls_back_to_primary_web():
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "search_query": "glioblastoma clinical trial",
        "web_search_engine_name": "tavily",
        "secondary_web_search_engine_name": "pubmed",
    }
    agent_input = _agent_input()
    agent_input["messages"].append({
        "role": "assistant",
        "tool_calls": [{"name": "local_search_tool"}],
    })
    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=[
        {
            "search_engine": "pubmed",
            "search_results": [],
            "error": "PubMed ESearch returned error: Invalid term",
        },
        {
            "search_engine": "tavily",
            "search_results": [
                {
                    "title": "Fallback result",
                    "url": "https://example.com/fallback",
                    "content": "Fallback summary",
                }
            ],
        },
    ])

    await node._run_secondary_web_search_if_needed(
        state,
        agent_input,
        {"web_search_tool": web_tool},
    )

    assert [item.args[0]["search_engine_name"] for item in web_tool.invoke.await_args_list] == [
        "pubmed",
        "tavily",
    ]
    assert agent_input["web_page_search_record"] == [
        {
            "type": "page",
            "title": "Fallback result",
            "url": "https://example.com/fallback",
            "content": "Fallback summary",
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
        "secondary_web_search_engine_name": "pubmed",
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
async def test_direct_parallel_secondary_error_does_not_retry_or_repeat_primary(caplog):
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
            fallback_to_default=False,
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
async def test_direct_parallel_secondary_exception_does_not_retry_or_repeat_primary(caplog):
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
            fallback_to_default=False,
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
            fallback_to_default=False,
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
async def test_single_secondary_error_falls_back_to_primary_engine(caplog):
    caplog.set_level("INFO")
    node = InfoRetrievalNode()
    state = {
        "section_idx": 0,
        "step_title": "clinical evidence",
        "web_search_engine_name": "tavily",
    }
    web_tool = Mock()
    web_tool.invoke = AsyncMock(side_effect=[
        {
            "search_engine": "pubmed",
            "search_results": [],
            "error": "PubMed ESearch returned error: Invalid term",
        },
        {
            "search_engine": "tavily",
            "search_results": [{"title": "Fallback result"}],
        },
    ])

    result = await node._direct_search_with_retry(
        DirectSearchRequest(
            tool=web_tool,
            tool_name="web_search_tool",
            query="glioblastoma clinical trial",
            search_engine_name="pubmed",
        ),
        state,
    )

    assert result["search_engine"] == "tavily"
    assert result["search_results"] == [{"title": "Fallback result"}]
    assert [item.args[0]["search_engine_name"] for item in web_tool.invoke.await_args_list] == [
        "pubmed",
        "tavily",
    ]
    assert "Vertical search fallback to default" in caplog.text
    assert "engine=pubmed" in caplog.text
    assert "default_engine=tavily" in caplog.text
    assert "query=glioblastoma clinical trial" in caplog.text
    assert not any(record.levelname == "ERROR" for record in caplog.records)
