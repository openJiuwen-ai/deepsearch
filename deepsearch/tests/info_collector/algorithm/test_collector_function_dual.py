"""collector 后置过滤改读 source_date_scope 的契约测试。

验证 ``filter_web_records_by_temporal_scope`` 接受 ``TemporalScope`` 作为
source_date 约束的契约：越界文档被过滤、日期未知文档被保留。
"""

from datetime import date

from openjiuwen_deepsearch.algorithm.research_collector.collector_function import (
    filter_web_records_by_temporal_scope,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    TemporalScope,
)


def test_filter_uses_source_date_scope():
    """source_date 约束按来源发表日期过滤：越界丢、未知留。"""
    scope = TemporalScope(
        constraint_type="source_date",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )
    records = [
        {"date_metadata": {"parsed_date": "2026-06-01"}},
        {"date_metadata": {"parsed_date": "2025-01-01"}},  # 越界过滤
        {"date_metadata": {}},  # 未知保留
    ]
    kept = filter_web_records_by_temporal_scope(records, scope)
    assert len(kept) == 2
