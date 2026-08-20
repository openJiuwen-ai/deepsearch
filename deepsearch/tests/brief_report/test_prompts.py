"""Brief 独立工作流提示词的迁移契约测试。"""

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
                "fewer, higher-signal sections",
                "Do not add introduction, background, summary, conclusion, appendix",
                "comparison axes",
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
                "Query Design Rules",
                "smallest non-duplicative report-level query set",
                "Do not answer the research question",
                "blocking_gaps",
            ],
        ),
        (
            "brief_doc_evaluator",
            {
                "section": {"id": "1", "title": "比较", "research_steps": []},
                "candidates": [],
            },
            [
                "Evaluation Standard",
                "source quality",
                "conflicts",
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
                "Evidence Review Contract",
                "Writing guidance is editorial guidance only",
                "blocking_gap",
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
                "Executive Summary Contract",
                "2–4",
                "conclusion-first",
                "evidence gaps",
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
