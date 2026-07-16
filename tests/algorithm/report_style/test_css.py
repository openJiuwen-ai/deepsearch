"""Test raw CSS normalization and injection for report styling."""

import pytest

from openjiuwen_deepsearch.algorithm.report_style import css as css_module


def test_normalize_css_output_keeps_free_theme_rules():
    """保留模型生成的完整主题 CSS，而不再筛选声明。"""
    css = css_module.normalize_css_output(
        "```css\n.report-cover { background: linear-gradient(135deg, #0f172a, #2563eb); "
        "box-shadow: 0 20px 50px #0003; }\n```"
    )

    assert "linear-gradient" in css
    assert "box-shadow" in css


def test_normalize_css_output_keeps_nonrestricted_nested_theme_rules_unchanged():
    """保留渐变、阴影和嵌套选择器等非受限主题 CSS。"""
    raw_css = (
        ".report-section { background: linear-gradient(135deg, #0f172a, #2563eb); "
        "box-shadow: 0 20px 50px #0003; "
        "& .report-section__title { text-shadow: 0 1px 2px #0008; } }"
    )

    assert css_module.normalize_css_output(raw_css) == raw_css


def test_normalize_css_output_strips_optional_css_fence():
    """移除完整或截断的开头 CSS Markdown 围栏。"""
    assert css_module.normalize_css_output("```css\nh1 { color: red; }\n```") == "h1 { color: red; }"
    assert css_module.normalize_css_output("```CSS\nh1 { color: red; }") == "h1 { color: red; }"


@pytest.mark.parametrize("raw_css", [None, 123, b"h1 { color: red; }"])
def test_normalize_css_output_rejects_non_string_values(raw_css):
    """非字符串模型输出不能作为 CSS 注入。

    Args:
        raw_css: 模型返回的非字符串 CSS 内容。
    """
    with pytest.raises(ValueError, match="must be a string"):
        css_module.normalize_css_output(raw_css)


@pytest.mark.parametrize("raw_css", ["", "  ", "```css\n```"])
def test_normalize_css_output_rejects_empty_css(raw_css):
    """空字符串或空围栏应触发样式回退。

    Args:
        raw_css: 模型返回的空 CSS 内容。
    """
    with pytest.raises(ValueError, match="CSS is empty"):
        css_module.normalize_css_output(raw_css)


@pytest.mark.parametrize(
    "raw_css",
    [
        "@media screen { h1 { color: red; } }",
        ".report-cover { display: none; }",
        ".report-shell { width: 960px; min-width: 800px; max-width: 1440px; }",
        ".report-cover::before { content: 'replacement'; }",
        '@import url("https://example.com/theme.css");',
        r"@\6d edia screen { h1 { color: red; } }",
        r".report-cover { background: u\72l(https://example.com/cover.png); }",
        r".report-cover { d\69 splay: none; }",
        r".report-cover::before { c\6f ntent: 'replacement'; }",
        ".report-cover { opacity: 0%; }",
        r".report-\73hell { width: 960px; }",
    ],
)
def test_normalize_css_output_preserves_arbitrary_nonempty_css_verbatim(raw_css):
    """原样保留任意非空 CSS，而不执行声明、选择器或规则过滤。

    Args:
        raw_css: 模型返回的任意非空 CSS 文本。
    """
    assert css_module.normalize_css_output(raw_css) == raw_css


def test_inject_css_appends_generated_style_after_baseline_style():
    """在基础样式后注入已规整的主题 CSS。"""
    css = css_module.normalize_css_output("h1 { color: #123456; }")
    html = css_module.inject_css(
        "<html><head><style>body { color: black; }</style></head><body>x</body></html>",
        css,
    )

    assert html.index("body { color: black; }") < html.index('id="report-style-generated"')
    assert "h1 { color: #123456; }" in html


def test_inject_css_preserves_arbitrary_css_verbatim():
    """将规整后的任意 CSS 文本原样写入生成样式块。"""
    css = "@media screen { .report-cover { display: none; width: 960px; } }"
    html = css_module.inject_css("<html><head></head><body>x</body></html>", css)

    assert f'<style id="report-style-generated">\n{css}\n</style>' in html


def test_inject_css_rejects_html_without_head_closing_tag():
    """缺少页面 head 结束标签时拒绝注入。"""
    with pytest.raises(ValueError, match="head closing tag"):
        css_module.inject_css("<html><body>x</body></html>", "h1 { color: red; }")
