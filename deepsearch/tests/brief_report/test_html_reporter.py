"""Brief HTML 报告生成的确定性清洗、清理、校验与注入测试。"""

import json
from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
    ECHARTS_SHA256,
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
    """checked_citation 行内标记清洗为 [n]，文末条目保持不动。"""
    markdown = (
        "# 报告\n\n"
        "结论甲 [checked_citation:3][[1]](https://example.com/a)。\n\n"
        "结论乙 [checked_citation:7][[2]](https://example.com/b)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
        "[2]. [来源乙](https://example.com/b)\n"
    )
    pre = preprocess_markdown(markdown)

    assert "[checked_citation" not in pre.cleaned_markdown
    assert "结论甲 [1]。" in pre.cleaned_markdown
    assert "结论乙 [2]。" in pre.cleaned_markdown
    assert "[1]. [来源甲](https://example.com/a)" in pre.cleaned_markdown
    assert "[2]. [来源乙](https://example.com/b)" in pre.cleaned_markdown
    assert pre.inline_citation_numbers == [1, 2]
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
    assert "结论甲 [1]。" in pre.cleaned_markdown
    assert "结论乙 [2]。" in pre.cleaned_markdown
    assert "再次引用甲 [1]。" in pre.cleaned_markdown
    assert pre.inline_citation_numbers == [1, 2, 1]
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
    assert "说明 [1] 结束。" in pre.cleaned_markdown
    assert pre.reference_entries == [(1, "图", "https://example.com/img")]


def test_preprocess_parses_nested_and_escaped_paren_urls():
    """URL 解析必须复用 extract_markdown_url 以支持嵌套/转义括号。"""
    markdown = "引用 [source_tracer_result][来源](https://example.com/a\\(1\\))。\n"
    pre = preprocess_markdown(markdown)

    assert "引用 [1]。" in pre.cleaned_markdown
    assert pre.reference_entries == [(1, "来源", "https://example.com/a\\(1\\)")]


def test_preprocess_allows_markdown_without_any_citations():
    """无引用标记的输入是合法场景，原样返回。"""
    markdown = "# 报告\n\n没有引用的正文。\n"
    pre = preprocess_markdown(markdown)

    assert pre.cleaned_markdown == markdown
    assert pre.inline_citation_numbers == []
    assert pre.reference_entries == []


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


def test_sanitize_keeps_anchor_href_and_sup_link_validates_citations():
    """页内锚点 href 放行；sup 内嵌锚点链接的引用链路通过校验。"""
    doc = (
        '<!DOCTYPE html><html><head></head><body>'
        '<p>结论<sup><a href="#ref-1">1</a></sup>。</p>'
        '<section class="references"><ol><li id="ref-1">'
        '<a href="https://example.com/a">来源甲</a></li></ol></section>'
        "</body></html>"
    )
    cleaned = sanitize_html(doc)

    assert 'href="#ref-1"' in cleaned
    assert 'id="ref-1"' in cleaned
    assert 'href="https://example.com/a"' in cleaned
    # 相对路径等其他协议仍被剥离
    assert 'href="page.html"' not in sanitize_html(
        doc.replace('href="#ref-1"', 'href="page.html"')
    )

    # 锚点形态的 sup 同样计入引用编号校验（集合语义）
    html = _VALID_HTML.replace(
        "<p>结论<sup>1</sup>。</p>", '<p>结论<sup><a href="#ref-1">1</a></sup>。</p>'
    )
    errors, _ = validate_html_report(html, _valid_pre())
    assert errors == []


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


def _valid_pre():
    return preprocess_markdown(
        "# 报告\n\n"
        "## 1 范围\n\n### 1.1 概览\n\n结论 [checked_citation:1][[1]](https://example.com/a)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
    )


def test_validate_accepts_wellformed_html_report():
    """结构/图表/章节/引用均合规时校验通过。"""
    errors, warnings = validate_html_report(_VALID_HTML, _valid_pre())
    assert errors == []
    assert warnings == []


def test_validate_rejects_missing_structure_and_script_residue():
    """缺 DOCTYPE、script 残留、on* 属性、javascript: URL 均属硬错误。"""
    broken = _VALID_HTML.replace("<!DOCTYPE html>", "").replace(
        "</body>", '<script>x</script><p onclick="y()">z</p><a href="javascript:1">w</a></body>'
    )
    errors, _ = validate_html_report(broken, _valid_pre())
    assert "missing_doctype" in errors
    assert "script_tag_present" in errors
    assert "event_attribute_present" in errors
    assert "javascript_url_present" in errors


def test_validate_rejects_css_external_references_in_style_and_inline():
    """<style> 与内联 style 中的 url()/@import 均被拒绝（含注释与转义混淆变体）。"""
    dirty = _VALID_HTML.replace(
        "<style>p{color:#333}</style>",
        "<style>p{background:url(https://evil.example/x.png)}</style>",
    )
    errors, _ = validate_html_report(dirty, _valid_pre())
    assert "css_external_reference" in errors

    sneaky = _VALID_HTML.replace(
        "<style>p{color:#333}</style>",
        "<style>/*x*/p{background:\\75 rl(https://evil.example/x.png)}</style>",
    )
    errors, _ = validate_html_report(sneaky, _valid_pre())
    assert "css_external_reference" in errors

    inline = _VALID_HTML.replace(
        'style="height:360px"', 'style="height:360px;background:url(https://evil.example/x.png)"'
    )
    errors, _ = validate_html_report(inline, _valid_pre())
    assert "css_external_reference" in errors

    imported = _VALID_HTML.replace(
        "<style>p{color:#333}</style>", "<style>@import url('https://evil.example/x.css');</style>"
    )
    errors, _ = validate_html_report(imported, _valid_pre())
    assert "css_external_reference" in errors


def test_validate_rejects_chart_id_mismatch_and_bad_id_format():
    """占位元素与配置项必须一一对应，且 id 匹配白名单正则。"""
    mismatched = _VALID_HTML.replace(
        '<template id="chart-configs">[{"id":"c1"',
        '<template id="chart-configs">[{"id":"c9"',
    )
    errors, _ = validate_html_report(mismatched, _valid_pre())
    assert any(error.startswith("chart_config:") for error in errors)

    bad_id = _VALID_HTML.replace('data-chart-id="c1"', 'data-chart-id="1c"')
    errors, _ = validate_html_report(bad_id, _valid_pre())
    assert any(error.startswith("chart_config:") for error in errors)


def test_validate_rejects_broken_chart_config_json():
    """template 内容必须是合法 JSON 且 option 为对象。"""
    broken = _VALID_HTML.replace(
        '<template id="chart-configs">[{"id":"c1","option":{',
        '<template id="chart-configs">[{"id":"c1","option":{...broken',
    )
    errors, _ = validate_html_report(broken, _valid_pre())
    assert any(error.startswith("chart_config:") for error in errors)

    not_object = _VALID_HTML.replace(
        '<template id="chart-configs">[{"id":"c1","option":{"series":[{"type":"bar","data":[1,2]}]}}]</template>',
        '<template id="chart-configs">[{"id":"c1","option":[1,2]}]</template>',
    )
    errors, _ = validate_html_report(not_object, _valid_pre())
    assert any(error.startswith("chart_config:") for error in errors)


def test_validate_downgrades_missing_template_to_warning_and_injection_strips_placeholders():
    """占位存在但 template 缺失：降级为警告不触发重试，注入阶段移除占位元素。"""
    from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import inject_chart_scripts

    missing_template = _VALID_HTML.replace(
        '<template id="chart-configs">[{"id":"c1","option":{"series":[{"type":"bar","data":[1,2]}]}}]</template>',
        "",
    )
    errors, warnings = validate_html_report(missing_template, _valid_pre())
    assert errors == []
    assert any("template#chart-configs missing" in warning for warning in warnings)

    stripped = inject_chart_scripts(missing_template)
    assert 'class="echarts-chart"' not in stripped
    assert "<h1>报告</h1>" in stripped  # 其余内容不受影响


def test_validate_warns_on_missing_or_reordered_section_headings():
    """md 标题缺失/乱序属保真警告：不触发重试，仅记录日志。"""
    dropped = _VALID_HTML.replace("<h3>1.1 概览</h3>", "<h3>其他标题</h3>")
    errors, warnings = validate_html_report(dropped, _valid_pre())
    assert errors == []
    assert any(warning.startswith("missing_section_heading:") for warning in warnings)

    reordered = _VALID_HTML.replace("<h2>1 范围</h2><h3>1.1 概览</h3>", "<h3>1.1 概览</h3><h2>1 范围</h2>")
    errors, warnings = validate_html_report(reordered, _valid_pre())
    assert errors == []
    assert any(warning.startswith("missing_section_heading:") for warning in warnings)


def test_validate_warns_on_citation_marker_residue_and_unknown_or_missing_sup_numbers():
    """[n] 残留、幻觉编号、编号整体缺失、文献条目缺失均属保真警告。"""
    residue = _VALID_HTML.replace("<sup>1</sup>", "[1]")
    errors, warnings = validate_html_report(residue, _valid_pre())
    assert errors == []
    assert "raw_inline_citation_marker_left" in warnings

    renumbered = _VALID_HTML.replace("<sup>1</sup>", "<sup>2</sup>")
    errors, warnings = validate_html_report(renumbered, _valid_pre())
    assert errors == []
    assert any(
        warning.startswith("sup_citation_unknown_numbers")
        for warning in warnings
    )
    assert any(
        warning.startswith("sup_citation_missing_numbers")
        for warning in warnings
    )

    no_refs = _VALID_HTML.replace('<a href="https://example.com/a">来源甲</a>', "<span>来源甲</span>")
    errors, warnings = validate_html_report(no_refs, _valid_pre())
    assert errors == []
    assert any(
        warning.startswith("missing_reference_entries")
        for warning in warnings
    )


def test_validate_allows_sup_count_and_order_drift_for_covered_numbers():
    """编号集合覆盖时，重复次数差异与顺序调换不构成校验失败。"""
    # md 引用序列 [3, 1, 3]：HTML 漏转一个 [3] 实例、且顺序调换，集合仍覆盖 {1, 3}。
    pre = preprocess_markdown(
        "# 报告\n\n## 1 范围\n\n"
        "结论甲 [checked_citation:1][[3]](https://example.com/a) 结论乙 [checked_citation:2][[1]](https://example.com/a) 结论丙 [checked_citation:3][[3]](https://example.com/a)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
        "[3]. [来源丙](https://example.com/a)\n"
    )
    drifted = _VALID_HTML.replace(
        "结论<sup>1</sup>。",
        "结论丙<sup>3</sup>。结论甲<sup>1</sup>。",
    )
    errors, warnings = validate_html_report(drifted, pre)
    assert errors == []
    assert warnings == []


def test_validate_skips_citation_checks_when_markdown_has_no_citations():
    """md 无引用时跳过引用完整性校验。"""
    plain_html = (
        '<!DOCTYPE html><html><head><title>t</title></head><body>'
        "<h1>报告</h1><h2>1 范围</h2><p>正文。</p></body></html>"
    )
    pre = preprocess_markdown("# 报告\n\n## 1 范围\n\n正文。\n")
    errors, warnings = validate_html_report(plain_html, pre)
    assert errors == []
    assert warnings == []


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
    assert "data-chart-id=\"' + configs[i].id + '\"]" in injected
    assert '"renderMode": "richText"' in injected
    assert '"trigger": "axis"' in injected
    assert injected.rstrip().endswith("</html>")
    assert "<!DOCTYPE html>" in injected


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


def test_inject_chart_scripts_without_configs_is_noop():
    """无图表配置时注入为空操作。"""
    plain = "<!DOCTYPE html><html><head></head><body><p>x</p></body></html>"
    assert inject_chart_scripts(plain) == plain


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
    """AI 声明随语言切换并插入 body 末尾；含徽章与自适应样式。"""
    zh = inject_ai_notice(_INJECT_BASE, "zh-CN")
    en = inject_ai_notice(_INJECT_BASE, "en-US")

    assert "本研究报告由 AI 生成，仅供参考" in zh
    assert "This research report was generated by AI and is for reference only." in en
    # AI 徽章与 CSS 变量 fallback 样式存在，观感融入报告设计系统
    assert ">AI</span>" in zh
    assert "var(--text-muted," in zh
    assert "var(--accent," in zh
    assert zh.rstrip().endswith("</html>")


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


def test_final_security_assert_passes_on_fully_injected_report():
    """完整注入链路后的产物通过终检。"""
    html = inject_ai_notice(
        inject_echarts_library(inject_chart_scripts(_INJECT_BASE)),
        "zh-CN",
    )
    final_security_assert(html)  # 不抛异常


def test_final_security_assert_rejects_extra_script_outside_markers():
    """系统脚本之外的 script 残留触发终检断言。"""
    html = inject_chart_scripts(_INJECT_BASE) + '<script>alert(1)</script>'
    with pytest.raises(RuntimeError):
        final_security_assert(html)


_LLM_HTML = (
    "<html_report><!DOCTYPE html><html><head><title>t</title></head><body>"
    "<h1>报告</h1><h2>1 范围</h2><p>结论<sup>1</sup>。</p>"
    '<a href="https://example.com/a">来源甲</a>'
    "</body></html></html_report>"
)


def _pipeline_markdown():
    return (
        "# 报告\n\n## 1 范围\n\n结论 [checked_citation:1][[1]](https://example.com/a)。\n\n"
        "[1]. [来源甲](https://example.com/a)\n"
    )


@pytest.mark.asyncio
async def test_generate_brief_html_report_success_returns_injected_html(monkeypatch):
    """成功路径：清洗→LLM→清理→校验→注入全链路产出 HTML。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    calls = []

    async def fake_invoke(llm, messages, **kwargs):
        calls.append(messages)
        return {"content": _LLM_HTML}

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)

    html = await generate_brief_html_report(
        llm=object(), markdown=_pipeline_markdown(), language="zh-CN"
    )

    assert html.lower().startswith("<!doctype html>")
    assert "本研究报告由 AI 生成，仅供参考" in html
    assert "echarts" in html  # 内嵌库标记块
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_generate_brief_html_report_retries_with_error_feedback(monkeypatch, caplog):
    """安全类硬校验失败时携带具体错误重试；保真类缺陷只记警告不驱动重试。"""
    import logging

    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    # 缺章节标题与 sup 引用（保真警告）+ 图表配置 id 不匹配（安全硬错误，
    # sanitizer 不会修复，能存活到校验阶段；script 类违规在 sanitize 阶段已被清除）。
    outputs = [
        {
            "content": (
                "<html_report><!DOCTYPE html><html><head></head><body>"
                '<p>结论</p>'
                '<div class="echarts-chart" data-chart-id="c1" style="height:360px"></div>'
                '<template id="chart-configs">[{"id":"c9","option":{"series":[{"type":"bar","data":[1]}]}}]</template>'
                "</body></html></html_report>"
            )
        },
        {"content": _LLM_HTML},
    ]
    calls = []

    async def fake_invoke(llm, messages, **kwargs):
        calls.append(messages)
        return outputs[len(calls) - 1]

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        html = await generate_brief_html_report(
            llm=object(), markdown=_pipeline_markdown(), language="zh-CN"
        )

    assert html.lower().startswith("<!doctype html>")
    assert len(calls) == 2
    retry_content = calls[1][-1]["content"]
    assert "chart_config:" in retry_content
    # 保真类问题不进入重试反馈：重试一次约 3 分钟，只用于安全违规。
    assert "missing_section_heading" not in retry_content
    warning_messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Validation failed" in message and "chart_config:" in message
        for message in warning_messages
    )
    assert any(
        "Content fidelity warning" in message and "missing_section_heading" in message
        for message in warning_messages
    )


@pytest.mark.asyncio
async def test_generate_brief_html_report_accepts_fidelity_warnings_without_retry(monkeypatch, caplog):
    """仅存在保真类缺陷（漏标题/漏引用）时第一次输出即被接受，不触发重试。"""
    import logging

    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    calls = []

    async def fake_invoke(llm, messages, **kwargs):
        calls.append(messages)
        return {
            "content": "<html_report><!DOCTYPE html><html><head></head><body><p>结论</p></body></html></html_report>"
        }

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        html = await generate_brief_html_report(
            llm=object(), markdown=_pipeline_markdown(), language="zh-CN"
        )

    assert html.lower().startswith("<!doctype html>")
    assert len(calls) == 1
    warning_messages = [record.getMessage() for record in caplog.records]
    assert any("Content fidelity warning" in message for message in warning_messages)
    assert not any("Validation failed" in message for message in warning_messages)


@pytest.mark.asyncio
async def test_generate_brief_html_report_extracts_bare_doctype_without_wrapper(monkeypatch):
    """模型漏写 <html_report> 包裹标签时按 doctype 兜底提取（MVP 兼容）。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    bare_html = _LLM_HTML.replace("<html_report>", "").replace("</html_report>", "")
    calls = []

    async def fake_invoke(llm, messages, **kwargs):
        calls.append(messages)
        return {"content": f"以下是转换结果：\n{bare_html}\n希望对你有帮助。"}

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)

    html = await generate_brief_html_report(
        llm=object(), markdown=_pipeline_markdown(), language="zh-CN"
    )

    assert html.lower().startswith("<!doctype html>")
    assert "本研究报告由 AI 生成，仅供参考" in html
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_generate_brief_html_report_recovers_missing_html_closing_tag(monkeypatch):
    """输出以 </html_report> 结尾但缺 </html> 闭合：补全闭合而非误判截断重试。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    unclosed = _LLM_HTML.replace("</html>", "").replace("</html_report>", "</html_report>")
    assert "</html>" not in unclosed  # 前置确认构造正确
    calls = []

    async def fake_invoke(llm, messages, **kwargs):
        calls.append(messages)
        return {"content": unclosed}

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)

    html = await generate_brief_html_report(
        llm=object(), markdown=_pipeline_markdown(), language="zh-CN"
    )

    assert html.lower().startswith("<!doctype html>")
    assert html.lower().rstrip().endswith("</html>")
    assert "本研究报告由 AI 生成，仅供参考" in html
    assert len(calls) == 1  # 不应触发重试


@pytest.mark.asyncio
async def test_generate_brief_html_report_adds_truncation_hint_on_unclosed_tag(monkeypatch, caplog):
    """<html_report> 未闭合按截断处理，重试附带精简指令。"""
    import logging

    from openjiuwen_deepsearch.algorithm.brief_report import html_reporter as module

    outputs = [
        {"content": "<html_report><!DOCTYPE html><html><head></head><body>" + "x" * 50},
        {"content": _LLM_HTML},
    ]
    calls = []

    async def fake_invoke(llm, messages, **kwargs):
        calls.append(messages)
        return outputs[len(calls) - 1]

    monkeypatch.setattr(module, "ainvoke_llm_with_stats", fake_invoke)

    with caplog.at_level(logging.WARNING, logger=module.__name__):
        await generate_brief_html_report(llm=object(), markdown=_pipeline_markdown(), language="zh-CN")

    retry_content = calls[1][-1]["content"]
    assert "truncated" in retry_content.lower()
    assert "Reduce CSS size" in retry_content
    # 未匹配到 <html_report> 块时必须记录原始输出头尾，便于定位截断原因。
    warning_messages = [record.getMessage() for record in caplog.records]
    assert any(
        "No <html_report> block found" in message and "raw_chars=" in message
        for message in warning_messages
    )


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
