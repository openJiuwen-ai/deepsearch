"""时间约束强化 v2 ③:报告选材软着陆时效排序的单元测试。

覆盖:
- _resolve_temporal_scope:从 research_intent 提取并归一化时间边界与
  constraint_type 三元组;
- _compute_doc_temporal_status:多来源日期合并 + 状态/分数映射,
  含 content_date "只奖不罚"(violation 中性化为 0 分);
- _optimize_document_set 的时效项(有界、同分决胜)与唯一覆盖豁免;
- temporal_scope 关闭(None)时行为与引入前完全一致;
- _log_temporal_distribution 观测日志。
全部为纯算法路径,0 LLM 调用。
"""

import logging
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.report.config import TEMPORAL_TIMELINESS_WEIGHT
from openjiuwen_deepsearch.algorithm.report.report import (
    Reporter,
    _compute_doc_temporal_status,
    _log_temporal_distribution,
    _resolve_temporal_scope,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import TemporalScope


# ---------- Fixtures ----------

def _make_reporter():
    with patch.object(Reporter, "__init__", lambda self, name: None):
        reporter = Reporter.__new__(Reporter)
        reporter._llm = MagicMock()
        return reporter


def _doc(idx, title=None, url=None, publish_time=None, date_info=None):
    doc = {
        "title": title or f"doc-{idx}",
        "url": url or f"https://example.com/page/{idx}",
        "original_content": f"content-{idx}",
        "key_passages": [f"passage-{idx}"],
    }
    if publish_time is not None:
        doc["publish_time"] = publish_time
    if date_info is not None:
        doc["date_info"] = date_info
    return doc


def _rationale(rid, desc):
    return {"id": rid, "description": desc, "type": "factual"}


def _coverage_result(docs, matrix, reliability=None, noise=None):
    return {
        "filtered_docs": docs,
        "coverage_matrix": matrix,
        "reliability_scores": reliability or {},
        "noise_scores": noise or {},
    }


SCOPE_2024 = (date(2024, 1, 1), date(2024, 12, 31))


# ---------- _resolve_temporal_scope ----------

def test_resolve_temporal_scope_absent():
    assert _resolve_temporal_scope({}) is None
    assert _resolve_temporal_scope({"research_intent": {}}) is None
    assert _resolve_temporal_scope({"research_intent": {"temporal_scope": None}}) is None


def test_resolve_temporal_scope_dict_with_date_objects():
    current_inputs = {
        "research_intent": {
            "temporal_scope": {
                "constraint_type": "source_date",
                "start_date": date(2024, 1, 1),
                "end_date": date(2024, 12, 31),
            }
        }
    }
    assert _resolve_temporal_scope(current_inputs) == (date(2024, 1, 1), date(2024, 12, 31), "source_date")


def test_resolve_temporal_scope_dict_with_iso_strings():
    """经 JSON 往返后边界可能是 ISO 字符串,需归一化为 date。"""
    current_inputs = {
        "research_intent": {
            "temporal_scope": {
                "constraint_type": "content_date",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            }
        }
    }
    assert _resolve_temporal_scope(current_inputs) == (
        date(2024, 1, 1), date(2024, 12, 31), "content_date",
    )


def test_resolve_temporal_scope_model_object():
    scope = TemporalScope(
        constraint_type="source_date",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
    )
    assert _resolve_temporal_scope({"research_intent": {"temporal_scope": scope}}) == (
        date(2024, 1, 1), date(2024, 12, 31), "source_date",
    )


def test_resolve_temporal_scope_invalid_constraint_type_normalized_to_none():
    """constraint_type 取值非法时归一为 None(按 source_date 语义处理)。"""
    current_inputs = {
        "research_intent": {
            "temporal_scope": {"constraint_type": "bogus",
                               "start_date": "2024-01-01", "end_date": "2024-12-31"}
        }
    }
    assert _resolve_temporal_scope(current_inputs) == (
        date(2024, 1, 1), date(2024, 12, 31), None,
    )


def test_resolve_temporal_scope_open_boundary():
    current_inputs = {
        "research_intent": {
            "temporal_scope": {"constraint_type": "source_date",
                               "start_date": "2024-01-01", "end_date": None}
        }
    }
    assert _resolve_temporal_scope(current_inputs) == (date(2024, 1, 1), None, "source_date")


def test_resolve_temporal_scope_no_boundaries_returns_none():
    current_inputs = {
        "research_intent": {
            "temporal_scope": {"constraint_type": "source_date",
                               "start_date": None, "end_date": None}
        }
    }
    assert _resolve_temporal_scope(current_inputs) is None


# ---------- _compute_doc_temporal_status ----------

def test_compute_status_none_scope_short_circuits():
    """temporal_scope 为 None:早退返回 unknown/0.0,即使字段是垃圾也不解析。"""
    doc = {"publish_time": object(), "url": None, "date_info": "garbage"}
    assert _compute_doc_temporal_status(doc, None) == ("unknown", 0.0)


def test_compute_status_publish_time_high_confidence_compliant():
    doc = _doc(0, publish_time="2024-03-15")
    assert _compute_doc_temporal_status(doc, SCOPE_2024) == ("compliant", 1.0)


def test_compute_status_publish_time_high_confidence_violation():
    doc = _doc(0, publish_time="2023-06-01")
    assert _compute_doc_temporal_status(doc, SCOPE_2024) == ("violation", -1.0)


def test_compute_status_url_fallback_medium_confidence():
    """publish_time 占位文本不可用时,URL 日期模式兜底(medium)。"""
    doc = _doc(0, url="https://example.com/news/2024/03/15/story",
               publish_time="未提供时间信息")
    assert _compute_doc_temporal_status(doc, SCOPE_2024) == ("compliant", 0.5)


def test_compute_status_date_info_used_with_stored_confidence():
    """date_info 直接使用,置信度以存储值为准(low → compliant +0.5)。"""
    doc = _doc(
        0,
        date_info={"date": "2024-05-01", "granularity": "month",
                   "confidence": "low", "source": "llm_inferred"},
    )
    assert _compute_doc_temporal_status(doc, SCOPE_2024) == ("compliant", 0.5)


def test_compute_status_no_date_info_is_unknown():
    doc = _doc(0, publish_time="未提供时间信息")
    assert _compute_doc_temporal_status(doc, SCOPE_2024) == ("unknown", 0.0)


def test_compute_status_same_rank_conflict_degrades_to_unknown():
    """同档(high)来源日期矛盾 → merge_doc_dates 降级 unknown,不猜。"""
    doc = _doc(
        0,
        publish_time="2024-03-15",
        date_info={"date": "2023-01-01", "granularity": "day",
                   "confidence": "high", "source": "html_meta:article:published_time"},
    )
    assert _compute_doc_temporal_status(doc, SCOPE_2024) == ("unknown", 0.0)


def test_compute_status_coarse_granularity_overlap_is_unknown():
    """粒度不足导致区间跨边界(content_date 常见形态)→ unknown 0 分,中性。"""
    doc = _doc(0, publish_time="2024")  # 年粒度:[2024-01-01, 2024-12-31]
    scope = (date(2024, 6, 1), None)
    assert _compute_doc_temporal_status(doc, scope) == ("unknown", 0.0)


# ---------- content_date "只奖不罚"(v2 ③修正) ----------

def test_compute_status_content_date_violation_is_neutral():
    """content_date:发布/写作时间不是事实时间的有效证据,
    violation 惩罚是语义错误 → 分数强制 0.0(状态仍如实为 violation)。"""
    doc = _doc(0, publish_time="2023-06-01")  # high 置信 violation
    assert _compute_doc_temporal_status(doc, SCOPE_2024, "content_date") == ("violation", 0.0)


def test_compute_status_content_date_violation_medium_confidence_is_neutral():
    """content_date:中置信(URL 兜底)violation 同样中性化。"""
    doc = _doc(0, url="https://example.com/news/2023/06/01/story",
               publish_time="未提供时间信息")
    assert _compute_doc_temporal_status(doc, SCOPE_2024, "content_date") == ("violation", 0.0)


def test_compute_status_content_date_compliant_keeps_reward():
    """content_date:compliant 奖励保留(高置信 +1.0 / 低置信 +0.5)。"""
    doc = _doc(0, publish_time="2024-03-15")
    assert _compute_doc_temporal_status(doc, SCOPE_2024, "content_date") == ("compliant", 1.0)
    doc_low = _doc(
        0,
        date_info={"date": "2024-05-01", "granularity": "month",
                   "confidence": "low", "source": "llm_inferred"},
    )
    assert _compute_doc_temporal_status(doc_low, SCOPE_2024, "content_date") == ("compliant", 0.5)


def test_compute_status_source_date_violation_keeps_penalty():
    """source_date:violation 惩罚维持不变(高置信 -1.0)。"""
    doc = _doc(0, publish_time="2023-06-01")
    assert _compute_doc_temporal_status(doc, SCOPE_2024, "source_date") == ("violation", -1.0)


# ---------- _optimize_document_set 时效项 ----------

def test_temporal_term_breaks_ties():
    """覆盖完全相同时,时效分决定选择顺序(同分决胜)。"""
    reporter = _make_reporter()
    docs = [_doc(0, "old"), _doc(1, "new")]
    rationales = [_rationale("r1", "a"), _rationale("r2", "b")]
    matrix = {"doc_0": {"r1": 0.5}, "doc_1": {"r2": 0.5}}
    coverage = _coverage_result(
        docs, matrix, reliability={"doc_0": 1.0, "doc_1": 1.0},
    )

    selected, values = reporter._optimize_document_set(
        docs, rationales, coverage, top_k=2,
        temporal_scores=[-0.5, 1.0],
    )

    assert [d["title"] for d in selected] == ["new", "old"]
    assert values[0] == pytest.approx(0.5 + TEMPORAL_TIMELINESS_WEIGHT * 1.0)


def test_temporal_term_is_bounded_by_coverage_gain():
    """时效项(最大 ±w_t)不能盖过真实覆盖差异。"""
    reporter = _make_reporter()
    docs = [_doc(0, "high-coverage-violation"), _doc(1, "low-coverage-compliant")]
    rationales = [_rationale("r1", "a"), _rationale("r2", "b")]
    matrix = {"doc_0": {"r1": 0.9}, "doc_1": {"r2": 0.1}}
    coverage = _coverage_result(
        docs, matrix, reliability={"doc_0": 1.0, "doc_1": 1.0},
    )

    selected, _ = reporter._optimize_document_set(
        docs, rationales, coverage, top_k=2,
        temporal_scores=[-1.0, 1.0],
    )

    # 0.9 - w_t ≈ 0.75 > 0.1 + w_t ≈ 0.25:覆盖差异 0.8 远大于时效项振幅 0.3
    assert selected[0]["title"] == "high-coverage-violation"


def test_unique_coverage_exemption_keeps_sole_source():
    """唯一覆盖来源即使边际价值(含时效惩罚)<= 0 也必须保留。"""
    reporter = _make_reporter()
    docs = [
        {"title": "main-a", "url": "https://example.com/a",
         "original_content": "出口总额同比增长", "key_passages": ["p"]},
        {"title": "main-b", "url": "https://example.com/b",
         "original_content": "目的国结构分析", "key_passages": ["p"]},
        {"title": "sole-r2-source", "url": "https://example.com/c",
         "original_content": "关税壁垒案例", "key_passages": ["p"]},
    ]
    rationales = [_rationale("r1", "a"), _rationale("r2", "b")]
    matrix = {
        "doc_0": {"r1": 0.9},
        "doc_1": {"r1": 0.9},
        "doc_2": {"r2": 0.5},  # r2 的唯一有效覆盖(>= 0.3)
    }
    coverage = _coverage_result(
        docs, matrix,
        reliability={"doc_0": 1.0, "doc_1": 1.0, "doc_2": 0.0},
        noise={"doc_2": 1.0},
    )
    # doc_2 无时效边际价值 = 0.5 - 0.3*1.0 - 0.2*1.0 = 0.0(不 > 0 → 停止);
    # 含时效惩罚 = 0.0 + 0.15*(-1.0) - 冗余 < 0,但豁免强制保留
    selected, values = reporter._optimize_document_set(
        docs, rationales, coverage, top_k=5,
        temporal_scores=[0.0, 0.0, -1.0],
    )

    selected_titles = [d["title"] for d in selected]
    assert "sole-r2-source" in selected_titles
    # 记录真实边际价值(负值,含 n-gram 冗余,不粉饰为正值)
    assert values[selected_titles.index("sole-r2-source")] < 0.0


def test_unique_coverage_exemption_not_applied_without_temporal():
    """temporal_scores 为 None 时豁免不生效,保持引入前行为。"""
    reporter = _make_reporter()
    docs = [_doc(0, "main-a"), _doc(1, "main-b"), _doc(2, "sole-r2-source")]
    rationales = [_rationale("r1", "a"), _rationale("r2", "b")]
    matrix = {
        "doc_0": {"r1": 0.9},
        "doc_1": {"r1": 0.9},
        "doc_2": {"r2": 0.5},
    }
    coverage = _coverage_result(
        docs, matrix,
        reliability={"doc_0": 1.0, "doc_1": 1.0, "doc_2": 0.0},
        noise={"doc_2": 1.0},
    )
    # doc_2 边际价值 = 0.5 - 0.3*1.0 - 0.2*1.0 = 0.0(不 > 0 → 停止),旧行为

    selected, _ = reporter._optimize_document_set(docs, rationales, coverage, top_k=5)

    assert "sole-r2-source" not in [d["title"] for d in selected]


def test_none_temporal_scores_byte_identical_behavior():
    """temporal_scores=None 与不传该参数逐字节一致(选中集与边际价值完全相同)。"""
    reporter = _make_reporter()
    docs = [_doc(i, f"export data {i}") for i in range(6)]
    rationales = [_rationale("r1", "export"), _rationale("r2", "destination")]
    matrix = {
        "doc_0": {"r1": 0.9, "r2": 0.1},
        "doc_1": {"r1": 0.8, "r2": 0.2},
        "doc_2": {"r1": 0.1, "r2": 0.8},
        "doc_3": {"r1": 0.05, "r2": 0.05},
        "doc_4": {"r1": 0.0, "r2": 0.0},
        "doc_5": {"r1": 0.0, "r2": 0.0},
    }
    coverage = _coverage_result(
        docs, matrix,
        reliability={f"doc_{i}": 0.8 for i in range(6)},
        noise={"doc_3": 0.5},
    )

    baseline = reporter._optimize_document_set(docs, rationales, coverage, top_k=5)
    explicit_none = reporter._optimize_document_set(
        docs, rationales, coverage, top_k=5, temporal_scores=None,
    )

    assert [d["title"] for d in baseline[0]] == [d["title"] for d in explicit_none[0]]
    assert baseline[1] == explicit_none[1]


# ---------- _log_temporal_distribution ----------

def test_log_temporal_distribution(caplog):
    logger_name = "openjiuwen_deepsearch.algorithm.report.report"
    with caplog.at_level(logging.INFO, logger=logger_name):
        _log_temporal_distribution(
            3, ["compliant", "unknown", "unknown", "violation"]
        )
    assert any(
        "[temporal]" in record.message
        and "compliant 25.0%" in record.message
        and "unknown 50.0%" in record.message
        and "violation 25.0%" in record.message
        for record in caplog.records
    )


def test_log_temporal_distribution_empty_no_log(caplog):
    logger_name = "openjiuwen_deepsearch.algorithm.report.report"
    with caplog.at_level(logging.INFO, logger=logger_name):
        _log_temporal_distribution(3, [])
    assert not any("[temporal]" in record.message for record in caplog.records)
