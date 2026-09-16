"""查询边界注入产双指令。

验证 ``build_temporal_scope_prompt_context`` 在双约束（source_date + content_date）
下同时产出两条指令，并在兼容拼接字段 ``temporal_scope_instruction`` 中合并；同时
覆盖 content_date-only 始终写搜索词、source_date + Tavily 不写搜索词的 embed 决策。
"""
from datetime import date

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TemporalScope,
    build_temporal_scope_prompt_context,
)


def test_dual_constraint_emits_both_instructions():
    ri = ResearchIntent(
        source_date_scope=TemporalScope(
            constraint_type="source_date",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        ),
        content_date_scope=TemporalScope(
            constraint_type="content_date",
            start_date=date(2020, 1, 1),
            end_date=date(2022, 12, 31),
        ),
    )
    ctx = build_temporal_scope_prompt_context(
        ri, engine_name="tavily", scholarly_enabled=False
    )
    assert ctx["has_temporal_scope"] is True
    assert "published" in ctx["source_date_instruction"]
    assert "facts and data" in ctx["content_date_instruction"]
    assert ctx["temporal_scope_instruction"] == (
        ctx["source_date_instruction"] + " " + ctx["content_date_instruction"]
    )


def test_content_date_only_still_embeds():
    ri = ResearchIntent(
        content_date_scope=TemporalScope(
            constraint_type="content_date",
            start_date=date(2020, 1, 1),
            end_date=date(2022, 12, 31),
        )
    )
    ctx = build_temporal_scope_prompt_context(ri, engine_name="tavily")
    assert ctx["temporal_embed_in_query"] is True  # content_date 始终写搜索词
    assert ctx["source_date_instruction"] == ""


def test_source_date_tavily_no_embed():
    ri = ResearchIntent(
        source_date_scope=TemporalScope(
            constraint_type="source_date",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
    )
    ctx = build_temporal_scope_prompt_context(ri, engine_name="tavily")
    assert ctx["temporal_embed_in_query"] is False  # Tavily 原生过滤


def test_no_constraint_returns_same_six_key_shape():
    ctx = build_temporal_scope_prompt_context(ResearchIntent())
    assert ctx == {
        "has_temporal_scope": False,
        "source_date_instruction": "",
        "content_date_instruction": "",
        "temporal_scope_instruction": "",
        "temporal_embed_in_query": False,
        "temporal_query_instruction": "",
    }
