"""Tests for is_title_blocked in research_collector.collector_function.

Covers P0-4: title blocking rules — exact match, Jaccard similarity,
prefix/suffix differentiation, and metadata-wrapping containment.
"""
from openjiuwen_deepsearch.algorithm.research_collector.collector_function import is_title_blocked


class TestIsTitleBlocked:
    def test_exact_match_blocked(self):
        assert is_title_blocked("Deep Learning for NLP", ["Deep Learning for NLP"]) is True

    def test_different_title_not_blocked(self):
        """Two papers with similar words but different topics should NOT be blocked."""
        # Rule 4 Jaccard: 3 shared tokens out of 8 union -> 0.375 < 0.70 -> not blocked
        assert is_title_blocked(
            "Deep Learning for Computer Vision",
            ["Deep Learning for Natural Language Processing"]
        ) is False

    def test_prefix_with_different_suffix_not_blocked(self):
        """Blocked title is a prefix of target but target has different content -> not blocked."""
        assert is_title_blocked(
            "Deep Learning: A Survey",
            ["Deep Learning"]
        ) is False

    def test_containment_blocked_title_in_target(self):
        """When blocked title is contained in target (same paper with metadata), should block.

        The blocked title is long enough (>=30 normalized chars) to trigger Rule 3
        containment matching, and the target has metadata wrapping at the start/end.
        """
        assert is_title_blocked(
            "[PDF] Deep Learning for Natural Language Processing Survey Author Name",
            ["Deep Learning for Natural Language Processing Survey"]
        ) is True

    def test_short_title_high_overlap_not_blocked(self):
        """Short titles with high word overlap but different meanings should be careful.
        'AI Trends' vs 'AI Trends 2024' -- 2 shared out of 3 union = 0.67 < 0.70 -> not blocked"""
        assert is_title_blocked("AI Trends 2024", ["AI Trends"]) is False
