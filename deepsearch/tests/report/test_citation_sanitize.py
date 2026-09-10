from openjiuwen_deepsearch.algorithm.report.report_utils import (
    _strip_chart_markup,
    sanitize_citation_markers,
)


def test_sanitize_leaves_text_without_markers_unchanged():
    assert sanitize_citation_markers("普通正文，无锚点。") == "普通正文，无锚点。"


def test_sanitize_strips_well_formed_bracket_markers():
    assert sanitize_citation_markers("收入增长20%[citation:3]。") == "收入增长20%。"


def test_sanitize_strips_malformed_angle_lt_left_with_bracket_right():
    assert sanitize_citation_markers("收入增长20%<citation:3]。") == "收入增长20%。"


def test_sanitize_strips_malformed_angle_gt_left_with_paren_right():
    assert sanitize_citation_markers("见>citation:3)的数据。") == "见的数据。"


def test_sanitize_strips_malformed_angle_pair():
    assert sanitize_citation_markers("a<citation:3>b") == "ab"


def test_sanitize_strips_malformed_paren_left():
    # 模型误输出 (citation:3) 时，左括号 ( 原正则字符类漏掉，残留孤立 (
    assert sanitize_citation_markers("a(citation:3)b") == "ab"


def test_sanitize_strips_malformed_paren_pair():
    assert sanitize_citation_markers("(citation:3)") == ""


def test_sanitize_strips_multiple_mixed_markers():
    assert sanitize_citation_markers("[citation:1]x<citation:2]") == "x"


def test_sanitize_preserves_non_numeric_citation_text():
    # citation: 后非数字不匹配，不误伤
    assert sanitize_citation_markers("见[citation:abc]") == "见[citation:abc]"


def test_sanitize_preserves_checked_citation_markers():
    # checked_citation 是另一套标记，由专用剥离器处理，此处不应破坏
    assert sanitize_citation_markers("[checked_citation:3]") == "[checked_citation:3]"


def test_strip_chart_markup_clears_malformed_citation_delimiters():
    assert _strip_chart_markup("标题<citation:3]数据") == "标题数据"
