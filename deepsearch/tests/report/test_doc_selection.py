"""Tests for document selection methods in report.py.

Covers: _select_by_rationale_coverage, _verify_coverage,
_extract_and_score_documents. These are pure-algorithm or
mock-LLM methods.
"""

from unittest.mock import patch, MagicMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context


# ---------- Fixtures ----------

def _make_reporter():
    """Create a Reporter with a mock LLM (not used by algorithmic methods)."""
    with patch.object(Reporter, "__init__", lambda self, name: None):
        reporter = Reporter.__new__(Reporter)
        reporter._llm = MagicMock()
        return reporter


def _doc(idx, title=None, url=None):
    return {
        "doc_title": title or f"doc-{idx}",
        "doc_url": url or f"https://example.com/{idx}",
        "passage_text": f"passage-{idx}",
    }


def _rationale(rid, desc):
    return {"id": rid, "description": desc, "type": "factual"}


def _coverage_result(docs, matrix, reliability=None, noise=None):
    return {
        "filtered_docs": docs,
        "coverage_matrix": matrix,
        "reliability_scores": reliability or {},
        "noise_scores": noise or {},
    }


# ---------- _select_by_rationale_coverage ----------

def test_select_by_rationale_selects_high_coverage_docs_first():
    """每个 rationale 按 coverage 分数降序选择 top_k 个文档。"""
    reporter = _make_reporter()
    docs = [_doc(0, "出口数据"), _doc(1, "目的国分析"), _doc(2, "无关内容")]
    rationales = [_rationale("r1", "出口数据"), _rationale("r2", "目的国")]
    matrix = {
        "doc_0": {"r1": 0.9, "r2": 0.1},
        "doc_1": {"r1": 0.1, "r2": 0.9},
        "doc_2": {"r1": 0.05, "r2": 0.05},
    }
    coverage = _coverage_result(docs, matrix)

    selected, values = reporter._select_by_rationale_coverage(docs, rationales, coverage, top_k=5)

    assert len(selected) >= 2
    # doc_0 and doc_1 should be selected (highest coverage for r1 and r2)
    selected_titles = [d["doc_title"] for d in selected]
    assert "出口数据" in selected_titles
    assert "目的国分析" in selected_titles


def test_select_by_rationale_respects_top_k_limit():
    """每个 rationale 最多选择 top_k 个文档。"""
    reporter = _make_reporter()
    docs = [_doc(i) for i in range(15)]
    rationales = [_rationale("r1", "common topic")]
    matrix = {f"doc_{i}": {"r1": 0.5} for i in range(15)}
    coverage = _coverage_result(docs, matrix)

    selected, _ = reporter._select_by_rationale_coverage(docs, rationales, coverage, top_k=3)

    # 1 rationale × top_k=3 = at most 3 docs
    assert len(selected) <= 3


def test_select_by_rationale_deduplicates_across_rationales():
    """同一文档被多个 rationale 选中时只保留一次(按对象身份去重)。"""
    reporter = _make_reporter()
    docs = [_doc(0, "共享文档"), _doc(1, "r1专属"), _doc(2, "r2专属")]
    rationales = [_rationale("r1", "主题1"), _rationale("r2", "主题2")]
    # doc_0 对两个 rationale 都有最高分
    matrix = {
        "doc_0": {"r1": 0.9, "r2": 0.9},
        "doc_1": {"r1": 0.5, "r2": 0.1},
        "doc_2": {"r1": 0.1, "r2": 0.5},
    }
    coverage = _coverage_result(docs, matrix)

    selected, values = reporter._select_by_rationale_coverage(docs, rationales, coverage, top_k=2)

    # doc_0 只出现一次(去重), doc_1 和 doc_2 各被一个 rationale 选中
    selected_ids = [id(d) for d in selected]
    assert len(selected_ids) == len(set(selected_ids))  # 无重复
    # doc_0 应在结果中
    assert any(d["doc_title"] == "共享文档" for d in selected)


def test_select_by_rationale_empty_docs():
    """空文档列表应返回空结果。"""
    reporter = _make_reporter()
    selected, values = reporter._select_by_rationale_coverage([], [], {}, top_k=5)
    assert selected == []
    assert values == []


def test_select_by_rationale_empty_rationales():
    """空 rationale 列表应返回空结果。"""
    reporter = _make_reporter()
    docs = [_doc(0), _doc(1)]
    matrix = {"doc_0": {"r1": 0.5}, "doc_1": {"r1": 0.5}}
    coverage = _coverage_result(docs, matrix)

    selected, values = reporter._select_by_rationale_coverage(docs, [], coverage, top_k=5)
    assert selected == []
    assert values == []


def test_select_by_rationale_marginal_values_are_best_scores():
    """marginal_values 应为每个选中文档的最佳 coverage 分数。"""
    reporter = _make_reporter()
    docs = [_doc(0), _doc(1)]
    rationales = [_rationale("r1", "test")]
    matrix = {"doc_0": {"r1": 0.8}, "doc_1": {"r1": 0.6}}
    coverage = _coverage_result(docs, matrix)

    selected, values = reporter._select_by_rationale_coverage(docs, rationales, coverage, top_k=5)

    assert len(selected) == 2
    # doc_0 has higher score, should be selected first with 0.8
    assert values[0] == 0.8
    assert values[1] == 0.6


def test_select_by_rationale_skips_zero_score_docs():
    """coverage 分数为 0 的文档仍会被选中(top_k 内), 因为不按分数阈值过滤。"""
    reporter = _make_reporter()
    docs = [_doc(0), _doc(1)]
    rationales = [_rationale("r1", "test")]
    matrix = {"doc_0": {"r1": 0.0}, "doc_1": {"r1": 0.0}}
    coverage = _coverage_result(docs, matrix)

    selected, values = reporter._select_by_rationale_coverage(docs, rationales, coverage, top_k=5)

    # 新实现不按分数阈值过滤, 0 分文档也会被选中
    assert len(selected) == 2
    assert all(v == 0.0 for v in values)


# ---------- _verify_coverage ----------

def test_verify_coverage_all_covered():
    reporter = _make_reporter()
    docs = [_doc(0, "出口"), _doc(1, "目的国")]
    rationales = [_rationale("r1", "出口数据"), _rationale("r2", "目的国")]
    matrix = {
        "doc_0": {"r1": 0.8, "r2": 0.1},
        "doc_1": {"r1": 0.1, "r2": 0.8},
    }
    coverage = _coverage_result(docs, matrix)

    result = reporter._verify_coverage(docs, rationales, coverage, section_idx=1)

    assert len(result["uncovered_rationales"]) == 0
    assert result["coverage_rate"] == 1.0
    assert result["limitations"] == []


def test_verify_coverage_some_uncovered():
    reporter = _make_reporter()
    docs = [_doc(0, "出口")]
    rationales = [_rationale("r1", "出口"), _rationale("r2", "目的国")]
    matrix = {"doc_0": {"r1": 0.9, "r2": 0.1}}
    coverage = _coverage_result(docs, matrix)

    result = reporter._verify_coverage(docs, rationales, coverage, section_idx=1)

    assert len(result["uncovered_rationales"]) == 1
    assert result["uncovered_rationales"][0]["id"] == "r2"
    assert result["coverage_rate"] == 0.5
    assert len(result["limitations"]) == 1
    assert "目的国" in result["limitations"][0]


def test_verify_coverage_weak_coverage():
    reporter = _make_reporter()
    docs = [_doc(0, "partial")]
    rationales = [_rationale("r1", "partial")]
    matrix = {"doc_0": {"r1": 0.4}}  # between 0.3 and 0.6 = weak
    coverage = _coverage_result(docs, matrix)

    result = reporter._verify_coverage(docs, rationales, coverage, section_idx=1)

    # 0.4 is in weak range [0.3, 0.6), not uncovered (< 0.3)
    assert len(result["weak_rationales"]) == 1
    assert len(result["uncovered_rationales"]) == 0
    assert result["coverage_rate"] == 1.0


def test_verify_coverage_empty_rationales():
    reporter = _make_reporter()
    result = reporter._verify_coverage([], [], {}, section_idx=1)
    assert result["coverage_rate"] == 1.0
    assert result["uncovered_rationales"] == []


def test_verify_coverage_empty_matrix():
    reporter = _make_reporter()
    docs = [_doc(0)]
    rationales = [_rationale("r1", "test")]
    coverage = _coverage_result(docs, {})

    result = reporter._verify_coverage(docs, rationales, coverage, section_idx=1)

    assert len(result["uncovered_rationales"]) == 1
    assert result["coverage_rate"] == 0.0


def test_verify_coverage_only_checks_selected_docs():
    """A rationale covered only by a non-selected doc should be 'uncovered'."""
    reporter = _make_reporter()
    all_docs = [_doc(0, "出口"), _doc(1, "目的国")]
    selected = [all_docs[0]]  # only doc 0 selected, doc 1 not in report
    rationales = [_rationale("r1", "出口数据"), _rationale("r2", "目的国")]
    matrix = {
        "doc_0": {"r1": 0.9, "r2": 0.1},
        "doc_1": {"r1": 0.1, "r2": 0.8},  # doc_1 covers r2 but is NOT selected
    }
    coverage = _coverage_result(all_docs, matrix)

    result = reporter._verify_coverage(selected, rationales, coverage, section_idx=1)

    # r2 should be uncovered because doc_1 (which covers r2) was not selected
    assert len(result["uncovered_rationales"]) == 1
    assert result["uncovered_rationales"][0]["id"] == "r2"
    assert result["coverage_rate"] == 0.5


def test_select_by_rationale_tolerates_float_coverage_entries():
    """_select_by_rationale_coverage must not crash when coverage_matrix contains
    a float value for some doc_key (defensive guard for malformed LLM output)."""
    reporter = _make_reporter()
    docs = [_doc(0, "good"), _doc(1, "malformed"), _doc(2, "zero")]
    rationales = [_rationale("r1", "topic")]
    # doc_1 maps to a float instead of a dict — must be treated as 0.0 coverage.
    matrix = {
        "doc_0": {"r1": 0.9},
        "doc_1": 0.8,      # malformed float
        "doc_2": {"r1": 0.0},
    }
    coverage = _coverage_result(docs, matrix)

    selected, values = reporter._select_by_rationale_coverage(docs, rationales, coverage, top_k=5)

    # No crash; doc_1 treated as 0.0 so it sorts last; all 3 still selected (top_k=5).
    assert len(selected) == 3
    titles = [d["doc_title"] for d in selected]
    assert "good" in titles
    assert "malformed" in titles


def test_verify_coverage_tolerates_float_coverage_entries():
    """_verify_coverage must not crash when coverage_matrix contains a float value."""
    reporter = _make_reporter()
    docs = [_doc(0, "good"), _doc(1, "malformed")]
    rationales = [_rationale("r1", "topic")]
    matrix = {
        "doc_0": {"r1": 0.9},
        "doc_1": 0.8,  # malformed float
    }
    coverage = _coverage_result(docs, matrix)

    result = reporter._verify_coverage(docs, rationales, coverage, section_idx=1)

    # No crash; r1 covered by doc_0 (0.9 >= 0.6).
    assert result["coverage_rate"] == 1.0
    assert len(result["uncovered_rationales"]) == 0


# ---------- _extract_and_score_documents (extractive summarization) ----------

def _raw_doc(idx, title=None, url=None, content=None):
    """Create a raw doc-level dict (before passage splitting)."""
    return {
        "doc_id": f"doc-{idx}",
        "title": title or f"article-{idx}",
        "url": url or f"https://example.com/{idx}",
        "source": "example.com",
        "publish_time": "2025-01-01",
        "doc_time": "2025-01-01",
        "original_content": content or f"This is the full content of article {idx}. " * 20,
    }


def _make_extract_llm_output(passages_by_doc):
    """Build mock LLM output for extractive summarization.

    Args:
        passages_by_doc: list of lists, each inner list is
            [{"text": "...", "rationale_ids": ["r1"], "scores": {"r1": {"coverage": 0.9, "reliability": 0.8, "analysis": 0.7, "presentation": 0.6}}}]
    """
    documents = []
    for doc_idx, passages in enumerate(passages_by_doc):
        documents.append({"doc_index": doc_idx, "passages": passages})
    return {"documents": documents}


@pytest.mark.asyncio
async def test_extract_and_score_produces_passages():
    """_extract_and_score_documents returns passage-level dicts with correct fields and weighted scores."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, "风险度量", content="持有56-73只股票的建议来自TWSD模型。")]
    rationales = [_rationale("r1", "风险度量方法")]

    # New 4-dimension scoring format with total_score
    mock_llm_result = MagicMock()
    mock_llm_result.get.return_value = '{"documents": [{"doc_index": 0, "passages": [{"text": "持有56-73只股票的建议来自TWSD模型。", "rationale_ids": ["r1"], "scores": {"r1": {"coverage": 1.0, "reliability": 0.8, "analysis": 0.5, "presentation": 0.7, "total_score": 0.88}}}]}]}'

    with patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.report.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, rationales,
            )

    assert error == ""
    assert "filtered_docs" in result
    assert "coverage_matrix" in result
    assert len(result["filtered_docs"]) >= 1
    # Check passage-level fields
    passage = result["filtered_docs"][0]
    assert "doc_url" in passage
    assert "doc_title" in passage
    assert "passage_text" in passage
    assert passage["passage_text"] == "持有56-73只股票的建议来自TWSD模型。"
    # Check coverage_matrix uses total_score from LLM output
    assert "doc_0" in result["coverage_matrix"]
    assert abs(result["coverage_matrix"]["doc_0"].get("r1") - 0.88) < 0.001


@pytest.mark.asyncio
async def test_extract_and_score_degrades_on_total_failure():
    """When all batches fail, degrades to original docs as passages (truncated to 500 chars)."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, content="A" * 1000)]

    with patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", side_effect=Exception("LLM down")):
        with patch("openjiuwen_deepsearch.algorithm.report.report.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    # Degraded: original docs as passages
    assert len(result["filtered_docs"]) >= 1
    assert len(result["filtered_docs"][0]["passage_text"]) <= 500
    assert result["coverage_matrix"] == {}  # no scores in degraded path
    assert "LLM" in error


@pytest.mark.asyncio
async def test_extract_and_score_skips_malformed_scores():
    """Non-numeric score values are treated as 0.0 (no crash)."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, content="test content")]

    # scores with bad values in dimensions
    mock_llm_result = MagicMock()
    mock_llm_result.get.return_value = '{"documents": [{"doc_index": 0, "passages": [{"text": "test", "rationale_ids": ["r1"], "scores": {"r1": {"coverage": "bad", "reliability": null, "analysis": 0.5, "presentation": 0.6}}}]}]}'

    with patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.report.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    assert error == ""
    assert "doc_0" in result["coverage_matrix"]
    # coverage=0.0(bad) + reliability=0.0(null) + analysis=0.5 + presentation=0.6
    # 0.6*0 + 0.2*0 + 0.1*0.5 + 0.1*0.6 = 0.11
    assert abs(result["coverage_matrix"]["doc_0"]["r1"] - 0.11) < 0.001


@pytest.mark.asyncio
async def test_extract_and_score_preserves_parent_doc_metadata():
    """Each extracted passage inherits doc_url/doc_title/source from its parent document."""
    reporter = _make_reporter()
    raw_docs = [_raw_doc(0, title="Investment Guide", url="https://finance.example.com/1",
                          content="Diversification requires 30-50 stocks.")]

    mock_llm_result = MagicMock()
    mock_llm_result.get.return_value = '{"documents": [{"doc_index": 0, "passages": [{"text": "Diversification requires 30-50 stocks.", "rationale_ids": ["r1"], "scores": {"r1": {"coverage": 0.9, "reliability": 0.8, "analysis": 0.6, "presentation": 0.7}}}]}]}'

    with patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.report.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "diversification")],
            )

    assert error == ""
    passage = result["filtered_docs"][0]
    assert passage["doc_url"] == "https://finance.example.com/1"
    assert passage["doc_title"] == "Investment Guide"
    assert passage["source"] == "example.com"


# ---------- _select_by_rationale_coverage: count fix ----------

def test_select_by_rationale_count_per_rationale_not_blocked_by_seen():
    """每个 rationale 独立获得 top_k 个新文档，已见文档不消耗该 rationale 的配额。

    场景：2 个 rationale，top_k=2。
    - doc_0 对两个 rationale 都最高分
    - doc_1 对 r1 次高分
    - doc_2 对 r2 次高分
    - doc_3 对 r2 第三高分

    r1 选中 doc_0, doc_1（2 个新文档 = top_k）。
    r2 跳过已见的 doc_0，仍应选中 doc_2, doc_3（2 个新文档 = top_k），
    而不是因为 doc_0 已见消耗一次迭代而只选到 doc_2。
    """
    reporter = _make_reporter()
    docs = [
        _doc(0, "共享文档"),
        _doc(1, "r1专属"),
        _doc(2, "r2专属"),
        _doc(3, "r2第三"),
    ]
    rationales = [_rationale("r1", "主题1"), _rationale("r2", "主题2")]
    matrix = {
        "doc_0": {"r1": 0.9, "r2": 0.9},
        "doc_1": {"r1": 0.8, "r2": 0.1},
        "doc_2": {"r1": 0.1, "r2": 0.8},
        "doc_3": {"r1": 0.05, "r2": 0.7},
    }
    coverage = _coverage_result(docs, matrix)

    selected, values = reporter._select_by_rationale_coverage(docs, rationales, coverage, top_k=2)

    selected_titles = [d["doc_title"] for d in selected]
    # r1 选中 doc_0(共享) + doc_1(r1专属)
    assert "共享文档" in selected_titles
    assert "r1专属" in selected_titles
    # r2 跳过已见的 doc_0，仍应选中 doc_2 + doc_3
    assert "r2专属" in selected_titles
    assert "r2第三" in selected_titles
    # 总共 4 个新文档
    assert len(selected) == 4


# ---------- _extract_and_score_documents: content truncation ----------

@pytest.mark.asyncio
async def test_extract_and_score_truncates_long_content():
    """_extract_batch 和 degraded path 应将超过 15000 字符的内容截断。"""
    from openjiuwen_deepsearch.algorithm.report.report import MAX_EXTRACT_DOC_CHARS

    reporter = _make_reporter()
    long_content = "X" * 20000  # > 15000 chars
    raw_docs = [_raw_doc(0, "长文档", content=long_content)]

    mock_llm_result = MagicMock()
    mock_llm_result.get.return_value = '{"documents": [{"doc_index": 0, "passages": [{"text": "truncated content", "rationale_ids": ["r1"], "scores": {"r1": {"coverage": 0.5, "reliability": 0.5, "analysis": 0.5, "presentation": 0.5}}}]}]}'

    captured_content = {}

    def capture_apply_system_prompt(name, ctx):
        # Capture the user content sent to LLM
        messages = ctx.get("messages", [])
        if messages:
            captured_content["text"] = messages[0].get("content", "")
        return [{"role": "user", "content": "test"}]

    with patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", return_value=mock_llm_result):
        with patch("openjiuwen_deepsearch.algorithm.report.report.apply_system_prompt", side_effect=capture_apply_system_prompt):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    assert error == ""
    # Verify the content sent to LLM was truncated
    assert "text" in captured_content
    sent_text = captured_content["text"]
    # The content portion should not exceed MAX_EXTRACT_DOC_CHARS
    # Find the content after "Content: " in the document block
    content_start = sent_text.find("Content: ")
    assert content_start != -1, "Document content should be present in LLM input"
    content_part = sent_text[content_start + len("Content: "):]
    # The content is followed by "\n\n" and trailing instruction text, so split there
    doc_content = content_part.split("\n\n")[0]
    # The content should be truncated to exactly MAX_EXTRACT_DOC_CHARS
    assert len(doc_content) == MAX_EXTRACT_DOC_CHARS


@pytest.mark.asyncio
async def test_extract_and_score_degraded_truncates_long_content():
    """Degraded path 也应将超过 15000 字符的内容截断到 MAX_EXTRACT_DOC_CHARS。"""
    from openjiuwen_deepsearch.algorithm.report.report import MAX_EXTRACT_DOC_CHARS

    reporter = _make_reporter()
    long_content = "Y" * 20000  # > 15000 chars
    raw_docs = [_raw_doc(0, "降级长文档", content=long_content)]

    with patch("openjiuwen_deepsearch.algorithm.report.report.ainvoke_llm_with_stats", side_effect=Exception("LLM down")):
        with patch("openjiuwen_deepsearch.algorithm.report.report.apply_system_prompt", side_effect=lambda name, ctx: [{"role": "user", "content": "test"}]):
            result, error = await reporter._extract_and_score_documents(
                {"section_idx": 1, "section_task": "test", "section_description": "",
                 "max_generate_retry_num": 1},
                raw_docs, [_rationale("r1", "test")],
            )

    assert len(result["filtered_docs"]) >= 1
    passage_text = result["filtered_docs"][0]["passage_text"]
    # Degraded path truncates to MAX_EXTRACT_DOC_CHARS (not 500)
    assert len(passage_text) <= MAX_EXTRACT_DOC_CHARS
