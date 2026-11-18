import pytest

from openjiuwen_deepsearch.algorithm.chart_generation.figure_placeholders import (
    FigurePlaceholderGenerator,
)


pytestmark = pytest.mark.unit


def _report_with_sections(include_toc: bool) -> str:
    toc = "# 目录\n\n[第一章](#chapter-1)\n\n" if include_toc else ""
    return (
        "# 报告标题\n\n"
        f"{toc}"
        "# 摘要\n\n摘要内容\n\n"
        "# 1. 第一章\n\n## 1.1 小节\n\n第一章正文\n\n"
        "# 2. 第二章\n\n## 2.1 小节\n\n第二章正文\n\n"
        "# 结论\n\n结论内容\n\n"
        "# 参考文章\n\n参考内容\n"
    )


@pytest.mark.parametrize("include_toc", [False, True])
def test_split_report_by_h1_excludes_non_body_sections_and_reindexes(include_toc):
    sections = FigurePlaceholderGenerator._split_report_by_h1(
        _report_with_sections(include_toc)
    )

    assert [section["title"] for section in sections] == ["1. 第一章", "2. 第二章"]
    assert [section["index"] for section in sections] == [1, 2]


def test_split_report_by_h1_titles_clean_with_chapter_anchors():
    """锚点行在 H1 之后，切章 title 不应含锚点 HTML。"""
    report = (
        "# 报告标题\n\n"
        "# 目录\n\n[1. 第一章](#chapter-1)\n\n"
        "# 摘要\n\n摘要内容\n\n"
        '# 1. 第一章\n<a id="chapter-1"></a>\n\n## 1.1 小节\n\n第一章正文\n\n'
        '# 2. 第二章\n<a id="chapter-2"></a>\n\n## 2.1 小节\n\n第二章正文\n\n'
        "# 结论\n\n结论内容\n\n"
        "# 参考文章\n\n参考内容\n"
    )
    sections = FigurePlaceholderGenerator._split_report_by_h1(report)

    assert [section["title"] for section in sections] == ["1. 第一章", "2. 第二章"]
    for section in sections:
        assert "<a id" not in section["title"]


def test_split_report_by_h1_handles_duplicate_titles():
    """同名 H1 出现两次时，第二个章节的 content 不能为空、也不能吞掉后续章节。"""
    report = (
        "# 报告标题\n\n"
        "# 背景\n\n第一段背景。\n\n"
        "# 背景\n\n第二段背景。\n"
    )
    sections = FigurePlaceholderGenerator._split_report_by_h1(report)

    assert [section["title"] for section in sections] == ["背景", "背景"]
    assert [section["content"] for section in sections] == ["第一段背景。", "第二段背景。"]


def test_split_report_by_h1_ignores_headings_inside_code_fence():
    """代码块里的 ``# 注释`` 不是 H1，不能切出新章节。"""
    report = (
        "# 报告标题\n\n"
        "# 1. 使用说明\n\n"
        "示例如下：\n\n"
        "```bash\n# install deps\npip install x\n```\n\n"
        "# 2. 结束\n\n收尾。\n"
    )
    sections = FigurePlaceholderGenerator._split_report_by_h1(report)

    assert [section["title"] for section in sections] == ["1. 使用说明", "2. 结束"]
    assert "pip install x" in sections[0]["content"]
    assert "install deps" in sections[0]["content"]


def test_split_report_by_h1_keeps_first_chapter_when_report_has_no_title():
    """报告没有总标题时，第一个 H1 就是正文首章，不能被当成标题丢掉。"""
    report = "# 1. 第一章\n\n正文一\n\n# 2. 第二章\n\n正文二\n"
    sections = FigurePlaceholderGenerator._split_report_by_h1(report)

    assert [section["title"] for section in sections] == ["1. 第一章", "2. 第二章"]
    assert [section["content"] for section in sections] == ["正文一", "正文二"]


def test_split_section_by_h2_handles_duplicate_titles():
    section = {
        "index": 2,
        "title": "2. 第二章",
        "content": "## 2.1 小节\n\n甲内容\n\n## 2.1 小节\n\n乙内容\n",
    }
    sub_sections = FigurePlaceholderGenerator._split_section_by_h2(section)

    assert [sub["content"] for sub in sub_sections] == ["甲内容", "乙内容"]
    assert [sub["index_h2"] for sub in sub_sections] == [0, 1]

