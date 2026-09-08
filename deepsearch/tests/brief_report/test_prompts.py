"""Brief 独立工作流提示词的迁移契约测试。"""

from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt


@pytest.mark.parametrize(
    ("template_name", "context", "required_rules"),
    [
        (
            "brief_outliner",
            {
                "query": "比较两个方案并给出建议",
                "language": "zh-CN",
                "audience_role": "业务负责人",
                "tone": "直接",
                "task_type": "comparison",
                "required_dimensions": ["成本", "风险"],
                "comparison_targets": ["方案 A", "方案 B"],
                "has_temporal_scope": False,
                "clarification_questions": "",
                "user_feedback": "",
                "report_template": "保留成本和风险比较表",
            },
            [
                "User Structure Preservation",
                "Do not add introduction, background, summary, conclusion, appendix",
            ],
        ),
        (
            "brief_collector_query_generation",
            {
                "outline": {"title": "报告", "sections": []},
                "task_type": "comparison",
                "required_dimensions": ["成本"],
                "comparison_targets": ["方案 A", "方案 B"],
                "has_temporal_scope": False,
                "executed_queries": ["已执行查询"],
                "blocking_gaps": [],
                "user_query": "比较两个方案",
            },
            [
                "smallest non-duplicative report-level query set",
                "Do not answer the research question",
            ],
        ),
        (
            "brief_doc_evaluator",
            {
                "section": {"id": "1", "title": "比较", "research_steps": []},
                "candidates": [],
            },
            [
                "source quality",
                "blocking_gap",
            ],
        ),
        (
            "brief_evidence_review",
            {
                "outline": {"title": "报告", "sections": []},
                "section_evidence": {},
                "citation_registry": [],
                "audience_role": "业务负责人",
                "tone": "直接",
                "user_format": "要点列表",
            },
            [
                "Writing guidance is editorial guidance only",
                "Do not modify the outline",
            ],
        ),
        (
            "brief_reporter",
            {
                "title": "报告",
                "user_query": "比较两个方案",
                "language": "zh-CN",
                "audience_role": "业务负责人",
                "tone": "直接",
                "user_format": "要点列表",
                "gaps": [],
                "chapters": [],
            },
            [
                "conclusion-first",
                "evidence gaps",
            ],
        ),
        (
            "brief_html_reporter",
            {
                "language": "zh-CN",
                "messages": [{"role": "user", "content": "Report title: 报告"}],
            },
            [
                "single-file",
                "Zero-Script Contract",
                'id="brief-sections"',
            ],
        ),
        (
            "brief_html_section",
            {
                "language": "zh-CN",
                "messages": [{"role": "user", "content": "Section Markdown:\n## 1 范围"}],
            },
            [
                "Content Fidelity",
                "Zero-Script Contract",
                "chart-configs",
                "ECharts data integrity",
                "Never set `connectNulls: true`",
            ],
        ),
    ],
)
def test_brief_workflow_prompts_preserve_migrated_quality_contract(template_name, context, required_rules):
    """独立节点可改变输入结构，但不得丢失原 Brief 的核心写作和证据约束。"""
    prompt = apply_system_prompt(template_name, context)[0]["content"]
    normalized_prompt = " ".join(prompt.split())

    for rule in required_rules:
        assert " ".join(rule.split()) in normalized_prompt


def test_brief_html_prompts_share_common_contract_template():
    """HTML shell 与章节 Prompt 应通过同一个公共契约模板复用规则。"""
    prompts_dir = Path(__file__).resolve().parents[2] / "openjiuwen_deepsearch/algorithm/prompts"
    common_path = prompts_dir / "brief_html_common.md"
    include = '{% include "brief_html_common.md" %}'

    assert common_path.is_file()
    common_source = common_path.read_text(encoding="utf-8")
    assert "Shared HTML Contract" in common_source
    assert "Citation Contract" not in common_source

    for template_name in ("brief_html_reporter", "brief_html_section"):
        source = (prompts_dir / f"{template_name}.md").read_text(encoding="utf-8")
        assert include in source
        rendered = apply_system_prompt(
            template_name,
            {
                "language": "zh-CN",
                "messages": [{"role": "user", "content": "context"}],
            },
        )[0]["content"]
        assert "Shared HTML Contract" in rendered
        assert rendered.count("Zero-Script Contract") == 1
        assert "Citation Contract" not in rendered
        assert "Convert inline citation" not in rendered


def test_brief_html_section_prompt_keeps_text_outside_css_bar_fill():
    """CSS 填充条只能承载视觉，不应让模型把可读文字放进薄条里。"""
    rendered = apply_system_prompt(
        "brief_html_section",
        {
            "language": "zh-CN",
            "messages": [{"role": "user", "content": "Section Markdown:\n## 1 对比"}],
        },
    )[0]["content"]
    normalized_prompt = " ".join(rendered.split())

    assert "visual-only" in normalized_prompt
    assert "MUST contain no text" in normalized_prompt
    assert "value rendered inside the filled bar" not in normalized_prompt
