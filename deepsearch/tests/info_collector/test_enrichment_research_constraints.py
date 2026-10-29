"""已有研究约束贯穿网页候选选择与正文压缩。"""

from copy import deepcopy
import json
from unittest.mock import Mock

from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages
from openjiuwen_deepsearch.algorithm.research_collector.webpage_enrichment import (
    build_compression_user_payload, build_enrichment_candidates, build_selection_user_payload,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment import WebPageEnrichmentNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import build_evidence_constraint_prompt_context


def intent():
    return {
        "required_dimensions": ["efficiency, concentration and temperature from one experiment"],
        "comparison_targets": ["carbon steel in 1 M HCl"],
        "temporal_scope": {"constraint_type": "source_date", "end_date": "2020-12-31"},
        "content_date_scope": None,
        "exclude_url": ["https://example.com/excluded"],
    }


def test_constraints_preserve_legacy_input_and_exclusion_switch():
    original = intent()
    snapshot = deepcopy(original)
    off = build_evidence_constraint_prompt_context(original)
    on = build_evidence_constraint_prompt_context(original, exclusion_enabled=True)
    assert "2020-12-31" in off['source_date_instruction']
    assert off['comparison_targets'] == original['comparison_targets']
    assert 'exclusion_instruction' not in off
    assert 'https://example.com/excluded' in on['exclusion_instruction']
    on['required_dimensions'].clear()
    assert original == snapshot


def test_dual_boundaries_remain_separate_and_invalid_state_does_not_leak(caplog):
    raw = {**intent(), "source_date_scope": {"constraint_type": "source_date", "end_date": "2022-12-31"},
           "content_date_scope": {"constraint_type": "content_date", "end_date": "2020-06-30"}}
    constraints = build_evidence_constraint_prompt_context(raw)
    assert '2022-12-31' in constraints['source_date_instruction']
    assert '2020-06-30' in constraints['content_date_instruction']
    assert build_evidence_constraint_prompt_context({"required_dimensions": [["private secret"]]}) == {}
    assert "private secret" not in caplog.text
    assert build_evidence_constraint_prompt_context(None) == {}


def test_node_and_both_payloads_forward_constraints_and_known_publication_time():
    values = {"collector_context.research_intent": intent(), "config.exclusion_constraint_enable": True}
    session = Mock()
    session.get_global_state.side_effect = values.get
    state = WebPageEnrichmentNode()._pre_handle({}, session, None)
    doc = {"url": "https://example.com/2026/resource", "title": "Inhibitor results", "doc_time": "2019-01-02"}
    candidates = build_enrichment_candidates([doc])
    assert state['max_urls'] == 5
    selected = build_selection_user_payload(state, candidates)
    compressed = build_compression_user_payload(state, doc, {"content": "source"})
    assert selected['max_urls'] == 5
    assert selected['research_constraints'] == compressed['research_constraints']
    assert selected['research_constraints']['required_dimensions'] == intent()['required_dimensions']
    assert selected['candidates'][0]['publish_time'] == compressed['document']['publish_time'] == '2019-01-02'
    no_date = build_enrichment_candidates([{**doc, "doc_time": ""}])[0]
    assert no_date['publish_time'] == ""  # 不从URL中的2026猜日期。
    for name, payload in [('collector_webpage_enrichment_select', selected), ('collector_webpage_enrichment_compress', compressed)]:
        messages = build_prompt_messages(name, {"payload": json.dumps(payload)})
        assert '2020-12-31' in messages[1]['content']
        assert 'https://example.com/excluded' not in messages[0]['content']


def test_unconstrained_task_does_not_invent_boundary():
    assert build_selection_user_payload({}, [])['max_urls'] == 5
    payload = build_compression_user_payload({}, {}, {"content": "raw"})
    assert 'research_constraints' not in payload
    assert payload['document']['publish_time'] == ''
