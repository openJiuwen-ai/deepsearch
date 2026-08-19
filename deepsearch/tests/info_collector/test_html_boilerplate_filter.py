# -*- coding: utf-8 -*-
"""DOM 级网页正文噪声过滤器及其富化路径集成的测试。

规则与参数依据实验推荐方案(baseline + 规则过滤器,88 篇语料实测零误杀)。
"""

from unittest.mock import patch

import pytest
import requests
from bs4 import BeautifulSoup

from openjiuwen_deepsearch.algorithm.research_collector.html_boilerplate_filter import (
    CHROME_FEATURE_WORDS,
    detect_boilerplate,
    extract_clean_main_text,
    filter_boilerplate_blocks,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment import (
    WebPageEnrichmentNode,
)

_FULL_HTML_FETCH_TARGET = (
    "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
    "webpage_enrichment._fetch_html_document"
)
_DIRECT_FETCH_TARGET = (
    "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
    "webpage_enrichment.WebFetchWebpageAdapter.fetch_webpage_sync"
)
_JINA_FETCH_TARGET = (
    "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
    "webpage_enrichment.WebFetchWebpageAdapter.fetch_via_jina_reader_sync"
)


class ExposedWebPageEnrichmentNode(WebPageEnrichmentNode):
    """公开受保护方法，便于测试抓取取舍逻辑。"""

    async def fetch_webpage(
        self,
        url: str,
        timeout_seconds: int,
        minimum_content_length: int = 200,
    ) -> dict:
        """调用节点网页抓取方法。"""
        return await self._fetch_webpage(url, timeout_seconds, minimum_content_length)


# 参照实验中 camet.org.cn 统计报告页构造:薄正文 + 重导航 + 备案号页脚。
_CAMET_STYLE_HTML = """
<html><body>
<div>
<ul>
<li><a href="/">网站首页</a></li>
<li><a href="/party">党建工作与会员之家服务平台入口</a></li>
<li><a href="/gangling">中国城市轨道交通智慧城轨发展纲要(修订版V2.0 2026—2035年)</a></li>
<li><a href="/report">统计报告与行业发展年度报告专栏页面</a></li>
</ul>
</div>
<div>
<p>采编时间：2025年4月10日 来源：中国城市轨道交通协会办公室</p>
<p><a href="/files/2024.pdf">城市轨道交通2024年度统计和分析报告</a></p>
</div>
<div>
<a href="/beian1">京ICP备19038936号-1</a>
<a href="/beian2">京公网安备 11010202008651号</a>
<a href="/sitemap">网站地图与联系我们</a>
</div>
</body></html>
"""

# 数据表格型正文:短、数字多、无链接、无特征词,规则过滤器必须零误杀。
_DATA_TABLE_HTML = """
<html><body>
<div class="content">
<p>2024年各地城市轨道交通客运量统计表如下所示,数据来源于年度统计报告。</p>
<table>
<tr><td>城市</td><td>客运量(万人次)</td><td>日均</td><td>站点数</td></tr>
<tr><td>无锡</td><td>5084.2</td><td>34.6</td><td>97</td></tr>
<tr><td>苏州</td><td>931.2</td><td>46.2</td><td>120</td></tr>
</table>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# 规则过滤器单元测试
# ---------------------------------------------------------------------------


def test_rule_filter_removes_nav_menu_and_beian_footer():
    """camet 同构页:导航菜单与备案号页脚块应被规则删除,薄正文保留。"""
    soup = BeautifulSoup(_CAMET_STYLE_HTML, "html.parser")

    removed = filter_boilerplate_blocks(soup)

    assert removed >= 2
    remaining = soup.get_text(" ", strip=True)
    assert "智慧城轨发展纲要" not in remaining
    assert "2026—2035年" not in remaining
    assert "ICP备" not in remaining
    assert "公网安备" not in remaining
    assert "采编时间：2025年4月10日" in remaining
    assert "城市轨道交通2024年度统计和分析报告" in remaining


def test_extract_clean_main_text_outputs_thin_content_only():
    """完整提取管线的输出只含正文,不泄漏导航/页脚 chrome。"""
    text = extract_clean_main_text(_CAMET_STYLE_HTML)

    assert "采编时间：2025年4月10日" in text
    assert "纲要" not in text
    assert "ICP备" not in text
    assert "网站地图" not in text


def test_rule_filter_keeps_data_table_rows():
    """数据表格行("无锡 5084.2 34.6 97"类)无链接无特征词,必须零误杀。"""
    soup = BeautifulSoup(_DATA_TABLE_HTML, "html.parser")

    removed = filter_boilerplate_blocks(soup)

    assert removed == 0
    text = extract_clean_main_text(_DATA_TABLE_HTML)
    for marker in ("无锡", "5084.2", "34.6", "97", "931.2"):
        assert marker in text


def test_layout_container_over_half_page_text_is_never_removed():
    """占全页文本 >50% 的布局容器即使命中双条件也永不删除(宁可漏杀)。"""
    paragraph = "正文段落" * 60  # 240 字正文
    # 链接文本总量(12 x ~22 字)超过容器文本一半,容器同时命中"相关阅读",
    # 若无保险丝将满足"链接密度 >= 0.5 且命中特征词"的删除条件。
    links = "\n".join(
        f'<a href="/{i}">相关阅读：城市轨道交通行业发展专题报道之{i}</a>' for i in range(12)
    )
    html = f"""
    <html><body>
    <div>
    <p>{paragraph}</p>
    {links}
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")

    removed = filter_boilerplate_blocks(soup)

    # 外层容器占全页文本过半,保险丝要求永不删除;链接块随容器整体保留(漏杀可接受)。
    assert removed == 0
    remaining = soup.get_text(" ", strip=True)
    assert paragraph in remaining
    assert "相关阅读" in remaining


def test_inner_chrome_block_removed_while_large_wrapper_kept():
    """大容器受保护时,其内部满足双条件的子块仍会被单独评估并删除。"""
    paragraph = "正文段落" * 100  # 400 字正文,确保容器文本占比 >50%
    html = f"""
    <html><body>
    <div>
    <p>{paragraph}</p>
    <div>
    <a href="/a">相关阅读：专题报道之一</a>
    <a href="/b">上一篇：旧闻回顾</a>
    <a href="/c">下一篇：前瞻分析</a>
    </div>
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")

    removed = filter_boilerplate_blocks(soup)

    assert removed == 1
    remaining = soup.get_text(" ", strip=True)
    assert paragraph in remaining
    assert "相关阅读" not in remaining
    assert "上一篇" not in remaining


def test_link_density_without_chrome_word_is_kept():
    """链接密度达标但未命中特征词的块不删除(规则一的双条件边界)。"""
    html = """
    <html><body>
    <div>
    <a href="/a">城市轨道交通2023年度统计和分析报告</a>
    <a href="/b">城市轨道交通2022年度统计和分析报告</a>
    </div>
    <p>正文内容</p>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")

    removed = filter_boilerplate_blocks(soup)

    assert removed == 0
    assert "2023年度统计和分析报告" in soup.get_text(" ", strip=True)


def test_chrome_word_with_low_link_density_is_kept():
    """命中特征词但链接密度不足的块不删除(如讨论备案制度的正文)。"""
    html = """
    <html><body>
    <div>
    <p>ICP备案管理办法修订解读:本文详细分析备案制度的沿革与影响,
    涉及网站建设、接入服务与监管要求等多个方面的具体规定。</p>
    <a href="/more">查看全文</a>
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")

    removed = filter_boilerplate_blocks(soup)

    assert removed == 0
    assert "ICP备案管理办法修订解读" in soup.get_text(" ", strip=True)


def test_single_link_download_row_is_kept():
    """单链接下载行("…报告.pdf")即使链接密度为 1 也是正文而非链接汤。"""
    html = """
    <html><body>
    <p><a href="/files/report.pdf">城市轨道交通2024年度统计和分析报告.pdf</a></p>
    <p>采编时间：2025年4月10日</p>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")

    removed = filter_boilerplate_blocks(soup)

    assert removed == 0
    assert "城市轨道交通2024年度统计和分析报告.pdf" in soup.get_text(" ", strip=True)


def test_pure_link_soup_without_chrome_word_is_removed():
    """无特征词但链接密度 >= 0.85 且链接数 >= 3 的纯链接汤应删除。"""
    paragraph = "采编时间：2025年4月10日 来源：某某协会办公室,本期统计报告现已正式发布。"
    html = f"""
    <html><body>
    <div>
    <a href="/a">年度统计报告之一</a>
    <a href="/b">年度统计报告之二</a>
    <a href="/c">年度统计报告之三</a>
    </div>
    <p>{paragraph}</p>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")

    removed = filter_boilerplate_blocks(soup)

    assert removed == 1
    remaining = soup.get_text(" ", strip=True)
    assert "年度统计报告之一" not in remaining
    assert paragraph in remaining


def test_extract_clean_main_text_handles_empty_input():
    """空输入返回空串,不抛异常。"""
    assert extract_clean_main_text("") == ""
    assert extract_clean_main_text("   ") == ""


def test_detect_boilerplate_flags_chrome_words():
    """含中英 chrome 特征词的文本判定为污染。"""
    assert detect_boilerplate("正文内容\n京ICP备19038936号-1\n其他内容") is True
    assert detect_boilerplate("Some article body\nAll rights reserved.") is True
    assert detect_boilerplate("") is False


def test_detect_boilerplate_passes_clean_data_text():
    """干净数据文本不判定为污染。"""
    assert detect_boilerplate("无锡 5084.2 34.6 97\n苏州 931.2 46.2 120") is False


def test_chrome_feature_words_cover_zh_and_en():
    """特征词表同时覆盖中英双语(与实验推荐词表一致)。"""
    assert "ICP备" in CHROME_FEATURE_WORDS
    assert "相关阅读" in CHROME_FEATURE_WORDS
    assert "All rights reserved" in CHROME_FEATURE_WORDS
    assert "Privacy Policy" in CHROME_FEATURE_WORDS


# ---------------------------------------------------------------------------
# 集成路径测试(mock 网络层)
# ---------------------------------------------------------------------------

_POLLUTED_HARNESS_CONTENT = (
    "城市轨道交通2024年度统计和分析报告现已发布,包含客运量、运营里程等核心数据。"
    * 4
    + "\n相关阅读：智慧城轨发展纲要(修订版V2.0 2026—2035年)\n京ICP备19038936号-1"
)

_CLEAN_PARAGRAPH = (
    "城市轨道交通2024年度统计和分析报告现已发布,包含客运量、运营里程、"
    "线网规模与客流强度等核心数据,供行业参考。"
) * 5

_FULL_HTML_WITH_CHROME = f"""
<html><head>
<meta property="article:published_time" content="2025-04-10T08:00:00Z" />
</head><body>
<div class="article-content"><p>{_CLEAN_PARAGRAPH}</p></div>
<div>
<a href="/beian1">京ICP备19038936号-1</a>
<a href="/beian2">京公网安备 11010202008651号</a>
<a href="/sitemap">网站地图与联系我们</a>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_polluted_harness_content_is_replaced_by_clean_text():
    """harness 结果命中特征词且净化输出达标时,正文替换为净化输出。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/report"
    direct_result = {"url": url, "status_code": 200, "content": _POLLUTED_HARNESS_CONTENT}

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _FULL_HTML_FETCH_TARGET,
        return_value=_FULL_HTML_WITH_CHROME,
    ):
        result = await node.fetch_webpage(url, 45)

    assert result["fetch_method"] == "harness_webpage_fetch"
    assert result["content"].strip() == _CLEAN_PARAGRAPH
    assert "相关阅读" not in result["content"]
    assert "ICP备" not in result["content"]
    # 同一份完整 HTML 的日期解析行为保持不变。
    assert result["doc_date"]["date"] == "2025-04-10"


@pytest.mark.asyncio
async def test_clean_harness_content_is_not_replaced():
    """harness 结果未命中特征词时保留原文,不做替换。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/clean"
    clean_content = "x" * 300
    direct_result = {"url": url, "status_code": 200, "content": clean_content}

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _FULL_HTML_FETCH_TARGET,
        return_value=_FULL_HTML_WITH_CHROME,
    ):
        result = await node.fetch_webpage(url, 45)

    assert result["content"] == clean_content


@pytest.mark.asyncio
async def test_full_html_fetch_failure_degrades_silently():
    """完整 HTML 抓取失败时静默降级,保留 harness 原文(即使其含 chrome)。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/slow"
    direct_result = {"url": url, "status_code": 200, "content": _POLLUTED_HARNESS_CONTENT}

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _FULL_HTML_FETCH_TARGET,
        side_effect=requests.Timeout("full html fetch timed out"),
    ):
        result = await node.fetch_webpage(url, 45)

    assert result["content"] == _POLLUTED_HARNESS_CONTENT
    assert result["fetch_method"] == "harness_webpage_fetch"
    assert "doc_date" not in result


@pytest.mark.asyncio
async def test_short_clean_text_does_not_replace_polluted_content():
    """净化输出低于最低正文长度门槛时,保留 harness 原文不替换。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/thin"
    direct_result = {"url": url, "status_code": 200, "content": _POLLUTED_HARNESS_CONTENT}
    thin_html = "<html><body><p>薄正文</p></body></html>"

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _FULL_HTML_FETCH_TARGET,
        return_value=thin_html,
    ):
        result = await node.fetch_webpage(url, 45)

    assert result["content"] == _POLLUTED_HARNESS_CONTENT


@pytest.mark.asyncio
async def test_jina_path_skips_full_html_fetch():
    """Jina fallback 路径没有 HTML,不触发完整 HTML 抓取与净化替换。"""
    node = ExposedWebPageEnrichmentNode()
    url = "https://a.com/fallback"
    direct_result = {"url": url, "status_code": 200, "content": "short"}
    jina_result = {"url": url, "status_code": 200, "content": "y" * 300}

    with patch(_DIRECT_FETCH_TARGET, return_value=direct_result), patch(
        _JINA_FETCH_TARGET,
        return_value=jina_result,
    ), patch(_FULL_HTML_FETCH_TARGET) as mock_full_html_fetch:
        result = await node.fetch_webpage(url, 45)

    assert result["fetch_method"] == "jina_reader"
    assert result["content"] == "y" * 300
    mock_full_html_fetch.assert_not_called()
