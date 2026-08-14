import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import requests

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.arxiv import (
    ArxivSearchAPIWrapper,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common import (
    ScholarlySearchResponseError,
    ServiceRequestControl,
    reset_scholarly_request_controls,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.pubmed import PubMedSearchAPIWrapper


class DummyResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None, content=b""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text
        self.headers = headers or {}
        self.content = content

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


@pytest.fixture(autouse=True)
def _reset_scholarly_request_controls():
    reset_scholarly_request_controls()
    yield
    reset_scholarly_request_controls()


@pytest.mark.asyncio
async def test_async_rate_waiter_rechecks_cooldown_before_release():
    control = ServiceRequestControl()
    await control.wait_async(20)
    started_at = time.monotonic()
    waiter = asyncio.create_task(control.wait_async(20))
    await asyncio.sleep(0.01)
    control.defer(0.1)

    await waiter

    assert time.monotonic() - started_at >= 0.09


@pytest.mark.asyncio
async def test_sync_rate_waiter_rechecks_cooldown_set_from_async_task():
    control = ServiceRequestControl()
    control.wait_sync(20)
    started_at = time.monotonic()
    waiter = asyncio.create_task(asyncio.to_thread(control.wait_sync, 20))
    await asyncio.sleep(0.01)
    control.defer(0.1)

    await waiter

    assert time.monotonic() - started_at >= 0.09


@pytest.mark.asyncio
async def test_rate_waiters_remain_spaced_after_shared_cooldown():
    control = ServiceRequestControl()
    await control.wait_async(20)
    started_at = time.monotonic()
    released_at = []

    async def wait_and_record():
        await control.wait_async(20)
        released_at.append(time.monotonic())

    first_waiter = asyncio.create_task(wait_and_record())
    await asyncio.sleep(0.005)
    control.defer(0.1)
    second_waiter = asyncio.create_task(wait_and_record())
    await asyncio.gather(first_waiter, second_waiter)

    released_at.sort()
    assert released_at[0] - started_at >= 0.09
    assert released_at[1] - released_at[0] >= 0.04


@pytest.mark.parametrize("wrapper_class", [PubMedSearchAPIWrapper, ArxivSearchAPIWrapper])
def test_scholarly_full_text_config_can_be_overridden_through_extension(wrapper_class):
    wrapper = wrapper_class(extension={
        "scholarly_fetch_full_text": False,
        "scholarly_max_full_text_results": 2,
        "scholarly_full_text_timeout_seconds": 12,
        "scholarly_max_full_text_length": 4321,
    })

    assert wrapper.fetch_full_text is False
    assert wrapper.max_full_text_results == 2
    assert wrapper.full_text_timeout_seconds == 12
    assert wrapper.max_full_text_length == 4321


@pytest.mark.parametrize(("configured", "expected"), [
    (False, False),
    (True, True),
    ("false", False),
    (" FALSE ", False),
    ("true", True),
    (" TRUE ", True),
])
@pytest.mark.parametrize("wrapper_class", [PubMedSearchAPIWrapper, ArxivSearchAPIWrapper])
def test_scholarly_fetch_full_text_parses_boolean_extension(wrapper_class, configured, expected):
    wrapper = wrapper_class(extension={"scholarly_fetch_full_text": configured})

    assert wrapper.fetch_full_text is expected


@pytest.mark.parametrize("wrapper_class", [PubMedSearchAPIWrapper, ArxivSearchAPIWrapper])
def test_scholarly_fetch_full_text_rejects_invalid_boolean_extension(wrapper_class):
    with pytest.raises(ValueError, match="scholarly_fetch_full_text"):
        wrapper_class(extension={"scholarly_fetch_full_text": "no"})


@pytest.mark.parametrize("wrapper_class", [PubMedSearchAPIWrapper, ArxivSearchAPIWrapper])
def test_scholarly_search_defaults_to_one_result_per_query(wrapper_class):
    wrapper = wrapper_class()

    assert wrapper.max_web_search_results == 1
    assert wrapper.max_full_text_results == 1


@pytest.mark.parametrize("wrapper_class", [PubMedSearchAPIWrapper, ArxivSearchAPIWrapper])
def test_scholarly_search_defaults_to_one_request_every_three_seconds(wrapper_class):
    assert wrapper_class().requests_per_second == pytest.approx(1 / 3)


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


def test_pubmed_results_fetches_exact_pmid_without_esearch():
    wrapper = PubMedSearchAPIWrapper(fetch_full_text=False)
    fetch_text = "<PubmedArticleSet></PubmedArticleSet>"

    with patch.object(wrapper, "_search_ids") as search_ids, \
            patch.object(wrapper, "_get_text", return_value=fetch_text) as get_text:
        wrapper.results("PMID: 38132429")

    search_ids.assert_not_called()
    assert get_text.call_args.kwargs["params"]["id"] == "38132429"


def test_pubmed_bare_numeric_query_uses_esearch():
    wrapper = PubMedSearchAPIWrapper(fetch_full_text=False)

    with patch.object(wrapper, "_search_ids", return_value=[]) as search_ids, \
            patch.object(wrapper, "_get_text") as get_text:
        result = wrapper.results("2024")

    assert result == []
    search_ids.assert_called_once()
    assert search_ids.call_args.args[0] == "2024"
    get_text.assert_not_called()


def test_pubmed_exact_pmid_does_not_match_descriptive_query():
    wrapper = PubMedSearchAPIWrapper()

    assert wrapper._exact_pmid("38132429 study limitations") == ""


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
    assert rows[0]["full_text"] == ""
    assert rows[0]["content_type"] == "abstract"
    assert rows[0]["full_text_status"] == "unavailable"
    assert rows[0]["skip_webpage_enrichment"] is True


def test_pubmed_parse_fetch_xml_extracts_pmcid():
    wrapper = PubMedSearchAPIWrapper(fetch_full_text=False)
    xml = """
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>123</PMID>
          <Article><ArticleTitle>Open article</ArticleTitle></Article>
        </MedlineCitation>
        <PubmedData><ArticleIdList>
          <ArticleId IdType="pubmed">123</ArticleId>
          <ArticleId IdType="pmc">PMC999</ArticleId>
        </ArticleIdList></PubmedData>
      </PubmedArticle>
    </PubmedArticleSet>
    """

    rows = wrapper._parse_fetch_xml(xml, ["123"])

    assert rows[0]["pmcid"] == "PMC999"


def test_pubmed_parse_pmc_xml_keeps_body_sections_tables_and_captions():
    wrapper = PubMedSearchAPIWrapper(max_full_text_length=10_000)
    xml = """
    <article><body>
      <sec><title>Methods</title><p>Participants were enrolled.</p></sec>
      <sec><title>Results</title><p>Outcomes improved.</p>
        <table-wrap><label>Table 1</label><caption><p>Main outcomes</p></caption>
          <table><tr><th>Group</th><th>Rate</th></tr><tr><td>A</td><td>61%</td></tr></table>
        </table-wrap>
        <fig><label>Figure 1</label><caption><p>Outcome trend</p></caption></fig>
      </sec>
    </body></article>
    """

    text, truncated = wrapper._parse_pmc_xml(xml)

    assert "Methods" in text
    assert "Participants were enrolled." in text
    assert "Table 1" in text
    assert "Group | Rate" in text
    assert "A | 61%" in text
    assert "Figure 1 Outcome trend" in text
    assert truncated is False


def test_pubmed_parse_pmc_xml_does_not_repeat_paragraphs_inside_tables_or_figures():
    wrapper = PubMedSearchAPIWrapper(max_full_text_length=10_000)
    xml = """
    <article><body><sec>
      <table-wrap><label>Table 1</label><caption><p>Table caption</p></caption>
        <table><tr><td>Label <p>Nested cell value</p></td></tr></table>
      </table-wrap>
      <fig><label>Figure 1</label><caption><p>Figure caption</p></caption></fig>
    </sec></body></article>
    """

    text, _ = wrapper._parse_pmc_xml(xml)

    assert text.count("Table caption") == 1
    assert text.count("Nested cell value") == 1
    assert text.count("Figure caption") == 1


def test_pubmed_results_does_not_fetch_pmc_when_full_text_disabled():
    wrapper = PubMedSearchAPIWrapper(fetch_full_text=False)
    row = {
        "title": "Study",
        "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
        "content": "Abstract.",
        "source": "pubmed",
        "source_id": "1",
        "pmcid": "PMC1",
    }

    with patch.object(wrapper, "_search_ids", return_value=["1"]), \
            patch.object(wrapper, "_get_text", return_value="<PubmedArticleSet />") as get_text, \
            patch.object(wrapper, "_parse_fetch_xml", return_value=[row]):
        results = wrapper.results("study")

    assert get_text.call_count == 1
    assert results[0]["content"] == "Abstract."


def test_pubmed_enrich_rows_fetches_only_configured_pmc_results():
    wrapper = PubMedSearchAPIWrapper(max_full_text_results=1)
    rows = [
        {"source": "pubmed", "source_id": "1", "content": "Abstract 1", "pmcid": "PMC1", "full_text_status": "unavailable"},
        {"source_id": "2", "content": "Abstract 2", "pmcid": "PMC2", "full_text_status": "unavailable"},
    ]
    pmc_xml = "<article><body><sec><title>Body</title><p>Complete article text.</p></sec></body></article>"

    with patch.object(wrapper, "_get_text", return_value=pmc_xml) as get_text:
        enriched = wrapper._enrich_rows_sync(rows, verify=False)

    assert get_text.call_count == 1
    assert get_text.call_args.kwargs["params"]["db"] == "pmc"
    assert enriched[0]["content"] == "Abstract 1"
    assert enriched[0]["full_text"] == "Body\n\nComplete article text."
    assert enriched[0]["content_type"] == "full_text"
    assert enriched[0]["full_text_format"] == "jats_xml"
    assert enriched[0]["full_text_status"] == "available"
    assert enriched[1]["full_text_status"] == "unavailable"


def test_pubmed_enrich_rows_marks_one_pmc_failure_without_losing_abstract():
    wrapper = PubMedSearchAPIWrapper(max_full_text_results=1)
    rows = [{
        "source_id": "1",
        "content": "Keep this abstract",
        "pmcid": "PMC1",
        "full_text_status": "unavailable",
    }]

    with patch.object(wrapper, "_get_text", side_effect=RuntimeError("timeout")):
        enriched = wrapper._enrich_rows_sync(rows, verify=False)

    assert enriched[0]["content"] == "Keep this abstract"
    assert enriched[0]["full_text"] == ""
    assert enriched[0]["content_type"] == "abstract"
    assert enriched[0]["full_text_status"] == "failed"


@pytest.mark.asyncio
async def test_pubmed_async_enrichment_uses_pmc_params():
    wrapper = PubMedSearchAPIWrapper(max_full_text_results=1)
    client = Mock()
    pmc_xml = "<article><body><p>Complete article text.</p></body></article>"
    rows = [{"source_id": "1", "content": "Abstract", "pmcid": "PMC1", "full_text_status": "unavailable"}]

    with patch.object(wrapper, "_aget_text", AsyncMock(return_value=pmc_xml)) as get_text:
        enriched = await wrapper._enrich_rows_async(rows, client)

    assert get_text.await_count == 1
    assert get_text.await_args.kwargs["params"]["db"] == "pmc"
    assert enriched[0]["full_text_status"] == "available"


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
async def test_pubmed_async_request_rejects_esearch_error_payload():
    wrapper = PubMedSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(return_value=DummyResponse(json_data={"error": "Invalid term"}))

    with pytest.raises(ScholarlySearchResponseError, match="ESearch returned error"):
        await wrapper._aget_json(client, "https://example.com/esearch.fcgi", {})

    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_pubmed_async_request_treats_esearch_errorlist_as_nonfatal_warning():
    wrapper = PubMedSearchAPIWrapper()
    client = Mock()
    payload = {
        "esearchresult": {
            "idlist": [],
            "errorlist": {"phrasesnotfound": ["bad syntax"]},
        }
    }
    client.get = AsyncMock(return_value=DummyResponse(json_data=payload))

    raw = await wrapper._aget_json(client, "https://example.com/esearch.fcgi", {})

    assert raw == payload
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_pubmed_async_request_keeps_ids_when_esearch_also_has_warning():
    wrapper = PubMedSearchAPIWrapper()
    payload = {
        "esearchresult": {
            "idlist": ["38132429"],
            "errorlist": {"phrasesnotfound": ["38132429"]},
        }
    }
    client = Mock()
    client.get = AsyncMock(return_value=DummyResponse(json_data=payload))

    raw = await wrapper._aget_json(client, "https://example.com/esearch.fcgi", {})

    assert wrapper._parse_ids(raw) == ["38132429"]


@pytest.mark.asyncio
async def test_pubmed_async_request_retries_429_and_honors_retry_after():
    wrapper = PubMedSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(side_effect=[
        DummyResponse(status_code=429, headers={"Retry-After": "7"}),
        DummyResponse(json_data={"esearchresult": {"idlist": ["38132429"]}}),
    ])

    with patch.object(wrapper, "_wait_for_async_rate_limit", AsyncMock()) as rate_limit, \
            patch(
                "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common."
                "NCBI_REQUEST_CONTROL.defer"
            ) as defer:
        raw = await wrapper._aget_json(client, "https://example.com/esearch.fcgi", {})

    assert wrapper._parse_ids(raw) == ["38132429"]
    assert client.get.await_count == 2
    assert rate_limit.await_count == 2
    defer.assert_called_once_with(7.0)


@pytest.mark.asyncio
async def test_pubmed_async_request_stops_after_three_503_responses():
    wrapper = PubMedSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(return_value=DummyResponse(status_code=503))

    with patch.object(wrapper, "_wait_for_async_rate_limit", AsyncMock()), \
            patch(
                "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep, pytest.raises(RuntimeError, match="status 503"):
        await wrapper._aget_text(client, "https://example.com/efetch.fcgi", {})

    assert client.get.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [1.0, 2.0]


@pytest.mark.asyncio
async def test_pubmed_terminal_429_still_updates_shared_cooldown():
    wrapper = PubMedSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(return_value=DummyResponse(
        status_code=429,
        headers={"Retry-After": "7"},
    ))

    with patch.object(wrapper, "_wait_for_async_rate_limit", AsyncMock()), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common."
        "NCBI_REQUEST_CONTROL.defer"
    ) as defer, pytest.raises(RuntimeError, match="status 429"):
        await wrapper._aget_text(client, "https://example.com/efetch.fcgi", {})

    assert client.get.await_count == 3
    assert defer.call_count == 3


@pytest.mark.asyncio
async def test_pubmed_async_rate_limit_is_shared_across_wrapper_instances():
    first = PubMedSearchAPIWrapper(requests_per_second=20)
    second = PubMedSearchAPIWrapper(requests_per_second=20)
    request_times = []

    async def record_request(wrapper):
        await wrapper._wait_for_async_rate_limit()
        request_times.append(time.monotonic())

    await asyncio.gather(record_request(first), record_request(second))

    request_times.sort()
    assert request_times[1] - request_times[0] >= 0.04


@pytest.mark.asyncio
async def test_pubmed_rate_limit_is_shared_between_async_and_sync_requests():
    async_wrapper = PubMedSearchAPIWrapper(requests_per_second=20)
    sync_wrapper = PubMedSearchAPIWrapper(requests_per_second=20)

    await async_wrapper._wait_for_async_rate_limit()
    async_request_at = time.monotonic()
    await asyncio.to_thread(sync_wrapper._wait_for_sync_rate_limit)
    sync_request_at = time.monotonic()

    assert sync_request_at - async_request_at >= 0.04


@pytest.mark.asyncio
async def test_pubmed_rate_limits_efetch_requests_too():
    wrapper = PubMedSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(return_value=DummyResponse(text="ok"))

    with patch.object(wrapper, "_wait_for_async_rate_limit", AsyncMock()) as rate_limit:
        await wrapper._aget_text(client, "https://example.com/efetch.fcgi", {})

    rate_limit.assert_awaited_once()


def test_pubmed_sync_request_retries_temporary_connection_error():
    wrapper = PubMedSearchAPIWrapper()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.pubmed.requests.get",
        side_effect=[requests.ConnectionError("temporary"), DummyResponse(text="ok")],
    ) as get:
        with patch.object(wrapper, "_wait_for_sync_rate_limit"), patch(
            "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.common.time.sleep"
        ) as sleep:
            text = wrapper._get_text("https://example.com/efetch.fcgi", {}, False)

    assert text == "ok"
    assert get.call_count == 2
    sleep.assert_called_once_with(1.0)


def test_pubmed_parse_ids_allows_valid_empty_result_without_error_payload():
    wrapper = PubMedSearchAPIWrapper()

    assert wrapper._parse_ids({"esearchresult": {"idlist": [], "count": "0"}}) == []


def test_arxiv_parse_atom_rejects_invalid_or_non_feed_response():
    wrapper = ArxivSearchAPIWrapper()

    with pytest.raises(ScholarlySearchResponseError, match="invalid XML"):
        wrapper._parse_atom("<feed>")
    with pytest.raises(ScholarlySearchResponseError, match="non-Atom feed"):
        wrapper._parse_atom("<html><body>Service unavailable</body></html>")


def test_arxiv_parse_atom_allows_valid_empty_feed():
    wrapper = ArxivSearchAPIWrapper()

    assert wrapper._parse_atom('<feed xmlns="http://www.w3.org/2005/Atom"></feed>') == []


def test_arxiv_parse_atom_rejects_official_error_feed_entry():
    wrapper = ArxivSearchAPIWrapper()
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/api/errors#bad_query</id>
        <title>Error</title>
        <summary>Invalid query syntax.</summary>
      </entry>
    </feed>
    """

    with pytest.raises(ScholarlySearchResponseError, match="returned error"):
        wrapper._parse_atom(xml)


def test_arxiv_parse_atom_adds_full_text_contract_without_changing_summary():
    wrapper = ArxivSearchAPIWrapper(fetch_full_text=False)
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>http://arxiv.org/abs/2501.00001v1</id><title>Study</title>
      <summary>Useful summary.</summary><published>2025-01-01T00:00:00Z</published>
    </entry></feed>
    """

    rows = wrapper._parse_atom(xml)

    assert rows[0]["content"] == "Useful summary."
    assert rows[0]["full_text"] == ""
    assert rows[0]["content_type"] == "abstract"
    assert rows[0]["full_text_status"] == "unavailable"
    assert rows[0]["skip_webpage_enrichment"] is True


def test_arxiv_parse_atom_preserves_legacy_identifier_category():
    wrapper = ArxivSearchAPIWrapper(fetch_full_text=False)
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>http://arxiv.org/abs/hep-ph/0309395v2</id><title>Legacy study</title>
      <summary>Useful summary.</summary>
    </entry></feed>
    """

    rows = wrapper._parse_atom(xml)

    assert rows[0]["source_id"] == "hep-ph/0309395v2"


def test_arxiv_parse_html_extracts_article_and_removes_navigation():
    wrapper = ArxivSearchAPIWrapper(max_full_text_length=10_000)
    html = """
    <html><body><nav>Skip navigation</nav><article>
      <h1>Paper title</h1><section><h2>Introduction</h2>
      <p>This is the complete article body with enough useful scholarly content for extraction.</p>
      </section><script>ignore()</script>
    </article></body></html>
    """

    text, truncated = wrapper._parse_arxiv_html(html)

    assert "Paper title" in text
    assert "complete article body" in text
    assert "Skip navigation" not in text
    assert "ignore()" not in text
    assert truncated is False


def test_arxiv_enrich_rows_prefers_html_and_respects_limit():
    wrapper = ArxivSearchAPIWrapper(max_full_text_results=1, min_full_text_length=20)
    rows = [
        {"source": "arxiv", "source_id": "2501.00001v1", "content": "Summary 1", "full_text_status": "unavailable"},
        {"source_id": "2501.00002v1", "content": "Summary 2", "full_text_status": "unavailable"},
    ]
    html = "<article><h1>Study</h1><p>This is sufficiently long official HTML full text.</p></article>"

    with patch.object(wrapper, "_get_text", return_value=html) as get_text, \
            patch.object(wrapper, "_get_bytes") as get_bytes:
        enriched = wrapper._enrich_rows_sync(rows)

    assert get_text.call_count == 1
    get_bytes.assert_not_called()
    assert enriched[0]["content"] == "Summary 1"
    assert enriched[0]["full_text_status"] == "available"
    assert enriched[0]["full_text_format"] == "html"
    assert enriched[1]["full_text_status"] == "unavailable"


def test_arxiv_enrich_rows_falls_back_to_pdf():
    wrapper = ArxivSearchAPIWrapper(max_full_text_results=1, min_full_text_length=20)
    rows = [{"source_id": "2501.00001v1", "content": "Summary", "full_text_status": "unavailable"}]

    with patch.object(wrapper, "_get_text", return_value="<html>no article</html>"), \
            patch.object(wrapper, "_get_bytes", return_value=b"%PDF-test"), \
            patch.object(wrapper, "_extract_pdf_text", return_value=("Complete PDF article text long enough.", False)):
        enriched = wrapper._enrich_rows_sync(rows)

    assert enriched[0]["content"] == "Summary"
    assert enriched[0]["full_text"] == "Complete PDF article text long enough."
    assert enriched[0]["full_text_format"] == "pdf"
    assert enriched[0]["full_text_url"] == "https://arxiv.org/pdf/2501.00001v1"


def test_arxiv_enrich_rows_falls_back_to_dot_pdf_suffix_after_404():
    wrapper = ArxivSearchAPIWrapper(max_full_text_results=1, min_full_text_length=20)
    rows = [{"source_id": "hep-ph/0309395v2", "content": "Summary", "full_text_status": "unavailable"}]

    not_found = requests.HTTPError("404")
    not_found.response = DummyResponse(status_code=404)
    with patch.object(wrapper, "_get_text", return_value="<html>no article</html>"), \
            patch.object(wrapper, "_get_bytes", side_effect=[not_found, b"%PDF-test"]) as get_bytes, \
            patch.object(wrapper, "_extract_pdf_text", return_value=("Complete PDF article text long enough.", False)):
        enriched = wrapper._enrich_rows_sync(rows)

    assert [call.args[0] for call in get_bytes.call_args_list] == [
        "https://arxiv.org/pdf/hep-ph/0309395v2",
        "https://arxiv.org/pdf/hep-ph/0309395v2.pdf",
    ]
    assert enriched[0]["full_text_status"] == "available"
    assert enriched[0]["full_text_url"] == "https://arxiv.org/pdf/hep-ph/0309395v2.pdf"


def test_arxiv_enrich_rows_does_not_switch_pdf_url_after_connection_error():
    wrapper = ArxivSearchAPIWrapper(max_full_text_results=1)
    rows = [{"source_id": "2501.00001v1", "content": "Summary", "full_text_status": "unavailable"}]

    with patch.object(wrapper, "_get_text", return_value="<html>no article</html>"), \
            patch.object(wrapper, "_get_bytes", side_effect=requests.ConnectionError("temporary")) as get_bytes:
        wrapper._enrich_rows_sync(rows)

    assert get_bytes.call_count == 1
    assert rows[0]["full_text_status"] == "failed"


def test_arxiv_enrich_rows_marks_failure_and_keeps_summary():
    wrapper = ArxivSearchAPIWrapper(max_full_text_results=1)
    rows = [{"source_id": "2501.00001v1", "content": "Keep summary", "full_text_status": "unavailable"}]

    with patch.object(wrapper, "_get_text", side_effect=RuntimeError("html failed")), \
            patch.object(wrapper, "_get_bytes", side_effect=RuntimeError("pdf failed")):
        enriched = wrapper._enrich_rows_sync(rows)

    assert enriched[0]["content"] == "Keep summary"
    assert enriched[0]["full_text"] == ""
    assert enriched[0]["full_text_status"] == "failed"


@pytest.mark.asyncio
async def test_arxiv_async_request_retries_http_429_error():
    wrapper = ArxivSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(side_effect=[
        DummyResponse(status_code=429, headers={"Retry-After": "0"}),
        DummyResponse(text="<feed />"),
    ])

    with patch.object(wrapper, "_wait_for_async_rate_limit", AsyncMock()):
        text = await wrapper._aget_text(client, "https://example.com/api/query")

    assert text == "<feed />"
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_arxiv_async_request_does_not_retry_permanent_400_error():
    wrapper = ArxivSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(return_value=DummyResponse(status_code=400))

    with patch.object(wrapper, "_wait_for_async_rate_limit", AsyncMock()), \
            pytest.raises(RuntimeError, match="status 400"):
        await wrapper._aget_text(client, "https://example.com/api/query")

    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_arxiv_download_concurrency_is_shared_across_wrapper_instances():
    wrappers = [ArxivSearchAPIWrapper() for _ in range(3)]
    release = asyncio.Event()
    two_started = asyncio.Event()
    active = 0
    max_active = 0

    async def get(_url):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            two_started.set()
        await release.wait()
        active -= 1
        return DummyResponse(content=b"pdf")

    clients = []
    for _ in wrappers:
        client = Mock()
        client.get = AsyncMock(side_effect=get)
        clients.append(client)

    tasks = [
        asyncio.create_task(wrapper._aget_bytes(client, f"https://arxiv.org/pdf/{index}"))
        for index, (wrapper, client) in enumerate(zip(wrappers, clients))
    ]
    await asyncio.wait_for(two_started.wait(), timeout=1)
    await asyncio.sleep(0.02)

    assert active == 2
    assert max_active == 2

    release.set()
    await asyncio.gather(*tasks)
    assert sum(client.get.await_count for client in clients) == 3


@pytest.mark.asyncio
async def test_arxiv_download_does_not_inherit_atom_api_interval():
    wrapper = ArxivSearchAPIWrapper(requests_per_second=1 / 3)
    client = Mock()
    client.get = AsyncMock(return_value=DummyResponse(content=b"pdf"))

    await wrapper._wait_for_async_rate_limit()
    content = await asyncio.wait_for(
        wrapper._aget_bytes(client, "https://arxiv.org/pdf/2501.00001"),
        timeout=0.2,
    )

    assert content == b"pdf"


@pytest.mark.asyncio
async def test_arxiv_async_pdf_extraction_runs_off_event_loop():
    wrapper = ArxivSearchAPIWrapper(min_full_text_length=1)
    row = {"source_id": "2501.00001v1", "content": "Summary", "full_text_status": "unavailable"}
    client = Mock()

    with patch.object(wrapper, "_aget_text", AsyncMock(return_value="<html>no article</html>")), \
            patch.object(wrapper, "_aget_bytes", AsyncMock(return_value=b"%PDF-test")), \
            patch.object(wrapper, "_extract_pdf_text", return_value=("PDF text", False)) as extract, \
            patch(
                "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.arxiv.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=("PDF text", False),
            ) as to_thread:
        await wrapper._enrich_one_async(row, client)

    to_thread.assert_awaited_once_with(extract, b"%PDF-test")
    assert row["full_text_status"] == "available"


def test_arxiv_pdf_extraction_stops_after_reaching_character_limit():
    class FakeTextPage:
        def __init__(self, text):
            self.text = text

        def get_text_range(self):
            return self.text

        def close(self):
            pass

    class FakePage:
        def __init__(self, text):
            self.text = text
            self.opened = False

        def get_textpage(self):
            self.opened = True
            return FakeTextPage(self.text)

        def close(self):
            pass

    class FakeDocument:
        def __init__(self):
            self.pages = [FakePage("12345"), FakePage("should not be parsed")]

        def __len__(self):
            return len(self.pages)

        def __iter__(self):
            return iter(self.pages)

        def close(self):
            pass

    document = FakeDocument()
    wrapper = ArxivSearchAPIWrapper(max_full_text_length=5)

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.arxiv.pdfium.PdfDocument",
        return_value=document,
    ):
        text, truncated = wrapper._extract_pdf_text(b"pdf")

    assert text == "12345"
    assert truncated is True
    assert document.pages[0].opened is True
    assert document.pages[1].opened is False


@pytest.mark.asyncio
async def test_arxiv_async_request_retries_temporary_connection_error():
    wrapper = ArxivSearchAPIWrapper()
    client = Mock()
    client.get = AsyncMock(side_effect=[
        httpx.ConnectError("temporary"),
        DummyResponse(text="<feed />"),
    ])

    with patch.object(wrapper, "_wait_for_async_rate_limit", AsyncMock()):
        text = await wrapper._aget_text(client, "https://example.com/api/query")

    assert text == "<feed />"
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_arxiv_async_api_requests_are_spaced_to_avoid_rate_limit_bursts():
    wrapper = ArxivSearchAPIWrapper(
        fetch_full_text=False,
        requests_per_second=20,
    )
    request_times = []

    async def get(_url):
        request_times.append(time.monotonic())
        return DummyResponse(text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')

    client = Mock()
    client.get = AsyncMock(side_effect=get)

    await asyncio.gather(
        wrapper._aget_text(client, "https://export.arxiv.org/api/query?one"),
        wrapper._aget_text(client, "https://export.arxiv.org/api/query?two"),
    )

    assert len(request_times) == 2
    assert request_times[1] - request_times[0] >= 0.04


@pytest.mark.asyncio
async def test_arxiv_aresults_enables_redirect_following():
    wrapper = ArxivSearchAPIWrapper(fetch_full_text=False)
    client = Mock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.arxiv.httpx.AsyncClient",
        return_value=client,
    ) as client_class, patch.object(wrapper, "_aget_text", AsyncMock(return_value=(
        '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    ))):
        await wrapper.aresults("any model generated query")

    assert client_class.call_args.kwargs["follow_redirects"] is True
