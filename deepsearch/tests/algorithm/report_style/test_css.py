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


def test_append_cover_title_contrast_safeguard_corrects_dark_title_on_dark_gradient():
    """深色渐变封面上的深色标题应在注入前被自动修正。"""
    css = """
    :root { --text-primary: #1a202c; }
    h1, h2 { color: var(--text-primary); }
    .report-cover {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    }
    .report-cover h1 { font-size: 2.5rem; }
    """

    safeguarded = css_module.append_cover_title_contrast_safeguard(css)

    assert safeguarded.endswith(
        ".report-cover > h1 {\n"
        "    color: #ffffff !important;\n"
        "}\n"
    )


def test_append_cover_title_contrast_safeguard_ignores_css_comments_before_selectors():
    """注释不应阻止封面规则和全局标题规则被识别。"""
    css = """
    /* Typography Hierarchy */
    h1 { color: #1a202c; }
    /* Report Cover */
    .report-cover { background: linear-gradient(135deg, #0f172a, #1e293b); }
    """

    safeguarded = css_module.append_cover_title_contrast_safeguard(css)

    assert "color: #ffffff !important;" in safeguarded


def test_append_cover_title_contrast_safeguard_keeps_css_without_cover_background():
    """未修改封面背景的主题应保持模型原有标题设计。"""
    css = "h1 { color: #1a202c; }\n.report-section { padding: 24px; }"

    assert css_module.append_cover_title_contrast_safeguard(css) == css


def test_append_cover_title_contrast_safeguard_keeps_readable_cover_title():
    """已满足对比度要求的封面标题不应被服务端覆写。"""
    css = ".report-cover { background-color: #ffffff; }\n.report-cover > h1 { color: #111827; }"

    assert css_module.append_cover_title_contrast_safeguard(css) == css


def test_append_cover_title_contrast_safeguard_adds_backdrop_for_unparseable_background():
    """无法解析的封面背景应使用标题自身的不透明底板兜底。"""
    css = ".report-cover { background: url(cover.svg); }\nh1 { color: #1a202c; }"

    safeguarded = css_module.append_cover_title_contrast_safeguard(css)

    assert "background-color: #0f172a !important;" in safeguarded
    assert "color: #ffffff !important;" in safeguarded


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
