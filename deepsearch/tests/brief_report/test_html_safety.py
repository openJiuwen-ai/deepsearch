"""Brief HTML 白名单清理与安全校验测试。"""

import json

import pytest

from openjiuwen_deepsearch.algorithm.brief_report.html_charts import (
    inject_chart_scripts,
    inject_echarts_library,
)
from openjiuwen_deepsearch.algorithm.brief_report.html_content import preprocess_markdown
from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
    inject_ai_notice,
    validate_html_report,
)
from openjiuwen_deepsearch.algorithm.brief_report.html_safety import (
    final_security_assert,
    sanitize_html,
)


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


_INJECT_BASE = (
    '<!DOCTYPE html><html><head><title>t</title></head><body>'
    "<h1>1 范围</h1><p>结论<sup>1</sup>。</p>"
    '<div class="echarts-chart" data-chart-id="c1" style="height:360px"></div>'
    '<template id="chart-configs">'
    '[{"id":"c1","option":{"tooltip":{"trigger":"axis"},"series":[{"type":"bar","data":[1,2]}]}}]'
    "</template></body></html>"
)


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
