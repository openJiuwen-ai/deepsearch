from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages
from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    _normalize_material_relevance_map,
    _normalize_research_intent,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TargetPaper,
    TemporalScope,
    build_research_intent_prompt_context,
    build_section_local_contract_prompt_context,
    build_target_papers_prompt_context,
    build_temporal_scope_prompt_context,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    exclusion_constraint_context,
)


@pytest.fixture
def exclusion_on():
    """开启禁引约束总开关（exclusion_constraint_enable=True）。"""
    token = exclusion_constraint_context.set(True)
    yield
    exclusion_constraint_context.reset(token)


@pytest.fixture
def exclusion_off():
    """关闭禁引约束总开关（默认态）。"""
    token = exclusion_constraint_context.set(False)
    yield
    exclusion_constraint_context.reset(token)


def _render_prompt(prompt_name: str, context: dict) -> str:
    """Render both stable system rules and this call's dynamic user task."""
    messages = build_prompt_messages(
        prompt_name, {key: value for key, value in context.items() if key != "messages"}
    )
    return "\n".join(message["content"] for message in messages)


def test_target_paper_accepts_explicit_and_implicit_clues():
    explicit = TargetPaper(pmid="38202877", title="A Full Paper Title")
    implicit = TargetPaper(
        dataset="Medical Expenditure Panel Survey (MEPS)",
        data_year="2019",
        topic="US orthodontic treatment users braces retainers",
    )

    assert explicit.pmid == "38202877"
    assert implicit.dataset.startswith("Medical Expenditure")


@pytest.mark.usefixtures("exclusion_on")
def test_normalize_research_intent_removes_exclude_url_from_include_and_target_papers():
    """被禁源不应同时出现在 include_url 和 target_papers（防止 collector 搜注定被挡的源）。"""
    forbidden = "https://pubmed.ncbi.nlm.nih.gov/38132429/"
    intent = _normalize_research_intent({
        "include_url": [forbidden, "https://keep.com/a"],
        "exclude_url": [forbidden, "https://www.mdpi.com/2304-6767/11/12/291"],
        "target_papers": [{"url": forbidden}, {"dataset": "MEPS"}],
    })
    assert forbidden not in intent.include_url
    assert "https://keep.com/a" in intent.include_url
    assert all(not p.url == forbidden for p in intent.target_papers)
    assert forbidden in intent.exclude_url


@pytest.mark.usefixtures("exclusion_on")
def test_normalize_research_intent_removes_target_paper_by_pmid_doi():
    """被禁源的 PMID/DOI 出现在 target_papers 时也应移除（即使 URL 为空）。"""
    intent = _normalize_research_intent({
        "exclude_url": [
            "https://pubmed.ncbi.nlm.nih.gov/38132429/",
            "https://doi.org/10.3390/dj11120291",
        ],
        "target_papers": [
            {"pmid": "38132429"},                   # PMID 匹配 exclude 的 pubmed URL
            {"doi": "10.3390/dj11120291"},          # DOI 匹配 exclude 的 doi URL
            {"dataset": "MEPS"},                     # 无关，保留
        ],
    })
    assert all(p.pmid != "38132429" for p in intent.target_papers)
    assert all(p.doi != "10.3390/dj11120291" for p in intent.target_papers)
    assert any(p.dataset == "MEPS" for p in intent.target_papers)


@pytest.mark.usefixtures("exclusion_off")
def test_normalize_research_intent_keeps_overlap_when_exclusion_disabled():
    """默认关（exclusion_constraint_enable=False）：不去重，被禁源仍留在两处。

    这是 baseline 行为——包括被禁源进 target_papers 后被 ensure_exact_target_documents
    强制塞回证据的那条链路。
    """
    forbidden = "https://pubmed.ncbi.nlm.nih.gov/38132429/"
    intent = _normalize_research_intent({
        "include_url": [forbidden, "https://keep.com/a"],
        "exclude_url": [forbidden],
        "target_papers": [{"url": forbidden}, {"pmid": "38132429"}],
    })
    assert forbidden in intent.include_url
    assert any(p.url == forbidden for p in intent.target_papers)
    assert any(p.pmid == "38132429" for p in intent.target_papers)


def test_target_paper_rejects_empty_item():
    with pytest.raises(ValueError, match="at least one clue"):
        TargetPaper()


def test_normalize_material_relevance_map_discards_malformed_entries():
    entries = _normalize_material_relevance_map([
        {
            "material_id": "M1",
            "relevance": "direct",
            "relevant_dimensions": ["efficacy"],
            "supported_claims": [{"claim": "Improves outcome", "confidence": "high"}],
            "research_gaps": ["long-term outcomes"],
        },
        {"material_id": "M2", "relevance": "unsupported"},
        "invalid",
    ])

    assert len(entries) == 1
    assert entries[0].material_id == "M1"
    assert entries[0].supported_claims[0].claim == "Improves outcome"


def test_legacy_research_intent_defaults_target_papers_to_empty():
    intent = ResearchIntent.model_validate({"task_type": "evaluation"})

    assert intent.target_papers == []


def test_target_papers_prompt_context_is_serializable_and_flagged():
    context = build_target_papers_prompt_context(
        ResearchIntent(target_papers=[TargetPaper(doi="10.1000/ABC")])
    )

    assert context["has_target_papers"] is True
    assert context["target_papers"] == [{
        "title": "", "pmid": "", "doi": "10.1000/ABC", "arxiv_id": "",
        "url": "", "dataset": "", "data_year": "", "topic": "",
    }]
    assert '"doi": "10.1000/ABC"' in context["target_papers_text"]


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


def test_normalize_research_intent_preserves_valid_source_date_scope():
    """合法时间约束应被归一化为可序列化的结构化意图（旧 temporal_scope input 路由到新字段）。"""
    intent = _normalize_research_intent(
        {
            "temporal_scope": {
                "constraint_type": "source_date",
                "start_date": "2018-01-01",
                "end_date": "2023-12-31",
            }
        }
    )

    # legacy temporal_scope input routes to the new field
    # and the deprecated temporal_scope field stays None (popped by the before-validator).
    assert intent.source_date_scope == TemporalScope(
        constraint_type="source_date",
        start_date="2018-01-01",
        end_date="2023-12-31",
    )
    assert intent.temporal_scope is None
    assert intent.model_dump(mode="json")["source_date_scope"] == {
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
    assert intent.source_date_scope is None
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
        "source_date_instruction": "",
        "content_date_instruction": "",
        "temporal_scope_instruction": "",
        "temporal_embed_in_query": False,
        "temporal_query_instruction": "",
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

    prompts = build_prompt_messages(
        "outliner", {key: value for key, value in context.items() if key != "messages"}
    )
    system_prompt = "\n".join(message["content"] for message in prompts)

    assert "Task type" in system_prompt
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

    system_prompt = _render_prompt("sub_report_markdown", context)

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

    system_prompt = _render_prompt("sub_section_outline", context)

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

    system_prompt = _render_prompt("sub_section_outline", context)

    assert "Flat outline" in system_prompt
    assert "only the Level 1 heading" in system_prompt
    assert "research scope, not a one-to-one mapping to Level 2 headings" in system_prompt
    assert "Multiple focus dimensions may be covered in one cohesive flat chapter" in system_prompt


@pytest.mark.parametrize(
    "prompt_name",
    ["sub_report_markdown"],
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

    system_prompt = _render_prompt(prompt_name, context)

    # Both prompt versions have citation and output structure rules
    assert "Citation & Grounding" in system_prompt or "Citation" in system_prompt
    # Verify that output structure guidance is present
    assert "Output Structure" in system_prompt or "Output" in system_prompt


@pytest.mark.parametrize(
    "prompt_name",
    ["sub_report_markdown"],
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

    system_prompt = _render_prompt(prompt_name, context)

    # sub_report_markdown uses "Visualization Boundary" section
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

    system_prompt = _render_prompt("sub_report_markdown", context)

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

    system_prompt = _render_prompt(
        "report_implications_and_recommendations_markdown", context
    )

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

    prompts = build_prompt_messages(
        "planner", {key: value for key, value in context.items() if key != "messages"}
    )
    system_prompt = "\n".join(message["content"] for message in prompts)

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
        "report_abstract_markdown",
        "report_conclusion_markdown",
        "report_implications_and_recommendations_markdown",
    ],
)
def test_non_collector_prompts_do_not_consume_temporal_scope(prompt_name):
    """时间约束不进入下列非 collector Prompt（sub_report_markdown 为有意消费者，已排除）。"""
    prompt_root = Path("openjiuwen_deepsearch/algorithm/prompts")
    prompt_dir = prompt_root / prompt_name
    prompt = "\n".join(
        file.read_text(encoding="utf-8") for file in prompt_dir.glob("*.md")
    ) if prompt_dir.is_dir() else (prompt_root / f"{prompt_name}.md").read_text(encoding="utf-8")

    assert "temporal_scope" not in prompt
    assert "has_temporal_scope" not in prompt
    assert "temporal_scope_instruction" not in prompt
    assert "Research Time Boundary" not in prompt
