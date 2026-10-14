"""Brief 主链用户停止（StopRequested）传播的行为测试。

实机事故(2026-10-14):brief 分支全部节点的 ``except Exception`` 把
StopRequested 吞成"正常失败收尾"(next_node=END),图引擎判"正常完成"
清除 checkpoint;算法层 retry/降级/gather 分流同样吞停止导致空转或
降级正常返回。本文件逐点验证停止必须原样上抛——上抛后由图的异常
保存分支落盘快照,resume 时从被停止节点续跑。
"""

from unittest.mock import AsyncMock

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent import brief_nodes
from openjiuwen_deepsearch.framework.openjiuwen.agent.brief_nodes import (
    BriefEvidenceReviewNode,
    BriefHtmlReporterNode,
    BriefInfoCollectorNode,
    BriefOutlineNode,
    BriefReporterNode,
    BriefSubReporterNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.stop_control import StopRequested
from openjiuwen_deepsearch.algorithm.brief_report.collector import generate_brief_queries
from openjiuwen_deepsearch.algorithm.brief_report.evaluation import evaluate_brief_sections
from openjiuwen_deepsearch.algorithm.brief_report.html_content import BriefHtmlSectionChunk
from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import _generate_section_fragments
from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefCitationRecord,
    BriefChapter,
    BriefCollectionResult,
    BriefOutline,
    BriefOutlineRequest,
    BriefQueryRequest,
    BriefSearchResult,
    BriefSectionEvidence,
    BriefSummaryRequest,
    BriefWritingRequest,
    BriefWorkflowState,
)
from openjiuwen_deepsearch.algorithm.brief_report.outline import generate_brief_outline
from openjiuwen_deepsearch.algorithm.brief_report.review import review_brief_evidence
from openjiuwen_deepsearch.algorithm.brief_report.models import BriefReviewRequest
from openjiuwen_deepsearch.algorithm.brief_report.writer import (
    generate_brief_summary,
    write_brief_chapters,
)

_STOP = StopRequested("deepresearch task stopped by user")


def _outline() -> BriefOutline:
    return BriefOutline.model_validate(
        {
            "title": "停止传播测试",
            "sections": [
                {
                    "id": "1",
                    "title": "范围",
                    "goal": "验证研究范围",
                    "research_steps": [
                        {"id": "1-1", "requirement": "验证数据"},
                        {"id": "1-2", "requirement": "验证差异"},
                    ],
                },
                {
                    "id": "2",
                    "title": "结论",
                    "goal": "形成研究结论",
                    "research_steps": [
                        {"id": "2-1", "requirement": "验证结论"},
                        {"id": "2-2", "requirement": "验证风险"},
                    ],
                },
            ],
        }
    )


def _collection() -> BriefCollectionResult:
    return BriefCollectionResult(
        section_evidence={
            "1": BriefSectionEvidence.model_validate(
                {
                    "coverage": [
                        {
                            "step_id": "1-1",
                            "status": "covered",
                            "reason": "已有数据",
                        }
                    ]
                }
            )
        },
        citation_registry=[
            BriefCitationRecord(
                source_id="s1",
                index=1,
                title="数据来源",
                url="https://example.com/data",
                original_content="证据原文",
            )
        ],
    )


def _search_result() -> BriefSearchResult:
    return BriefSearchResult(
        source_id="s1",
        title="数据来源",
        url="https://example.com/data",
        snippet="证据摘要",
        search_rank=1,
        section_ids=["1"],
    )


# ---------------------------------------------------------------------------
# 算法层：retry 循环 / 降级 / gather 分流不得吞停止
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outline_stop_propagates_without_retry(monkeypatch):
    """大纲生成中停止必须立即上抛，不得空转重试预算。"""
    invoke = AsyncMock(side_effect=_STOP)
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.outline.ainvoke_llm_with_stats", invoke
    )
    with pytest.raises(StopRequested):
        await generate_brief_outline(object(), BriefOutlineRequest(query="测试主题"))
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_collector_stop_propagates_without_retry(monkeypatch):
    """Query 生成中停止必须立即上抛，不得空转重试预算。"""
    invoke = AsyncMock(side_effect=_STOP)
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.collector.ainvoke_llm_with_stats", invoke
    )
    with pytest.raises(StopRequested):
        await generate_brief_queries(
            object(),
            BriefQueryRequest(outline=_outline(), user_query="测试主题"),
        )
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_review_stop_propagates_instead_of_fallback(monkeypatch):
    """证据审阅中停止必须上抛，不得降级为确定性 fallback 的正常返回。"""
    invoke = AsyncMock(side_effect=_STOP)
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.review.ainvoke_llm_with_stats", invoke
    )
    request = BriefReviewRequest(
        outline=_outline(),
        collection=_collection(),
        llm=object(),
        audience_role="",
        tone="",
        user_format="",
    )
    with pytest.raises(StopRequested):
        await review_brief_evidence(request)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_chapter_writing_stop_propagates_from_retry(monkeypatch):
    """章节写作单章 retry 中停止必须上抛，不得按章节失败继续。"""
    invoke = AsyncMock(side_effect=_STOP)
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke
    )
    with pytest.raises(StopRequested):
        await write_brief_chapters(
            BriefWritingRequest(
                llm=object(),
                outline=_outline(),
                collection=_collection(),
            )
        )


@pytest.mark.asyncio
async def test_summary_stop_propagates_instead_of_fallback(monkeypatch):
    """核心摘要生成中停止必须上抛，不得降级为 fallback 摘要正常返回。"""
    invoke = AsyncMock(side_effect=_STOP)
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke
    )
    with pytest.raises(StopRequested):
        await generate_brief_summary(
            BriefSummaryRequest(
                llm=object(),
                title="停止传播测试",
                language="zh-CN",
                chapters=[BriefChapter(section_id="1", raw_markdown="章节内容")],
                section_evidence={},
                citation_registry=[],
            )
        )


@pytest.mark.asyncio
async def test_evaluation_stop_propagates_instead_of_fallback(monkeypatch):
    """分片评估中停止必须上抛，不得降级为规则评估的正常返回。"""
    invoke = AsyncMock(side_effect=_STOP)
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.evaluation.ainvoke_llm_with_stats", invoke
    )
    with pytest.raises(StopRequested):
        await evaluate_brief_sections(
            object(), _outline(), {"1": [_search_result()], "2": []}
        )


@pytest.mark.asyncio
async def test_html_section_stop_not_swallowed_as_section_error(monkeypatch):
    """HTML 章节片段生成中停止不得被 gather 当作章节失败吞进重试循环。"""
    invoke = AsyncMock(side_effect=_STOP)
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.html_reporter.ainvoke_llm_with_stats", invoke
    )
    with pytest.raises(StopRequested):
        await _generate_section_fragments(
            object(),
            "<html><head></head><body></body></html>",
            [BriefHtmlSectionChunk(section_id="1", title="范围", markdown="## 范围\n内容")],
            "zh-CN",
            [],
        )


# ---------------------------------------------------------------------------
# 节点层：except Exception → 失败收尾/降级 不得吞停止
# ---------------------------------------------------------------------------


class _BriefSession:
    """运行 Brief 主链节点所需的最小会话替身。"""

    def __init__(self, state: BriefWorkflowState | None = None):
        self.values = {
            "search_context.original_query": "停止传播测试",
            "search_context.language": "zh-CN",
            "search_context.research_intent": {},
            "search_context.questions": "",
            "search_context.user_feedback": "",
            "search_context.report_template": "",
            "search_context.current_report": type(
                "Report", (), {"checked_trace_source_report_content": "# 报告", "report_content": ""}
            )(),
        }
        if state is not None:
            self.values["search_context.brief_state"] = state.model_dump()
        self.write_custom_stream = AsyncMock()

    def get_global_state(self, key):
        return self.values.get(key)

    def update_global_state(self, values):
        self.values.update(values)


def _full_state() -> BriefWorkflowState:
    return BriefWorkflowState(
        outline=_outline(),
        collection=_collection(),
        chapters=[BriefChapter(section_id="1", raw_markdown="章节内容")],
    )


@pytest.mark.asyncio
async def test_outline_node_stop_propagates(monkeypatch):
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "generate_brief_outline", AsyncMock(side_effect=_STOP))
    with pytest.raises(StopRequested):
        await BriefOutlineNode()._do_invoke({}, _BriefSession(), None)


@pytest.mark.asyncio
async def test_info_collector_node_stop_propagates(monkeypatch):
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "generate_brief_queries", AsyncMock(side_effect=_STOP))
    with pytest.raises(StopRequested):
        await BriefInfoCollectorNode()._do_invoke(
            {}, _BriefSession(BriefWorkflowState(outline=_outline())), None
        )


@pytest.mark.asyncio
async def test_evidence_review_node_stop_propagates(monkeypatch):
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "review_brief_evidence", AsyncMock(side_effect=_STOP))
    with pytest.raises(StopRequested):
        await BriefEvidenceReviewNode()._do_invoke(
            {}, _BriefSession(BriefWorkflowState(outline=_outline(), collection=_collection())), None
        )


@pytest.mark.asyncio
async def test_sub_reporter_node_stop_propagates(monkeypatch):
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "write_brief_chapters", AsyncMock(side_effect=_STOP))
    with pytest.raises(StopRequested):
        await BriefSubReporterNode()._do_invoke(
            {}, _BriefSession(BriefWorkflowState(outline=_outline(), collection=_collection())), None
        )


@pytest.mark.asyncio
async def test_reporter_node_stop_propagates(monkeypatch):
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "generate_brief_summary", AsyncMock(side_effect=_STOP))
    with pytest.raises(StopRequested):
        await BriefReporterNode()._do_invoke({}, _BriefSession(_full_state()), None)


@pytest.mark.asyncio
async def test_html_reporter_node_stop_does_not_fallback_to_markdown(monkeypatch):
    """HTML 转写中停止必须上抛，不得降级为 markdown 保留产物正常完成。"""
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "generate_brief_html_report", AsyncMock(side_effect=_STOP))
    node = BriefHtmlReporterNode()
    monkeypatch.setattr(node, "_fallback_to_markdown", lambda *_a, **_k: pytest.fail(
        "停止不得走 markdown 降级路径"
    ))
    with pytest.raises(StopRequested):
        await node._do_invoke({}, _BriefSession(), None)
