"""双字段意图合并契约测试。

直接调 ``FeedbackHandlerNode._merge_reparsed_intent``（非逻辑复刻），覆盖
``main_graph_nodes.py`` 中合并段的两条新字段分支（``source_date_scope`` 与
``content_date_scope``）及 content_date 的 keep-existing 语义。
"""
from datetime import date
from unittest.mock import Mock

from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import (
    FeedbackHandlerNode,
)


def test_intent_merge_preserves_both_fields():
    """current 无 scope；incoming 同时带 source_date_scope 与 content_date_scope。

    合并段两条分支均应 populate：
      - ``if incoming_intent.source_date_scope is not None``（:543-544）
      - ``if incoming_intent.content_date_scope is not None``（:546-547）
    """
    session = Mock()
    session.get_global_state.return_value = {}  # current 无任何 scope
    node = FeedbackHandlerNode()

    merged = node._merge_reparsed_intent(
        session,
        {
            "research_intent": {
                "source_date_scope": {
                    "constraint_type": "source_date",
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                },
                "content_date_scope": {
                    "constraint_type": "content_date",
                    "start_date": "2020-01-01",
                    "end_date": "2022-12-31",
                },
            }
        },
    )

    assert merged["source_date_scope"]["start_date"] == date(2026, 1, 1)
    assert merged["source_date_scope"]["constraint_type"] == "source_date"
    assert merged["content_date_scope"]["start_date"] == date(2020, 1, 1)
    assert merged["content_date_scope"]["constraint_type"] == "content_date"


def test_merge_keeps_existing_content_date_when_reparse_has_none():
    """current 有 content_date_scope；incoming 无任何 scope → 合并保留 current 的 content_date_scope。

    覆盖 content_date 的 keep-existing 语义（incoming 字段为 None 时不覆写）。
    """
    session = Mock()
    session.get_global_state.return_value = {
        "content_date_scope": {
            "constraint_type": "content_date",
            "start_date": "2020-01-01",
            "end_date": "2022-12-31",
        }
    }
    node = FeedbackHandlerNode()

    merged = node._merge_reparsed_intent(
        session,
        {"research_intent": {}},  # incoming 无 scope
    )

    assert merged["content_date_scope"]["start_date"] == date(2020, 1, 1)
    assert merged["content_date_scope"]["constraint_type"] == "content_date"
