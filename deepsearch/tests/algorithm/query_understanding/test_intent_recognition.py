# -*- coding: UTF-8 -*-
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    IntentRecognitionResult,
    MAX_RESEARCH_QUERY_LENGTH,
    _to_str_list,
    classify_and_recognize_intent,
    normalize_research_query,
    recognize_report_intent,
    web_search_for_query,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import ResearchIntent


@pytest.fixture
def sample_tool_response():
    return {
        "tool_calls": [
            {
                "name": "emit_report_intent",
                "args": {
                    "research_query": "AI Agent trends",
                    "language": "zh-CN",
                    "section_count": 5,
                    "audience_role": "研发负责人",
                    "tone": "analytical",
                    "report_type": "professional",
                    "include_url": ["https://example.com/a", "https://example.com/b"],
                    "exclude_url": [],
                    "include_domains": [],
                    "exclude_domains": [],
                },
                 "id": "tc1",
                "type": "tool_call",
            }
        ],
        "content": "",
    }


@pytest.mark.asyncio
async def test_recognize_report_intent_success(sample_tool_response):
    mock_llm = Mock()
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": mock_llm},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=sample_tool_response,
    ):
        result = await recognize_report_intent(
            {
                "original_query": "Write a report: AI Agent\nhttps://example.com/a",
                "llm_model_name": "basic",
                "messages": [],
            }
        )

    assert isinstance(result, IntentRecognitionResult)
    assert result.original_query == "Write a report: AI Agent\nhttps://example.com/a"
    assert result.research_query == "AI Agent trends"
    assert result.research_intent.section_count == 5
    assert result.research_intent.audience_role == "研发负责人"
    assert result.research_intent.tone == "analytical"
    assert result.research_intent.report_type == "professional"
    assert "https://example.com/a" in result.research_intent.include_url
    assert "example.com" in result.research_intent.include_domains


@pytest.mark.asyncio
async def test_recognize_report_intent_no_tool_calls_fallback():
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value={"tool_calls": [], "content": ""},
    ):
        q = "原始问题全文"
        result = await recognize_report_intent({"original_query": q, "llm_model_name": "basic"})

    assert result.original_query == q
    assert result.research_query == q
    assert result.research_intent == ResearchIntent()
    assert result.research_intent.report_type is None


@pytest.mark.asyncio
async def test_recognize_report_intent_exception_fallback():
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        side_effect=RuntimeError("llm down"),
    ):
        q = "fallback text"
        result = await recognize_report_intent({"original_query": q, "llm_model_name": "basic"})

    assert result.original_query == q
    assert result.research_query == q
    assert result.research_intent.section_count is None
    assert result.research_intent.report_type is None


@pytest.mark.asyncio
async def test_empty_original_query():
    result = await recognize_report_intent({"original_query": "", "llm_model_name": "basic"})
    assert result.original_query == ""
    assert result.research_query == ""
    assert result.research_intent == ResearchIntent()
    assert result.research_intent.report_type is None


@pytest.mark.asyncio
async def test_normalize_invalid_report_type_defaults_none():
    legacy_response = {
        "tool_calls": [
            {
                "name": "emit_report_intent",
                "args": {
                    "research_query": "topic",
                    "language": "zh-CN",
                    "report_type": "deep_research",
                },
                "id": "tc1",
                "type": "tool_call",
            }
        ],
        "content": "",
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=legacy_response,
    ):
        result = await recognize_report_intent(
            {"original_query": "深度研究 topic", "llm_model_name": "basic"}
        )

    assert result.research_intent.report_type is None


@pytest.mark.asyncio
async def test_normalize_invalid_section_count():
    bad_response = {
        "tool_calls": [
            {
                "args": {
                    "research_query": "topic",
                    "language": "zh-CN",
                    "section_count": -1,
                    "include_url": ["https://x.y/z"],
                },
            }
        ]
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=bad_response,
    ):
        result = await recognize_report_intent(
            {"original_query": "topic", "llm_model_name": "basic"}
        )

    assert result.research_intent.section_count is None
    assert "x.y" in result.research_intent.include_domains
    assert result.research_intent.report_type is None


@pytest.mark.asyncio
async def test_report_type_brief_is_preserved():
    brief_response = {
        "tool_calls": [
            {
                "name": "emit_report_intent",
                    "args": {
                        "research_query": "topic",
                        "language": "zh-CN",
                        "report_type": "brief",
                    },
                "id": "tc1",
                "type": "tool_call",
            }
        ],
        "content": "",
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=brief_response,
    ):
        result = await recognize_report_intent(
            {"original_query": "我要精简版", "llm_model_name": "basic"}
        )

    assert result.research_intent.report_type == "brief"


@pytest.mark.asyncio
async def test_report_type_remains_none_when_tool_omits_it():
    missing_type_response = {
        "tool_calls": [
            {
                "name": "emit_report_intent",
                "args": {
                    "research_query": "AI Agent 工程化落地趋势",
                    "language": "zh-CN",
                },
                "id": "tc1",
                "type": "tool_call",
            }
        ],
        "content": "",
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=missing_type_response,
    ):
        result = await recognize_report_intent(
            {
                "original_query": "请研究 AI Agent 工程化落地趋势",
                "llm_model_name": "basic",
                "messages": [
                    {"role": "assistant", "content": "Clarification questions:\n1. 你希望精简版还是专业版？"},
                    {"role": "user", "content": "希望本次报告是精简版"},
                ],
            }
        )

    assert result.research_intent.report_type is None


@pytest.mark.asyncio
async def test_classify_and_recognize_intent_research_request(sample_tool_response):
    """研究请求：LLM 返回 tool_call → 解析结构化参数。"""
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=sample_tool_response,
    ):
        result = await classify_and_recognize_intent(
            {
                "original_query": "帮我研究一下 AI Agent 趋势",
                "llm_model_name": "basic",
                "messages": [],
            }
        )

    assert isinstance(result, IntentRecognitionResult)
    assert result.original_query == "帮我研究一下 AI Agent 趋势"
    assert result.research_query == "AI Agent trends"
    assert result.research_intent.section_count == 5


@pytest.mark.asyncio
async def test_classify_and_recognize_intent_no_tool_calls_fallback():
    """LLM 不返回 tool_calls → fallback（所有查询均进入研究流程）。"""
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value={"tool_calls": [], "content": "This is a simple question answer."},
    ):
        q = "今天天气怎么样？"
        result = await classify_and_recognize_intent(
            {
                "original_query": q,
                "llm_model_name": "basic",
            }
        )

    assert isinstance(result, IntentRecognitionResult)
    assert result.original_query == q
    assert result.research_query == q


@pytest.mark.asyncio
async def test_classify_and_recognize_intent_exception_fallback():
    """异常兜底：LLM 调用抛异常 → 返回 _default_fallback 结果。"""
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM service unavailable"),
    ):
        q = "研究一下量子计算进展"
        result = await classify_and_recognize_intent(
            {"original_query": q, "llm_model_name": "basic"}
        )

    assert result.original_query == q
    assert result.research_query == q


@pytest.mark.asyncio
async def test_classify_and_recognize_intent_empty_query():
    """空查询：original_query 为空 → 直接返回 _default_fallback。"""
    result = await classify_and_recognize_intent({"original_query": ""})

    assert result.original_query == ""
    assert result.research_query == ""


@pytest.mark.asyncio
async def test_classify_and_recognize_intent_invalid_tool_args():
    """无效 tool_call args：args 为字符串而非 dict → 返回 _default_fallback。"""
    invalid_args_response = {
        "tool_calls": [
            {
                "name": "emit_report_intent",
                "args": "invalid_string_not_dict",
                "id": "tc1",
                "type": "tool_call",
            }
        ],
        "content": "",
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=invalid_args_response,
    ):
        q = "研究 AI Agent 的未来"
        result = await classify_and_recognize_intent(
            {"original_query": q, "llm_model_name": "basic"}
        )

    assert result.original_query == q
    assert result.research_query == q


# ──────────────────────────────────────────────
# normalize_research_query 测试
# ──────────────────────────────────────────────


def test_normalize_research_query_truncates_to_max_length():
    long_query = "研" * (MAX_RESEARCH_QUERY_LENGTH + 50)
    assert len(normalize_research_query(long_query)) == MAX_RESEARCH_QUERY_LENGTH


@pytest.mark.asyncio
async def test_recognize_report_intent_truncates_long_research_query():
    long_research_query = "x" * (MAX_RESEARCH_QUERY_LENGTH + 100)
    response = {
        "tool_calls": [
            {
                "name": "emit_report_intent",
                "args": {
                    "research_query": long_research_query,
                    "language": "zh-CN",
                },
                "id": "tc1",
                "type": "tool_call",
            }
        ],
        "content": "",
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=response,
    ):
        result = await recognize_report_intent(
            {"original_query": "原始问题", "llm_model_name": "basic"}
        )

    assert len(result.research_query) == MAX_RESEARCH_QUERY_LENGTH
    assert result.research_query == long_research_query[:MAX_RESEARCH_QUERY_LENGTH]


# ──────────────────────────────────────────────
# web_search_for_query 测试
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_search_success():
    """网页搜索成功返回结果"""
    mock_results = [{"title": "Test", "url": "https://example.com", "content": "Test content"}]

    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.run_web_search",
        new_callable=AsyncMock,
        return_value={"search_results": mock_results},
    ):
        result = await web_search_for_query({
            "query": "test query",
            "web_search_engine_name": "tavily",
        })

    assert result["search_results"] == mock_results
    assert result["error_msg"] == ""


@pytest.mark.asyncio
async def test_web_search_failure():
    """网页搜索异常处理"""
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.run_web_search",
        new_callable=AsyncMock,
        side_effect=Exception("Search engine unavailable"),
    ):
        result = await web_search_for_query({
            "query": "test query",
            "web_search_engine_name": "tavily",
        })

    assert result["search_results"] == []
    assert "Search engine unavailable" in result["error_msg"]


@pytest.mark.asyncio
async def test_web_search_default_engine():
    """web_search_engine_name 未传入时使用默认引擎 petal"""
    mock_results = [{"title": "Test", "url": "https://example.com", "content": "Test content"}]

    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.run_web_search",
        new_callable=AsyncMock,
        return_value={"search_results": mock_results},
    ) as mock_search:
        result = await web_search_for_query({"query": "test query"})

    assert result["search_results"] == mock_results
    assert result["error_msg"] == ""
    assert mock_search.call_args[0][1] == "petal"


@pytest.mark.asyncio
async def test_web_search_empty_engine_name_uses_default():
    """web_search_engine_name 为空字符串时，or 运算符兜底到 petal"""
    mock_results = [{"title": "Test", "url": "https://example.com", "content": "Test content"}]

    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.run_web_search",
        new_callable=AsyncMock,
        return_value={"search_results": mock_results},
    ) as mock_search:
        result = await web_search_for_query({
            "query": "test query",
            "web_search_engine_name": "",
        })

    assert result["search_results"] == mock_results
    assert mock_search.call_args[0][1] == "petal"


# ──────────────────────────────────────────────
# _to_str_list 测试
# ──────────────────────────────────────────────


def test_to_str_list_none():
    """None 输入返回空列表"""
    assert _to_str_list(None) == []


def test_to_str_list_list_passthrough():
    """列表直接返回"""
    assert _to_str_list(["a", "b"]) == ["a", "b"]


def test_to_str_list_empty_string():
    """空字符串返回空列表"""
    assert _to_str_list("") == []


def test_to_str_list_english_comma():
    """英文逗号分隔"""
    assert _to_str_list("成本,性能") == ["成本", "性能"]


def test_to_str_list_chinese_comma():
    """中文逗号分隔"""
    assert _to_str_list("成本，性能") == ["成本", "性能"]


def test_to_str_list_chinese_enumeration_comma():
    """顿号分隔"""
    assert _to_str_list("成本、性能、安全") == ["成本", "性能", "安全"]


def test_to_str_list_english_semicolon():
    """英文分号分隔"""
    assert _to_str_list("成本;性能") == ["成本", "性能"]


def test_to_str_list_chinese_semicolon():
    """中文分号分隔"""
    assert _to_str_list("成本；性能") == ["成本", "性能"]


def test_to_str_list_newline():
    """换行符分隔"""
    assert _to_str_list("成本\n性能") == ["成本", "性能"]


def test_to_str_list_mixed_separators():
    """混合分隔符"""
    assert _to_str_list("成本，性能、安全;可靠\n稳定") == ["成本", "性能", "安全", "可靠", "稳定"]


def test_to_str_list_extra_spaces_and_empty_items():
    """多余空格和空项被过滤"""
    assert _to_str_list("成本, , 性能") == ["成本", "性能"]


def test_to_str_list_non_list_str_none():
    """非 list/str/None 类型返回空列表"""
    assert _to_str_list(123) == []




@pytest.mark.asyncio
async def test_recognize_report_intent_exclude_url_and_domains_kept_separate():
    """exclude_url 与 exclude_domains 按 LLM 提取结果各自保留，互不派生。"""
    response = {
        "tool_calls": [
            {
                "name": "emit_report_intent",
                "args": {
                    "research_query": "topic",
                    "language": "zh-CN",
                    "exclude_url": [
                        "https://www.mdpi.com/2073-445X/11/9/1529",
                        "https://www.mdpi.com/2410-3888/8/2/80",
                    ],
                },
                "id": "tc1",
                "type": "tool_call",
            }
        ],
        "content": "",
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=response,
    ):
        result = await recognize_report_intent(
            {"original_query": "不要引用这两篇文章", "llm_model_name": "basic"}
        )

    assert len(result.research_intent.exclude_url) == 2
    # 关键断言：exclude_url 的域名不得被派生进 exclude_domains
    assert result.research_intent.exclude_domains == []


@pytest.mark.asyncio
async def test_recognize_report_intent_emits_exclude_intent_log(caplog):
    """exclude 字段非空时输出 [EXCLUDE_INTENT] 日志；为空时不输出。"""
    import logging

    response = {
        "tool_calls": [
            {
                "name": "emit_report_intent",
                "args": {
                    "research_query": "topic",
                    "language": "zh-CN",
                    "exclude_url": ["https://www.mdpi.com/2073-445X/11/9/1529"],
                },
                "id": "tc1",
                "type": "tool_call",
            }
        ],
        "content": "",
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=response,
    ):
        with caplog.at_level(logging.INFO):
            await recognize_report_intent({"original_query": "不要引用某文", "llm_model_name": "basic"})

    assert any("[EXCLUDE_INTENT]" in record.getMessage() for record in caplog.records)
    assert any("mdpi.com/2073-445X/11/9/1529" in record.getMessage() for record in caplog.records)


def _exclude_intent_tool_response(**extra_args):
    args = {
        "research_query": "topic",
        "language": "zh-CN",
        "exclude_url": ["https://www.mdpi.com/2073-445X/11/9/1529"],
    }
    args.update(extra_args)
    return {
        "tool_calls": [
            {"name": "emit_report_intent", "args": args, "id": "tc1", "type": "tool_call"}
        ],
        "content": "",
    }


def _patched_llm(response):
    return patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=response,
    )


@pytest.mark.asyncio
async def test_classify_and_recognize_intent_emits_exclude_intent_log(caplog):
    """主工作流路径 classify_and_recognize_intent 也应输出 [EXCLUDE_INTENT] 日志。"""
    import logging

    p1, p2 = _patched_llm(_exclude_intent_tool_response())
    with p1, p2:
        with caplog.at_level(logging.INFO):
            await classify_and_recognize_intent(
                {"original_query": "不要引用某文", "llm_model_name": "basic"})

    assert any("[EXCLUDE_INTENT]" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_exclude_intent_log_redacted_in_sensitive_mode(caplog):
    """敏感模式下 [EXCLUDE_INTENT] 只记录计数，不输出 URL/标题/query。"""
    import logging

    p1, p2 = _patched_llm(_exclude_intent_tool_response(
        exclude_titles=["Some Sensitive Paper Title"]))
    with p1, p2, patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.LogManager.is_sensitive",
        return_value=True,
    ):
        with caplog.at_level(logging.INFO):
            await recognize_report_intent(
                {"original_query": "不要引用某文", "llm_model_name": "basic"})

    messages = [record.getMessage() for record in caplog.records
                if "[EXCLUDE_INTENT]" in record.getMessage()]
    assert messages, "EXCLUDE_INTENT log missing"
    assert any("redacted" in m for m in messages)
    assert not any("mdpi.com" in m or "Sensitive Paper" in m or "不要引用某文" in m
                   for m in messages)
