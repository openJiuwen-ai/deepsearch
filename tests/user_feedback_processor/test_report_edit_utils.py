from openjiuwen_deepsearch.algorithm.user_feedback_processor.report_edit_utils import (
    strip_markup_in_range,
    strip_markup_in_range_with_metadata,
)


def test_strip_markup_in_range_removes_checked_citations_and_collects_inference_ids():
    text = "前缀[checked_citation:7][[1]](https://a.com)[结论](#inference:2)后缀"

    stripped, removed_ranges, removed_ids = strip_markup_in_range(text, 0, len(text))

    assert stripped == "前缀结论后缀"
    assert removed_ranges == {(2, 42)}
    assert removed_ids == [2]


def test_strip_markup_in_range_supports_checked_citation_urls_with_parentheses():
    text = "前缀[checked_citation:1][[1]](https://example.com/a_(b))后缀"

    stripped, removed_ranges, removed_ids = strip_markup_in_range(text, 0, len(text))

    assert stripped == "前缀后缀"
    assert removed_ranges == {(2, 54)}
    assert removed_ids == []


def test_strip_markup_in_range_supports_nested_and_escaped_parentheses_in_urls():
    text = (
        "甲[checked_citation:12][[1]](https://example.com/a_(b_(c)))"
        "乙[[2]](https://example.com/escaped_\\(x\\))丙"
    )

    result = strip_markup_in_range_with_metadata(text, 0, len(text))

    assert result.text == "甲乙丙"
    assert [item.marker_text for item in result.removed_citations] == [
        "[checked_citation:12][[1]](https://example.com/a_(b_(c)))",
        "[[2]](https://example.com/escaped_\\(x\\))",
    ]


def test_strip_markup_in_range_leaves_unclosed_parenthesized_url_unchanged():
    text = "甲[checked_citation:12][[1]](https://example.com/a_(b)乙"

    result = strip_markup_in_range_with_metadata(text, 0, len(text))

    assert result.text == text
    assert result.removed_citations == []


def test_strip_markup_in_range_keeps_plain_text_outside_selected_span():
    text = "开头[checked_citation:1][[1]](https://a.com)正文尾部"
    start = text.index("正文")
    end = len(text)

    stripped, removed_ranges, removed_ids = strip_markup_in_range(text, start, end)

    assert stripped == text
    assert removed_ranges == set()
    assert removed_ids == []


def test_strip_markup_in_range_removes_plain_citation_markers_when_trace_source_is_disabled():
    text = "前缀[citation: 1][结论](#inference:2)后缀"

    stripped, removed_ranges, removed_ids = strip_markup_in_range(text, 0, len(text))

    assert stripped == "前缀结论后缀"
    assert removed_ranges == {(2, 15)}
    assert removed_ids == [2]


def test_strip_markup_metadata_records_checked_citation_id_and_boundary_map():
    text = "前缀事实A[checked_citation:7][[3]](https://a.com)事实B后缀"
    start = text.index("事实A")
    end = text.index("后缀")

    result = strip_markup_in_range_with_metadata(text, start, end)

    assert result.text == "前缀事实A事实B后缀"
    assert result.clean_range_start == start
    assert result.clean_range_end == start + len("事实A事实B")
    assert [item.checked_citation_id for item in result.removed_citations] == [7]
    local_boundary_after_a = len("事实A")
    raw_after_citation = text.index("事实B")
    assert result.clean_boundary_to_raw_boundary[local_boundary_after_a] == raw_after_citation


def test_strip_markup_metadata_maps_inference_display_text_to_full_raw_marker():
    text = "前缀[结论A](#inference:3)后缀"
    start = text.index("[结论A]")
    end = text.index("后缀")

    result = strip_markup_in_range_with_metadata(text, start, end)

    assert result.text == "前缀结论A后缀"
    assert result.removed_inference_ids == [3]
    assert text[
        result.clean_boundary_to_raw_boundary[0]:result.clean_boundary_to_raw_boundary[len("结论A")]
    ] == "[结论A](#inference:3)"
