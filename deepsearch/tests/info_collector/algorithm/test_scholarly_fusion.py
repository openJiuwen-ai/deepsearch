from openjiuwen_deepsearch.algorithm.research_collector.scholarly_fusion import fuse_scholarly_records


def test_fuses_same_doi_and_preserves_best_metadata():
    records = [
        {
            "academic_source": "arxiv",
            "academic_source_id": "A1",
            "doi": "https://doi.org/10.1000/ABC",
            "title": "A useful paper",
            "content": "short",
            "full_text_candidates": [{"url": "https://example.org/a.pdf", "format": "pdf"}],
        },
        {
            "academic_source": "semantic_scholar",
            "academic_source_id": "S1",
            "doi": "10.1000/abc",
            "title": "A useful paper",
            "content": "a much longer abstract",
            "authors": ["Ada Lovelace"],
        },
    ]

    fused = fuse_scholarly_records(records)

    assert len(fused) == 1
    assert fused[0]["doi"] == "https://doi.org/10.1000/ABC"
    assert fused[0]["content"] == "a much longer abstract"
    assert fused[0]["matched_sources"] == ["arxiv", "semantic_scholar"]
    assert fused[0]["source_ids"] == {"arxiv": "A1", "semantic_scholar": "S1"}
    assert fused[0]["full_text_candidates"] == [
        {"url": "https://example.org/a.pdf", "format": "pdf"}
    ]


def test_repeated_fusion_preserves_all_provenance():
    first_pass = fuse_scholarly_records([
        {
            "academic_source": "arxiv",
            "academic_source_id": "A1",
            "doi": "10.1000/repeated",
            "title": "Repeated fusion",
        },
        {
            "academic_source": "pubmed",
            "academic_source_id": "123",
            "doi": "https://doi.org/10.1000/REPEATED",
            "title": "Repeated fusion",
        },
    ])

    second_pass = fuse_scholarly_records(first_pass)

    assert second_pass == first_pass
    assert second_pass[0]["matched_sources"] == ["arxiv", "pubmed"]
    assert second_pass[0]["source_ids"] == {"arxiv": "A1", "pubmed": "123"}


def test_does_not_fuse_plain_web_result_with_scholarly_record():
    records = fuse_scholarly_records([
        {
            "title": "Publisher landing page",
            "url": "https://publisher.example/paper",
            "doi": "10.1000/cross-type",
        },
        {
            "title": "Scholarly record",
            "url": "https://doi.org/10.1000/cross-type",
            "doi": "10.1000/cross-type",
            "source": "semantic_scholar",
            "source_id": "S1",
        },
    ])

    assert len(records) == 2
    assert records[0] == {
        "title": "Publisher landing page",
        "url": "https://publisher.example/paper",
        "doi": "10.1000/cross-type",
    }
    assert records[1]["matched_sources"] == ["semantic_scholar"]
    assert records[1]["source_ids"] == {"semantic_scholar": "S1"}


def test_fusion_matches_normalized_doi_without_rewriting_display_value():
    records = [
        {
            "title": "Paper",
            "url": "https://one.test",
            "doi": "10.1000/ABC",
            "source": "pubmed",
        },
        {
            "title": "Paper",
            "url": "https://two.test",
            "doi": "https://doi.org/10.1000/abc",
            "source": "arxiv",
        },
    ]

    fused = fuse_scholarly_records(records)

    assert len(fused) == 1
    assert fused[0]["doi"] == "10.1000/ABC"


def test_fuses_by_pmid_and_keeps_full_text():
    records = [
        {"academic_source": "pubmed", "pmid": "123", "title": "Trial", "full_text": "body"},
        {"academic_source": "semantic_scholar", "pmid": "PMID:123", "title": "Trial"},
    ]

    fused = fuse_scholarly_records(records)

    assert len(fused) == 1
    assert fused[0]["pmid"] == "123"
    assert fused[0]["full_text"] == "body"


def test_fuses_by_normalized_title_and_year_without_identifiers():
    records = [
        {
            "academic_source": "arxiv", "title": "Attention Is All You Need!",
            "authors": ["Ashish Vaswani"], "published": "2017-06-12",
        },
        {
            "academic_source": "semantic_scholar", "title": "attention is all you need",
            "authors": ["Ashish Vaswani"], "published": "2017",
        },
        {
            "academic_source": "pubmed", "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani"], "published": "2018",
        },
    ]

    fused = fuse_scholarly_records(records)

    assert len(fused) == 2
    assert fused[0]["matched_sources"] == ["arxiv", "semantic_scholar"]


def test_bridge_record_merges_all_existing_components():
    records = [
        {
            "academic_source": "arxiv",
            "academic_source_id": "A1",
            "doi": "10.1000/bridge",
            "title": "Earliest record",
            "content": "short",
        },
        {
            "academic_source": "pubmed",
            "academic_source_id": "123",
            "pmid": "123",
            "title": "Second record",
            "content": "a longer abstract",
        },
        {
            "academic_source": "semantic_scholar",
            "academic_source_id": "S1",
            "doi": "https://doi.org/10.1000/BRIDGE",
            "pmid": "PMID:123",
            "title": "Bridge record",
            "content": "the longest available scholarly content",
        },
    ]

    fused = fuse_scholarly_records(records)

    assert len(fused) == 1
    assert fused[0]["title"] == "Earliest record"
    assert fused[0]["matched_sources"] == ["arxiv", "pubmed", "semantic_scholar"]
    assert fused[0]["source_ids"] == {
        "arxiv": "A1",
        "pubmed": "123",
        "semantic_scholar": "S1",
    }
    assert fused[0]["doi"] == "10.1000/bridge"
    assert fused[0]["pmid"] == "123"
    assert fused[0]["content"] == "the longest available scholarly content"


def test_does_not_merge_non_scholarly_records():
    records = [
        {"url": "https://one.example", "title": "Same"},
        {"url": "https://two.example", "title": "Same"},
    ]

    assert fuse_scholarly_records(records) == records


def test_does_not_fuse_primary_and_scholarly_records_by_canonical_url():
    records = [
        {
            "retrieval_source": "tavily",
            "url": "https://Example.org/paper/?utm_source=search",
            "title": "Useful paper",
            "content": "web summary",
        },
        {
            "academic_source": "semantic_scholar",
            "academic_source_id": "S1",
            "url": "https://example.org/paper",
            "title": "Useful paper",
            "content": "scholarly abstract",
        },
    ]

    fused = fuse_scholarly_records(records)

    assert len(fused) == 2
    assert fused[0]["content"] == "web summary"
    assert fused[0]["matched_sources"] == ["tavily"]
    assert fused[1]["content"] == "scholarly abstract"
    assert fused[1]["matched_sources"] == ["semantic_scholar"]
    assert fused[1]["source_ids"] == {"semantic_scholar": "S1"}


def test_title_author_year_identity_is_conservative():
    records = [
        {
            "academic_source": "arxiv",
            "title": "Shared title",
            "authors": ["Ada Lovelace"],
            "published": "2020",
        },
        {
            "academic_source": "semantic_scholar",
            "title": "shared title",
            "authors": ["Ada Lovelace", "Grace Hopper"],
            "published": "2020-06-01",
        },
        {
            "academic_source": "pubmed",
            "title": "Shared title",
            "authors": ["Different Author"],
            "published": "2020",
        },
    ]

    fused = fuse_scholarly_records(records)

    assert len(fused) == 2
