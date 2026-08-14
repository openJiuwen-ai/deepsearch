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
