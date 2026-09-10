import re

from openjiuwen_deepsearch.algorithm.research_collector.target_paper import (
    find_exact_target_paper_facts,
    normalize_arxiv_id,
    normalize_doi,
    normalize_pmid,
    normalize_title,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import (
    target_paper_key,
)


def test_target_paper_key_is_session_path_safe_and_canonical():
    versioned = target_paper_key({"arxiv_id": "1706.03762v7"})
    canonical = target_paper_key({"arxiv_id": "1706.03762"})

    assert versioned == canonical
    assert re.fullmatch(r"tp_[0-9a-f]{64}", versioned)


def test_target_paper_key_separates_implicit_target_clues():
    assert target_paper_key({"dataset": "ImageNet", "data_year": "2012"}) != target_paper_key({
        "dataset": "COCO",
        "data_year": "2014",
    })


def test_identifier_normalization():
    assert normalize_pmid(" PMID: 38202877 ") == "38202877"
    assert normalize_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert normalize_arxiv_id("https://arxiv.org/abs/2401.01234v2") == "2401.01234"
    assert normalize_title("  A  Full Paper Title. ") == "a full paper title"


def test_normalize_arxiv_id_supports_legacy_ids_and_url_wrappers():
    assert normalize_arxiv_id("hep-th/9901001v3") == "hep-th/9901001"
    assert normalize_arxiv_id("arXiv:hep-th/9901001v2") == "hep-th/9901001"
    assert normalize_arxiv_id("https://arxiv.org/abs/hep-th/9901001v1") == "hep-th/9901001"
    assert normalize_arxiv_id("arxiv.org/abs/2401.01234v2") == "2401.01234"
    assert normalize_arxiv_id("arxiv.org/pdf/hep-th/9901001v2.pdf") == "hep-th/9901001"
    assert normalize_arxiv_id(
        "https://arxiv.org/pdf/hep-th/9901001v4.pdf?download=1#page=2"
    ) == "hep-th/9901001"
    assert normalize_arxiv_id("https://arxiv.org/html/2401.01234v2") == "2401.01234"


def test_find_exact_target_paper_facts_uses_strong_equality_only():
    facts = find_exact_target_paper_facts(
        [{"pmid": "38202877", "title": "A Full Paper Title"}],
        [{
            "title": "A Full Paper Title",
            "url": "https://pubmed.ncbi.nlm.nih.gov/38202877/",
            "academic_source": "pubmed",
            "academic_source_id": "38202877",
            "doi": "10.1000/ABC",
        }],
    )

    assert facts == ["Target paper located: PMID 38202877, A Full Paper Title."]


def test_doi_and_arxiv_matches_use_canonical_identifiers():
    facts = find_exact_target_paper_facts(
        [
            {"doi": "DOI:10.1000/ABC"},
            {"arxiv_id": "arXiv:2401.01234v1"},
        ],
        [
            {"title": "DOI Paper", "doi": "https://doi.org/10.1000/abc"},
            {"title": "arXiv Paper", "academic_source": "arxiv", "academic_source_id": "2401.01234v3"},
        ],
    )

    assert facts == [
        "Target paper located: DOI 10.1000/abc, DOI Paper.",
        "Target paper located: arXiv 2401.01234, arXiv Paper.",
    ]


def test_legacy_arxiv_target_matches_returned_academic_source_id():
    facts = find_exact_target_paper_facts(
        [{"arxiv_id": "https://arxiv.org/pdf/hep-th/9901001v3.pdf"}],
        [{
            "title": "Legacy arXiv Paper",
            "academic_source": "arxiv",
            "academic_source_id": "hep-th/9901001v1",
        }],
    )

    assert facts == [
        "Target paper located: arXiv hep-th/9901001, Legacy arXiv Paper."
    ]


def test_pmid_can_be_read_from_canonical_url():
    facts = find_exact_target_paper_facts(
        [{"pmid": "38202877"}],
        [{"title": "Paper", "url": "https://pubmed.ncbi.nlm.nih.gov/38202877/"}],
    )

    assert facts == ["Target paper located: PMID 38202877, Paper."]


def test_target_paper_url_matches_a_canonicalized_document_url():
    facts = find_exact_target_paper_facts(
        [{"url": "https://journal.example.org/article/42/?utm_source=test"}],
        [{"title": "Paper", "url": "https://journal.example.org/article/42"}],
    )

    assert facts == ["Target paper located: URL, Paper."]


def test_normalized_full_title_matches_without_strong_identifier():
    facts = find_exact_target_paper_facts(
        [{"title": "Ａ Full   Paper Title。"}],
        [{"title": "A full paper title"}],
    )

    assert facts == ["Target paper located: exact title, A full paper title."]


def test_partial_title_and_implicit_fingerprint_do_not_match():
    assert find_exact_target_paper_facts(
        [{"title": "A Full Paper Title"}],
        [{"title": "A Full Paper Title With Extra Words"}],
    ) == []
    assert find_exact_target_paper_facts(
        [{"dataset": "MEPS", "data_year": "2019", "topic": "orthodontics"}],
        [{"title": "Orthodontics using MEPS 2019"}],
    ) == []


def test_strong_identifier_mismatch_does_not_fall_back_to_title():
    assert find_exact_target_paper_facts(
        [{"pmid": "111", "title": "Same Title"}],
        [{"title": "Same Title", "academic_source_id": "222", "academic_source": "pubmed"}],
    ) == []


def test_duplicate_and_malformed_inputs_return_stable_unique_facts():
    papers = [None, {}, {"pmid": "38202877"}, {"pmid": "38202877"}]
    documents = [None, {"title": "Paper", "academic_source": "pubmed", "academic_source_id": "38202877"}]

    assert find_exact_target_paper_facts(papers, documents) == [
        "Target paper located: PMID 38202877, Paper."
    ]
