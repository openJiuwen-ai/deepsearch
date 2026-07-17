from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.arxiv import (
    ArxivSearchAPIWrapper,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    ScholarlySearchResponseError,
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
async def test_pubmed_aresults_uses_request_helpers_for_esearch_and_efetch():
    wrapper = PubMedSearchAPIWrapper()
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


def test_pubmed_parse_fetch_xml_rejects_invalid_or_error_response():
    wrapper = PubMedSearchAPIWrapper()

    with pytest.raises(ScholarlySearchResponseError, match="invalid XML"):
        wrapper._parse_fetch_xml("<PubmedArticleSet>", ["1"])
    with pytest.raises(ScholarlySearchResponseError, match="returned error"):
        wrapper._parse_fetch_xml("<ERROR>Bad request</ERROR>", ["1"])
    with pytest.raises(ScholarlySearchResponseError, match="unexpected XML root"):
        wrapper._parse_fetch_xml("<html><body>Service unavailable</body></html>", ["1"])


def test_pubmed_parse_fetch_xml_allows_valid_empty_article_set():
    wrapper = PubMedSearchAPIWrapper()

    assert wrapper._parse_fetch_xml("<PubmedArticleSet></PubmedArticleSet>", ["1"]) == []


@pytest.mark.asyncio
async def test_pubmed_async_request_fails_fast_on_rate_limit_payload_without_pre_request_limit():
    wrapper = PubMedSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(return_value=DummyResponse(json_data={"error": "API rate limit exceeded"}))

    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        await wrapper._aget_json(client, "https://example.com/esearch.fcgi", {})

    assert client.get.await_count == 1


def test_arxiv_parse_atom_rejects_invalid_or_non_feed_response():
    wrapper = ArxivSearchAPIWrapper()

    with pytest.raises(ScholarlySearchResponseError, match="invalid XML"):
        wrapper._parse_atom("<feed>")
    with pytest.raises(ScholarlySearchResponseError, match="non-Atom feed"):
        wrapper._parse_atom("<html><body>Service unavailable</body></html>")


def test_arxiv_parse_atom_allows_valid_empty_feed():
    wrapper = ArxivSearchAPIWrapper()

    assert wrapper._parse_atom('<feed xmlns="http://www.w3.org/2005/Atom"></feed>') == []


@pytest.mark.asyncio
async def test_arxiv_async_request_fails_fast_on_429_without_pre_request_limit():
    wrapper = ArxivSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(return_value=DummyResponse(status_code=429, headers={"Retry-After": "0"}))

    with pytest.raises(RuntimeError, match="status 429"):
        await wrapper._aget_text(client, "https://example.com/api/query")

    assert client.get.await_count == 1
