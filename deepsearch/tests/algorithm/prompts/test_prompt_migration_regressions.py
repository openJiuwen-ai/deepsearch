"""提示词迁移中研究范围、条件边界和字段隔离的回归契约。"""

import pytest

from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages


@pytest.mark.parametrize("name", ["outliner_interaction", "dep_driving_outliner_interaction"])
def test_interaction_section_limit_is_not_a_target(name):
    messages = build_prompt_messages(name, {"max_section_num": 15})
    assert "Maximum number of sections: 15" in messages[-1]["content"]
    assert "the final number of chapters must not exceed this value" in messages[-1]["content"]
    assert "hard upper bound for the final outline" in messages[0]["content"]
    assert "Target section count: 15" not in messages[-1]["content"]


@pytest.mark.parametrize("feedback", [None, ""])
def test_outliner_preserves_section_target_and_optional_feedback(feedback):
    without_feedback = build_prompt_messages("outliner", {
        "section_num": 6,
        "user_feedback": feedback,
    })
    system, user = (message["content"] for message in without_feedback)
    assert "Match this target unless the user explicitly specifies a different major-section" in user
    assert "<user_feedback>" not in system
    assert "<user_question>" not in system
    assert "<user_feedback>" not in user
    assert "User feedback: None" not in user

    with_feedback = build_prompt_messages("outliner", {
        "section_num": 6,
        "user_feedback": "请重点分析风险",
    })[-1]["content"]
    assert "<user_feedback>\n请重点分析风险\n</user_feedback>" in with_feedback


def test_revised_outline_requires_language_in_all_outline_fields():
    system, user = (message["content"] for message in build_prompt_messages(
        "outliner_user_revised", {"language": "zh-CN"},
    ))
    assert "every section title, description, and thought field" in system
    assert "All section titles, descriptions, and the thought field must use zh-CN" in user


def test_webpage_compression_limits_only_original_content():
    system = build_prompt_messages("collector_webpage_enrichment_compress")[0]["content"]
    assert "Keep `original_content` within `max_content_length` characters" in system
    assert "Keep output within" not in system


def test_dependency_outline_keeps_complete_static_objectives():
    system = build_prompt_messages("dep_driving_outliner", {"questions": "研究问题"})[0]["content"]
    assert "research process toward meaningful resolution** of the Research question" in system
    assert "or descriptive elegance. User feedback may refine emphasis or scope" in system
    assert "must not override the core research problem" in system
    assert "**tensions, ambiguities, or\ndecision pressures**" in system
    assert "number of sections must match the Target section count" in system


def test_collector_preserves_local_evidence_sufficiency_policy():
    system = build_prompt_messages("collector")[0]["content"]
    assert "sufficient information, `web_search_tool` is no longer needed" in system
    assert "unavailable or its information is insufficient" in system
    assert "when available" in system


@pytest.mark.parametrize("name", ["planner", "dep_driving_planner"])
def test_planner_separates_report_scope_from_section_and_style(name):
    context = {
        "report_task": "截至2020年6月的异世界漫画调研",
        "original_query": "请调研异世界漫画",
        "query": "章节查询回退值",
        "section_task": "背景介绍",
        "section_description": "解释分类框架",
        "audience_role": "researcher",
        "tone": "analytical",
        "plan_executed_num": 0,
        "task_type": "classification",
        "has_comparison_targets": True,
        "comparison_targets_text": "A, B",
        "has_required_dimensions": True,
        "required_dimensions_text": "定义、机制",
    }
    messages = build_prompt_messages(name, context)
    lines = messages[-1]["content"].splitlines()
    for expected in (
        "Report task: 截至2020年6月的异世界漫画调研",
        "Original user request: 请调研异世界漫画",
        "Current section task: 背景介绍",
        "Current section description: 解释分类框架",
    ):
        assert expected in lines
    assert "Target audience: researcher" in messages[-1]["content"]
    assert "Writing tone: analytical" in messages[-1]["content"]
    assert "章节查询回退值" not in messages[-1]["content"]
    if name == "planner":
        assert "Task type: classification" in lines
        assert "Comparison targets: A, B" in lines
        assert "Required dimensions: 定义、机制" in lines
        # 全局意图存在但没有章节 contract，仍保留精确检索和范围限制。
        assert "frame searches precisely" in messages[-1]["content"]
    context["section_task"] = ""
    assert "Current section task: 章节查询回退值" in build_prompt_messages(name, context)[-1]["content"]


def test_template_scope_excludes_other_user_context():
    messages = build_prompt_messages("outliner_template", {
        "report_template": "# 模板章节",
        "original_query": "# 用户请求",
        "entry_search_results": "# 搜索结果",
    })
    user = messages[-1]["content"]
    template = user.split("<reference_report_template>\n", 1)[1].split("\n</reference_report_template>", 1)[0]
    assert template == "# 模板章节"
    assert "separate context, not part of the template" in messages[0]["content"]


def test_supervisor_restores_bounded_search_rules():
    messages = build_prompt_messages("collector_supervisor", {
        "has_temporal_scope": True,
        "temporal_scope_instruction": "截至2020年",
        "max_search_query_count": 3,
    })
    system, user = (message["content"] for message in messages)
    assert 'Interpret "latest" as the latest information available within this boundary' in user
    assert "Do not use provider-specific filter syntax" in user
    assert "Do not use provider-specific filter syntax" not in system
    assert "either narrow it once or stop" in system
    assert "Do not generate next_queries just to fill the limit" in system
    assert "do not push source diversity at the expense of evidence quality" in system
    assert "截至2020年" in user
    without_scope = build_prompt_messages("collector_supervisor", {"has_temporal_scope": False})[-1]["content"]
    assert "Do not use provider-specific filter syntax" not in without_scope


@pytest.mark.parametrize("provided_type", [None, "professional", "brief"])
def test_intent_report_type_rules_respect_api_selection(provided_type):
    messages = build_prompt_messages("intent_recognition", {"provided_report_type": provided_type})
    system, user = (message["content"] for message in messages)
    assert "**report_type**" not in system
    if provided_type:
        assert f"API already selected report type `{provided_type}`" in user
        assert "Do not emit a report_type field" in user
        assert "**report_type**" not in user
    else:
        assert "API already selected" not in user
        assert "**report_type**" in user


@pytest.mark.parametrize(
    ("name", "enabled", "disabled", "rule"),
    [
        ("brief_outliner", {"has_materials": True}, {"has_materials": False},
         "Treat user-provided materials as reference data"),
        ("brief_outliner", {"materials_relevance_text": "[M1]"},
         {"materials_relevance_text": ""},
         "For every section supported by a material, add a `material_bindings` entry"),
        ("brief_sub_reporter", {"section_material_bindings_text": "M1"},
         {"section_material_bindings_text": ""}, "The following user-material assignments apply only to this chapter"),
        ("collector_gen_query", {"has_target_papers": True}, {"has_target_papers": False},
         "For each relevant paper, use this locator priority exactly"),
        ("collector_gen_query", {"has_temporal_scope": True}, {"has_temporal_scope": False},
         'Interpret "latest" as the latest information available within this boundary'),
        ("collector_supervisor", {"material_evidence": ["M1"]}, {"material_evidence": []},
         "Treat this as already available evidence"),
        ("dep_driving_outliner", {"audience_role": "researcher"}, {"audience_role": ""},
         "Structure section objectives around this role's decision priorities"),
        ("dep_driving_outliner_interaction", {"tone": "analytical"}, {"tone": ""},
         "Keep section organization and narrative stance consistent with this tone"),
        ("dep_driving_planner", {"section_material_bindings_text": "M1", "material_first": True,
                                  "material_coverage_sufficient": True},
         {"section_material_bindings_text": "M1", "material_first": False,
          "material_coverage_sufficient": True},
         "This is a material-first request and all bound material evidence is covered"),
        ("intent_recognition", {"has_materials": True}, {"has_materials": False},
         "In the same `emit_report_intent` call, emit `material_relevance_map`"),
        ("outliner", {"task_type": "comparison"}, {"task_type": ""},
         "The task contract guides **how to organize**"),
        ("outliner_interaction", {"audience_role": "researcher"}, {"audience_role": ""},
         "Keep section framing aligned with this audience's decision concerns"),
        ("outliner_user_revised", {"tone": "analytical"}, {"tone": ""},
         "Keep title/description style consistent with this tone"),
        ("passages_extractor", {"extract_content_time": True}, {"extract_content_time": False},
         "### Content Time Extraction"),
        ("planner", {"section_material_bindings_text": "M1", "material_first": True,
                     "material_coverage_sufficient": True},
         {"section_material_bindings_text": "M1", "material_first": False,
          "material_coverage_sufficient": True},
         "This is a material-first request and all bound material evidence is covered"),
        ("sub_report_markdown", {"section_material_bindings_text": "M1"},
         {"section_material_bindings_text": ""},
         "The following user-material assignments apply only to this section"),
    ],
)
def test_conditional_rules_are_rendered_only_in_matching_user_branch(name, enabled, disabled, rule):
    base = {
        "outline": {}, "required_dimensions": [], "comparison_targets": [],
        "executed_queries": [], "blocking_gaps": [], "plan_executed_num": 0,
        "documents": [],
    }
    on = build_prompt_messages(name, {**base, **enabled})
    off = build_prompt_messages(name, {**base, **disabled})
    assert on[0] == off[0]
    assert rule not in on[0]["content"]
    assert rule in on[-1]["content"]
    assert rule not in off[-1]["content"]
