from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.arxiv import (
    ArxivSearchAPIWrapper,
    arxiv_rate_limiter,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.pubmed import PubMedSearchAPIWrapper


class DummyResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


@pytest.mark.asyncio
async def test_pubmed_aresults_uses_limited_helper_for_esearch_and_esummary():
    wrapper = PubMedSearchAPIWrapper(rate_limit_backoff_base_seconds=0)
    search_raw = {"esearchresult": {"idlist": ["1"]}}
    fetch_text = """
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>1</PMID>
          <Article>
            <ArticleTitle>Study</ArticleTitle>
            <Abstract><AbstractText>Useful abstract.</AbstractText></Abstract>
          </Article>
        </MedlineCitation>
      </PubmedArticle>
    </PubmedArticleSet>
    """

    with patch.object(wrapper, "_aget_json", AsyncMock(return_value=search_raw)) as mock_get_json, \
            patch.object(wrapper, "_aget_text", AsyncMock(return_value=fetch_text)) as mock_get_text:
        results = await wrapper.aresults("glioblastoma clinical trial")

    assert mock_get_json.await_count == 1
    assert mock_get_text.await_count == 1
    assert results[0]["title"] == "Study"
    assert results[0]["content"] == "Useful abstract."


def test_pubmed_parse_fetch_xml_prefers_structured_abstract_content():
    wrapper = PubMedSearchAPIWrapper()
    xml = """
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>123</PMID>
          <Article>
            <Journal>
              <Title>Journal of Tests</Title>
              <JournalIssue><PubDate><Year>2025</Year><Month>Jan</Month></PubDate></JournalIssue>
            </Journal>
            <ArticleTitle>Clinical Trial Study</ArticleTitle>
            <Abstract>
              <AbstractText Label="METHODS">Randomized trial with 100 patients.</AbstractText>
              <AbstractText Label="RESULTS">Treatment improved survival.</AbstractText>
              <AbstractText Label="CONCLUSIONS">Therapy showed benefit.</AbstractText>
            </Abstract>
            <AuthorList>
              <Author><ForeName>Ada</ForeName><LastName>Lovelace</LastName></Author>
            </AuthorList>
            <PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
            <ELocationID EIdType="doi">10.1000/test</ELocationID>
          </Article>
        </MedlineCitation>
      </PubmedArticle>
    </PubmedArticleSet>
    """

    rows = wrapper._parse_fetch_xml(xml, ["123"])

    assert rows[0]["source_id"] == "123"
    assert rows[0]["title"] == "Clinical Trial Study"
    assert "METHODS: Randomized trial" in rows[0]["content"]
    assert "RESULTS: Treatment improved survival" in rows[0]["content"]
    assert "CONCLUSIONS: Therapy showed benefit" in rows[0]["content"]
    assert rows[0]["journal"] == "Journal of Tests"
    assert rows[0]["published"] == "2025 Jan"
    assert rows[0]["authors"] == ["Ada Lovelace"]
    assert rows[0]["publication_types"] == ["Randomized Controlled Trial"]
    assert rows[0]["doi"] == "10.1000/test"


def test_pubmed_parse_fetch_xml_falls_back_to_bibliographic_content_without_abstract():
    wrapper = PubMedSearchAPIWrapper()
    xml = """
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>456</PMID>
          <Article>
            <Journal>
              <Title>Bibliographic Journal</Title>
              <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>
            </Journal>
            <ArticleTitle>No Abstract Study</ArticleTitle>
            <AuthorList>
              <Author><ForeName>Grace</ForeName><LastName>Hopper</LastName></Author>
            </AuthorList>
          </Article>
        </MedlineCitation>
      </PubmedArticle>
    </PubmedArticleSet>
    """

    rows = wrapper._parse_fetch_xml(xml, ["456"])

    assert rows[0]["content"] == "Bibliographic Journal | 2024 | Grace Hopper"


@pytest.mark.asyncio
async def test_pubmed_async_request_retries_429_after_rate_limit_acquire():
    wrapper = PubMedSearchAPIWrapper(rate_limit_backoff_base_seconds=0)
    client = Mock()
    client.get = AsyncMock(side_effect=[
        DummyResponse(status_code=429, headers={"Retry-After": "0"}),
        DummyResponse(json_data={"ok": True}),
    ])

    with patch.object(wrapper, "_aacquire_rate_limit", AsyncMock()) as mock_acquire, \
            patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.pubmed."
                  "asyncio.sleep", AsyncMock()) as mock_sleep:
        raw = await wrapper._aget_json(client, "https://example.com/esearch.fcgi", {})

    assert raw == {"ok": True}
    assert mock_acquire.await_count == 2
    mock_sleep.assert_awaited_once_with(0.0)


@pytest.mark.asyncio
async def test_pubmed_async_request_retries_rate_limit_payload():
    wrapper = PubMedSearchAPIWrapper(rate_limit_backoff_base_seconds=0)
    client = Mock()
    client.get = AsyncMock(side_effect=[
        DummyResponse(json_data={"error": "API rate limit exceeded"}),
        DummyResponse(json_data={"ok": True}),
    ])

    with patch.object(wrapper, "_aacquire_rate_limit", AsyncMock()) as mock_acquire, \
            patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.pubmed."
                  "asyncio.sleep", AsyncMock()) as mock_sleep:
        raw = await wrapper._aget_json(client, "https://example.com/esearch.fcgi", {})

    assert raw == {"ok": True}
    assert mock_acquire.await_count == 2
    mock_sleep.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_arxiv_async_request_retries_429_after_three_second_limiter():
    wrapper = ArxivSearchAPIWrapper(rate_limit_backoff_base_seconds=0)
    client = Mock()
    client.get = AsyncMock(side_effect=[
        DummyResponse(status_code=429, headers={"Retry-After": "0"}),
        DummyResponse(text="<feed xmlns='http://www.w3.org/2005/Atom'></feed>"),
    ])

    with patch.object(arxiv_rate_limiter, "aacquire", AsyncMock()) as mock_acquire, \
            patch("openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.arxiv."
                  "asyncio.sleep", AsyncMock()) as mock_sleep:
        text = await wrapper._aget_text(client, "https://example.com/api/query")

    assert text.startswith("<feed")
    assert mock_acquire.await_count == 2
    mock_sleep.assert_awaited_once_with(0.0)
