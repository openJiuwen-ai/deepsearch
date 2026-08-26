# -*- coding: UTF-8 -*-
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
    IntentRecognitionResult,
    MAX_RESEARCH_QUERY_LENGTH,
    _create_emit_intent_tool,
    _default_fallback,
    _normalize_research_intent,
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
                    "temporal_scope": {
                        "constraint_type": "source_date",
                        "end_date": "2024-03-31",
                    },
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
    assert result.research_intent.temporal_scope.end_date.isoformat() == "2024-03-31"


def test_emit_report_intent_tool_uses_basic_temporal_scope_schema():
    """意图识别工具的时间范围 schema 只使用基础关键字。"""
    tool = _create_emit_intent_tool()
    temporal_schema = tool.card.input_params["properties"]["temporal_scope"]

    assert temporal_schema["type"] == "object"
    assert temporal_schema["properties"]["constraint_type"]["enum"] == [
        "source_date",
        "content_date",
    ]
    assert temporal_schema["properties"]["start_date"]["format"] == "date"
    assert temporal_schema["properties"]["end_date"]["format"] == "date"
    assert "anyOf" not in temporal_schema
    assert "oneOf" not in temporal_schema


def test_target_paper_url_is_preserved_and_added_to_include_url():
    intent = _normalize_research_intent({
        "target_papers": [{"url": "https://journal.example.org/article/42"}],
    })

    assert intent.target_papers[0].url == "https://journal.example.org/article/42"
    assert intent.include_url == ["https://journal.example.org/article/42"]


def test_normalize_intent_identifies_arxiv_id_from_target_paper_url():
    intent = _normalize_research_intent({
        "target_papers": [{"url": "https://arxiv.org/abs/1706.03762v7"}],
    })

    assert intent.target_papers[0].url == "https://arxiv.org/abs/1706.03762v7"
    assert intent.target_papers[0].arxiv_id == "1706.03762"

    tool = _create_emit_intent_tool()
    assert "url" in tool.card.input_params["properties"]["target_papers"]["items"]["properties"]


def test_normalize_target_papers_merges_canonical_arxiv_duplicates():
    intent = _normalize_research_intent({
        "target_papers": [
            {
                "title": "Attention Is All You Need",
                "arxiv_id": "1706.03762v7",
            },
            {
                "arxiv_id": "1706.03762",
                "url": "https://arxiv.org/abs/1706.03762v7",
            },
        ],
    })

    assert [paper.model_dump(exclude_defaults=True) for paper in intent.target_papers] == [{
        "title": "Attention Is All You Need",
        "arxiv_id": "1706.03762",
        "url": "https://arxiv.org/abs/1706.03762v7",
    }]


@pytest.mark.parametrize("prompt_name", ["intent_recognition.md", "intent_recognition_entry.md"])
def test_intent_prompts_require_paper_urls_in_both_intent_chains(prompt_name):
    prompt = (Path("openjiuwen_deepsearch/algorithm/prompts") / prompt_name).read_text(encoding="utf-8")

    assert "target_papers" in prompt
    assert "include_url" in prompt
    assert "paper URL" in prompt


def test_normalize_target_papers_drops_empty_items_and_deduplicates():
    intent = _normalize_research_intent({
        "target_papers": [
            {"pmid": " 38202877 ", "title": "Paper"},
            {"pmid": "38202877", "title": "Paper"},
            {},
            "invalid",
            {"dataset": "MEPS", "data_year": 2019, "topic": "orthodontic treatment"},
            {"dataset": "MEPS", "data_year": "2019", "topic": "orthodontic treatment"},
        ]
    })

    assert [paper.model_dump() for paper in intent.target_papers] == [
        {
                "title": "Paper", "pmid": "38202877", "doi": "", "arxiv_id": "", "url": "",
            "dataset": "", "data_year": "", "topic": "",
        },
        {
                "title": "", "pmid": "", "doi": "", "arxiv_id": "", "url": "",
            "dataset": "MEPS", "data_year": "2019", "topic": "orthodontic treatment",
        },
    ]


def test_emit_intent_tool_declares_target_papers_without_search_terms():
    schema = _create_emit_intent_tool().card.input_params
    target_schema = schema["properties"]["target_papers"]

    assert target_schema["type"] == "array"
    assert set(target_schema["items"]["properties"]) == {
        "title", "pmid", "doi", "arxiv_id", "url", "dataset", "data_year", "topic",
    }
    assert "search_terms" not in target_schema["items"]["properties"]


@pytest.mark.parametrize("prompt_name", ["intent_recognition.md", "intent_recognition_entry.md"])
def test_intent_prompt_defines_target_paper_contract(prompt_name):
    prompt = (Path("openjiuwen_deepsearch/algorithm/prompts") / prompt_name).read_text(encoding="utf-8")

    assert "target_papers" in prompt
    assert all(identifier in prompt for identifier in ("PMID", "DOI", "arXiv ID"))
    assert all(clue in prompt for clue in ("dataset", "data year", "topic"))
    assert "Do not invent" in prompt
    assert "search_terms" in prompt
    assert "not temporal_scope" in prompt


@pytest.mark.parametrize("prompt_name", ["intent_recognition.md", "intent_recognition_entry.md"])
def test_intent_prompt_defines_temporal_normalization_rules(prompt_name):
    """两个意图 Prompt 必须使用一致的模糊日期与包含边界规则。"""
    prompt = (Path("openjiuwen_deepsearch/algorithm/prompts") / prompt_name).read_text(encoding="utf-8")

    assert "March 31" in prompt
    assert "June 30" in prompt
    assert "December 31" in prompt
    assert "previous year" in prompt
    assert "previous month" in prompt
    assert "inclusive" in prompt


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
    assert "is_fallback" not in result.model_dump()


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
    assert "is_fallback" not in result.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("请分析 PMID 38132429 对应论文", {"pmid": "38132429"}),
        ("请分析 DOI: 10.1000/Example.1", {"doi": "10.1000/Example.1"}),
        ("请分析 arXiv: 1706.03762v7", {"arxiv_id": "1706.03762v7"}),
    ],
)
async def test_recognize_report_intent_fallback_preserves_explicit_paper_identifier(query, expected):
    """Intent failures must not discard explicit paper constraints."""
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        side_effect=RuntimeError("llm down"),
    ):
        result = await recognize_report_intent({"original_query": query, "llm_model_name": "basic"})

    assert [paper.model_dump(exclude_defaults=True) for paper in result.research_intent.target_papers] == [expected]


@pytest.mark.asyncio
async def test_successful_intent_merges_explicit_target_paper_omitted_by_llm():
    response = {
        "tool_calls": [{
            "name": "emit_report_intent",
            "args": {
                "research_query": "Transformer architecture",
                "language": "zh-CN",
                "target_papers": [],
            },
        }],
    }
    query = "请使用 https://arxiv.org/abs/1706.03762v7 这篇论文"
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": Mock()},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=response,
    ):
        result = await recognize_report_intent({"original_query": query, "llm_model_name": "basic"})

    assert [paper.model_dump(exclude_defaults=True) for paper in result.research_intent.target_papers] == [{
        "url": "https://arxiv.org/abs/1706.03762v7",
        "arxiv_id": "1706.03762",
    }]
    assert "https://arxiv.org/abs/1706.03762v7" in result.research_intent.include_url
    assert "arxiv.org" not in result.research_intent.include_domains


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("请分析 PMID 38132429 对应论文", {"pmid": "38132429"}),
        ("请分析 DOI: 10.1000/Example.1", {"doi": "10.1000/Example.1"}),
        ("请分析 arXiv: 1706.03762v7", {"arxiv_id": "1706.03762v7"}),
    ],
)
def test_default_fallback_preserves_explicit_paper_identifier(query, expected):
    result = _default_fallback(query)

    assert [paper.model_dump(exclude_defaults=True) for paper in result.research_intent.target_papers] == [expected]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "请使用 https://pubmed.ncbi.nlm.nih.gov/38132429/ 这篇论文",
            {"url": "https://pubmed.ncbi.nlm.nih.gov/38132429/", "pmid": "38132429"},
        ),
        (
            "请使用 https://arxiv.org/abs/1706.03762v7 这篇论文",
            {"url": "https://arxiv.org/abs/1706.03762v7", "arxiv_id": "1706.03762"},
        ),
    ],
)
def test_default_fallback_preserves_and_identifies_academic_paper_url(query, expected):
    result = _default_fallback(query)

    assert [paper.model_dump(exclude_defaults=True) for paper in result.research_intent.target_papers] == [expected]


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


def test_emit_intent_tool_schema_hides_report_type_when_provided():
    """API 已指定 report_type 时，tool schema 移除该字段（硬约束）。"""
    tool = _create_emit_intent_tool(provided_report_type="brief")
    assert "report_type" not in tool.card.input_params["properties"]

    tool_default = _create_emit_intent_tool()
    assert "report_type" in tool_default.card.input_params["properties"]

    tool_none = _create_emit_intent_tool(provided_report_type=None)
    assert "report_type" in tool_none.card.input_params["properties"]


def test_intent_prompts_suppress_report_type_when_provided():
    """两个意图识别 prompt：provided 时完全不渲染 report_type 相关内容；缺省保持现状。"""
    base_ctx = {"original_query": "AI Agent 趋势", "messages": []}
    for prompt_name in ("intent_recognition_entry", "intent_recognition"):
        provided = apply_system_prompt(prompt_name, {**base_ctx, "provided_report_type": "brief"})
        content = provided[0]["content"]
        assert "report_type" not in content

        default = apply_system_prompt(prompt_name, dict(base_ctx))
        default_content = default[0]["content"]
        assert "emit `report_type` accordingly" in default_content


@pytest.mark.asyncio
async def test_classify_and_recognize_intent_passes_provided_report_type(sample_tool_response):
    """入口函数从 current_inputs 读取 provided_report_type 并注入 prompt 与 tool。"""
    mock_llm = Mock()
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": mock_llm},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=sample_tool_response,
    ) as mock_invoke:
        await classify_and_recognize_intent({
            "original_query": "AI Agent 趋势",
            "llm_model_name": "basic",
            "messages": [],
            "provided_report_type": "brief",
        })

    prompts = mock_invoke.call_args.args[1]
    assert "report_type" not in prompts[0]["content"]


@pytest.mark.asyncio
async def test_recognize_report_intent_passes_provided_report_type(sample_tool_response):
    """反馈重解析入口同样透传 provided_report_type。"""
    mock_llm = Mock()
    with patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_context",
        return_value={"basic": mock_llm},
    ), patch(
        "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition.llm_utils.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
        return_value=sample_tool_response,
    ) as mock_invoke:
        await recognize_report_intent({
            "original_query": "AI Agent 趋势",
            "llm_model_name": "basic",
            "messages": [],
            "provided_report_type": "professional",
        })

    prompts = mock_invoke.call_args.args[1]
    assert "report_type" not in prompts[0]["content"]
