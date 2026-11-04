from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import requests

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    ScholarlySearchResponseError,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.semantic_scholar import (
    SEMANTIC_SCHOLAR_REQUEST_CONTROL,
    SemanticScholarSearchAPIWrapper,
)


def _payload():
    return {"data": [{
        "paperId": "649def34f8be52c8b66281af98ae884c09aef38b",
        "title": "Attention Is All You Need",
        "abstract": "Transformer architecture.",
        "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
        "publicationDate": "2017-06-12",
        "year": 2017,
        "venue": "NeurIPS",
        "journal": {"name": "NeurIPS"},
        "externalIds": {"DOI": "10.48550/arXiv.1706.03762", "ArXiv": "1706.03762", "PubMed": "123"},
        "citationCount": 123,
        "url": "https://www.semanticscholar.org/paper/649def34",
        "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
    }]}


def test_maps_semantic_scholar_paper():
    row = SemanticScholarSearchAPIWrapper()._parse_response(_payload())[0]
    assert row["source"] == "semantic_scholar"
    assert row["source_id"] == "649def34f8be52c8b66281af98ae884c09aef38b"
    assert row["doi"] == "10.48550/arXiv.1706.03762"
    assert row["arxiv_id"] == "1706.03762"
    assert row["pmid"] == "123"
    assert row["citation_count"] == 123
    assert row["full_text_candidates"] == [{
        "url": "https://arxiv.org/pdf/1706.03762",
        "format": "pdf",
        "source": "semantic_scholar",
        "kind": "semantic_scholar_pdf",
        "priority": 60,
    }]
    assert row["full_text_status"] == "unavailable"


@patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.semantic_scholar.requests.get")
def test_sync_key_is_header_only(mock_get):
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"data": []}
    response.raise_for_status.return_value = None
    mock_get.return_value = response
    SemanticScholarSearchAPIWrapper(search_api_key=b"secret").results("transformers")
    kwargs = mock_get.call_args.kwargs
    assert kwargs["headers"] == {"x-api-key": "secret"}
    assert "secret" not in str(kwargs["params"])


@pytest.mark.asyncio
async def test_async_params_and_key_header():
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"data": []}
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    with patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.semantic_scholar.httpx.AsyncClient", return_value=context):
        await SemanticScholarSearchAPIWrapper(search_api_key="key", max_web_search_results=3).aresults("graph")
    assert client.get.call_args.kwargs["headers"] == {"x-api-key": "key"}
    assert client.get.call_args.kwargs["params"]["limit"] == 3


@pytest.mark.asyncio
async def test_async_request_uses_shared_rate_control_without_retry(monkeypatch):
    wrapper = SemanticScholarSearchAPIWrapper(search_api_key="key")
    response_429 = Mock(status_code=429, headers={"Retry-After": "0"})
    response_429.aclose = AsyncMock()
    response_429.raise_for_status.side_effect = httpx.HTTPStatusError(
        "limited", request=httpx.Request("GET", "https://example.test"),
        response=httpx.Response(429, request=httpx.Request("GET", "https://example.test")),
    )
    client = AsyncMock()
    client.get.return_value = response_429
    context = AsyncMock()
    context.__aenter__.return_value = client
    waits = AsyncMock()
    monkeypatch.setattr(SEMANTIC_SCHOLAR_REQUEST_CONTROL, "wait_async", waits)
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.semantic_scholar.httpx.AsyncClient",
        return_value=context,
    ):
        with pytest.raises(ScholarlySearchResponseError, match="HTTP 429"):
            await wrapper.aresults("graph")

    assert client.get.await_count == 1
    waits.assert_awaited_with(0.5)


@patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.semantic_scholar.requests.get")
def test_sync_request_does_not_retry_503(mock_get):
    response = Mock(status_code=503, headers={})
    error = requests.HTTPError("unavailable")
    error.response = response
    response.raise_for_status.side_effect = error
    mock_get.return_value = response

    with pytest.raises(ScholarlySearchResponseError, match="HTTP 503"):
        SemanticScholarSearchAPIWrapper().results("graph")

    assert mock_get.call_count == 1


def test_malformed_payload_and_bad_sibling():
    wrapper = SemanticScholarSearchAPIWrapper()
    for value in (None, [], {}, {"data": {}}):
        with pytest.raises(ScholarlySearchResponseError):
            wrapper._parse_response(value)
    payload = _payload()
    payload["data"].insert(0, "bad")
    assert len(wrapper._parse_response(payload)) == 1


def test_abstract_fallback_and_unsafe_pdf():
    paper = _payload()["data"][0]
    paper["abstract"] = ""
    paper["openAccessPdf"] = {"url": "file:///tmp/paper.pdf"}
    row = SemanticScholarSearchAPIWrapper()._parse_response({"data": [paper]})[0]
    assert "Attention Is All You Need" in row["content"]
    assert row["full_text_candidates"] == []


@pytest.mark.parametrize("value", [0, -1, 11])
def test_result_limit_validation(value):
    with pytest.raises(ValueError):
        SemanticScholarSearchAPIWrapper(max_web_search_results=value)


def test_http_error_does_not_leak_key():
    request = requests_request = httpx.Request("GET", "https://example.test?x=TOPSECRET")
    response = httpx.Response(429, request=request)
    error = httpx.HTTPStatusError("TOPSECRET", request=requests_request, response=response)
    sanitized = SemanticScholarSearchAPIWrapper._sanitized_request_error(error)
    assert "TOPSECRET" not in str(sanitized)
