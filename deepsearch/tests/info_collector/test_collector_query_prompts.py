from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
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
