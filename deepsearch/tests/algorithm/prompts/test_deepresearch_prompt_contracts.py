# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""Repository-wide contracts for stable DeepResearch prompt templates."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from jinja2 import meta

from openjiuwen_deepsearch.algorithm.prompts import message_builder


@dataclass(frozen=True)
class PromptContract:
    """The Jinja variables intentionally exposed by one DeepResearch prompt.

    ``required_vars`` are the base payload fields rendered unconditionally.
    ``optional_vars`` are branch-only/defaulted fields (or ``current_date``,
    which the Builder supplies when declared) that callers may omit.
"""

    name: str
    required_vars: frozenset[str]
    optional_vars: frozenset[str] = frozenset()


_PROMPT_VARIABLES = {
    "brief_collector_query_generation": ("blocking_gaps", "comparison_targets", "current_date", "executed_queries", "has_temporal_scope", "material_first", "materials_analysis_text", "materials_relevance_text", "outline", "required_dimensions", "task_type", "temporal_scope_instruction", "user_query"),
    "brief_doc_evaluator": ("candidates", "current_date", "section"),
    "brief_evidence_review": ("audience_role", "citation_registry", "outline", "section_evidence", "tone", "user_format"),
    "brief_html_common": (),
    "brief_html_reporter": ("language", "request_content", "retry_feedback"),
    "brief_html_section": ("language", "request_content", "retry_feedback", "section_id"),
    "brief_outliner": ("audience_role", "clarification_questions", "comparison_targets", "current_date", "has_materials", "has_temporal_scope", "language", "materials_count", "materials_manifest_text", "materials_relevance_text", "query", "report_template", "required_dimensions", "task_type", "temporal_scope_instruction", "tone", "user_feedback"),
    "brief_reporter": ("audience_role", "language", "main_content", "tone", "user_format"),
    "brief_sub_reporter": ("audience_role", "collected_information", "current_chapter_outline", "current_section", "current_section_description", "current_section_format_requirements", "exclusion_instruction", "has_exclusion", "language", "outline", "section_material_bindings_text", "tone", "writing_guidance"),
    "chart_compliance_validate": ("extracted_chart_json", "section_outline"),
    "chart_data_traceability_check": ("extracted_chart_json", "origin_content"),
    "collector": ("current_date", "language", "query", "remaining_steps"),
    "collector_final": ("evidence_pack", "language", "ledger_brief", "missing_evidence", "plan_thought", "plan_title", "step_background_knowledge", "step_description", "step_title"),
    "collector_gen_query": ("current_date", "has_target_papers", "has_temporal_scope", "language", "max_search_query_count", "plan_thought", "plan_title", "step_description", "step_title", "target_papers_text", "temporal_query_instruction", "temporal_scope_instruction"),
    "collector_supervisor": ("current_date", "evidence_table", "has_temporal_scope", "language", "ledger_brief", "material_evidence", "max_search_query_count", "plan_thought", "plan_title", "step_description", "step_title", "temporal_query_instruction", "temporal_scope_instruction"),
    "collector_webpage_enrichment_compress": ("payload",),
    "collector_webpage_enrichment_select": ("payload",),
    "content_recognition": ("report",),
    "dep_driving_outliner": ("audience_role", "current_date", "entry_search_results", "has_materials", "language", "materials_count", "materials_manifest_text", "materials_relevance_text", "original_query", "questions", "section_num", "tone", "user_feedback"),
    "dep_driving_outliner_interaction": ("audience_role", "current_date", "current_outline", "entry_search_results", "language", "max_section_num", "original_query", "previous_feedback", "questions", "report_template", "tone", "user_feedback"),
    "dep_driving_planner": ("audience_role", "bound_materials_analysis_text", "current_date", "has_materials", "language", "material_coverage_sufficient", "material_first", "materials_count", "materials_manifest_text", "materials_relevance_text", "max_step_num", "original_query", "plan_background_knowledge", "plan_executed_num", "query", "report_task", "section_description", "section_idx", "section_material_bindings_text", "section_material_coverage_text", "section_task", "tone"),
    "extract_message_prompt": ("datas",),
    "generate_questions": ("entry_search_results", "language", "query", "report_type"),
    "generate_transition_sentence": ("language", "summary_next", "summary_prev", "title_next", "title_prev", "user_query"),
    "human_evaluator": ("user_feedback",),
    "infer_conclusion_prompt": ("conclusion", "language", "reference"),
    "infer_extract_conclusion_prompt": ("input", "language"),
    "infer_filter_inference_prompt": ("input", "language"),
    "infer_structured_prompt": ("conclusion", "inference", "language"),
    "infer_supplement_prompt": ("graphs", "language"),
    "infer_validate_prompt": ("language", "references", "statement"),
    "info_organizer": (),
    "insert_visualization": ("numbered_report", "retry_feedback", "visualization_data"),
    "intent_recognition": ("current_date", "has_materials", "materials_analysis_text", "materials_count", "materials_manifest_text", "original_query", "provided_report_type"),
    "new_task_assessment": ("clean_section_text", "current_date", "historical_doc_infos", "language", "section_title", "selected_text", "supported_edit_strategies", "user_instruction"),
    "new_task_rewrite_section": ("clean_section_text", "clean_selected_text", "doc_infos", "edit_strategy", "language", "major_section_text", "major_section_title", "new_subsection_title", "section_title", "selected_subsection_title", "user_instruction"),
    "outline_mode_router": ("question",),
    "outliner": ("audience_role", "brief_outline", "comparison_targets_text", "current_date", "entry_search_results", "has_comparison_targets", "has_materials", "has_required_dimensions", "language", "material_first", "materials_count", "materials_manifest_text", "materials_relevance_text", "original_query", "questions", "required_dimensions_text", "section_num", "task_type", "tone", "user_feedback"),
    "outliner_interaction": ("audience_role", "current_date", "current_outline", "entry_search_results", "max_section_num", "original_query", "previous_feedback", "questions", "report_template", "tone", "user_feedback"),
    "outliner_template": ("current_date", "entry_search_results", "language", "original_query", "questions", "report_template", "user_feedback"),
    "outliner_user_revised": ("audience_role", "current_date", "current_outline", "entry_search_results", "language", "original_query", "questions", "tone", "user_outline"),
    "passages_extractor": ("documents", "extract_content_time", "rationales_text", "retry_feedback", "section_description", "section_task"),
    "planner": ("allowed_dimensions_text", "audience_role", "bound_materials_analysis_text", "comparison_targets_text", "current_date", "has_allowed_dimensions", "has_comparison_targets", "has_materials", "has_required_dimensions", "is_final_decision_section", "language", "material_coverage_sufficient", "material_first", "materials_count", "materials_manifest_text", "materials_relevance_text", "max_step_num", "original_query", "query", "report_task", "required_dimensions_text", "section_description", "section_focus", "section_material_bindings_text", "section_material_coverage_text", "section_task", "task_type", "tone"),
    "programmer": ("doc_infos", "language", "remaining_steps", "save_path"),
    "query_rewrite": ("current_date", "language"),
    "rationale_generator": ("focus_dimensions", "overall_outline", "report_task", "retry_feedback", "section_description", "section_focus", "section_task", "step_summaries"),
    "report_abstract_markdown": ("audience_role", "language", "main_content", "tone"),
    "report_conclusion_markdown": ("audience_role", "language", "main_content", "tone"),
    "report_implications_and_recommendations_markdown": ("audience_role", "comparison_targets_text", "current_outline", "has_comparison_targets", "has_required_dimensions", "language", "main_content", "report_task", "required_dimensions_text", "task_type", "tone", "user_query", "user_role"),
    "report_style_css": ("abstract", "citation_count", "headings", "image_count", "report_title", "table_count"),
    "source_matching": ("content_recognition_result", "search_record"),
    "sub_report_markdown": ("allowed_dimensions_text", "audience_role", "background_knowledge", "citation_infos", "comparison_targets_text", "current_chapter_outline", "current_date", "current_section", "current_section_description", "current_section_format_requirements", "current_subsection", "exclusion_instruction", "has_allowed_dimensions", "has_comparison_targets", "has_exclusion", "has_required_dimensions", "has_temporal_scope", "is_final_decision_section", "language", "references", "required_dimensions_text", "required_target_citations", "retry_feedback", "section_focus", "section_id", "section_iscore", "section_material_bindings_text", "structured_evidence_guide", "task_type", "temporal_scope_instruction", "tone"),
    "sub_report_sidecar": ("language", "outline", "retry_feedback", "section_id", "sub_report_content", "user_query"),
    "sub_report_summary": ("audience_role", "language", "outline", "paragraph_style", "section_id", "sub_report_content", "tone", "user_query"),
    "sub_section_outline": ("allowed_dimensions_text", "comparison_targets_text", "core_context", "core_from_background", "current_outline", "has_allowed_dimensions", "has_comparison_targets", "has_required_dimensions", "has_template", "is_final_decision_section", "language", "report_task", "required_dimensions_text", "retry_feedback", "section_description", "section_focus", "section_format_requirements", "section_idx", "section_iscore", "section_title", "structured_evidence_guide", "task_type"),
    "sub_section_visualization_content": ("desired_chart_type", "language", "origin_content", "retry_feedback", "section_outline"),
    "sub_section_visualization_normalize_units": ("language", "records_json"),
    "supplementary_search_rewrite_selected_and_related": ("collector_summary", "doc_infos", "language", "section_text_clean", "selected_text_clean", "user_instruction"),
    "supplementary_search_rewrite_selected_only": ("collector_summary", "doc_infos", "language", "section_text_clean", "selected_text_clean", "user_instruction"),
    "supplementary_search_task": ("current_date", "language", "section_text_clean", "selected_text_clean", "user_instruction"),
    "synonym_rewrite_expand": ("language", "original_text", "user_instruction"),
    "synonym_rewrite_polish": ("language", "original_text", "user_instruction"),
    "synonym_rewrite_shorten": ("language", "original_text", "user_instruction"),
    "template_semantic_extract": ("extracted_structure", "file_content"),
    "template_structure_extract": ("file_content",),
    "truth_verification_assessment": ("current_date", "doc_infos", "language", "section_heading", "user_instruction", "verified_paragraph"),
    "truth_verification_search_task": ("current_date", "initial_summary", "language", "section_heading", "user_instruction", "verified_paragraph"),
    "vlm_collect_data_prompt": ("data_sources", "language", "tasks"),
    "vlm_find_insert_point_prompt": ("language", "section_contents"),
    "vlm_generate_chart_code_prompt": ("chart_data", "chart_description", "chart_title", "chart_type", "font_path", "history_messages"),
    "vlm_iterate_prompt": ("chart_data", "chart_description", "chart_title", "chart_type", "history_suggestion"),
    "vlm_model_capability_probe": (),
    "material_reduce_summaries": ("SUMMARY_MAX_TOKENS", "material_title", "original_query", "partial_summaries"),
    "material_summarize": ("SUMMARY_MAX_TOKENS", "chunk", "material_title", "original_query"),
}

# Variables guarded by a conditional/default are safe to omit from a caller's
# context.  ``current_date`` is also optional at the call site because the
# Builder injects it for prompts that declare it.
_OPTIONAL_VARIABLES = {
    "brief_collector_query_generation": {"current_date", "has_temporal_scope", "material_first", "materials_analysis_text", "materials_relevance_text", "temporal_scope_instruction"},
    "brief_doc_evaluator": {"current_date"},
    "brief_html_reporter": {"retry_feedback"},
    "brief_html_section": {"retry_feedback", "section_id"},
    "brief_outliner": {"current_date", "has_materials", "has_temporal_scope", "materials_count", "materials_manifest_text", "materials_relevance_text", "temporal_scope_instruction"},
    "brief_reporter": {"audience_role", "tone", "user_format"},
    "brief_sub_reporter": {"audience_role", "exclusion_instruction", "has_exclusion", "section_material_bindings_text", "tone", "writing_guidance"},
    "collector": {"current_date", "query"},
    "collector_gen_query": {"current_date", "has_target_papers", "has_temporal_scope", "target_papers_text", "temporal_query_instruction", "temporal_scope_instruction"},
    "collector_supervisor": {"current_date", "has_temporal_scope", "material_evidence", "temporal_query_instruction", "temporal_scope_instruction"},
    "dep_driving_outliner": {"audience_role", "current_date", "has_materials", "materials_count", "materials_manifest_text", "materials_relevance_text", "original_query", "tone"},
    "dep_driving_outliner_interaction": {"audience_role", "current_date", "original_query", "tone"},
    "dep_driving_planner": {"audience_role", "bound_materials_analysis_text", "current_date", "has_materials", "material_coverage_sufficient", "material_first", "materials_count", "materials_manifest_text", "materials_relevance_text", "original_query", "report_task", "section_description", "section_material_bindings_text", "section_material_coverage_text", "section_task", "tone"},
    "insert_visualization": {"retry_feedback"},
    "intent_recognition": {"current_date", "has_materials", "materials_analysis_text", "materials_count", "materials_manifest_text", "provided_report_type"},
    "new_task_assessment": {"current_date", "user_instruction"},
    "new_task_rewrite_section": {"major_section_text", "major_section_title", "new_subsection_title", "selected_subsection_title", "user_instruction"},
    "outliner": {"audience_role", "brief_outline", "comparison_targets_text", "current_date", "has_comparison_targets", "has_materials", "has_required_dimensions", "material_first", "materials_count", "materials_manifest_text", "materials_relevance_text", "original_query", "required_dimensions_text", "task_type", "tone"},
    "outliner_interaction": {"audience_role", "current_date", "original_query", "tone"},
    "outliner_template": {"current_date", "original_query"},
    "outliner_user_revised": {"audience_role", "current_date", "original_query", "tone"},
    "passages_extractor": {"retry_feedback"},
    "planner": {"allowed_dimensions_text", "audience_role", "bound_materials_analysis_text", "comparison_targets_text", "current_date", "has_allowed_dimensions", "has_comparison_targets", "has_materials", "has_required_dimensions", "is_final_decision_section", "material_coverage_sufficient", "material_first", "materials_count", "materials_manifest_text", "materials_relevance_text", "original_query", "report_task", "required_dimensions_text", "section_description", "section_focus", "section_material_bindings_text", "section_material_coverage_text", "section_task", "task_type", "tone"},
    "query_rewrite": {"current_date"},
    "rationale_generator": {"retry_feedback"},
    "report_abstract_markdown": {"audience_role", "tone"},
    "report_conclusion_markdown": {"audience_role", "tone"},
    "report_implications_and_recommendations_markdown": {"audience_role", "comparison_targets_text", "has_comparison_targets", "has_required_dimensions", "required_dimensions_text", "task_type", "tone", "user_role"},
    "sub_report_markdown": {"allowed_dimensions_text", "audience_role", "comparison_targets_text", "current_date", "current_subsection", "exclusion_instruction", "has_allowed_dimensions", "has_comparison_targets", "has_exclusion", "has_required_dimensions", "has_temporal_scope", "is_final_decision_section", "required_dimensions_text", "required_target_citations", "retry_feedback", "section_focus", "section_material_bindings_text", "structured_evidence_guide", "task_type", "temporal_scope_instruction", "tone"},
    "sub_report_sidecar": {"retry_feedback"},
    "sub_report_summary": {"audience_role", "paragraph_style", "tone"},
    "sub_section_outline": {"allowed_dimensions_text", "comparison_targets_text", "has_allowed_dimensions", "has_comparison_targets", "has_required_dimensions", "is_final_decision_section", "required_dimensions_text", "retry_feedback", "section_focus", "section_iscore", "structured_evidence_guide", "task_type"},
    "sub_section_visualization_content": {"retry_feedback"},
    "supplementary_search_rewrite_selected_and_related": {"user_instruction"},
    "supplementary_search_rewrite_selected_only": {"user_instruction"},
    "supplementary_search_task": {"current_date", "user_instruction"},
    "synonym_rewrite_expand": {"user_instruction"},
    "synonym_rewrite_polish": {"user_instruction"},
    "synonym_rewrite_shorten": {"user_instruction"},
    "truth_verification_assessment": {"current_date", "user_instruction"},
    "truth_verification_search_task": {"current_date", "user_instruction"},
}

DEEPRESEARCH_PROMPT_CONTRACTS = tuple(
    PromptContract(
        name,
        frozenset(variables) - frozenset(_OPTIONAL_VARIABLES.get(name, set())),
        frozenset(_OPTIONAL_VARIABLES.get(name, set())),
    )
    for name, variables in _PROMPT_VARIABLES.items()
)

DATE_PROMPTS = frozenset(
    {
        "intent_recognition",
        "outliner",
        "outliner_interaction",
        "outliner_user_revised",
        "outliner_template",
        "dep_driving_outliner",
        "dep_driving_outliner_interaction",
        "planner",
        "dep_driving_planner",
        "collector",
        "collector_gen_query",
        "collector_supervisor",
        "query_rewrite",
        "sub_report_markdown",
        "brief_outliner",
        "brief_collector_query_generation",
        "brief_doc_evaluator",
        "supplementary_search_task",
        "truth_verification_search_task",
        "new_task_assessment",
        "truth_verification_assessment",
    }
)

PROMPT_ROOT = Path(message_builder.PROMPT_ROOT)
PACKAGE_ROOT = PROMPT_ROOT.parents[1]
_LEGACY_BUILDER_NAMES = ("apply_system_prompt", "apply_vlm_prompt")
_LEGACY_DEEPSEARCH_PATHS = {
    Path("algorithm/prompts/template.py"),
    # DeepSearch/search infrastructure is intentionally outside this migration
    # and keeps the legacy prompt builder for compatibility.
    Path("algorithm/search_agent"),
    Path("algorithm/search_index"),
    Path("algorithm/search_nodes"),
    Path("algorithm/search_tools"),
}


def _is_legacy_deepsearch_path(path: Path) -> bool:
    relative_path = path.relative_to(PACKAGE_ROOT)
    return any(
        relative_path == legacy_path or legacy_path in relative_path.parents
        for legacy_path in _LEGACY_DEEPSEARCH_PATHS
    )


def _deepresearch_python_files() -> list[Path]:
    roots = (
        PROMPT_ROOT.parent,
        PACKAGE_ROOT / "framework/openjiuwen/agent/collector_graph",
    )
    return sorted(
        path
        for root in roots
        for path in root.rglob("*.py")
        if not _is_legacy_deepsearch_path(path)
    )


def _template_sources(template_name: str) -> str:
    """Read a template and all literal includes for literal-token checks."""
    environment = message_builder._create_environment()
    visited: set[str] = set()
    sources: list[str] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        source, _, _ = environment.loader.get_source(environment, name)
        sources.append(source)
        parsed = environment.parse(source)
        for referenced_name in meta.find_referenced_templates(parsed):
            if referenced_name is not None:
                visit(referenced_name)

    visit(template_name)
    return "\n".join(sources)


@pytest.mark.parametrize(
    "contract", DEEPRESEARCH_PROMPT_CONTRACTS, ids=lambda contract: contract.name
)
def test_prompt_contract(contract: PromptContract):
    environment = message_builder._create_environment()
    system_vars = message_builder._template_variables(
        environment, f"{contract.name}/system.md", recursive=True
    )
    user_name = f"{contract.name}/user.md"
    user_vars = (
        message_builder._template_variables(environment, user_name, recursive=True)
        if (PROMPT_ROOT / user_name).is_file()
        else set()
    )

    assert system_vars == set()
    assert user_vars == contract.required_vars | contract.optional_vars


def test_contracts_cover_every_directory_prompt():
    prompt_directories = {
        path.name for path in PROMPT_ROOT.iterdir() if (path / "system.md").is_file()
    }
    assert {contract.name for contract in DEEPRESEARCH_PROMPT_CONTRACTS} == prompt_directories


def test_current_date_is_limited_to_design_whitelist_and_systems_are_time_free():
    environment = message_builder._create_environment()
    prompts_with_current_date: set[str] = set()

    for contract in DEEPRESEARCH_PROMPT_CONTRACTS:
        system_name = f"{contract.name}/system.md"
        system_text = _template_sources(system_name)
        assert "CURRENT_TIME" not in system_text

        user_name = f"{contract.name}/user.md"
        if not (PROMPT_ROOT / user_name).is_file():
            continue
        user_vars = message_builder._template_variables(
            environment, user_name, recursive=True
        )
        user_source = _template_sources(user_name)
        assert "CURRENT_TIME" not in user_source
        if "current_date" in user_vars:
            prompts_with_current_date.add(contract.name)

    assert prompts_with_current_date == DATE_PROMPTS


def test_deepresearch_python_does_not_use_legacy_prompt_builders():
    violations = {
        path.relative_to(PACKAGE_ROOT).as_posix(): name
        for path in _deepresearch_python_files()
        for name in _LEGACY_BUILDER_NAMES
        if name in path.read_text(encoding="utf-8")
    }
    assert violations == {}
