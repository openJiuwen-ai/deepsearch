from datetime import date

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TemporalScope,
    build_temporal_scope_prompt_context,
)


def _render_prompt(prompt_name: str, context: dict) -> str:
    messages = apply_system_prompt(prompt_name, context)
    return "\n".join(message["content"] for message in messages)


def test_collector_gen_query_prompt_allows_source_language_queries():
    rendered_prompt = _render_prompt(
        "collector_gen_query",
        {
            "plan_title": "China EV supply chain",
            "plan_thought": "Collect current market and supplier evidence.",
            "step_title": "Global supplier benchmarks",
            "step_description": "Find authoritative benchmark data from global sources.",
            "max_search_query_count": 3,
            "language": "zh-CN",
            "report_type": "professional",
        },
    )

    assert "Query language is not restricted by the report language" in rendered_prompt
    assert 'Write non-query JSON fields, such as "missing_evidence", in zh-CN' in rendered_prompt
    assert 'The strings inside "queries" are exempt from this output-language rule' in rendered_prompt
    assert "Choose English, Chinese, another local language, or mixed-language wording" in rendered_prompt
    assert "most likely to retrieve authoritative evidence" in rendered_prompt
    assert "Do not produce more than 3 queries" in rendered_prompt


def test_collector_gen_query_prompt_defines_target_paper_locator_contract():
    rendered_prompt = _render_prompt(
        "collector_gen_query",
        {
            "plan_title": "Orthodontic users",
            "plan_thought": "Find evidence from the target study.",
            "step_title": "Study evidence",
            "step_description": "Locate the study and extract demographic findings.",
            "max_search_query_count": 3,
            "language": "zh-CN",
            "report_type": "professional",
            "has_target_papers": True,
            "target_papers_text": '[{"dataset":"MEPS","data_year":"2019","topic":"orthodontics"}]',
        },
    )

    assert "Target papers" in rendered_prompt
    assert "PMID > DOI > arXiv ID > full title > implicit fingerprint" in rendered_prompt
    assert "at most one locator query" in rendered_prompt
    assert 'search_engine_names` to `["pubmed"]`' in rendered_prompt
    assert 'search_engine_names` to `["arxiv"]`' in rendered_prompt
    assert "English academic terminology" in rendered_prompt
    assert "Keep the locator query separate" in rendered_prompt
    assert "dataset observation year is not a publication-date boundary" in rendered_prompt
    assert "isolated collector step" in rendered_prompt


def test_collector_gen_query_prompt_uses_plural_scholarly_engine_contract():
    rendered_prompt = _render_prompt(
        "collector_gen_query",
        {
            "plan_title": "Cross-domain research",
            "plan_thought": "Collect scholarly evidence.",
            "step_title": "Clinical AI evidence",
            "step_description": "Find medical and technical papers.",
            "max_search_query_count": 3,
            "language": "en-US",
            "report_type": "professional",
        },
    )

    assert '"search_engine_names"' in rendered_prompt
    assert '"search_engine_name":' not in rendered_prompt
    assert "`search_engine_name`" not in rendered_prompt
    assert '"semantic_scholar"' in rendered_prompt
    assert '"openalex"' not in rendered_prompt
    assert '"semantic_scholar"' in rendered_prompt
    assert "ordinary academic" in rendered_prompt
    assert "cross-domain" in rendered_prompt


def test_collector_supervisor_prompt_allows_source_language_follow_up_queries():
    rendered_prompt = _render_prompt(
        "collector_supervisor",
        {
            "plan_title": "China EV supply chain",
            "plan_thought": "Collect current market and supplier evidence.",
            "step_title": "Global supplier benchmarks",
            "step_description": "Find authoritative benchmark data from global sources.",
            "ledger_brief": "missing_evidence: official global supplier benchmark",
            "evidence_table": "[]",
            "max_search_query_count": 2,
            "language": "zh-CN",
            "report_type": "professional",
        },
    )

    assert "Query language is not restricted by the report language" in rendered_prompt
    assert (
        'Write non-query JSON fields, such as "knowledge_gap", "known_facts", '
        'and "missing_evidence", in zh-CN'
    ) in rendered_prompt
    assert 'The strings inside "next_queries" are exempt from this output-language rule' in rendered_prompt
    assert "Choose English, Chinese, another local language, or mixed-language wording" in rendered_prompt
    assert "Do not force all follow-up queries into `zh-CN`" in rendered_prompt
    assert "Do not produce more than 2 next_queries" in rendered_prompt


def test_collector_supervisor_prompt_keeps_target_paper_failure_non_blocking():
    rendered_prompt = _render_prompt(
        "collector_supervisor",
        {
            "plan_title": "Target study",
            "plan_thought": "Use the specified study when available.",
            "step_title": "Evidence",
            "step_description": "Find evidence.",
            "ledger_brief": "Missing evidence: target paper not located",
            "evidence_table": "[]",
            "max_search_query_count": 2,
            "language": "en-US",
            "report_type": "professional",
        },
    )

    assert "target paper" in rendered_prompt
    assert "evidence limitation, not a workflow error" in rendered_prompt
    assert "at most one broader follow-up" in rendered_prompt
    assert "must not abort report generation" in rendered_prompt
    assert "Never substitute another paper" in rendered_prompt
    assert "implicit fingerprint" in rendered_prompt


def test_collector_gen_query_prompt_requires_natural_language_temporal_scope():
    """首轮 query 应由 LLM 自然表达时间限制，且时间短语不计关键词上限。"""
    context = {
        "plan_title": "AI benchmark",
        "plan_thought": "Collect benchmark evidence.",
        "step_title": "Historical results",
        "step_description": "Find benchmark results.",
        "max_search_query_count": 3,
        "language": "en-US",
        "report_type": "professional",
    }
    context.update(
        build_temporal_scope_prompt_context(
            {
                "content_date_scope": {
                    "constraint_type": "content_date",
                    "start_date": "2020-01-01",
                    "end_date": "2022-12-31",
                }
            }
        )
    )

    rendered_prompt = _render_prompt("collector_gen_query", context)

    assert "Research Time Boundary" in rendered_prompt
    assert "facts and data from 2020-01-01 through 2022-12-31" in rendered_prompt
    assert "Express this boundary naturally in every query as a constraint time phrase" in rendered_prompt
    assert "time phrase does not count toward the five topical keywords" in rendered_prompt


def test_collector_supervisor_prompt_requires_temporal_follow_up_queries():
    """补搜 query 应继续遵守资料发表时间范围。"""
    context = {
        "plan_title": "Energy policy",
        "plan_thought": "Collect policy sources.",
        "step_title": "Policy changes",
        "step_description": "Find authoritative sources.",
        "ledger_brief": "missing_evidence: policy timeline",
        "evidence_table": [],
        "max_search_query_count": 2,
        "language": "en-US",
        "report_type": "professional",
    }
    context.update(
        build_temporal_scope_prompt_context(
            {
                "source_date_scope": {
                    "constraint_type": "source_date",
                    "end_date": "2021-12-31",
                }
            }
        )
    )

    rendered_prompt = _render_prompt("collector_supervisor", context)

    assert "Research Time Boundary" in rendered_prompt
    assert "published on or before 2021-12-31" in rendered_prompt
    assert "Express this boundary naturally in every query as a constraint time phrase" in rendered_prompt
    assert "time phrase does not count toward the five topical keywords" in rendered_prompt
    assert "total number of topical keywords should not exceed 5" in rendered_prompt


def test_collector_gen_query_prompt_tavily_source_date_omits_constraint_time_phrase():
    """Tavily×source_date 且无副引擎时，搜索词不带约束时间词，但主题年份放行。"""
    context = {
        "plan_title": "AI benchmark",
        "plan_thought": "Collect benchmark evidence.",
        "step_title": "Historical results",
        "step_description": "Find benchmark results.",
        "max_search_query_count": 3,
        "language": "en-US",
        "report_type": "professional",
    }
    context.update(
        build_temporal_scope_prompt_context(
            {
                "source_date_scope": {
                    "constraint_type": "source_date",
                    "end_date": "2023-12-31",
                }
            },
            engine_name="tavily",
            scholarly_enabled=False,
        )
    )

    rendered_prompt = _render_prompt("collector_gen_query", context)

    assert context["temporal_embed_in_query"] is False
    assert "Research Time Boundary" in rendered_prompt
    assert "Do NOT add any constraint time phrase" in rendered_prompt
    assert "the engine filters by date natively" in rendered_prompt
    assert "Topical years that are part of the research subject are still allowed" in rendered_prompt
    assert "Express this boundary naturally" not in rendered_prompt


def test_collector_supervisor_prompt_renders_current_time_without_scope():
    """无时间约束时，front-matter 的 CURRENT_TIME 变量被实际值替换（非裸字符串），补搜 query 指向当前时间。"""
    rendered_prompt = _render_prompt(
        "collector_supervisor",
        {
            "plan_title": "Energy policy",
            "plan_thought": "Collect policy sources.",
            "step_title": "Policy changes",
            "step_description": "Find authoritative sources.",
            "ledger_brief": "missing_evidence: policy timeline",
            "evidence_table": [],
            "max_search_query_count": 2,
            "language": "zh-CN",
            "report_type": "professional",
        },
    )

    # front-matter 的 CURRENT_TIME 被实际值替换，而非留下裸模板变量
    assert "CURRENT TIME:" in rendered_prompt
    assert "{{CURRENT_TIME}}" not in rendered_prompt
    assert "{{ CURRENT_TIME }}" not in rendered_prompt
    # 无约束分支：补搜 query 指向当前时间
    assert "most current information is gathered" in rendered_prompt
    assert "current time is" in rendered_prompt
    # 无约束时不应出现时间边界块
    assert "Research Time Boundary" not in rendered_prompt


def test_collector_supervisor_prompt_renders_current_time_with_scope():
    """有时间约束时，front-matter CURRENT_TIME 仍渲染，但补搜走时间边界分支。"""
    context = {
        "plan_title": "Energy policy",
        "plan_thought": "Collect policy sources.",
        "step_title": "Policy changes",
        "step_description": "Find authoritative sources.",
        "ledger_brief": "missing_evidence: policy timeline",
        "evidence_table": [],
        "max_search_query_count": 2,
        "language": "zh-CN",
        "report_type": "professional",
    }
    context.update(
        build_temporal_scope_prompt_context(
            {
                "source_date_scope": {
                    "constraint_type": "source_date",
                    "end_date": "2024-12-31",
                }
            }
        )
    )

    rendered_prompt = _render_prompt("collector_supervisor", context)

    assert "CURRENT TIME:" in rendered_prompt
    assert "{{CURRENT_TIME}}" not in rendered_prompt
    assert "{{ CURRENT_TIME }}" not in rendered_prompt
    # 有约束分支：出现时间边界，不出现"most current information"兜底句
    assert "Research Time Boundary" in rendered_prompt
    assert "most current information is gathered" not in rendered_prompt


def test_collector_gen_query_prompt_renders_dual_constraint_merged_embed_guidance():
    """双约束(Tavily)下 collector_gen_query 渲染合并 embed 指引——content_date 事实时段词
    在 temporal_query_instruction 中,source_date 发表时间词不在(Tavily 原生过滤不 embed source_date)。"""
    ctx = build_temporal_scope_prompt_context(
        ResearchIntent(
            source_date_scope=TemporalScope(
                constraint_type="source_date", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
            ),
            content_date_scope=TemporalScope(
                constraint_type="content_date", start_date=date(2020, 1, 1), end_date=date(2022, 12, 31)
            ),
        ),
        engine_name="tavily",
    )
    rendered = _render_prompt(
        "collector_gen_query",
        {
            "plan_title": "x",
            "plan_thought": "x",
            "step_title": "x",
            "step_description": "x",
            "max_search_query_count": 3,
            "language": "zh-CN",
            "report_type": "professional",
            **ctx,
        },
    )
    assert "facts/events occurred" in rendered
    assert "tied to the publication date" not in rendered


def test_brief_collector_query_generation_prompt_renders_dual_constraint_concat_boundaries():
    """双约束下 brief_collector_query_generation 渲染拼接兼容字段 temporal_scope_instruction——
    source 边界(2026)与 content 边界(2020)同现。"""
    ctx = build_temporal_scope_prompt_context(
        ResearchIntent(
            source_date_scope=TemporalScope(
                constraint_type="source_date", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
            ),
            content_date_scope=TemporalScope(
                constraint_type="content_date", start_date=date(2020, 1, 1), end_date=date(2022, 12, 31)
            ),
        ),
        engine_name="tavily",
    )
    rendered = _render_prompt(
        "brief_collector_query_generation",
        {
            "outline": [],
            "task_type": "",
            "required_dimensions": [],
            "comparison_targets": [],
            "executed_queries": [],
            "blocking_gaps": [],
            "user_query": "review retrospective reports",
            **ctx,
        },
    )
    assert "2026" in rendered
    assert "2020" in rendered
