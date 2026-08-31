"""Brief HTML 内容预处理与引用转换测试。"""

from openjiuwen_deepsearch.algorithm.brief_report.html_content import (
    BriefHtmlPreprocessResult,
    _render_references_html,
    _split_report_markdown,
    convert_inline_citations,
    preprocess_markdown,
)
from openjiuwen_deepsearch.algorithm.brief_report.html_safety import sanitize_html


def test_preprocess_strips_checked_citation_markers_and_keeps_entries():
    """checked_citation 行内标记清洗为 [[n]](URL)（md 原生形态），文末条目保持不动。"""
    markdown = (
        "# 报告\n\n"
        "结论甲 [checked_citation:3][[1]](https://example.com/a)。\n\n"
        "结论乙 [checked_citation:7][[2]](https://example.com/b)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
        "[2]. [来源乙](https://example.com/b)\n"
    )
    pre = preprocess_markdown(markdown)

    assert "[checked_citation" not in pre.cleaned_markdown
    assert "结论甲 [[1]](https://example.com/a)。" in pre.cleaned_markdown
    assert "结论乙 [[2]](https://example.com/b)。" in pre.cleaned_markdown
    assert "[1]. [来源甲](https://example.com/a)" in pre.cleaned_markdown
    assert "[2]. [来源乙](https://example.com/b)" in pre.cleaned_markdown
    assert pre.reference_entries == [
        (1, "来源甲", "https://example.com/a"),
        (2, "来源乙", "https://example.com/b"),
    ]


def test_preprocess_handles_source_tracer_fallback_and_deduplicates_urls():
    """回退形态按 URL 去重并按首次出现编号，且追加文末条目。"""
    markdown = (
        "# 报告\n\n"
        "结论甲 [source_tracer_result][来源甲](https://example.com/a)。\n\n"
        "结论乙 [source_tracer_result][来源乙](https://example.com/b)。\n\n"
        "再次引用甲 [source_tracer_result][来源甲](https://example.com/a)。\n"
    )
    pre = preprocess_markdown(markdown)

    assert "[source_tracer_result]" not in pre.cleaned_markdown
    assert "结论甲 [[1]](https://example.com/a)。" in pre.cleaned_markdown
    assert "结论乙 [[2]](https://example.com/b)。" in pre.cleaned_markdown
    assert "再次引用甲 [[1]](https://example.com/a)。" in pre.cleaned_markdown
    assert pre.reference_entries == [
        (1, "来源甲", "https://example.com/a"),
        (2, "来源乙", "https://example.com/b"),
    ]
    assert "[1]. [来源甲](https://example.com/a)" in pre.cleaned_markdown
    assert "[2]. [来源乙](https://example.com/b)" in pre.cleaned_markdown


def test_preprocess_converts_image_references_into_text_citations():
    """source_tracer 图片引用（! 前缀）按文本引用统一处理并进入参考文献集合。"""
    markdown = "说明 ![source_tracer_result][图](https://example.com/img) 结束。\n"
    pre = preprocess_markdown(markdown)

    assert "![" not in pre.cleaned_markdown
    assert "[source_tracer_result]" not in pre.cleaned_markdown
    assert "说明 [[1]](https://example.com/img) 结束。" in pre.cleaned_markdown
    assert pre.reference_entries == [(1, "图", "https://example.com/img")]


def test_preprocess_parses_nested_and_escaped_paren_urls():
    """URL 解析必须复用 extract_markdown_url 以支持嵌套/转义括号。"""
    markdown = "引用 [source_tracer_result][来源](https://example.com/a\\(1\\))。\n"
    pre = preprocess_markdown(markdown)

    assert "引用 [[1]](https://example.com/a\\(1\\))。" in pre.cleaned_markdown
    assert pre.reference_entries == [(1, "来源", "https://example.com/a\\(1\\)")]


def test_preprocess_handles_source_title_containing_closing_bracket():
    """来源标题含 ] 时，fallback 标记仍必须被规范化而不能裸露给用户。"""
    pre = preprocess_markdown(
        "结论 [source_tracer_result][Apple [2026] Q2](https://example.com/a)。\n"
    )

    assert "[source_tracer_result]" not in pre.cleaned_markdown
    assert "结论 [[1]](https://example.com/a)。" in pre.cleaned_markdown
    assert pre.reference_entries == [(1, "Apple [2026] Q2", "https://example.com/a")]


def test_convert_inline_citations_discards_checked_citation_instance_ids():
    """Brief HTML 只保留引用编号与链接，不暴露 checked_citation 内部实例 ID。"""
    pre = preprocess_markdown(
        "甲 [checked_citation:abc123][[1]](https://example.com/a)，"
        "乙 [checked_citation:def456][[1]](https://example.com/a)。\n"
        "[1]. [来源](https://example.com/a)\n"
    )

    rendered = convert_inline_citations(f"<p>{pre.cleaned_markdown}</p>", pre)
    cleaned = sanitize_html(f"<!DOCTYPE html><html><head></head><body>{rendered}</body></html>")

    assert "checked_citation" not in cleaned
    assert "data-citation-id" not in cleaned
    assert "data-checked-citation" not in cleaned
    assert cleaned.count('href="https://example.com/a"') == 2


def test_convert_inline_citations_renders_non_http_fallback_without_raw_markdown():
    """非 HTTP 回退来源没有可点击链接时，仍应显示上标编号而非原始 markdown。"""
    pre = preprocess_markdown("结论 [source_tracer_result][内部来源](内部来源)。\n")

    rendered = convert_inline_citations(f"<p>{pre.cleaned_markdown}</p>", pre)

    assert "[[1]](内部来源)" not in rendered
    assert '<sup class="cite-ref">[1]</sup>' in rendered


def test_render_references_uses_english_heading_for_normalized_language():
    """规范化后的英文语言值必须生成英文参考文献标题。"""
    pre = BriefHtmlPreprocessResult(
        cleaned_markdown="",
        reference_entries=[(1, "Source", "https://example.com/source")],
    )

    references_html = _render_references_html(pre, "en")

    assert '<section class="references"><h2>References</h2>' in references_html


def test_split_report_markdown_extracts_summary_sections_and_skips_references():
    """拆分：标题/摘要/章节提取正确；参考文献节与散落条目行剔除。"""
    cleaned = (
        "# 市场分析\n\n## 核心摘要\n\n摘要结论 [1]。\n\n## 1 规模\n\n规模 [1]。\n\n"
        "## 2 格局\n\n格局 [1]。\n\n## 参考文章\n\n[1]. [来源甲](https://example.com/a)\n"
    )
    title, summary_md, sections = _split_report_markdown(cleaned)

    assert title == "市场分析"
    assert summary_md.startswith("## 核心摘要")
    assert [chunk.section_id for chunk in sections] == ["1", "2"]
    assert sections[0].title == "规模"
    assert sections[1].markdown.startswith("## 2 格局")
    assert all("来源甲" not in chunk.markdown for chunk in sections)


def test_split_report_markdown_does_not_split_h2_inside_fenced_code_block():
    """围栏代码中的 ## 不是章节标题，不能生成幻影章节或截断正文。"""
    markdown = (
        "# 报告\n\n## 核心摘要\n\n摘要\n\n## 1 实施\n\n"
        "```bash\n## install instructions\necho ok\n```\n\n正文\n\n## 参考文章\n"
    )

    _title, _summary, sections = _split_report_markdown(markdown)

    assert [section.section_id for section in sections] == ["1"]
    assert "## install instructions" in sections[0].markdown
    assert "正文" in sections[0].markdown
