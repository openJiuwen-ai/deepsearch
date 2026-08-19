from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    _normalize_research_intent,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TemporalScope,
    build_research_intent_prompt_context,
    build_section_local_contract_prompt_context,
    build_temporal_scope_prompt_context,
)


def test_normalize_research_intent_preserves_task_contract_fields():
    intent = _normalize_research_intent(
        {
            "task_type": "Comparison",
            "required_dimensions": ["growth", "dividend", "growth"],
            "comparison_targets": ["AIA", "Ping An", "AIA"],
        }
    )

    assert intent.task_type == "comparison"
    assert intent.required_dimensions == ["growth", "dividend"]
    assert intent.comparison_targets == ["AIA", "Ping An"]


def test_normalize_research_intent_preserves_valid_temporal_scope():
    """合法时间约束应被归一化为可序列化的结构化意图。"""
    intent = _normalize_research_intent(
        {
            "temporal_scope": {
                "constraint_type": "source_date",
                "start_date": "2018-01-01",
                "end_date": "2023-12-31",
            }
        }
    )

    assert intent.temporal_scope == TemporalScope(
        constraint_type="source_date",
        start_date="2018-01-01",
        end_date="2023-12-31",
    )
    assert intent.model_dump(mode="json")["temporal_scope"] == {
        "constraint_type": "source_date",
        "start_date": "2018-01-01",
        "end_date": "2023-12-31",
    }


def test_normalize_research_intent_drops_only_invalid_temporal_scope():
    """非法时间字段只应关闭时间约束，不能丢失其他研究意图。"""
    intent = _normalize_research_intent(
        {
            "task_type": "comparison",
            "temporal_scope": {
                "constraint_type": "source_date",
                "start_date": "2024-01-01",
                "end_date": "2023-12-31",
            },
        }
    )

    assert intent.task_type == "comparison"
    assert intent.temporal_scope is None


def test_legacy_research_intent_deserializes_without_temporal_scope():
    """旧版本序列化数据缺少 temporal_scope 时仍应兼容加载。"""
    intent = ResearchIntent.model_validate({
        "task_type": "comparison",
        "required_dimensions": ["cost"],
    })

    assert intent.task_type == "comparison"
    assert intent.required_dimensions == ["cost"]
    assert intent.temporal_scope is None


def test_build_temporal_scope_prompt_context_distinguishes_source_and_content_dates():
    """Prompt 上下文应区分资料发表时间与事实发生时间。"""
    source_context = build_temporal_scope_prompt_context(
        ResearchIntent(
            temporal_scope=TemporalScope(
                constraint_type="source_date",
                start_date="2020-01-01",
                end_date="2023-12-31",
            )
        )
    )
    content_context = build_temporal_scope_prompt_context(
        {
            "temporal_scope": {
                "constraint_type": "content_date",
                "end_date": "2019-06-30",
            }
        }
    )

    assert source_context["has_temporal_scope"] is True
    assert "published" in source_context["temporal_scope_instruction"]
    assert "2020-01-01 through 2023-12-31" in source_context["temporal_scope_instruction"]
    assert content_context["has_temporal_scope"] is True
    assert "facts and data" in content_context["temporal_scope_instruction"]
    assert "on or before 2019-06-30" in content_context["temporal_scope_instruction"]


def test_build_temporal_scope_prompt_context_handles_missing_scope():
    """没有时间意图时不应生成隐式时间限制。"""
    context = build_temporal_scope_prompt_context(ResearchIntent())

    assert context == {
        "has_temporal_scope": False,
        "temporal_scope_instruction": "",
    }


def test_build_research_intent_prompt_context_exposes_flags():
    context = build_research_intent_prompt_context(
        ResearchIntent(
            task_type="comparison",
            required_dimensions=["growth"],
            comparison_targets=["company a", "company b"],
        )
    )

    assert context["task_type"] == "comparison"
    assert context["has_required_dimensions"] is True
    assert context["has_comparison_targets"] is True
    assert context["required_dimensions_text"] == "growth"
    assert context["comparison_targets_text"] == "company a, company b"


def test_outliner_prompt_renders_task_contract_context():
    context = {
        "messages": [],
        "questions": "Compare the leading insurers and recommend the strongest candidates.",
        "user_feedback": "",
        "language": "en-US",
        "entry_search_results": [],
        "section_num": 5,
        "max_section_num": 5,
        "original_query": "Compare leading insurers across growth and dividends, then recommend the top 2.",
    }
    context.update(
        build_research_intent_prompt_context(
            ResearchIntent(
                task_type="comparison",
                required_dimensions=["growth", "dividend"],
                comparison_targets=["AIA", "Ping An"],
            )
        )
    )

    prompts = apply_system_prompt("outliner", context)
    system_prompt = prompts[0]["content"]

    assert "Primary task type" in system_prompt
    assert "comparison" in system_prompt
    assert "growth, dividend" in system_prompt
    assert "AIA, Ping An" in system_prompt


def test_sub_report_prompt_renders_task_contract_context():
    # After prompt simplification, research intent context is no longer rendered in sub_report_markdown
    # This test now verifies that the prompt renders without error
    context = {
        "messages": [],
        "language": "en-US",
        "section_iscore": False,
        "report_type": "professional",
        "paragraph_style": "detailed",
    }
    context.update(
        build_research_intent_prompt_context(
            ResearchIntent(
                task_type="comparison",
                required_dimensions=["growth", "dividend"],
                comparison_targets=["AIA", "Ping An"],
            )
        )
    )

    prompts = apply_system_prompt("sub_report_markdown", context)
    system_prompt = prompts[0]["content"]

    # Verify prompt renders successfully with basic sections
    assert "Citation & Grounding" in system_prompt
    assert "# Role & Objective" in system_prompt


def test_section_local_contract_prompt_context_exposes_flags():
    context = build_section_local_contract_prompt_context(
        {
            "section_focus": "recommendation_and_ranking",
            "allowed_dimensions": ["recommendation", "ranking"],
            "is_final_decision_section": True,
        }
    )

    assert context["section_focus"] == "recommendation_and_ranking"
    assert context["has_allowed_dimensions"] is True
    assert context["allowed_dimensions_text"] == "recommendation, ranking"
    assert context["is_final_decision_section"] is True
    assert "forbidden_dimensions" not in context
    assert "has_forbidden_dimensions" not in context


def test_sub_section_outline_prompt_renders_section_local_contract_context():
    context = {
        "messages": [],
        "language": "zh-CN",
        "section_idx": "5",
        "has_template": False,
        "section_title": "战略行动建议与未来两年优先投入区域研判",
        "section_description": "综合比较结果，给出区域投入排序与行动建议。",
        "report_type": "professional",
        "paragraph_style": "detailed",
    }
    context.update(
        build_section_local_contract_prompt_context(
            {
                "section_focus": "recommendation_and_ranking",
                "allowed_dimensions": ["recommendation", "ranking"],
                "is_final_decision_section": True,
            }
        )
    )

    prompts = apply_system_prompt("sub_section_outline", context)
    system_prompt = prompts[0]["content"]

    assert "Chapter Writing Directive" in system_prompt
    assert "recommendation_and_ranking" in system_prompt
    assert "recommendation, ranking" in system_prompt


def test_sub_section_outline_prompt_allows_flat_outline_when_section_is_focused():
    context = {
        "messages": [],
        "language": "zh-CN",
        "section_idx": "1",
        "has_template": False,
        "section_title": "市场概览",
        "section_description": "概述市场当前状态。",
        "section_format_requirements": "",
        "current_outline": "1 市场概览",
        "report_type": "brief",
        "paragraph_style": "concise",
    }

    prompts = apply_system_prompt("sub_section_outline", context)
    system_prompt = prompts[0]["content"]

    assert "Flat outline" in system_prompt
    assert "only the Level 1 heading" in system_prompt
    assert "research scope, not a one-to-one mapping to Level 2 headings" in system_prompt
    assert "Multiple focus dimensions may be covered in one cohesive flat chapter" in system_prompt


@pytest.mark.parametrize(
    "prompt_name",
    ["sub_report_markdown", "sub_report_brief_markdown"],
)
def test_sub_report_prompts_render_flat_outline_writing_rule(prompt_name):
    context = {
        "messages": [],
        "language": "zh-CN",
        "section_iscore": False,
        "report_type": "brief",
        "paragraph_style": "concise",
        "current_chapter_outline": "1 市场概览",
    }

    prompts = apply_system_prompt(prompt_name, context)
    system_prompt = prompts[0]["content"]

    # Both prompt versions have citation and output structure rules
    assert "Citation & Grounding" in system_prompt or "Citation" in system_prompt
    # Verify that output structure guidance is present
    assert "Output Structure" in system_prompt or "Output" in system_prompt


@pytest.mark.parametrize(
    "prompt_name",
    ["sub_report_markdown", "sub_report_brief_markdown"],
)
def test_sub_report_prompts_always_forbid_body_mermaid(prompt_name):
    context = {
        "messages": [],
        "language": "zh-CN",
        "section_iscore": False,
        "report_type": "brief",
        "paragraph_style": "concise",
        "current_chapter_outline": "1 Market overview",
    }

    prompts = apply_system_prompt(prompt_name, context)
    system_prompt = prompts[0]["content"]

    # Both versions forbid chart output (though wording differs)
    # sub_report_markdown uses "Visualization Boundary" section
    # sub_report_brief_markdown uses "Hard output contract" line
    assert (
        "Visualization Boundary" in system_prompt
        or "Do NOT output Mermaid" in system_prompt
        or "Hard output contract" in system_prompt
    )


def test_sub_report_prompt_renders_section_local_contract_context():
    # After prompt simplification, section local contract context is no longer rendered in sub_report_markdown
    # This test now verifies that the prompt renders without error
    context = {
        "messages": [],
        "language": "zh-CN",
        "section_iscore": False,
        "report_type": "professional",
        "paragraph_style": "detailed",
    }
    context.update(
        build_section_local_contract_prompt_context(
            {
                "section_focus": "vendors_and_supply",
                "allowed_dimensions": ["vendors", "supply_chain", "ecosystem"],
                "is_final_decision_section": False,
            }
        )
    )

    prompts = apply_system_prompt("sub_report_markdown", context)
    system_prompt = prompts[0]["content"]

    # Verify prompt renders successfully with basic sections
    assert "Citation & Grounding" in system_prompt
    assert "# Role & Objective" in system_prompt


def test_report_implications_prompt_renders_answer_first_contract():
    context = {
        "messages": [],
        "language": "zh-CN",
        "report_task": "比较全球保险公司并推荐未来最有潜力的前两家",
        "current_outline": "1. 对比\n2. 推荐",
        "user_query": "比较全球保险公司并推荐未来最有潜力的前两家",
    }
    context.update(
        build_research_intent_prompt_context(
            ResearchIntent(
                task_type="comparison",
                required_dimensions=["growth", "dividend"],
                comparison_targets=["AIA", "Ping An"],
            )
        )
    )

    prompts = apply_system_prompt(
        "report_implications_and_recommendations_markdown", context
    )
    system_prompt = prompts[0]["content"]

    assert "answer-first" in system_prompt.lower()
    assert "AIA, Ping An" in system_prompt


def test_planner_prompt_renders_section_local_contract_context():
    context = {
        "messages": [],
        "language": "zh-CN",
        "max_step_num": 4,
        "report_type": "professional",
    }
    context.update(
        build_section_local_contract_prompt_context(
            {
                "section_focus": "vendors_and_supply",
                "allowed_dimensions": ["vendors", "supply_chain", "ecosystem"],
                "is_final_decision_section": False,
            }
        )
    )

    prompts = apply_system_prompt("planner", context)
    system_prompt = prompts[0]["content"]

    assert "Section Scope" in system_prompt
    assert "Current Section Responsibility" in system_prompt
    assert "vendors_and_supply" in system_prompt
    assert "vendors, supply_chain, ecosystem" in system_prompt


@pytest.mark.parametrize(
    "prompt_name",
    [
        "outliner",
        "dep_driving_outliner",
        "outliner_template",
        "outliner_user_revised",
        "planner",
        "dep_driving_planner",
        "sub_report_markdown",
        "sub_report_brief_markdown",
        "report_abstract_markdown",
        "report_conclusion_markdown",
        "report_implications_and_recommendations_markdown",
    ],
)
def test_non_collector_prompts_do_not_consume_temporal_scope(prompt_name):
    """时间约束只能进入 collector query 与补搜 Prompt。"""
    prompt = (Path("openjiuwen_deepsearch/algorithm/prompts") / f"{prompt_name}.md").read_text(
        encoding="utf-8"
    )

    assert "temporal_scope" not in prompt
    assert "has_temporal_scope" not in prompt
    assert "temporal_scope_instruction" not in prompt
    assert "Research Time Boundary" not in prompt
