"""双活（dual-active）可观测断言。

端到端 in-process collector+report 流水线 + DRB-II macro-average 需活体 LLM/网络，
本仓无法运行；单题 ±12pp 噪声 + macro-only 意味着双约束
「同时生效」不应在分数上断言，而应在 **结构化日志同现** 上断言。本文件在 **单元
层级** 直接调用三个生产函数，验证双约束 ``ResearchIntent(source_date_scope=...,
content_date_scope=...)`` 下三条信号同现：

1. ``apply_web_search_temporal_scope`` (Tavily) 派发原生日期参数 —— 日志含
   ``apply_web_search_temporal_scope [...] native start_date=... end_date=...``
   （web_search.py）。
2. ``filter_web_records_by_temporal_scope`` 后置过滤 —— 日志含
   ``source_date filter applied. raw=... kept=... filtered_out=... date_unknown=...``
   （collector_function.py）。
3. ``_resolve_content_date_scope`` gate 为真 —— content_date_scope 在场即
   content_time 抽取使能（report.py:2301 ``extract_content_time`` 由
   ``content_date_scope is not None`` 决定）。

活体 e2e 同现 eyeball + DRB-II 全量 macro-average 回归为合并前人工 follow-up
（见 task-10-report.md），不在本文件覆盖。
"""

import logging
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from openjiuwen_deepsearch.algorithm.research_collector.collector_function import (
    filter_web_records_by_temporal_scope,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TemporalScope,
    _resolve_content_date_scope,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import (
    apply_web_search_temporal_scope,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import (
    web_search_context,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SOURCE_DATE_SCOPE = TemporalScope(
    constraint_type="source_date",
    start_date=date(2026, 1, 1),
    end_date=date(2026, 12, 31),
)
CONTENT_DATE_SCOPE = TemporalScope(
    constraint_type="content_date",
    start_date=date(2020, 1, 1),
    end_date=date(2022, 12, 31),
)


def _make_dual_intent() -> ResearchIntent:
    """2026 年发表的、关于 2020~2022 年疫情的回顾报道意图（双约束）。"""
    return ResearchIntent(
        source_date_scope=SOURCE_DATE_SCOPE,
        content_date_scope=CONTENT_DATE_SCOPE,
    )


def _make_source_only_intent() -> ResearchIntent:
    """仅来源时间约束：content_time 抽取不应使能。"""
    return ResearchIntent(source_date_scope=SOURCE_DATE_SCOPE)


def _filter_records() -> list[dict]:
    """归一化 web 文档样本：跨 source_date 边界 + 一条日期未知。

    期望（2026 年边界）：
      - 2026-06-01  in-range      -> kept
      - 2026-01-01  起始边界包含 -> kept
      - 2025-01-01  早于起始      -> filtered_out (before_start_date)
      - 2027-06-01  晚于结束      -> filtered_out (after_end_date)
      - {}          日期未知      -> kept (date_unknown)
    即 raw=5 kept=3 filtered_out=2 date_unknown=1。
    """
    return [
        {"date_metadata": {"parsed_date": "2026-06-01"}},
        {"date_metadata": {"parsed_date": "2026-01-01"}},
        {"date_metadata": {"parsed_date": "2025-01-01"}},
        {"date_metadata": {"parsed_date": "2027-06-01"}},
        {"date_metadata": {}},
    ]


def _find_record(caplog, fragment: str) -> logging.LogRecord:
    """返回第一条 message 含 fragment 的日志记录，否则断言失败。"""
    matches = [r for r in caplog.records if fragment in r.getMessage()]
    assert matches, f"expected a log record containing {fragment!r}; got {[r.getMessage() for r in caplog.records]}"
    return matches[0]


# ---------------------------------------------------------------------------
# Signal #2: filter_web_records_by_temporal_scope caplog
# ---------------------------------------------------------------------------


def test_signal2_filter_logs_source_date_counts(caplog):
    """后置过滤函数为纯函数，可直接单测：日志的 raw/kept/filtered_out/date_unknown
    计数须与样本一致。"""
    caplog.set_level(logging.INFO, logger="openjiuwen_deepsearch.algorithm.research_collector.collector_function")

    records = _filter_records()
    kept = filter_web_records_by_temporal_scope(records, SOURCE_DATE_SCOPE)

    # 行为正确性：3 条保留（2 in-range + 1 unknown）
    assert len(kept) == 3
    # 信号 #2 日志同现且计数精确
    rec = _find_record(caplog, "source_date filter applied")
    msg = rec.getMessage()
    assert "raw=5" in msg
    assert "kept=3" in msg
    assert "filtered_out=2" in msg
    assert "date_unknown=1" in msg


# ---------------------------------------------------------------------------
# Signal #3: _resolve_content_date_scope gate
# ---------------------------------------------------------------------------


def test_signal3_content_time_gate_dual_vs_source_only():
    """content_date_scope 在场 → gate True（抽取使能）；仅 source_date → gate None。"""
    dual = _make_dual_intent()
    source_only = _make_source_only_intent()

    # 双约束：content_date gate 开
    cds = _resolve_content_date_scope(dual)
    assert cds is not None
    assert cds.constraint_type == "content_date"
    assert cds.start_date == date(2020, 1, 1)
    assert cds.end_date == date(2022, 12, 31)

    # 仅 source_date：content_date gate 关（抽取不使能）
    assert _resolve_content_date_scope(source_only) is None


# ---------------------------------------------------------------------------
# Signal #1: apply_web_search_temporal_scope (Tavily) native date dispatch
# ---------------------------------------------------------------------------


def test_signal1_apply_web_search_temporal_scope_tavily(caplog):
    """``web_search_context`` 为普通 ContextVar，可经 ``.set()`` 注入 Tavily wrapper
    后直接调用：返回 True、wrapper.start_date/end_date 按 §5.4 内包含→严格
    after/before 各外推一天写入、且日志同现。"""
    caplog.set_level(logging.INFO, logger="openjiuwen_deepsearch.framework.openjiuwen.tools.web_search")

    # 注入 mock Tavily wrapper（ContextVar 无 default，生产代码捕获 LookupError 兜底）
    wrapper = SimpleNamespace(start_date=None, end_date=None)
    token = web_search_context.set({"tavily": wrapper})
    try:
        ok = apply_web_search_temporal_scope("tavily", SOURCE_DATE_SCOPE)
    finally:
        web_search_context.reset(token)

    # 派发成功
    assert ok is True
    # §5.4：内包含（2026-01-01 起）→ 严格 after 外推一天 = 2025-12-31；
    #        内包含（2026-12-31 止）→ 严格 before 外推一天 = 2027-01-01。
    assert wrapper.start_date == (SOURCE_DATE_SCOPE.start_date - timedelta(days=1)).isoformat()
    assert wrapper.end_date == (SOURCE_DATE_SCOPE.end_date + timedelta(days=1)).isoformat()
    assert wrapper.start_date == "2025-12-31"
    assert wrapper.end_date == "2027-01-01"

    # 信号 #1 日志同现，含 constraint_type 与 native 边界
    rec = _find_record(caplog, "apply_web_search_temporal_scope")
    msg = rec.getMessage()
    assert "constraint_type=source_date" in msg
    assert "start_date=2025-12-31" in msg
    assert "end_date=2027-01-01" in msg


def test_signal1_apply_returns_false_for_unsupported_engine():
    """非 Tavily 引擎不在 TEMPORAL_SCOPE_SEARCH_ENGINES：返回 False，无原生派发
    （§6 多引擎边界）。content_time 抽取仍独立由信号 #3 决定。"""
    token = web_search_context.set({"google_serper": SimpleNamespace()})
    try:
        ok = apply_web_search_temporal_scope("google_serper", SOURCE_DATE_SCOPE)
    finally:
        web_search_context.reset(token)
    assert ok is False


# ---------------------------------------------------------------------------
# Co-presence: dual-active contract — signals #1+#2+#3 同现
# ---------------------------------------------------------------------------


def test_dual_constraint_signals_co_present(caplog):
    """给定双约束 intent，三条信号同现：Tavily 原生派发(#1) + 后置过滤日志(#2) +
    content_date gate True(#3)。这是双活契约的单元级断言。"""
    caplog.set_level(logging.INFO, logger="openjiuwen_deepsearch.framework.openjiuwen.tools.web_search")
    caplog.set_level(logging.INFO, logger="openjiuwen_deepsearch.algorithm.research_collector.collector_function")

    intent = _make_dual_intent()

    # --- 信号 #3：content_date gate（双约束必须在场） ---
    content_scope = _resolve_content_date_scope(intent)
    assert content_scope is not None, "dual intent must carry a content_date_scope (gate True)"

    # --- 信号 #1：Tavily 原生日期派发（source_date 边界外推一天） ---
    wrapper = SimpleNamespace(start_date=None, end_date=None)
    token = web_search_context.set({"tavily": wrapper})
    try:
        scope_ok = apply_web_search_temporal_scope("tavily", intent.source_date_scope)
    finally:
        web_search_context.reset(token)
    assert scope_ok is True
    assert wrapper.start_date == "2025-12-31"
    assert wrapper.end_date == "2027-01-01"

    # --- 信号 #2：后置过滤日志计数 ---
    records = _filter_records()
    kept = filter_web_records_by_temporal_scope(records, intent.source_date_scope)
    assert len(kept) == 3

    # --- 同现断言：两条结构化日志都在 caplog ---
    _find_record(caplog, "apply_web_search_temporal_scope")
    filter_rec = _find_record(caplog, "source_date filter applied")
    fmsg = filter_rec.getMessage()
    assert "raw=5" in fmsg and "kept=3" in fmsg and "filtered_out=2" in fmsg and "date_unknown=1" in fmsg
