from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt


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
