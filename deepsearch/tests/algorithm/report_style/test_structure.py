"""Test semantic DOM decoration for exported report HTML."""

from bs4 import BeautifulSoup

from openjiuwen_deepsearch.algorithm.report_style.structure import decorate_report_html


def test_decorate_report_html_wraps_cover_abstract_sections_and_data_blocks():
    """为报告内容添加稳定的页面语义结构与数据块标记。"""
    html = (
        '<html><body><h1>报告标题</h1><h1>摘要</h1><p>摘要内容</p>'
        '<h1>1. 市场</h1><p>正文</p><div class="table-wrap"><table><tr><td>1</td>'
        '</tr></table></div></body></html>'
    )

    result = decorate_report_html(html)
    soup = BeautifulSoup(result, "html.parser")
    shell = soup.select_one("main.report-shell")
    cover = soup.select_one("header.report-cover")
    abstract = soup.select_one("section.report-abstract")
    content = soup.select_one("div.report-content")
    sections = soup.select("section.report-section")

    assert soup.select_one("body.report-page") is not None
    assert shell is not None
    assert cover is not None
    assert abstract is not None
    assert content is not None
    assert sections
    assert shell in cover.parents
    assert shell in abstract.parents
    assert shell in content.parents
    assert all(shell in section.parents for section in sections)
    assert abstract.get_text(" ", strip=True) == "摘要 摘要内容"
    assert sections[0].get_text(" ", strip=True) == "1. 市场 正文 1"
    assert sections[0].find("h1").find_next_sibling("p").get_text(strip=True) == "正文"
    assert soup.select_one(".table-wrap.report-table") is not None


def test_decorate_report_html_marks_content_figures_and_keeps_resource_attributes():
    """为内容、图表标记语义类且不改写资源属性。"""
    html = (
        '<html><body><h1>报告标题</h1><h1>1. 市场</h1>'
        '<p><img src="charts/chart_1.png" alt="图表"/></p>'
        '<div class="mermaid-wrap"><div class="mermaid-rendered">'
        '<svg viewBox="0 0 10 10"><path d="M0 0"/></svg></div></div>'
        '<p><a href="infer/inference_7.html">依据</a></p></body></html>'
    )

    result = decorate_report_html(html)
    soup = BeautifulSoup(result, "html.parser")

    assert soup.select_one(".report-content") is not None
    assert soup.select_one("p.report-figure > img")["src"] == "charts/chart_1.png"
    assert "report-figure" in soup.select_one(".mermaid-wrap")["class"]
    assert soup.select_one("a")["href"] == "infer/inference_7.html"
    assert '<svg viewBox="0 0 10 10"><path d="M0 0"/></svg>' in result


def test_decorate_report_html_preserves_literal_svg_placeholder_comment():
    """保留与旧 SVG 占位符相同的报告注释。"""
    html = (
        "<html><body><h1>报告标题</h1><!--REPORT_SVG_0-->"
        '<svg viewBox="0 0 10 10"><path d="M0 0"/></svg></body></html>'
    )

    result = decorate_report_html(html)

    assert result.count("<!--REPORT_SVG_0-->") == 1
    assert result.count('<svg viewBox="0 0 10 10"><path d="M0 0"/></svg>') == 1
    assert decorate_report_html(result) == result


def test_decorate_report_html_uses_h2_cover_and_h2_sections_with_nested_headings():
    """将首个二级标题作为封面，并仅由后续二级标题开启章节。"""
    html = (
        "<html><body><h2>法律意见书</h2><h3>摘要</h3><p>摘要内容</p>"
        "<h2>一、事实认定</h2><h3>（一）交易背景</h3><p>事实内容</p>"
        '<h2>二、法律分析</h2><p><a href="infer/inference_7.html">依据</a></p>'
        "</body></html>"
    )

    result = decorate_report_html(html)
    soup = BeautifulSoup(result, "html.parser")
    cover = soup.select_one("header.report-cover")
    abstract = soup.select_one("section.report-abstract")
    content = soup.select_one("div.report-content")
    sections = content.select(":scope > section.report-section")

    assert cover is not None
    assert cover.find("h2").get_text(strip=True) == "法律意见书"
    assert abstract is not None
    assert abstract.find("h3").get_text(strip=True) == "摘要"
    assert abstract.find("p").get_text(strip=True) == "摘要内容"
    assert [section.find("h2").get_text(strip=True) for section in sections] == [
        "一、事实认定",
        "二、法律分析",
    ]
    assert sections[0].find("h3").get_text(strip=True) == "（一）交易背景"
    assert sections[1].select_one("a")["href"] == "infer/inference_7.html"
    assert decorate_report_html(result) == result


def test_decorate_report_html_creates_cover_and_section_without_headings():
    """在无标题文档中以首个内容块作为封面，并包装剩余正文。"""
    html = (
        '<html><body>\n  <p>无标题法律说明</p><p>正文内容</p><ul><li>要点</li></ul>'
        '<p><a href="infer/inference_8.html">依据</a></p>\n</body></html>'
    )

    result = decorate_report_html(html)
    soup = BeautifulSoup(result, "html.parser")
    cover = soup.select_one("header.report-cover")
    content = soup.select_one("div.report-content")
    section = content.select_one(":scope > section.report-section")

    assert cover is not None
    assert cover.find("p").get_text(strip=True) == "无标题法律说明"
    assert section is not None
    assert section.get_text(" ", strip=True) == "正文内容 要点 依据"
    assert section.select_one("a")["href"] == "infer/inference_8.html"


def test_decorate_report_html_creates_empty_section_when_only_heading_becomes_cover():
    """仅有标题时，封面后的正文容器仍保留稳定章节。"""
    html = "<html><body><h2>标题</h2></body></html>"

    result = decorate_report_html(html)
    soup = BeautifulSoup(result, "html.parser")
    shell = soup.select_one("main.report-shell")
    cover = soup.select_one("header.report-cover")
    content = soup.select_one("div.report-content")
    section = soup.select_one("div.report-content > section.report-section")

    assert soup.select_one("body.report-page") is not None
    assert shell is not None
    assert cover is not None
    assert content is not None
    assert section is not None
    assert cover.get_text(strip=True) == "标题"
    assert soup.get_text(strip=True) == "标题"
    assert decorate_report_html(result) == result


def test_decorate_report_html_creates_empty_section_when_only_paragraph_becomes_cover():
    """仅有段落时，封面后的正文容器仍保留稳定章节。"""
    html = "<html><body><p>说明</p></body></html>"

    result = decorate_report_html(html)
    soup = BeautifulSoup(result, "html.parser")
    shell = soup.select_one("main.report-shell")
    cover = soup.select_one("header.report-cover")
    content = soup.select_one("div.report-content")
    section = soup.select_one("div.report-content > section.report-section")

    assert soup.select_one("body.report-page") is not None
    assert shell is not None
    assert cover is not None
    assert content is not None
    assert section is not None
    assert cover.get_text(strip=True) == "说明"
    assert soup.get_text(strip=True) == "说明"
    assert decorate_report_html(result) == result
