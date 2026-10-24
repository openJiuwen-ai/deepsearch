import pytest

from openjiuwen_deepsearch.algorithm.user_feedback_processor.report_edit_utils import strip_citations_in_range


@pytest.mark.parametrize("marker", [
    "[citation: 2]",
    "[[2]](https://example.com/paper)",
    "[[2]](https://example.com/a_(b_(c)))",
])
def test_edit_cleans_only_complete_citations_inside_selection(marker):
    text = f"Before{marker}Selected{marker}After{marker}"
    start = text.index("Selected")
    end = text.index("After")
    assert strip_citations_in_range(text, start, end) == f"Before{marker}SelectedAfter{marker}"


@pytest.mark.parametrize("selection", [
    "Regular [link](https://example.com/a_(b))",
    "Unclosed [[2]](https://example.com/a_(b)",
    "[Section](#section-2)",
])
def test_edit_preserves_regular_markdown_and_incomplete_links(selection):
    assert strip_citations_in_range(selection, 0, len(selection)) == selection
