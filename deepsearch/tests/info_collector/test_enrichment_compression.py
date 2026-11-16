"""正文压缩、长度限制与成功写回的功能回归测试。"""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen_deepsearch.algorithm.research_collector.webpage_enrichment import WebPageEvidenceContent
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph import webpage_enrichment as module
from openjiuwen_deepsearch.common.common_constants import MAX_COLLECTOR_DOC_CONTENT_LENGTH as LIMIT


@pytest.mark.asyncio
@pytest.mark.parametrize('output', ['x' * LIMIT + ' VLSA', 'x' * (LIMIT - len(' VLSA')) + ' VLSA'])
async def test_compression_preserves_budget_without_extra_call(monkeypatch, output):
    node = module.WebPageEnrichmentNode()
    call = AsyncMock(return_value=WebPageEvidenceContent(original_content=output))
    monkeypatch.setattr(module, 'ainvoke_llm_with_stats', call)
    doc = {'original_content': 'VLSA', 'key_passages': ['VLSA']}
    result = await node._compress_content({'step_title': 'step'}, doc, {'content': 'fetched data'})
    assert len(result.original_content) <= LIMIT
    assert result.original_content == output[:LIMIT].strip()
    assert call.await_count == 1
    payload = json.loads(call.call_args.args[1][1]['content'])
    assert payload['required_facts'] == ['VLSA']
    assert payload['target_content_length'] == LIMIT - 500


@pytest.mark.asyncio
async def test_truncated_output_refreshes_passages_from_final_body(monkeypatch):
    node = module.WebPageEnrichmentNode()
    monkeypatch.setattr(module, 'ainvoke_llm_with_stats', AsyncMock(return_value=WebPageEvidenceContent(
        original_content='A' * LIMIT + 'removed tail evidence', key_passages=['removed tail evidence'],
    )))
    result = await node._compress_content({}, {}, {'content': 'raw'})
    assert result.key_passages
    assert all(passage in result.original_content for passage in result.key_passages)
    assert 'removed tail evidence' not in result.key_passages


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', [None, WebPageEvidenceContent(), RuntimeError('remote error')])
async def test_failed_or_empty_model_response_preserves_failure_without_extra_call(monkeypatch, outcome):
    node = module.WebPageEnrichmentNode()
    call = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    monkeypatch.setattr(module, 'ainvoke_llm_with_stats', call)
    monkeypatch.setattr(module, 'record_llm_retry_log', Mock())
    assert await node._compress_content({}, {}, {'content': 'raw'}) is None
    assert call.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('sensitive', [False, True])
async def test_compression_debug_reports_actual_input_and_output_without_sensitive_text(monkeypatch, caplog, sensitive):
    node = module.WebPageEnrichmentNode()
    raw = 'private webpage ' + 'x' * (LIMIT * 10)
    old = 'VLSA operates at 0.13 V.'
    monkeypatch.setattr(module.LogManager, 'is_sensitive', lambda: sensitive)
    monkeypatch.setattr(module, 'ainvoke_llm_with_stats', AsyncMock(return_value=WebPageEvidenceContent(
        original_content=old,
    )))
    caplog.set_level('DEBUG', logger=module.__name__)
    doc = {'doc_id': 'private document', 'original_content': old, 'key_passages': [old]}
    result = await node._compress_content({'section_idx': 2}, doc, {'content': raw})
    records = [record for record in caplog.records if 'enrichment_compression_debug' in record.getMessage()]
    details = [json.loads(record.getMessage().split('details_json=', 1)[1]) for record in records]
    before, after = details
    assert before['stage'] == 'input' and after['stage'] == 'output'
    assert before['fetched_len'] == len(raw)
    assert before['webpage_input_len'] == LIMIT * 10
    assert after['model_output_len'] == after['final_content_len'] == len(old)
    assert after['unchanged_from_existing'] is True
    assert after['truncated'] is False
    assert result.original_content == old
    assert all(record.skip_truncation for record in records)
    if sensitive:
        assert all('contents' not in item and 'doc_id' not in item for item in details)
        assert 'private webpage' not in caplog.text and 'private document' not in caplog.text
        assert old not in caplog.text
    else:
        assert before['contents']['webpage_content'] == raw[:LIMIT * 10]
        assert before['contents']['existing_content'] == old
        assert after['contents']['model_output'] == after['contents']['final_content'] == old


@pytest.mark.asyncio
@pytest.mark.parametrize('sensitive', [False, True])
async def test_unchanged_body_is_marked_fetched_without_update_success_log(monkeypatch, caplog, sensitive):
    node = module.WebPageEnrichmentNode()
    old = 'VLSA operates at 0.13 V.'
    doc = {'doc_id': 'one', 'url': 'https://private.example/page', 'original_content': old, 'key_passages': [old]}
    monkeypatch.setattr(module.LogManager, 'is_sensitive', lambda: sensitive)
    monkeypatch.setattr(node, '_fetch_webpage', AsyncMock(return_value={'content': 'article ' * 50}))
    monkeypatch.setattr(node, '_compress_content', AsyncMock(return_value=WebPageEvidenceContent(
        original_content=old, key_passages=[old],
    )))
    caplog.set_level('INFO')
    result = await node._enrich_selected_candidates({
        'new_doc_infos_current_loop': [doc], 'doc_infos': [doc],
        'candidates': [{'doc_index': 0, 'url': doc['url']}],
    }, [0])
    updated = result['new_doc_infos_current_loop'][0]
    assert updated['original_content'] == old
    assert updated['enrichment']['webpage_fetched'] is True
    assert updated['enrichment']['content_changed'] is False
    assert module.build_enrichment_candidates([updated]) == []
    assert 'enrichment_unchanged' in caplog.text
    assert 'fetch enriched doc.' not in caplog.text and 'enrichment succeeded' not in caplog.text
    if sensitive:
        assert 'private.example' not in caplog.text
