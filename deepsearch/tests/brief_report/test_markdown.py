"""Brief Markdown 确定性校验测试。"""

import markdown as markdown_renderer
import pytest

from openjiuwen_deepsearch.algorithm.brief_report.markdown import (
    format_brief_markdown, sanitize_brief_chapter, select_brief_markdown,
)
from openjiuwen_deepsearch.algorithm.brief_report.models import BriefSection


SECTION = BriefSection.model_validate({"id": "1", "title": "市场格局", "goal": "验证格局", "research_steps": [{"id": "1-1", "requirement": "验证份额", "evidence_type": "data"}, {"id": "1-2", "requirement": "验证厂商差异", "evidence_type": "comparison"}]})


def test_sanitize_preserves_deterministic_numbered_headings_and_removes_unknown_citation():
    """编号章节进入最终报告时必须保持与报告总标题一致的二、三级层级。"""
    result = sanitize_brief_chapter(
        "# 1 市场格局\n\n## 1.1 验证份额\nA 为 30%。[citation:1][citation:9]",
        SECTION,
        {1},
    )
    assert result.startswith("## 1 市场格局")
    assert "### 1.1 验证份额" in result
    assert "[citation:1]" in result and "[citation:9]" not in result


@pytest.mark.parametrize(
    ("checked", "assembled", "response", "content_type", "expected"),
    [
        (" checked ", "assembled", "primary", "text/markdown", " checked "),
        ("  ", "assembled", "primary", "text/markdown", "assembled"),
        (None, {}, "primary", "text/markdown", "primary"),
        (None, None, "primary", "", "primary"),
        (None, None, "<html>", "text/html", ""),
        (None, None, "primary", None, ""),
    ],
)
def test_select_brief_markdown_respects_priority_and_content_type(
    checked, assembled, response, content_type, expected,
):
    assert select_brief_markdown(
        checked=checked, assembled=assembled, response_content=response,
        response_content_type=content_type,
    ) == expected


def test_reference_entries_render_as_separate_paragraphs():
    raw = (
        "# 报告\n\n## 参考文章\n"
        "[1]. [来源甲](https://example.com/a)\n"
        "[2]. [来源乙](https://example.com/b)\n"
    )
    result = format_brief_markdown(raw, "zh-CN")
    rendered = markdown_renderer.markdown(result)
    assert '<p>[1]. <a href="https://example.com/a">来源甲</a></p>' in rendered
    assert '<p>[2]. <a href="https://example.com/b">来源乙</a></p>' in rendered
    assert format_brief_markdown(result, "zh-CN") == result


def test_reference_like_lines_inside_code_fences_are_unchanged():
    raw = (
        "# 报告\n\n````text\n````not-a-close\n"
        "[1]. [甲](https://example.com/a)\n[2]. [乙](https://example.com/b)\n"
        "````\n\n## 参考文章\n"
        "[1]. [甲](https://example.com/a)\n[2]. [乙](https://example.com/b)\n"
    )
    result = format_brief_markdown(raw, "zh-CN")
    assert "[1]. [甲](https://example.com/a)\n[2]. [乙](https://example.com/b)\n````" in result
    assert "[1]. [甲](https://example.com/a)\n\n[2]. [乙](https://example.com/b)" in result
