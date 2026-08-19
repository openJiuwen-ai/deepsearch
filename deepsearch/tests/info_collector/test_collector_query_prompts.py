from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    build_temporal_scope_prompt_context,
    resolve_temporal_embed_in_query,
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
                "temporal_scope": {
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
    assert "express this boundary naturally in every generated query" in rendered_prompt
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
                "temporal_scope": {
                    "constraint_type": "source_date",
                    "end_date": "2021-12-31",
                }
            }
        )
    )

    rendered_prompt = _render_prompt("collector_supervisor", context)

    assert "Research Time Boundary" in rendered_prompt
    assert "published on or before 2021-12-31" in rendered_prompt
    assert "every generated next query" in rendered_prompt
    assert "time phrase does not count toward the five topical keywords" in rendered_prompt
    assert "total number of topical keywords should not exceed 5" in rendered_prompt


def test_resolve_temporal_embed_in_query_signal_matrix():
    """信号矩阵:仅 tavily x source_date 时 query 不再携带时间词,其余场景带时间。"""
    # Tavily x source_date → 引擎原生过滤主导,query 不带时间(消双重约束)
    assert resolve_temporal_embed_in_query("tavily", "source_date") is False
    # 非 tavily x source_date → query 短语兜底
    assert resolve_temporal_embed_in_query("google", "source_date") is True
    assert resolve_temporal_embed_in_query("petal", "source_date") is True
    # 任意引擎 x content_date → query 短语指向事实时间
    assert resolve_temporal_embed_in_query("tavily", "content_date") is True
    # 引擎名取不到可靠值 → 保守回退为带时间
    assert resolve_temporal_embed_in_query(None, "source_date") is True
    assert resolve_temporal_embed_in_query("", "source_date") is True


def test_resolve_temporal_embed_in_query_secondary_engine():
    """副引擎会跑同一批 query 且不支持原生时间过滤时,即使主引擎是
    tavily 也必须嵌入时间词,否则副引擎结果完全失去时间约束信号。"""
    # 副引擎无原生过滤能力 → 嵌入时间词兜底
    assert resolve_temporal_embed_in_query("tavily", "source_date", "google") is True
    # 副引擎同为 tavily / 未配置( None 或空) → 保持消双重约束
    assert resolve_temporal_embed_in_query("tavily", "source_date", "tavily") is False
    assert resolve_temporal_embed_in_query("tavily", "source_date", None) is False
    assert resolve_temporal_embed_in_query("tavily", "source_date", "") is False
    # 副引擎参数不影响 content_date(始终带时间)
    assert resolve_temporal_embed_in_query("tavily", "content_date", "tavily") is True


def _gen_query_context():
    return {
        "plan_title": "AI benchmark",
        "plan_thought": "Collect benchmark evidence.",
        "step_title": "Recent results",
        "step_description": "Find benchmark results.",
        "max_search_query_count": 3,
        "language": "en-US",
        "report_type": "professional",
    }


def _supervisor_context():
    return {
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


def test_tavily_source_date_prompt_forbids_time_words_in_query():
    """Tavily x source_date:prompt 应说明边界已由引擎过滤,禁止 query 带时间词。"""
    intent = {"temporal_scope": {"constraint_type": "source_date", "start_date": "2024-01-01"}}

    context = _gen_query_context()
    context.update(build_temporal_scope_prompt_context(intent, engine_name="tavily"))
    rendered = _render_prompt("collector_gen_query", context)
    assert "already enforced by the search engine's native date filters" in rendered
    assert "express this boundary naturally in every generated query" not in rendered

    context = _supervisor_context()
    context.update(build_temporal_scope_prompt_context(intent, engine_name="tavily"))
    rendered = _render_prompt("collector_supervisor", context)
    assert "already enforced by the search engine's native date filters" in rendered
    assert "every generated next query" not in rendered


def test_non_tavily_open_ended_scope_prompt_requires_concrete_year_or_month():
    """非 tavily 开放边界(缺 end_date):禁止 latest/recent,必须换算成具体年份/月份。"""
    intent = {"temporal_scope": {"constraint_type": "source_date", "start_date": "2024-01-01"}}

    context = _gen_query_context()
    context.update(build_temporal_scope_prompt_context(intent, engine_name="google"))
    rendered = _render_prompt("collector_gen_query", context)
    assert "express this boundary naturally in every generated query" in rendered
    assert 'Never use vague time words such as "latest" or "recent"' in rendered
    assert "concrete year or month" in rendered

    context = _supervisor_context()
    context.update(build_temporal_scope_prompt_context(intent, engine_name="google"))
    rendered = _render_prompt("collector_supervisor", context)
    assert "every generated next query" in rendered
    assert 'Never use vague time words such as "latest" or "recent"' in rendered
    assert "concrete year or month" in rendered


def test_content_date_scope_prompt_keeps_natural_time_expression():
    """content_date(含 tavily):保持 query 自然表达时间边界的行为。"""
    context = build_temporal_scope_prompt_context(
        {"temporal_scope": {"constraint_type": "content_date", "end_date": "2021-12-31"}},
        engine_name="tavily",
    )

    assert context["has_temporal_scope"] is True
    assert context["temporal_embed_in_query"] is True
    assert context["temporal_open_ended"] is False
    assert "facts and data" in context["temporal_scope_instruction"]


def test_closed_boundary_scope_is_not_open_ended():
    """双边界齐全时不算开放边界,不触发 latest/recent 禁令。"""
    context = build_temporal_scope_prompt_context(
        {
            "temporal_scope": {
                "constraint_type": "source_date",
                "start_date": "2020-01-01",
                "end_date": "2022-12-31",
            }
        },
        engine_name="google",
    )

    assert context["temporal_embed_in_query"] is True
    assert context["temporal_open_ended"] is False

    rendered = _render_prompt("collector_gen_query", {**_gen_query_context(), **context})
    assert "Never use vague time words" not in rendered
