import pytest

from openjiuwen_deepsearch.algorithm.report.report_utils import strip_report_source_links


@pytest.mark.parametrize(("source", "expected"), [
    ("事实[citation:1]。", "事实。"),
    ("事实[[2]](https://example.com/paper)。", "事实。"),
    ("[来源文章](https://example.com/paper_(2026))", "来源文章"),
    ("来源：https://example.com/paper。正文", "来源：。正文"),
    ("[3](https://example.com/paper)", ""),
    ("<https://example.com/paper>", ""),
    ("[文献]: https://example.com/paper", ""),
    ("[第一章](#chapter-1)\n<a id=\"chapter-1\"></a>",
     "[第一章](#chapter-1)\n<a id=\"chapter-1\"></a>"),
    ("![趋势图](https://example.com/chart.png)", "![趋势图](https://example.com/chart.png)"),
])
def test_strip_report_source_links(source, expected):
    assert strip_report_source_links(source) == expected
