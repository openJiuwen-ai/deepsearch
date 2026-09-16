"""Brief HTML ECharts 配置与注入测试。"""

import hashlib
from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.brief_report.html_charts import (
    ECHARTS_SHA256,
    extract_fragment_charts,
    inject_chart_scripts,
    inject_echarts_library,
    validate_chart_option,
)
from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import (
    validate_html_report,
)


_VALID_HTML = (
    '<!DOCTYPE html><html><head><title>t</title><style>p{color:#333}</style></head><body>'
    "<h1>报告</h1><h2>1 范围</h2><h3>1.1 概览</h3><p>结论<sup>1</sup>。</p>"
    '<div class="echarts-chart" data-chart-id="c1" style="height:360px"></div>'
    '<template id="chart-configs">[{"id":"c1","option":{"series":[{"type":"bar","data":[1,2]}]}}]</template>'
    '<section><a href="https://example.com/a">来源甲</a></section>'
    "</body></html>"
)


def test_normalize_chart_option_drops_incomplete_ratio_series_and_prunes_axis():
    """占比序列存在缺失类别时不应继续绘制，并清理对应图例和副轴。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_charts as module

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
    from openjiuwen_deepsearch.algorithm.brief_report import html_charts as module

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
    from openjiuwen_deepsearch.algorithm.brief_report import html_charts as module

    option = {
        "xAxis": {"type": "category", "data": ["A", "B", "C"]},
        "series": [{"name": "规模", "type": "bar", "data": [1, 2]}],
    }

    normalized, warnings = module.normalize_chart_option(option)

    assert normalized["series"] == []
    assert any(warning.startswith("chart_series_length_mismatch:") for warning in warnings)


def test_normalize_chart_option_aligns_named_sparse_data_before_gap_check():
    """带年份 name 的稀疏序列先按横轴对齐，再识别缺失类别。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_charts as module

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
    from openjiuwen_deepsearch.algorithm.brief_report import html_charts as module

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


def test_validate_downgrades_missing_template_to_warning_and_injection_strips_placeholders():
    """占位存在但 template 缺失：降级为警告不触发重试，注入阶段移除占位元素。"""
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


def test_load_echarts_source_verifies_canonical_lf_bytes_on_windows_checkout(tmp_path, monkeypatch):
    """Git 把 vendor 换成 CRLF 时仍应校验原始 LF 内容，而不是误报篡改。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_charts as module

    canonical = b"first line\nsecond line\n"
    asset = tmp_path / "echarts.min.js"
    asset.write_bytes(canonical.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(module, "_ECHARTS_ASSET_PATH", asset)
    monkeypatch.setattr(module, "ECHARTS_SHA256", hashlib.sha256(canonical).hexdigest())

    assert module._load_echarts_source() == canonical.decode("utf-8")


def test_inject_echarts_library_rejects_missing_or_corrupted_vendor(monkeypatch):
    """vendor 缺失或 SHA-256 不匹配视为环境错误。"""
    from openjiuwen_deepsearch.algorithm.brief_report import html_charts as module

    monkeypatch.setattr(module, "_ECHARTS_ASSET_PATH", Path("/nonexistent/echarts.min.js"))
    with pytest.raises(FileNotFoundError):
        inject_echarts_library(_INJECT_BASE)

    monkeypatch.setattr(module, "_ECHARTS_ASSET_PATH", Path(__file__))
    with pytest.raises(ValueError):
        inject_echarts_library(_INJECT_BASE)


def test_extract_fragment_charts_renames_ids_and_strips_unpaired_placeholders():
    """章节图表提取：成对占位/配置重命名为 s{id}-c{k}，孤占位移除。"""
    fragment = (
        "<h2>1 范围</h2>"
        '<div class="echarts-chart" data-chart-id="c1" style="height:360px"></div>'
        '<div class="echarts-chart" data-chart-id="c9" style="height:360px"></div>'
        '<template id="chart-configs">'
        '[{"id":"c1","option":{"series":[{"type":"bar","data":[1]}]}},'
        '{"id":"cX","option":{"series":[{"type":"bar","data":[2]}]}}]'
        "</template>"
    )
    cleaned, configs = extract_fragment_charts(fragment, "1")

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

    cleaned, configs = extract_fragment_charts(fragment, "1")

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

    cleaned, configs = extract_fragment_charts(fragment, "2")

    assert cleaned.count('data-chart-id="s2-c1"') == 1
    assert 'data-chart-id="c1"' not in cleaned
    assert [config["id"] for config in configs] == ["s2-c1"]
