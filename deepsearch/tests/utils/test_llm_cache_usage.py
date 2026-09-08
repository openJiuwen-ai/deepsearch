"""验证缓存 token 提取、流式透传及汇总恢复。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.utils.common_utils import llm_utils


@pytest.mark.parametrize("payload, expected", [
    ({"cache_tokens": 12}, 12),
    ({"prompt_cache_hit_tokens": 13}, 13),
    ({"prompt_tokens_details": {"cached_tokens": 14}}, 14),
    ({"input_tokens_details": SimpleNamespace(cached_tokens=15)}, 15),
    ({"input_token_details": {"cache_read": 16}}, 16),
    ({"cache_read_input_tokens": 17, "cache_creation_input_tokens": 30}, 17),
    ({"token_usage": {"prompt_cache_hit_tokens": 18}}, 18),
    ({"cache_tokens": 0, "prompt_cache_hit_tokens": 19}, 19),
    ({"prompt_cache_hit_tokens": 0}, 0),
    ({}, None),
    ({"cache_tokens": None}, None),
    ({"cache_tokens": "bad"}, None),
])
def test_extract_cache_usage(payload, expected):
    """各供应商字段归一化，缺失与零命中可区分。"""
    assert llm_utils._extract_cache_tokens(payload) == expected


def test_cache_usage_survives_workflow_normalization():
    """缓存汇总保留到快照，并且不重复计入输入总量。"""
    session_id = "cache-usage-test"
    llm_utils.pop_workflow_llm_usage(session_id)
    try:
        llm_utils.add_workflow_llm_usage(session_id, 100, 20, 120, "collector", cache_tokens=60)
        llm_utils.add_workflow_llm_usage(session_id, 100, 20, 120, "collector")
        usage = llm_utils.normalize_workflow_llm_usage(llm_utils.get_workflow_llm_usage(session_id))
        assert usage["cache_tokens"] == 60
        assert list(usage).index("cache_tokens") == list(usage).index("total_tokens") + 1
        assert usage["input_tokens"] == 200
        assert usage["total_tokens"] == 240
        assert usage["agent_name_token_usage"][0]["cache_tokens"] == 60
    finally:
        llm_utils.pop_workflow_llm_usage(session_id)


def test_usage_only_stream_chunk_preserves_cache_tokens():
    """usage-only 流式补偿不能丢弃缓存命中。"""
    chunk = llm_utils._build_usage_only_chunk({"usage": {
        "prompt_tokens": 100, "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 60},
    }}, "demo")
    assert chunk.usage_metadata.cache_tokens == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_cache_tokens_reach_call_log_and_workflow(streaming):
    """流式及非流式调用均输出缓存统计并写入 workflow。"""
    from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata

    response = AssistantMessage(content="ok", usage_metadata=UsageMetadata(
        input_tokens=100, output_tokens=20, total_tokens=120, cache_tokens=60,
    ))
    model = SimpleNamespace(invoke=AsyncMock(return_value=response))
    session_id = "cache-call-test"
    token = llm_utils.session_id_ctx.set(session_id)
    llm_utils.pop_workflow_llm_usage(session_id)
    agent_name = "intent_recognition" if streaming else next(iter(llm_utils._DEEPSEARCH_NODE_IDS))
    try:
        with patch.object(llm_utils, "_is_llm_stats_enabled", return_value=True), \
             patch.object(llm_utils, "llm_astream", new=AsyncMock(return_value=response)), \
             patch.object(llm_utils.metrics_logger, "info") as log:
            await llm_utils.ainvoke_llm_with_stats(
                llm={"model": model, "model_name": "demo"},
                messages=[{"role": "user", "content": "hello"}], agent_name=agent_name,
            )
        assert any("'cache_tokens': 60" in str(call) for call in log.call_args_list)
        usage = llm_utils.get_workflow_llm_usage(session_id)
        assert usage["cache_tokens"] == 60
        assert usage["agent_name_token_usage"][0]["cache_tokens"] == 60
    finally:
        llm_utils.session_id_ctx.reset(token)
        llm_utils.pop_workflow_llm_usage(session_id)


def test_existing_stream_parser_preserves_raw_cache_usage():
    """已有 parser 返回 chunk 时也补全被遗漏的缓存字段。"""
    from openjiuwen.core.foundation.llm import AssistantMessageChunk, UsageMetadata

    chunk = AssistantMessageChunk(content="ok", usage_metadata=UsageMetadata(input_tokens=100))
    model = SimpleNamespace(
        _client=SimpleNamespace(_parse_stream_chunk=lambda raw: chunk),
        model_config=SimpleNamespace(model_name="demo"),
    )
    restore = llm_utils._install_usage_only_chunk_parser(model)
    try:
        parsed = model._client._parse_stream_chunk({"usage": {"prompt_cache_hit_tokens": 60}})
        assert parsed.usage_metadata.cache_tokens == 60
    finally:
        restore()
