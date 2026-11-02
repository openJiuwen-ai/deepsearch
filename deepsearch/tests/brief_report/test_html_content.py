"""Brief HTML 内容预处理与引用转换测试。"""

from openjiuwen_deepsearch.algorithm.brief_report.html_content import (
    split_report_markdown,
)


def test_split_report_markdown_extracts_summary_sections_and_skips_references():
    """拆分：标题/摘要/章节提取正确；参考文献节与散落条目行剔除。"""
    cleaned = (
        "# 市场分析\n\n## 核心摘要\n\n摘要结论 [1]。\n\n## 1 规模\n\n规模 [1]。\n\n"
        "## 2 格局\n\n格局 [1]。\n\n## 参考文章\n\n[1]. [来源甲](https://example.com/a)\n"
    )
    title, summary_md, sections = split_report_markdown(cleaned)

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

    _title, _summary, sections = split_report_markdown(markdown)

    assert [section.section_id for section in sections] == ["1"]
    assert "## install instructions" in sections[0].markdown
    assert "正文" in sections[0].markdown
