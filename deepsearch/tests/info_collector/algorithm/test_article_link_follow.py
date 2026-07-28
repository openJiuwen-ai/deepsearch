from dataclasses import dataclass

from openjiuwen_deepsearch.algorithm.research_collector import article_link_follow
from openjiuwen_deepsearch.algorithm.research_collector.article_link_follow import (
    ARTICLE_LINK_SOURCE_FIELD,
    ArticleLinkCandidate,
    build_article_link_source,
    build_article_link_candidates,
    select_article_link_candidates,
)


def candidates_from_content(
    content: str,
    parent_url: str = "https://example.com/base/article",
) -> list[ArticleLinkCandidate]:
    """Build candidates through the public article-link API for parser assertions."""
    return build_article_link_candidates(
        [{"doc_id": "parent", "url": parent_url, "original_content": content}],
        existing_urls=set(),
    )


def test_build_candidates_extracts_supported_link_forms_and_skips_depth_one_docs():
    docs = [
        {
            "doc_id": "a",
            "title": "Parent A",
            "url": "https://example.com/reports/a",
            "query": "official evidence",
            "original_content": (
                "See [official table](/data/table). "
                '<a href="https://agency.gov/report">Agency report</a> '
                "Mirror https://archive.org/report.pdf"
            ),
        },
        {
            "doc_id": "b",
            "url": "https://example.com/b",
            "original_content": "https://example.com/c",
            "discovery": {"method": "article_link_follow", "depth": 1},
        },
    ]

    candidates = build_article_link_candidates(docs, existing_urls=set())

    assert [item.url for item in candidates] == [
        "https://example.com/data/table",
        "https://agency.gov/report",
        "https://archive.org/report.pdf",
    ]
    assert candidates[0].anchor_text == "official table"
    assert all(item.parent_doc_id == "a" for item in candidates)


def test_build_candidates_deduplicates_self_existing_tracking_and_fragments():
    docs = [{
        "doc_id": "a",
        "url": "https://Example.com/a",
        "original_content": (
            "https://example.com/a#section "
            "https://example.com/b?utm_source=news#facts "
            "https://EXAMPLE.com/b "
            "https://example.com/existing"
        ),
    }]

    candidates = build_article_link_candidates(
        docs,
        existing_urls={"https://example.com/existing"},
    )

    assert [item.canonical_url for item in candidates] == ["https://example.com/b"]


def test_markdown_link_parser_preserves_balanced_parentheses_in_url():
    content = (
        "[GIC](https://en.wikipedia.org/wiki/GIC_(sovereign_wealth_fund))"
    )

    candidates = candidates_from_content(content)

    assert candidates[0].url == (
        "https://en.wikipedia.org/wiki/GIC_(sovereign_wealth_fund)"
    )


def test_wikipedia_oldid_for_parent_is_filtered_as_self_link():
    stats = article_link_follow.ArticleLinkCandidateBuildStats()
    docs = [{
        "doc_id": "wiki-parent",
        "url": "https://en.wikipedia.org/wiki/Santiago_Principles",
        "original_content": (
            "[revision](https://en.wikipedia.org/w/index.php?"
            "oldid=1345866452&title=Santiago_Principles)"
        ),
    }]

    candidates = build_article_link_candidates(
        docs, existing_urls=set(), stats=stats
    )

    assert candidates == []
    assert stats.self_link_filtered_count == 1


def test_wikipedia_system_and_action_links_are_filtered():
    stats = article_link_follow.ArticleLinkCandidateBuildStats()
    docs = [{
        "doc_id": "wiki-parent",
        "url": "https://en.wikipedia.org/wiki/Sovereign_wealth_fund",
        "original_content": " ".join((
            "https://en.wikipedia.org/w/index.php?title=Other&action=edit",
            "https://en.wikipedia.org/w/index.php?title=Other&diff=123",
            "https://en.wikipedia.org/wiki/Special:RecentChanges",
            "https://en.wikipedia.org/wiki/Talk:Sovereign_wealth_fund",
            "https://en.wikipedia.org/wiki/Research_report",
        )),
    }]

    candidates = build_article_link_candidates(
        docs, existing_urls=set(), stats=stats
    )

    assert [item.url for item in candidates] == [
        "https://en.wikipedia.org/wiki/Research_report"
    ]
    assert stats.wikipedia_system_filtered_count == 4


def test_build_candidates_rejects_non_http_and_binary_assets():
    docs = [{
        "doc_id": "a",
        "url": "https://example.com/a",
        "original_content": (
            "[mail](mailto:test@example.com) "
            "[script](javascript:alert(1)) "
            "[image](https://example.com/image.png) "
            "[archive](https://example.com/data.zip)"
        ),
    }]

    assert build_article_link_candidates(docs, existing_urls=set()) == []


def test_candidate_build_stats_explain_filter_funnel_without_changing_results():
    stats = article_link_follow.ArticleLinkCandidateBuildStats()
    docs = [{
        "doc_id": "a",
        "url": "https://example.com/a",
        "original_content": (
            "https://example.com/a "
            "https://example.com/existing "
            "https://example.com/attempted "
            "https://example.com/image.png "
            "https://example.com/report "
            "https://example.com/report"
        ),
    }]

    candidates = build_article_link_candidates(
        docs,
        existing_urls={"https://example.com/existing"},
        attempted_urls={"https://example.com/attempted"},
        stats=stats,
    )

    assert [item.canonical_url for item in candidates] == ["https://example.com/report"]
    assert stats.source_doc_count == 1
    assert stats.raw_extracted_link_count == 6
    assert stats.self_link_filtered_count == 1
    assert stats.existing_url_filtered_count == 1
    assert stats.attempted_url_filtered_count == 1
    assert stats.blocked_suffix_count == 1
    assert stats.duplicate_link_count == 1
    assert stats.final_candidate_count == 1


def test_build_article_link_source_preserves_bounded_links_and_context():
    content = (
        "Policy evidence before [official report](/reports/2026) after details. "
        '<a href="https://agency.gov/data">Agency dataset</a> supports the finding. '
        "Mirror https://archive.example.org/study."
    )

    source = build_article_link_source(content, max_links=2)
    candidates = candidates_from_content(source)

    assert [item.url for item in candidates] == [
        "https://example.com/reports/2026",
        "https://agency.gov/data",
    ]
    assert "Policy evidence before" in source
    assert "after details" in source
    assert "Agency dataset" in source
    assert "archive.example.org" not in source
    assert build_article_link_source(content, max_links=0) == ""


def test_build_article_link_source_returns_unique_link_count_without_reparsing():
    content = (
        "Before [first](/reports/one) and [duplicate](/reports/one). "
        '<a href="https://example.com/two">second</a>'
    )

    source, link_count = article_link_follow.build_article_link_source_with_count(
        content,
        max_links=20,
    )

    assert link_count == 2
    assert "/reports/one" in source
    assert "https://example.com/two" in source


def test_html_link_parser_does_not_cross_unclosed_anchor_tags():
    content = (
        '<a href="https://example.com/broken">broken '
        '<a href="https://example.com/report">Report</a>'
    )

    candidates = candidates_from_content(content)

    assert [(item.url, item.anchor_text) for item in candidates] == [
        ("https://example.com/report", "Report"),
    ]


def test_build_candidates_prefers_precompression_link_source():
    docs = [{
        "doc_id": "a",
        "title": "Parent A",
        "url": "https://example.com/article",
        "query": "official report",
        "original_content": "Compressed evidence without any links.",
        ARTICLE_LINK_SOURCE_FIELD: (
            "Supporting evidence [official report](/reports/2026) with context."
        ),
    }]

    candidates = build_article_link_candidates(docs, existing_urls=set())

    assert [item.url for item in candidates] == [
        "https://example.com/reports/2026",
    ]
    assert candidates[0].anchor_text == "official report"


def test_build_article_link_source_sanitizes_ambiguous_and_oversized_fields():
    content = (
        '<a href="https://example.com/report_(final)">'
        "https://labels.example.com/[report]</a> "
        f'<a href="https://example.com/{"x" * 3000}">oversized</a>'
    )

    source = build_article_link_source(content, max_links=20)
    candidates = candidates_from_content(source)
    unique_urls = [item.url for item in candidates]

    assert unique_urls == ["https://example.com/report_(final%29"]
    assert "labels.example.com" not in source
    assert len(source) < 3000


def test_build_article_link_source_uses_url_slug_for_missing_anchor():
    source = build_article_link_source(
        "Read https://www.preqin.com/insights/research/reports/"
        "sovereign-wealth-funds-in-motion for details."
    )

    candidates = candidates_from_content(source)

    assert candidates[0].anchor_text == "sovereign wealth funds in motion"
    assert "source link" not in source


@dataclass(frozen=True)
class CandidateOptions:
    """Optional fields for selection-test candidates."""

    anchor: str = ""
    before: str = ""
    after: str = ""
    position: int = 0


def _candidate(
    index: int,
    url: str,
    options: CandidateOptions | None = None,
) -> ArticleLinkCandidate:
    candidate_options = options or CandidateOptions()
    return ArticleLinkCandidate(
        candidate_index=index,
        url=url,
        canonical_url=url,
        anchor_text=candidate_options.anchor,
        context_before=candidate_options.before,
        context_after=candidate_options.after,
        parent_doc_id="a",
        parent_title="Parent",
        parent_url="https://example.com/a",
        query="official evidence",
        source_position=candidate_options.position,
    )


def test_rule_selection_hard_filters_homepage_and_action_links():
    candidates = [
        _candidate(0, "https://example.com/", CandidateOptions(anchor="Official report")),
        _candidate(1, "https://example.com/login", CandidateOptions(anchor="Login")),
        _candidate(2, "https://example.com/report", CandidateOptions(anchor="Official report")),
    ]

    selected = select_article_link_candidates(
        candidates,
        task_text="Find the official report",
        max_urls=3,
    )

    assert [item.candidate_index for item in selected] == [2]


def test_rule_selection_accepts_three_simple_match_reasons():
    candidates = [
        _candidate(0, "https://example.com/a", CandidateOptions(anchor="subsidy policy")),
        _candidate(1, "https://example.com/b", CandidateOptions(after="subsidy policy details")),
        _candidate(2, "https://example.com/dataset/2026", CandidateOptions(anchor="download")),
        _candidate(3, "https://example.com/about", CandidateOptions(anchor="About us")),
    ]

    selected = select_article_link_candidates(
        candidates,
        task_text="subsidy policy",
        max_urls=4,
    )

    assert [(item.candidate_index, item.reasons) for item in selected] == [
        (0, ("anchor_match",)),
        (1, ("context_match",)),
        (2, ("evidence_keyword",)),
    ]


def test_rule_selection_is_stable_and_does_not_fill_with_unmatched_links():
    candidates = [
        _candidate(
            0,
            "https://example.com/z-report",
            CandidateOptions(anchor="report", position=20),
        ),
        _candidate(
            1,
            "https://example.com/a-report",
            CandidateOptions(anchor="report", position=10),
        ),
        _candidate(
            2,
            "https://example.com/unrelated",
            CandidateOptions(anchor="About", position=0),
        ),
    ]

    selected = select_article_link_candidates(
        candidates,
        task_text="unrelated research topic",
        max_urls=3,
    )

    assert [item.candidate_index for item in selected] == [1, 0]
