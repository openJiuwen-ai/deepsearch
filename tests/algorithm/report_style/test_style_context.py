"""Test report-style prompt context extraction."""

from openjiuwen_deepsearch.algorithm.report_style import context as context_module


def test_build_style_context_uses_complete_abstract_and_heading_tree():
    """Use the full summary section and preserve every Markdown heading."""
    context = context_module.build_style_context(
        "# 标题\n\n# 摘要\n\n完整摘要第一段。\n\n完整摘要第二段。\n\n# 1 市场\n\n正文"
    )

    assert context.title == "标题"
    assert context.abstract == "完整摘要第一段。\n\n完整摘要第二段。"
    assert context.headings == ("# 标题", "# 摘要", "# 1 市场")
    assert context.table_count == 0
    assert context.to_prompt_dict()["abstract"] == context.abstract


def test_build_style_context_falls_back_to_first_body_block_at_4000_chars():
    """Limit the no-summary fallback without omitting the report title tree."""
    context = context_module.build_style_context("# 标题\n\n" + "甲" * 5000 + "\n\n# 后续\n\n乙")

    assert len(context.abstract) == 4000
    assert context.abstract == "甲" * 4000
    assert context.headings == ("# 标题", "# 后续")


def test_build_style_context_preserves_heading_levels_in_the_prompt_tree():
    """Keep Markdown heading levels so the LLM can infer report hierarchy."""
    context = context_module.build_style_context("# 总览\n\n## 市场规模\n\n### 区域分布\n\n正文")

    assert context.headings == ("# 总览", "## 市场规模", "### 区域分布")
    assert context.to_prompt_dict()["headings"] == "- # 总览\n- ## 市场规模\n- ### 区域分布"
