import datetime

from openjiuwen_deepsearch.algorithm.prompts.template import get_prompt_section
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    TemporalScope,
    build_temporal_scope_prompt_context,
)


def _content_date_intent() -> ResearchIntent:
    return ResearchIntent(
        temporal_scope=TemporalScope(
            constraint_type="content_date",
            start_date=datetime.date(2018, 1, 1),
            end_date=datetime.date(2023, 12, 31),
        )
    )


def _render_sub_report(**ctx) -> str:
    return get_prompt_section("sub_report_markdown", ctx)


def test_temporal_scope_instruction_rendered_for_content_date():
    ctx = build_temporal_scope_prompt_context(_content_date_intent())
    assert ctx["has_temporal_scope"] is True
    assert "facts and data" in ctx["temporal_scope_instruction"]
    assert "2018-01-01" in ctx["temporal_scope_instruction"]


def test_no_temporal_scope_yields_empty():
    ctx = build_temporal_scope_prompt_context(None)
    assert ctx["has_temporal_scope"] is False
    assert ctx["temporal_scope_instruction"] == ""


# --- temporal_query_instruction 按 constraint_type 分流（content_date 指事实时间，source_date 指发表时间）---

def _scope_intent(constraint_type, **scope_kwargs):
    return ResearchIntent(
        temporal_scope=TemporalScope(constraint_type=constraint_type, **scope_kwargs)
    )


def test_query_instruction_content_date_points_to_event_time():
    """content_date 的引导词指向事情发生时间，且明示不是发表时间。"""
    intent = _scope_intent(
        "content_date",
        start_date=datetime.date(2018, 1, 1),
        end_date=datetime.date(2023, 12, 31),
    )
    ctx = build_temporal_scope_prompt_context(intent)  # 默认 engine=None → embed True
    assert ctx["temporal_embed_in_query"] is True
    instruction = ctx["temporal_query_instruction"]
    assert "Express this boundary naturally in every query as a constraint time phrase" in instruction
    assert "facts/events" in instruction
    assert "not the publication date" in instruction


def test_query_instruction_source_date_points_to_publication_time():
    """source_date（非原生过滤引擎）的引导词指向发表时间。"""
    intent = _scope_intent(
        "source_date",
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 12, 31),
    )
    # engine_name=None 不在原生过滤引擎集 → embed True，走 source_date 文案
    ctx = build_temporal_scope_prompt_context(intent)
    assert ctx["temporal_embed_in_query"] is True
    instruction = ctx["temporal_query_instruction"]
    assert "Express this boundary naturally in every query as a constraint time phrase" in instruction
    assert "tied to the publication date" in instruction
    assert "facts/events" not in instruction


def test_query_instruction_source_date_tavily_omits_constraint_phrase():
    """source_date + Tavily 原生过滤：引导词让 LLM 不带约束时间词。"""
    intent = _scope_intent("source_date", end_date=datetime.date(2023, 12, 31))
    ctx = build_temporal_scope_prompt_context(intent, engine_name="tavily")
    assert ctx["temporal_embed_in_query"] is False
    instruction = ctx["temporal_query_instruction"]
    assert "Do NOT add any constraint time phrase" in instruction
    assert "the engine filters by date natively" in instruction
    assert "Express this boundary naturally" not in instruction


def test_sub_report_prompt_renders_temporal_scope_for_content_date():
    ctx = build_temporal_scope_prompt_context(_content_date_intent())
    rendered = _render_sub_report(
        has_temporal_scope=ctx["has_temporal_scope"],
        temporal_scope_instruction=ctx["temporal_scope_instruction"],
        CURRENT_TIME="fixed now",
    )
    # The temporal_scope_instruction is surfaced verbatim in the rendered prompt.
    assert "from 2018-01-01 through 2023-12-31" in rendered
    # content_date branch judges by the content's facts time, not publication time.
    assert "content's facts time" in rendered
    # Empty-time evidence is softened, not dropped.
    assert "lower the assertion strength" in rendered


def test_sub_report_prompt_renders_no_scope_fallback():
    ctx = build_temporal_scope_prompt_context(None)
    rendered = _render_sub_report(
        has_temporal_scope=ctx["has_temporal_scope"],
        temporal_scope_instruction=ctx["temporal_scope_instruction"],
        CURRENT_TIME="fixed now",
    )
    assert "No explicit time boundary" in rendered
    assert "fixed now" in rendered


# --- content_time surfaced in writing citation blocks (Task 6) ---

def _passage_item(content_time):
    """Build a passage-level classified_content item for infos rendering tests."""
    return {
        "index": 1,
        "doc_time": "2024-05-01",
        "content_time": content_time,
        "title": "t",
        "passage_text": "c",
        "scores": {},
    }


def test_build_citation_infos_includes_content_time_for_content_date():
    from openjiuwen_deepsearch.algorithm.report.report import build_citation_infos

    item = _passage_item({"start": "2019-01-01", "end": "2019-12-31"})
    infos = build_citation_infos([item])
    # content_date scenario: the citation block surfaces content_time (fact time).
    assert "content_time: 2019-01-01~2019-12-31" in infos
    # content_time must sit right after the publication time, before source.
    assert "publish_time: 2024-05-01|||content_time: 2019-01-01~2019-12-31|||source:" in infos


def test_build_citation_infos_excludes_content_time_for_source_date():
    from openjiuwen_deepsearch.algorithm.report.report import build_citation_infos

    # source_date scenario: content_time is None — no content_time field rendered.
    item = _passage_item(None)
    infos = build_citation_infos([item])
    assert "content_time:" not in infos
    # publication time is still present.
    assert "publish_time: 2024-05-01|||source:" in infos


def test_build_citation_infos_omits_content_time_without_start():
    from openjiuwen_deepsearch.algorithm.report.report import build_citation_infos

    # Malformed content_time (missing start) must not render a partial field.
    item = _passage_item({"start": "", "end": "2019-12-31"})
    infos = build_citation_infos([item])
    assert "content_time:" not in infos


def test_build_classified_content_propagates_content_time():
    from openjiuwen_deepsearch.algorithm.report.report_rationale_fulltext import (
        FullTextEvidence,
        build_classified_content,
    )

    fulltext = [
        FullTextEvidence(
            url="http://a",
            doc_title="FT",
            doc_time="2024-01-01",
            original_content="fulltext body",
        )
    ]
    passages = [
        {
            "doc_url": "http://b",
            "doc_title": "P",
            "doc_time": "2024-05-01",
            "passage_text": "passage",
            "original_content": "passage",
            "content_time": {"start": "2019-01-01", "end": "2019-12-31"},
            "scores": {},
        }
    ]
    classified = build_classified_content(fulltext, passages)
    # fulltext item (index 1): whole document has no single fact time -> None.
    assert classified[0].get("content_time") is None
    # passage item (index 2): content_time propagated from the passage dict.
    assert classified[1].get("content_time") == {
        "start": "2019-01-01",
        "end": "2019-12-31",
    }
