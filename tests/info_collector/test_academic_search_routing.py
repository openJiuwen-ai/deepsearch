from openjiuwen_deepsearch.config.config import WebSearchEngineConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder import (
    SearchQueryItem,
    SearchQueryList,
    normalize_search_query_item,
    route_secondary_search_engine_for_query,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import InfoRetrievalNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import RetrievalQuery
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.api_wrapper import (
    ArxivSearchAPIWrapper,
    PubMedSearchAPIWrapper,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import search_engine_mapping


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
