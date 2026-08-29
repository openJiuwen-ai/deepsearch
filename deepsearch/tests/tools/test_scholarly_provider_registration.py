import pytest
from pydantic import ValidationError

from openjiuwen_deepsearch.config.config import ScholarlySearchConfig
from openjiuwen_deepsearch.framework.openjiuwen.tools import search_api
from openjiuwen_deepsearch.framework.openjiuwen.tools import web_search
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api import scholarly_search
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    empty_full_text_fields,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.semantic_scholar import (
    DEFAULT_SEMANTIC_SCHOLAR_SEARCH_URL,
    SemanticScholarSearchAPIWrapper,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.arxiv import (
    DEFAULT_ARXIV_SEARCH_URL,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.pubmed import (
    DEFAULT_PUBMED_SEARCH_URL,
)
from openjiuwen_deepsearch.utils.constants_utils.search_engine_constants import SearchEngine
from openjiuwen_deepsearch.utils.constants_utils.scholarly_constants import (
    SCHOLARLY_PROVIDER_NAMES,
)


EXPECTED_EMPTY_FULL_TEXT_FIELDS = {
    "full_text": "",
    "content_type": "abstract",
    "full_text_url": "",
    "full_text_format": "",
    "full_text_status": "unavailable",
    "full_text_truncated": False,
}


def test_scholarly_provider_enum_values_and_registration_exports():
    assert SearchEngine.SEMANTIC_SCHOLAR.value == "semantic_scholar"
    assert SCHOLARLY_PROVIDER_NAMES == ("pubmed", "arxiv", "semantic_scholar")
    assert DEFAULT_PUBMED_SEARCH_URL.startswith("https://")
    assert DEFAULT_ARXIV_SEARCH_URL.startswith("https://")
    assert DEFAULT_SEMANTIC_SCHOLAR_SEARCH_URL.startswith("https://")
    assert scholarly_search.SemanticScholarSearchAPIWrapper is SemanticScholarSearchAPIWrapper
    assert search_api.SemanticScholarSearchAPIWrapper is SemanticScholarSearchAPIWrapper
    assert "SemanticScholarSearchAPIWrapper" in scholarly_search.__all__
    assert "SemanticScholarSearchAPIWrapper" in search_api.__all__
    assert web_search.search_engine_mapping["semantic_scholar"] is SemanticScholarSearchAPIWrapper
    assert not hasattr(SearchEngine, "OPENALEX")
    assert not hasattr(scholarly_search, "OpenAlexSearchAPIWrapper")
    assert not hasattr(search_api, "OpenAlexSearchAPIWrapper")
    assert "openalex" not in web_search.search_engine_mapping


def test_scholarly_config_rejects_removed_openalex_provider():
    assert "openalex" not in ScholarlySearchConfig.model_fields

    with pytest.raises(ValidationError):
        ScholarlySearchConfig.model_validate({"openalex": {"requests_per_second": 1.0}})


def test_empty_full_text_fields_returns_fresh_defaults():
    first = empty_full_text_fields()
    second = empty_full_text_fields()

    assert first == EXPECTED_EMPTY_FULL_TEXT_FIELDS
    assert second == EXPECTED_EMPTY_FULL_TEXT_FIELDS
    assert first is not second


def test_semantic_scholar_exposes_standard_constructor_without_network_for_blank_query():
    wrapper = SemanticScholarSearchAPIWrapper(
        search_api_key="test-key",
        search_url="https://example.test/search",
        max_web_search_results=3,
        extension={"future": True},
    )

    assert wrapper.search_api_key == "test-key"
    assert str(wrapper.search_url) == "**********"
    assert wrapper.max_web_search_results == 3
    assert wrapper.extension == {"future": True}
    assert wrapper.results("  ") == []


@pytest.mark.asyncio
async def test_semantic_scholar_exposes_async_blank_query_without_network():
    wrapper = SemanticScholarSearchAPIWrapper()

    assert await wrapper.aresults("") == []
