import pytest

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    _normalize_research_intent,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    build_research_intent_prompt_context,
    build_section_local_contract_prompt_context,
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

    assert "Chapter Writing Directive" in system_prompt
    assert "comparison matrix" in system_prompt.lower()
    assert "growth, dividend" in system_prompt
    assert "AIA, Ping An" in system_prompt


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


def test_sub_report_prompt_renders_section_local_contract_context():
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

    assert "Chapter Writing Directive" in system_prompt
    assert "vendors_and_supply" in system_prompt
    assert "vendors, supply_chain, ecosystem" in system_prompt
    assert "must not become a duplicate of other top-level chapters" in system_prompt


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
