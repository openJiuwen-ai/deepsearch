"""Brief HTML 报告生成的确定性清洗、清理、校验与注入测试。"""

import json
from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
    ECHARTS_SHA256,
    BriefHtmlPreprocessResult,
    _extract_fragment_charts,
    _render_references_html,
    _split_report_markdown,
    convert_inline_citations,
    final_security_assert,
    generate_brief_html_report,
    inject_ai_notice,
    inject_chart_scripts,
    inject_echarts_library,
    preprocess_markdown,
    sanitize_html,
    validate_chart_option,
    validate_html_report,
)


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


def test_sanitize_removes_script_and_event_attributes():
    """script 标签与 on* 事件属性必须被删除。"""
    dirty = (
        '<!DOCTYPE html><html><head><title>t</title></head><body>'
        '<p onclick="alert(1)">正文</p>'
        "<script>alert(2)</script>"
        "</body></html>"
    )
    cleaned = sanitize_html(dirty)

    assert "<script" not in cleaned
    assert "alert" not in cleaned
    assert "onclick" not in cleaned
    assert "<p>正文</p>" in cleaned


def test_sanitize_removes_embedded_frames_and_javascript_urls():
    """iframe/object/embed 与 javascript: URL 必须连同内容一起删除。"""
    dirty = (
        '<!DOCTYPE html><html><head></head><body>'
        '<iframe src="https://evil.example"></iframe>'
        '<object data="https://evil.example"></object>'
        '<a href="javascript:alert(1)">链接</a>'
        "</body></html>"
    )
    cleaned = sanitize_html(dirty)

    for tag in ("iframe", "object", "evil.example"):
        assert tag not in cleaned
    assert "javascript:" not in cleaned
    assert "链接" in cleaned


def test_sanitize_keeps_safe_semantic_content_containers():
    """安全的语义容器必须保留，以免 shell 的核心内容被整体删除。"""
    dirty = (
        '<!DOCTYPE html><html><head><title>t</title></head><body>'
        '<main><article><h1>Hero</h1><aside>摘要</aside>'
        '<figure><figcaption>图注</figcaption></figure>'
        '<details><summary>目录</summary><div id="brief-sections"></div></details>'
        '</article></main></body></html>'
    )

    cleaned = sanitize_html(dirty)

    for tag in ("main", "article", "aside", "figure", "figcaption", "details", "summary"):
        assert f"<{tag}>" in cleaned
    assert '<div id="brief-sections"></div>' in cleaned
    assert all(text in cleaned for text in ("Hero", "摘要", "图注", "目录"))


def test_sanitize_keeps_whitelisted_document_and_keeps_style_only_in_head():
    """白名单结构标签保留；head 内 style 保留、body 内 style 删除。"""
    doc = (
        '<!DOCTYPE html><html><head><title>标题</title>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<style>p{color:#333}</style></head><body>'
        '<div class="echarts-chart" data-chart-id="c1" style="height:360px">x</div>'
        "<style>body{margin:0}</style>"
        "</body></html>"
    )
    cleaned = sanitize_html(doc)

    assert cleaned.lower().startswith("<!doctype html>")
    assert "<title>标题</title>" in cleaned
    assert 'meta charset="utf-8"' in cleaned
    assert 'name="viewport"' in cleaned
    assert "p{color:#333}" in cleaned
    assert "body{margin:0}" not in cleaned
    assert 'class="echarts-chart"' in cleaned
    assert 'data-chart-id="c1"' in cleaned
    assert 'style="height:360px"' in cleaned


def test_sanitize_keeps_citation_link_attrs():
    """引用上标外链的 target/rel 放行且取值受限。"""
    doc = (
        '<!DOCTYPE html><html><head></head><body>'
        '<p>结论<sup class="cite-ref"><a href="https://example.com/a" '
        'target="_blank" rel="noopener noreferrer">[1]</a></sup>。</p>'
        '<section class="references"><ol><li id="ref-1">'
        '<a href="https://example.com/a">来源甲</a></li></ol></section>'
        "</body></html>"
    )
    cleaned = sanitize_html(doc)

    assert 'target="_blank"' in cleaned
    assert 'rel="noopener noreferrer"' in cleaned
    assert 'href="https://example.com/a"' in cleaned
    # 取值受限：其他 target / rel 组合被剥离
    assert 'target="_self"' not in sanitize_html(
        doc.replace('target="_blank"', 'target="_self"')
    )
    assert 'rel="stylesheet"' not in sanitize_html(
        doc.replace('rel="noopener noreferrer"', 'rel="stylesheet"')
    )
    # 相对路径等其他协议仍被剥离
    assert 'href="page.html"' not in sanitize_html(
        doc.replace('href="https://example.com/a" ', 'href="page.html" ', 1)
    )

def test_sanitize_keeps_only_chart_configs_template_and_escapes_payload():
    """非 chart-configs 的 template 被删除；template 注入载荷被剥离。"""
    dirty = (
        '<!DOCTYPE html><html><head></head><body>'
        '<template id="other">abc</template>'
        '<template id="chart-configs">[{"id":"c1","option":{"title":{"text":"'
        '</template><script>alert(1)</script>"}}}]</template>'
        "</body></html>"
    )
    cleaned = sanitize_html(dirty)

    assert '<template id="other">' not in cleaned
    assert "<script" not in cleaned
    assert "alert" not in cleaned
    assert '<template id="chart-configs">' in cleaned
    assert json.loads(cleaned.split('<template id="chart-configs">')[1].split("</template>")[0])[
        0
    ]["option"]["title"]["text"] == ""


def test_sanitize_preserves_literal_less_than_in_chart_config_json():
    """合法图表 JSON 字符串中的 < 不能被误当成 HTML 标签删除。"""
    dirty = (
        '<!DOCTYPE html><html><head></head><body>'
        '<div class="echarts-chart" data-chart-id="c1"></div>'
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"series":[{"name":"iOS<Android","type":"bar","data":[1]}]}}]'
        '</template></body></html>'
    )

    cleaned = sanitize_html(dirty)
    errors, _ = validate_html_report(cleaned)
    injected = inject_chart_scripts(cleaned)

    assert errors == []
    assert 'data-chart-id="c1"' in injected
    assert "iOS\\u003cAndroid" in injected


def test_sanitize_removes_img_tags_and_non_whitelisted_attributes():
    """img 不在白名单；非白名单属性被删除。"""
    dirty = (
        '<!DOCTYPE html><html><head></head><body>'
        '<img src="https://evil.example/x.png" alt="x">'
        '<p data-custom="1" class="ok">正文</p>'
        "</body></html>"
    )
    cleaned = sanitize_html(dirty)

    assert "<img" not in cleaned
    assert "evil.example" not in cleaned
    assert "data-custom" not in cleaned
    assert '<p class="ok">正文</p>' in cleaned


_VALID_HTML = (
    '<!DOCTYPE html><html><head><title>t</title><style>p{color:#333}</style></head><body>'
    "<h1>报告</h1><h2>1 范围</h2><h3>1.1 概览</h3><p>结论<sup>1</sup>。</p>"
    '<div class="echarts-chart" data-chart-id="c1" style="height:360px"></div>'
    '<template id="chart-configs">[{"id":"c1","option":{"series":[{"type":"bar","data":[1,2]}]}}]</template>'
    '<section><a href="https://example.com/a">来源甲</a></section>'
    "</body></html>"
)


def test_validate_accepts_wellformed_html_report():
    """结构和图表配置合规时校验通过。"""
    errors, warnings = validate_html_report(_VALID_HTML)
    assert errors == []
    assert warnings == []


def test_validate_rejects_missing_structure_and_script_residue():
    """缺 DOCTYPE、script 残留、on* 属性、javascript: URL 均属硬错误。"""
    broken = _VALID_HTML.replace("<!DOCTYPE html>", "").replace(
        "</body>", '<script>x</script><p onclick="y()">z</p><a href="javascript:1">w</a></body>'
    )
    errors, _ = validate_html_report(broken)
    assert "missing_doctype" in errors
    assert "script_tag_present" in errors
    assert "event_attribute_present" in errors
    assert "javascript_url_present" in errors


def test_event_attribute_checks_ignore_body_text_resembling_an_attribute():
    """正文中的 one=1 等普通文本不能被误判为事件属性。"""
    html = _VALID_HTML.replace("结论<sup>1</sup>。", "参数 one=1，set only=True。结论<sup>1</sup>。")

    errors, warnings = validate_html_report(html)

    assert errors == []
    assert warnings == []
    final_security_assert(html)


def test_final_security_assert_rejects_actual_event_attribute():
    """终检仍必须拒绝真实的 on* HTML 属性。"""
    html = _VALID_HTML.replace("<h1>报告</h1>", '<h1 onclick="alert(1)">报告</h1>')

    with pytest.raises(RuntimeError, match="event attribute"):
        final_security_assert(html)


def test_validate_rejects_css_external_references_in_style_and_inline():
    """<style> 与内联 style 中的 url()/@import 均被拒绝（含注释与转义混淆变体）。"""
    dirty = _VALID_HTML.replace(
        "<style>p{color:#333}</style>",
        "<style>p{background:url(https://evil.example/x.png)}</style>",
    )
    errors, _ = validate_html_report(dirty)
    assert "css_external_reference" in errors

    sneaky = _VALID_HTML.replace(
        "<style>p{color:#333}</style>",
        "<style>/*x*/p{background:\\75 rl(https://evil.example/x.png)}</style>",
    )
    errors, _ = validate_html_report(sneaky)
    assert "css_external_reference" in errors

    inline = _VALID_HTML.replace(
        'style="height:360px"', 'style="height:360px;background:url(https://evil.example/x.png)"'
    )
    errors, _ = validate_html_report(inline)
    assert "css_external_reference" in errors

    imported = _VALID_HTML.replace(
        "<style>p{color:#333}</style>", "<style>@import url('https://evil.example/x.css');</style>"
    )
    errors, _ = validate_html_report(imported)
    assert "css_external_reference" in errors


def test_validate_rejects_chart_id_mismatch_and_bad_id_format():
    """占位元素与配置项必须一一对应，且 id 匹配白名单正则。"""
    mismatched = _VALID_HTML.replace(
        '<template id="chart-configs">[{"id":"c1"',
        '<template id="chart-configs">[{"id":"c9"',
    )
    errors, _ = validate_html_report(mismatched)
    assert any(error.startswith("chart_config:") for error in errors)

    bad_id = _VALID_HTML.replace('data-chart-id="c1"', 'data-chart-id="1c"')
    errors, _ = validate_html_report(bad_id)
    assert any(error.startswith("chart_config:") for error in errors)


def test_validate_rejects_broken_chart_config_json():
    """template 内容必须是合法 JSON 且 option 为对象。"""
    broken = _VALID_HTML.replace(
        '<template id="chart-configs">[{"id":"c1","option":{',
        '<template id="chart-configs">[{"id":"c1","option":{...broken',
    )
    errors, _ = validate_html_report(broken)
    assert any(error.startswith("chart_config:") for error in errors)

    not_object = _VALID_HTML.replace(
        '<template id="chart-configs">[{"id":"c1","option":{"series":[{"type":"bar","data":[1,2]}]}}]</template>',
        '<template id="chart-configs">[{"id":"c1","option":[1,2]}]</template>',
    )
    errors, _ = validate_html_report(not_object)
    assert any(error.startswith("chart_config:") for error in errors)


def test_normalize_chart_option_drops_incomplete_ratio_series_and_prunes_axis():
    """占比序列存在缺失类别时不应继续绘制，并清理对应图例和副轴。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    option = {
        "xAxis": {
            "type": "category",
            "data": ["2019", "2023", "2028", "2035", "2050"],
        },
        "yAxis": [{"name": "万亿元"}, {"name": "占GDP(%)"}],
        "legend": {"data": ["市场规模(万亿元)", "占GDP比重(%)"]},
        "series": [
            {
                "name": "市场规模(万亿元)",
                "type": "bar",
                "data": [4.4, 7.1, 12.3, 19.1, 49.9],
            },
            {
                "name": "占GDP比重(%)",
                "type": "line",
                "yAxisIndex": 1,
                "connectNulls": True,
                "data": [None, 6, None, 9.6, 12.5],
            },
        ],
    }

    normalized, warnings = module.normalize_chart_option(option)

    assert [series["name"] for series in normalized["series"]] == ["市场规模(万亿元)"]
    assert normalized["yAxis"] == [{"name": "万亿元"}]
    assert normalized["legend"]["data"] == ["市场规模(万亿元)"]
    assert any(
        warning.startswith("chart_series_dropped_incomplete:")
        and "占GDP比重(%)" in warning
        for warning in warnings
    )


def test_normalize_chart_option_forces_line_gap_not_to_connect():
    """普通折线保留缺失点时必须显式断线，不能跨越空值连接。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    option = {
        "xAxis": {"type": "category", "data": ["A", "B", "C"]},
        "series": [
            {
                "name": "用户数",
                "type": "line",
                "connectNulls": True,
                "data": [1, None, 3],
            }
        ],
    }

    normalized, warnings = module.normalize_chart_option(option)

    assert normalized["series"][0]["data"] == [1, None, 3]
    assert normalized["series"][0]["connectNulls"] is False
    assert not any(warning.startswith("chart_series_dropped_incomplete:") for warning in warnings)


def test_normalize_chart_option_drops_series_with_unaligned_categories():
    """无法与分类轴一一对齐的序列必须丢弃，避免 ECharts 按位置错配年份。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    option = {
        "xAxis": {"type": "category", "data": ["A", "B", "C"]},
        "series": [{"name": "规模", "type": "bar", "data": [1, 2]}],
    }

    normalized, warnings = module.normalize_chart_option(option)

    assert normalized["series"] == []
    assert any(warning.startswith("chart_series_length_mismatch:") for warning in warnings)


def test_normalize_chart_option_aligns_named_sparse_data_before_gap_check():
    """带年份 name 的稀疏序列先按横轴对齐，再识别缺失类别。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    option = {
        "xAxis": {"type": "category", "data": ["2019", "2023", "2028"]},
        "series": [
            {
                "name": "占GDP比重(%)",
                "type": "line",
                "data": [
                    {"name": "2023", "value": 6},
                    {"name": "2028", "value": 7},
                ],
            }
        ],
    }

    normalized, warnings = module.normalize_chart_option(option)

    assert normalized["series"] == []
    assert any("missing categories: 2019" in warning for warning in warnings)


def test_normalize_chart_option_drops_named_series_with_unknown_category():
    """命名数据含分类轴之外的类别时必须丢弃，避免数量相等掩盖错配。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    option = {
        "xAxis": {"type": "category", "data": ["A", "B", "C"]},
        "series": [
            {
                "name": "规模",
                "type": "bar",
                "data": [
                    {"name": "A", "value": 1},
                    {"name": "B", "value": 2},
                    {"name": "D", "value": 3},
                ],
            }
        ],
    }

    normalized, warnings = module.normalize_chart_option(option)

    assert normalized["series"] == []
    assert any(warning.startswith("chart_series_category_mismatch:") for warning in warnings)


def test_assemble_html_report_applies_chart_semantic_fallback():
    """HTML 拼装阶段必须真正应用图表语义兜底，而不是只提供未调用的辅助函数。"""
    from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
        _assemble_html_report,
    )

    shell = (
        "<!DOCTYPE html><html><head><style>.card{padding:1px}</style></head><body>"
        "<h1>报告</h1>"
        '<div id="brief-sections"></div>'
        '<div id="brief-references"></div>'
        "</body></html>"
    )
    fragments = [
        '<section class="card"><h2>1 甲</h2>'
        '<div class="echarts-chart" data-chart-id="s1-c1"></div></section>'
    ]
    configs = [
        {
            "id": "s1-c1",
            "option": {
                "xAxis": {"type": "category", "data": ["2019", "2023", "2028"]},
                "yAxis": [{"name": "万亿元"}, {"name": "占GDP(%)"}],
                "legend": {"data": ["市场规模", "占GDP比重"]},
                "series": [
                    {"name": "市场规模", "type": "bar", "data": [4.4, 7.1, 12.3]},
                    {
                        "name": "占GDP比重",
                        "type": "line",
                        "yAxisIndex": 1,
                        "data": [None, 6, None],
                    },
                ],
            },
        }
    ]
    pre = preprocess_markdown("# 报告\n\n市场规模为 4.4、7.1 和 12.3。\n")

    assembled = _assemble_html_report(shell, fragments, configs, pre, "zh-CN")
    payload = assembled.split('<template id="chart-configs">', 1)[1].split(
        "</template>", 1
    )[0]
    normalized_configs = json.loads(payload)

    option = normalized_configs[0]["option"]
    assert [series["name"] for series in option["series"]] == ["市场规模"]
    assert option["yAxis"] == [{"name": "万亿元"}]
    assert option["legend"]["data"] == ["市场规模"]


def test_assemble_html_report_converts_inline_citations_outside_prompt_stages():
    """引用转换由拼装层确定性完成，shell 与章节模型只需保留原始标记。"""
    from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
        _assemble_html_report,
    )

    shell = (
        "<!DOCTYPE html><html><head><style>.cite-ref{color:#1F5FBF}</style></head><body>"
        "<p>摘要 [[1]](https://example.com/a)</p>"
        '<div id="brief-sections"></div><div id="brief-references"></div>'
        "</body></html>"
    )
    fragments = [
        '<section class="section"><h2>1 范围</h2>'
        "<p>章节结论 [[1]](https://example.com/a)。</p></section>"
    ]
    pre = preprocess_markdown(
        "# 报告\n\n## 1 范围\n\n"
        "章节结论 [checked_citation:1][[1]](https://example.com/a)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
    )

    assembled = _assemble_html_report(shell, fragments, [], pre, "zh-CN")
    expected = (
        '<sup class="cite-ref"><a href="https://example.com/a" '
        'target="_blank" rel="noopener noreferrer">[1]</a></sup>'
    )
    assert assembled.count(expected) == 2
    assert "[[1]](https://example.com/a)" not in assembled


def test_assemble_html_report_removes_text_from_css_bar_fill():
    """拼装层应兜底清除薄 CSS 填充条中的文字，避免文字被 8px 高度裁切。"""
    from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
        _assemble_html_report,
    )

    shell = (
        "<!DOCTYPE html><html><head><style>"
        ".bar-track{height:8px;overflow:hidden}.bar-fill{height:100%}"
        "</style></head><body>"
        '<div id="brief-sections"></div><div id="brief-references"></div>'
        "</body></html>"
    )
    fragments = [
        '<section class="section"><h2>1 对比</h2>'
        '<div class="bar-row">'
        '<span class="bar-label"><span class="name">海尔 — PLC有线方案</span>'
        '<span class="num">并列第一</span></span>'
        '<div class="bar-track"><div class="bar-fill b2" style="width:100%">'
        "<span>PLC有线</span>"
        "</div></div></div></section>"
    ]

    assembled = _assemble_html_report(
        shell, fragments, [], preprocess_markdown("# 报告\n\n无引用。\n"), "zh-CN"
    )

    assert (
        '<span class="name">海尔 — PLC有线方案</span>'
        '<span class="num">并列第一</span>'
    ) in assembled
    assert '<div class="bar-fill b2" style="width:100%"></div>' in assembled
    assert "<span>PLC有线</span>" not in assembled


def test_validate_downgrades_missing_template_to_warning_and_injection_strips_placeholders():
    """占位存在但 template 缺失：降级为警告不触发重试，注入阶段移除占位元素。"""
    from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import inject_chart_scripts

    missing_template = _VALID_HTML.replace(
        '<template id="chart-configs">[{"id":"c1","option":{"series":[{"type":"bar","data":[1,2]}]}}]</template>',
        "",
    )
    errors, warnings = validate_html_report(missing_template)
    assert errors == []
    assert any("template#chart-configs missing" in warning for warning in warnings)

    stripped = inject_chart_scripts(missing_template)
    assert 'class="echarts-chart"' not in stripped
    assert "<h1>报告</h1>" in stripped  # 其余内容不受影响


@pytest.mark.parametrize(
    ("option", "should_pass"),
    [
        ({"series": [{"type": "bar", "data": [1, 2]}]}, True),
        ({"title": {"text": "销量"}}, True),
        ({"graphic": {"elements": [{"style": {"image": "https://evil.example/x.png"}}]}}, False),
        ({"markPoint": {"data": [{"symbol": "image://evil"}]}}, False),
        ({"series": [{"data": [{"name": "a", "value": "data:text/html,x"}]}]}, False),
        ({"tooltip": {"formatter": "<b>x</b>"}}, False),
        ({"tooltip": {"formatter": "{b}: {c}"}}, True),
        ({"title": {"text": "HTTP://UPPER.example"}}, False),
        ({"series": "not-a-list"}, True),
    ],
)
def test_validate_chart_option_rejects_urls_and_formatter_html(option, should_pass):
    """option 递归校验：拒绝 URL 载荷与 formatter HTML，放行纯数据配置。"""
    error = validate_chart_option(option)
    assert (error is None) is should_pass


_INJECT_BASE = (
    '<!DOCTYPE html><html><head><title>t</title></head><body>'
    "<h1>1 范围</h1><p>结论<sup>1</sup>。</p>"
    '<div class="echarts-chart" data-chart-id="c1" style="height:360px"></div>'
    '<template id="chart-configs">'
    '[{"id":"c1","option":{"tooltip":{"trigger":"axis"},"series":[{"type":"bar","data":[1,2]}]}}]'
    "</template></body></html>"
)


def test_inject_chart_scripts_replaces_template_with_forced_richtext_tooltip():
    """注入后 template 被删除，脚本包含转义配置并强制 richText tooltip。"""
    injected = inject_chart_scripts(_INJECT_BASE)

    assert "<template" not in injected
    assert "<script>" in injected
    assert '"renderMode": "richText"' in injected
    assert '"trigger": "axis"' in injected


def test_inject_chart_scripts_escapes_closing_tags_in_payload():
    """配置中的 </script> 载荷必须被转义为 \\u003c，杜绝提前闭合。"""
    dirty = _INJECT_BASE.replace(
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"tooltip":{"trigger":"axis"},"series":[{"type":"bar","data":[1,2]}]}}]'
        "</template>",
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"title":{"text":"</script>"},'
        '"series":[{"type":"bar","data":[1,2]}]}}]'
        "</template>",
    )
    injected = inject_chart_scripts(dirty)
    script = injected.split("<script>")[1].split("</script>")[0]
    assert "</script>" not in script
    assert "\\u003c/script\\u003e" in script


def test_inject_chart_scripts_unescapes_entities_before_parsing():
    """sanitizer 转义后的 template 内容（&lt; 等）须先实体解码再 json.loads，保证数据保真。"""
    dirty = _INJECT_BASE.replace(
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"tooltip":{"trigger":"axis"},"series":[{"type":"bar","data":[1,2]}]}}]'
        "</template>",
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"title":{"text":"A &amp; B &lt;C&gt;"}},'
        '"series":[{"type":"bar","data":[1,2]}]}]'
        "</template>",
    )
    injected = inject_chart_scripts(dirty)
    script = injected.split("<script>")[1].split("</script>")[0]
    assert '"text": "A & B \\u003cC\\u003e"' in script


def test_inject_chart_scripts_drops_empty_config_template_without_runtime_script():
    """空配置 template 不应生成无效初始化脚本或保留空图表占位。"""
    empty = _INJECT_BASE.replace(
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"tooltip":{"trigger":"axis"},"series":[{"type":"bar","data":[1,2]}]}}]'
        "</template>",
        '<template id="chart-configs">[]</template>',
    )

    injected = inject_chart_scripts(empty)

    assert "<template" not in injected
    assert "class=\"echarts-chart\"" not in injected
    assert "<!--openjiuwen:chart-init-->" not in injected


def test_inject_echarts_library_inlines_verified_vendor_into_head():
    """echarts 以内联脚本注入 head，且内容来自通过 SHA-256 校验的 vendor 文件。"""
    injected = inject_echarts_library(_INJECT_BASE)

    head = injected.split("</head>")[0]
    assert head.count("<script>") == 1
    assert len(head) > 100_000  # echarts.min.js 约 1MB
    assert "echarts" in head[:10_000].lower() or len(head) > 100_000


def test_inject_echarts_library_rejects_missing_or_corrupted_vendor(monkeypatch):
    """vendor 缺失或 SHA-256 不匹配视为环境错误。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    monkeypatch.setattr(module, "_ECHARTS_ASSET_PATH", Path("/nonexistent/echarts.min.js"))
    with pytest.raises(FileNotFoundError):
        inject_echarts_library(_INJECT_BASE)

    monkeypatch.setattr(module, "_ECHARTS_ASSET_PATH", Path(__file__))
    with pytest.raises(ValueError):
        inject_echarts_library(_INJECT_BASE)


def test_inject_ai_notice_switches_by_language():
    """规范化后的英文语言值必须生成英文 AI 声明。"""
    zh = inject_ai_notice(_INJECT_BASE, "zh-CN")
    en = inject_ai_notice(_INJECT_BASE, "en")

    assert "本研究报告由 AI 生成，仅供参考" in zh
    assert "This research report was generated by AI and is for reference only." in en


def test_render_references_uses_english_heading_for_normalized_language():
    """规范化后的英文语言值必须生成英文参考文献标题。"""
    pre = BriefHtmlPreprocessResult(
        cleaned_markdown="",
        reference_entries=[(1, "Source", "https://example.com/source")],
    )

    references_html = _render_references_html(pre, "en")

    assert '<section class="references"><h2>References</h2>' in references_html


def test_full_injection_chain_anchors_on_real_closing_tags():
    """完整注入链路中 footer 必须锚定真正的 </body>，而非脚本库内部的假锚点。

    echarts.min.js 内部包含 "</body>" 字符串字面量；注入若按首个匹配
    定位，footer 会被埋进 head 的库脚本中（DOM 中丢失声明、篡改库内
    字符串值）。回归约束：footer 位于 echarts 库块结束标记之后。
    """
    html = inject_ai_notice(
        inject_echarts_library(inject_chart_scripts(_INJECT_BASE)),
        "zh-CN",
    )

    notice_index = html.find("本研究报告由 AI 生成")
    lib_end_index = html.find("<!--/openjiuwen:echarts-lib-->")
    assert notice_index > lib_end_index >= 0
    assert html.rstrip().endswith("</footer>\n</body></html>")
    final_security_assert(html)  # 不抛异常


def test_final_security_assert_rejects_extra_script_outside_markers():
    """系统脚本之外的 script 残留触发终检断言。"""
    html = inject_chart_scripts(_INJECT_BASE) + '<script>alert(1)</script>'
    with pytest.raises(RuntimeError):
        final_security_assert(html)


_SHELL_CSS = (
    "<style>"
    ".card{padding:16px}"
    ".section{background:#fff;padding:16px}"
    ".chart-card{padding:12px}"
    ".chart-title{font-weight:700}"
    ".chart-source{color:#888}"
    ".takeaways{margin-top:8px}"
    ".cite-ref{color:#1F5FBF}"
    ".references{padding:12px}"
    ".echarts-chart{height:360px}"
    ".bar-fill{background:#1F5FBF}"
    ".metric-card{padding:12px}"
    "</style>"
)

_LLM_SHELL = (
    "<html_report><!DOCTYPE html><html><head><title>t</title>"
    f"{_SHELL_CSS}</head><body>"
    "<h1>报告</h1>"
    '<div id="brief-sections"></div>'
    '<div id="brief-references"></div>'
    "</body></html></html_report>"
)

_LLM_SECTION = (
    "<html_section><section class=\"section\"><h2>1 范围</h2>"
    '<p>结论<sup class="cite-ref"><a href="https://example.com/a" '
    'target="_blank" rel="noopener noreferrer">[1]</a></sup>。</p>'
    "</section></html_section>"
)


def _pipeline_markdown():
    return (
        "# 报告\n\n## 1 范围\n\n结论 [checked_citation:1][[1]](https://example.com/a)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
    )


def _dispatch_fake(monkeypatch, shell_content, section_content, calls):
    """按 system prompt 区分 shell/章节调用并记录调用序。"""

    async def fake_invoke(llm, messages, **kwargs):
        calls.append(messages)
        if "<html_section>" in messages[0]["content"]:
            return {"content": section_content()}
        return {"content": shell_content()}

    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)
    return module


@pytest.mark.asyncio
async def test_generate_brief_html_report_success_returns_injected_html(monkeypatch):
    """成功路径：shell + 并行章节 → 确定性拼装 → 校验 → 注入全链路。"""
    calls: list = []
    module = _dispatch_fake(
        monkeypatch, lambda: _LLM_SHELL, lambda: _LLM_SECTION, calls
    )

    html = await generate_brief_html_report(
        llm=object(), markdown=_pipeline_markdown(), language="zh-CN"
    )

    assert html.lower().startswith("<!doctype html>")
    assert "本研究报告由 AI 生成，仅供参考" in html
    assert "<!--openjiuwen:echarts-lib-->" not in html
    assert "<!--openjiuwen:chart-init-->" not in html
    assert "<script" not in html.lower()
    # shell 挂载点被章节片段替换；参考文献确定性渲染；行内引用为 md 报告原生形态
    # （sup 上标 [n] 直达原网站，新窗口打开）
    assert '<div id="brief-sections"' not in html
    assert "<h2>1 范围</h2>" in html
    assert (
        '<sup class="cite-ref"><a href="https://example.com/a" '
        'target="_blank" rel="noopener noreferrer">[1]</a></sup>' in html
    )
    assert '<li id="ref-1"><a href="https://example.com/a">来源甲</a></li>' in html
    assert "<h2>参考文章</h2>" in html
    assert len(calls) == 2  # 1 shell + 1 章节
    assert module is not None


@pytest.mark.asyncio
async def test_generate_brief_html_report_inlines_echarts_only_when_chart_exists(monkeypatch):
    """存在有效图表配置时，最终报告仍内嵌 ECharts 与初始化脚本。"""
    calls: list = []
    chart_section = (
        "<html_section><section class=\"section\"><h2>1 范围</h2>"
        '<p>结论<sup class="cite-ref"><a href="https://example.com/a" '
        'target="_blank" rel="noopener noreferrer">[1]</a></sup>。</p>'
        '<div class="chart-card"><div class="echarts-chart" data-chart-id="c1" '
        'style="height:360px"></div></div>'
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"series":[{"type":"bar","data":[1]}]}}]'
        "</template></section></html_section>"
    )
    module = _dispatch_fake(monkeypatch, lambda: _LLM_SHELL, lambda: chart_section, calls)

    html = await generate_brief_html_report(
        llm=object(), markdown=_pipeline_markdown(), language="zh-CN"
    )

    assert "<!--openjiuwen:echarts-lib-->" in html
    assert "<!--openjiuwen:chart-init-->" in html
    assert html.count("<script>") == 2
    assert module is not None


@pytest.mark.asyncio
async def test_generate_brief_html_report_generates_sections_in_parallel(monkeypatch):
    """多章节报告：shell 一次 + 章节并行 N 次，拼装保持章节顺序。"""
    markdown = (
        "# 报告\n\n## 1 甲\n\n甲内容 [checked_citation:1][[1]](https://example.com/a)。\n\n"
        "## 2 乙\n\n乙内容 [checked_citation:2][[1]](https://example.com/a)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
    )
    calls: list = []

    def section_content():
        # 章节标题在 user 消息（Section Markdown）里，system prompt 不含正文
        prompt = calls[-1][-1]["content"]
        cite = (
            '<sup class="cite-ref"><a href="https://example.com/a" '
            'target="_blank" rel="noopener noreferrer">[1]</a></sup>'
        )
        if "1 甲" in prompt:
            return f"<html_section><h2>1 甲</h2><p>甲{cite}。</p></html_section>"
        return f"<html_section><h2>2 乙</h2><p>乙{cite}。</p></html_section>"

    _dispatch_fake(monkeypatch, lambda: _LLM_SHELL, section_content, calls)

    html = await generate_brief_html_report(
        llm=object(), markdown=markdown, language="zh-CN"
    )

    assert html.index("<h2>1 甲</h2>") < html.index("<h2>2 乙</h2>")
    assert len(calls) == 3  # 1 shell + 2 章节


@pytest.mark.asyncio
async def test_generate_brief_html_report_retries_only_failed_sections(monkeypatch):
    """单章失败时复用其他成功片段，下一轮只重新调用失败章节。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    markdown = (
        "# 报告\n\n## 1 甲\n\n甲内容 [checked_citation:1][[1]](https://example.com/a)。\n\n"
        "## 2 乙\n\n乙内容 [checked_citation:2][[1]](https://example.com/a)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
    )
    section_calls = {"1": 0, "2": 0}
    shell_calls = 0
    cite = (
        '<sup class="cite-ref"><a href="https://example.com/a" '
        'target="_blank" rel="noopener noreferrer">[1]</a></sup>'
    )

    async def fake_invoke(llm, messages, **kwargs):
        nonlocal shell_calls
        if "<html_section>" not in messages[0]["content"]:
            shell_calls += 1
            return {"content": _LLM_SHELL}
        prompt = messages[-1]["content"]
        section_id = "1" if "## 1 甲" in prompt else "2"
        section_calls[section_id] += 1
        if section_id == "2" and section_calls[section_id] == 1:
            return {"content": "truncated section"}
        title = "甲" if section_id == "1" else "乙"
        return {
            "content": (
                f'<html_section><section class="section"><h2>{section_id} {title}</h2>'
                f"<p>{title}{cite}。</p></section></html_section>"
            )
        }

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)

    html = await generate_brief_html_report(
        llm=object(), markdown=markdown, language="zh-CN"
    )

    assert html.index("<h2>1 甲</h2>") < html.index("<h2>2 乙</h2>")
    assert shell_calls == 1
    assert section_calls == {"1": 1, "2": 2}


@pytest.mark.asyncio
async def test_generate_brief_html_report_attributes_css_errors_to_failed_section(monkeypatch):
    """章节外链 CSS 应在片段阶段报错，避免重建 shell 和其他成功章节。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    markdown = (
        "# 报告\n\n## 1 甲\n\n甲内容 [checked_citation:1][[1]](https://example.com/a)。\n\n"
        "## 2 乙\n\n乙内容 [checked_citation:2][[1]](https://example.com/a)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
    )
    section_calls = {"1": 0, "2": 0}
    shell_calls = 0
    cite = (
        '<sup class="cite-ref"><a href="https://example.com/a" '
        'target="_blank" rel="noopener noreferrer">[1]</a></sup>'
    )

    async def fake_invoke(llm, messages, **kwargs):
        nonlocal shell_calls
        if "<html_section>" not in messages[0]["content"]:
            shell_calls += 1
            return {"content": _LLM_SHELL}
        prompt = messages[-1]["content"]
        section_id = "1" if "## 1 甲" in prompt else "2"
        section_calls[section_id] += 1
        title = "甲" if section_id == "1" else "乙"
        unsafe_style = (
            ' style="background:url(https://evil.example/pixel)"'
            if section_id == "2" and section_calls[section_id] == 1
            else ""
        )
        return {
            "content": (
                f'<html_section><section class="section"{unsafe_style}>'
                f"<h2>{section_id} {title}</h2><p>{title}{cite}。</p>"
                "</section></html_section>"
            )
        }

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)

    html = await generate_brief_html_report(
        llm=object(), markdown=markdown, language="zh-CN"
    )

    assert "evil.example" not in html
    assert shell_calls == 1
    assert section_calls == {"1": 1, "2": 2}


@pytest.mark.asyncio
async def test_generate_brief_html_report_retries_section_without_regenerating_shell(monkeypatch, caplog):
    """章节硬错误（template JSON 非法）触发重试且复用已成功的 shell。"""
    import logging

    bad_section = (
        "<html_section><h2>1 范围</h2>"
        '<p>结论<sup class="cite-ref"><a href="https://example.com/a" '
        'target="_blank" rel="noopener noreferrer">[1]</a></sup>。</p>'
        '<template id="chart-configs">[{"id":"c1","option":{...broken}}]</template>'
        "</html_section>"
    )
    section_outputs = [bad_section, _LLM_SECTION]
    calls: list = []
    shell_calls: list = []

    def shell_content():
        shell_calls.append(1)
        return _LLM_SHELL

    def section_content():
        return section_outputs[min(len(calls) - len(shell_calls) - 1, len(section_outputs) - 1)]

    module = _dispatch_fake(monkeypatch, shell_content, section_content, calls)

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        html = await generate_brief_html_report(
            llm=object(), markdown=_pipeline_markdown(), language="zh-CN"
        )

    assert html.lower().startswith("<!doctype html>")
    assert len(calls) == 3  # shell + 坏章节 + 重试章节
    assert len(shell_calls) == 1  # shell 未重复生成
    retry_content = calls[-1][-1]["content"]
    assert "chart_config:" in retry_content  # 章节错误反馈给对应章节
    assert "Fix: ECharts placeholders" in retry_content
    warning_messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Section generation failed" in message and "chart_config:" in message
        for message in warning_messages
    )


@pytest.mark.asyncio
async def test_generate_brief_html_report_accepts_shell_without_fixed_css_classes(monkeypatch):
    """缺少未使用的固定类不应触发整轮 shell 重试。"""
    incomplete_css = "<html_report><!DOCTYPE html><html><head><title>t</title>" \
        "<style>.card{padding:16px}</style></head><body>" \
        "<h1>报告</h1>" \
        '<div id="brief-sections"></div>' \
        '<div id="brief-references"></div>' \
        "</body></html></html_report>"
    calls: list = []

    def shell_content():
        return incomplete_css

    module = _dispatch_fake(
        monkeypatch, shell_content, lambda: _LLM_SECTION, calls
    )

    html = await generate_brief_html_report(
        llm=object(), markdown=_pipeline_markdown(), language="zh-CN"
    )

    assert html.lower().startswith("<!doctype html>")
    assert len(calls) == 2  # shell + 章节，各一次


@pytest.mark.asyncio
async def test_generate_brief_html_report_raises_after_exhausted_retries(monkeypatch):
    """重试耗尽抛 ValueError，由节点层转为 REPORT_GENERATE_ERROR。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    async def fake_invoke(llm, messages, **kwargs):
        return {"content": "no tags at all"}

    class _FakeConfig:
        def __init__(self):
            self.service_config = type("S", (), {"report_max_generate_retry_num": 2})()

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)
    monkeypatch.setattr(module, "Config", _FakeConfig)

    with pytest.raises(ValueError, match="brief html report generation failed"):
        await generate_brief_html_report(llm=object(), markdown=_pipeline_markdown(), language="zh-CN")


def test_split_report_markdown_extracts_summary_sections_and_skips_references():
    """拆分：标题/摘要/章节提取正确；参考文献节与散落条目行剔除。"""
    from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
        _split_report_markdown,
    )

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


def test_extract_fragment_charts_renames_ids_and_strips_unpaired_placeholders():
    """章节图表提取：成对占位/配置重命名为 s{id}-c{k}，孤占位移除。"""
    from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
        _extract_fragment_charts,
    )

    fragment = (
        "<h2>1 范围</h2>"
        '<div class="echarts-chart" data-chart-id="c1" style="height:360px"></div>'
        '<div class="echarts-chart" data-chart-id="c9" style="height:360px"></div>'
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"series":[{"type":"bar","data":[1]}]}},'
        '{"id":"cX","option":{"series":[{"type":"bar","data":[2]}]}}]'
        "</template>"
    )
    cleaned, configs = _extract_fragment_charts(fragment, "1")

    assert 'data-chart-id="s1-c1"' in cleaned
    assert "c9" not in cleaned
    assert "<template" not in cleaned
    assert [config["id"] for config in configs] == ["s1-c1"]
    assert configs[0]["option"]["series"][0]["data"] == [1]


def test_extract_fragment_charts_accepts_chart_template_with_id_after_other_attributes():
    """template 属性顺序不应影响配置提取与移除。"""
    fragment = (
        '<div class="echarts-chart" data-chart-id="c1"></div>'
        '<template class="cfg" id="chart-configs">'
        '[{"id":"c1","option":{"series":[{"type":"bar","data":[1]}]}}]'
        "</template>"
    )

    cleaned, configs = _extract_fragment_charts(fragment, "1")

    assert "<template" not in cleaned
    assert 'data-chart-id="s1-c1"' in cleaned
    assert [config["id"] for config in configs] == ["s1-c1"]


def test_extract_fragment_charts_strips_duplicate_placeholder_after_consuming_config():
    """重复的原 chart id 只能保留一对占位/配置，不能被全局替换制造失配。"""
    fragment = (
        '<div class="echarts-chart" data-chart-id="c1"></div>'
        '<div class="echarts-chart" data-chart-id="c1"></div>'
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"series":[{"type":"bar","data":[1]}]}}]'
        "</template>"
    )

    cleaned, configs = _extract_fragment_charts(fragment, "2")

    assert cleaned.count('data-chart-id="s2-c1"') == 1
    assert 'data-chart-id="c1"' not in cleaned
    assert [config["id"] for config in configs] == ["s2-c1"]


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


def test_assemble_html_report_merges_all_chart_configs():
    """拼装会保留全部章节图表配置，不按全局数量截断。"""
    from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
        _assemble_html_report,
    )

    shell = (
        "<!DOCTYPE html><html><head><style>.card{padding:1px}</style></head><body>"
        "<h1>报告</h1>"
        '<div id="brief-sections"></div>'
        '<div id="brief-references"></div>'
        "</body></html>"
    )
    fragments = [
        '<section class="card"><h2>1 甲</h2><div class="echarts-chart" data-chart-id="s1-c1"></div>'
        '<div class="echarts-chart" data-chart-id="s1-c2"></div></section>',
        '<section class="card"><h2>2 乙</h2><div class="echarts-chart" data-chart-id="s2-c1"></div>'
        '<div class="echarts-chart" data-chart-id="s2-c2"></div></section>',
    ]
    configs = [
        {"id": f"s{s}-c{k}", "option": {"series": [{"type": "bar", "data": [k]}]}}
        for s in (1, 2)
        for k in (1, 2)
    ]
    pre = preprocess_markdown("# 报告\n\n[1]. [来源甲](https://example.com/a)\n")

    assembled = _assemble_html_report(shell, fragments, configs, pre, "zh-CN")

    assert assembled.count('class="echarts-chart"') == 4
    assert 'data-chart-id="s2-c2"' in assembled
    assert "<h2>参考文章</h2>" in assembled
    assert '<li id="ref-1"><a href="https://example.com/a">来源甲</a></li>' in assembled
    # 合并 template 在 body 闭合前，内容经 HTML 转义
    assert assembled.index("<template") < assembled.rindex("</body>")
    errors, _ = validate_html_report(assembled)
    assert errors == []
