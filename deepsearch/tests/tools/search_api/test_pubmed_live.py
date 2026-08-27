"""Opt-in integration tests against the live NCBI E-utilities service."""

import os

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.scholarly_search.pubmed import (
    PubMedSearchAPIWrapper,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_NCBI_INTEGRATION_TESTS") != "1",
        reason="set RUN_NCBI_INTEGRATION_TESTS=1 to call the live NCBI service",
    ),
]


def test_live_pubmed_search_and_fetch_via_post():
    wrapper = PubMedSearchAPIWrapper(
        fetch_full_text=False,
        max_web_search_results=1,
        full_text_timeout_seconds=30,
    )

    results = wrapper.results("31452104[PMID]")

    assert len(results) == 1
    assert results[0]["source_id"] == "31452104"
    assert results[0]["title"]


def test_live_pmc_fetch_via_post():
    wrapper = PubMedSearchAPIWrapper(
        fetch_full_text=False,
        full_text_timeout_seconds=30,
    )

    xml = wrapper._get_text(
        f"{wrapper._resolved_search_url()}/efetch.fcgi",
        params=wrapper._pmc_fetch_params("PMC3531190"),
        verify=True,
    )

    assert "<article" in xml
    assert "PMC3531190" in xml
