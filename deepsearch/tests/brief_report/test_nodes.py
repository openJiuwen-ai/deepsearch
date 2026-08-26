"""Brief 节点运行时配置的行为测试。"""

import json
import logging
from unittest.mock import AsyncMock

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent import brief_nodes
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.brief_nodes import (
    BriefEvidenceReviewNode,
    BriefHtmlReporterNode,
    BriefInfoCollectorNode,
    BriefOutlineNode,
    BriefReportAssemblerNode,
    BriefReporterNode,
    BriefSubReporterNode,
)
from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefChapter,
    BriefCollectionContext,
    BriefCollectionResult,
    BriefEvidenceReview,
    BriefOutline,
    BriefQuery,
    BriefReportAssembly,
    BriefWorkflowState,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import ResearchIntent
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


@pytest.mark.parametrize(
    "node_class",
    [
        BriefOutlineNode,
        BriefInfoCollectorNode,
        BriefEvidenceReviewNode,
        BriefSubReporterNode,
        BriefReporterNode,
        BriefReportAssemblerNode,
        BriefHtmlReporterNode,
    ],
)
def test_brief_main_nodes_implement_three_phase_base_node_contract(node_class):
    """Brief 主节点必须显式实现 Session 读取、算法调用和状态回写三个阶段。"""
    assert node_class._pre_handle is not BaseNode._pre_handle
    assert node_class._do_invoke is not BaseNode._do_invoke
    assert node_class._post_handle is not BaseNode._post_handle


class _BriefSession:
    """运行 Brief 主链节点所需的最小会话替身。"""

    def __init__(self):
        self.values = {
            "search_context.original_query": "测试 Brief 生命周期日志",
            "search_context.language": "zh-CN",
            "search_context.research_intent": {},
            "search_context.questions": "",
            "search_context.user_feedback": "",
            "search_context.report_template": "",
        }
        self.write_custom_stream = AsyncMock()

    def get_global_state(self, key):
        return self.values.get(key)

    def update_global_state(self, values):
        self.values.update(values)


def _brief_outline():
    return BriefOutline.model_validate(
        {
            "title": "测试 Brief",
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


@pytest.mark.asyncio
async def test_brief_search_reuses_professional_search_tool_and_normalizes_result(monkeypatch):
    """Brief 搜索必须通过既有搜索工具调用，并将标准结果交给 Brief 采集流程。"""
    session = _BriefSession()
    session.values.update(
        {
            "config.info_collector_search_method": "web",
            "config.web_search_engine_config": type("Config", (), {"search_engine_name": "petal"})(),
        }
    )

    tool = type("Tool", (), {"invoke": AsyncMock(return_value={
        "search_engine": "petal",
        "search_results": [
            {"title": "来源", "url": "https://example.com/data", "content": "可用证据"}
        ],
    })})()
    monkeypatch.setattr(brief_nodes, "create_web_search_tool", lambda: tool)

    results = await brief_nodes._search_brief_queries(
        session,
        [BriefQuery(query="市场规模", section_ids=["1"], step_ids=["1-1"])],
        ResearchIntent(),
    )

    assert tool.invoke.await_args.args[0] == {"query": "市场规模", "search_engine_name": "petal"}
    assert len(results) == 1
    assert results[0].url == "https://example.com/data"
    assert results[0].section_ids == ["1"]


@pytest.mark.asyncio
async def test_brief_outline_streams_the_generated_outline(monkeypatch):
    """Brief 大纲应使用与专业版相同的 start/message/done 协议输出。"""
    outline = _brief_outline()
    stream = AsyncMock()
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "generate_brief_outline", AsyncMock(return_value=outline))
    monkeypatch.setattr(brief_nodes, "custom_stream_output", stream)

    result = await BriefOutlineNode()._do_invoke({}, _BriefSession(), None)

    assert result["next_node"] == NodeId.BRIEF_INFO_COLLECTOR.value
    stream.assert_awaited_once()
    _session, _stream_id, content, agent_name = stream.await_args.args
    assert json.loads(content) == outline.model_dump()
    assert agent_name == NodeId.BRIEF_OUTLINE.value


@pytest.mark.asyncio
async def test_brief_search_streams_each_normalized_source(monkeypatch):
    """Brief 搜索应逐条输出已通过规范化的来源，并携带来源 Query 与路由。"""
    session = _BriefSession()
    session.values.update(
        {
            "config.info_collector_search_method": "web",
            "config.web_search_engine_config": type("Config", (), {"search_engine_name": "petal"})(),
        }
    )
    tool = type("Tool", (), {"invoke": AsyncMock(return_value={
        "search_engine": "petal",
        "search_results": [
            {"title": "来源", "url": "https://example.com/data", "content": "可用证据"}
        ],
    })})()
    monkeypatch.setattr(brief_nodes, "create_web_search_tool", lambda: tool)

    await brief_nodes._search_brief_queries(
        session,
        [BriefQuery(query="市场规模", section_ids=["1"], step_ids=["1-1"])],
        ResearchIntent(),
    )

    session.write_custom_stream.assert_awaited_once()
    payload = session.write_custom_stream.await_args.args[0]
    assert payload["agent"] == NodeId.BRIEF_INFO_COLLECTOR.value
    assert payload["event"] == "summary_response"
    assert payload["message_type"] == "message_chunk"
    assert payload["section_ids"] == ["1"]
    assert payload["step_ids"] == ["1-1"]
    assert json.loads(payload["content"]) == {
        "title": "来源", "url": "https://example.com/data", "query": "市场规模",
    }


@pytest.mark.asyncio
async def test_brief_review_streams_user_visible_evidence_decision(monkeypatch):
    """审阅流只暴露是否补搜和证据缺口，不能泄露内部写作指引。"""
    outline = _brief_outline()
    collection = BriefCollectionResult.model_validate(
        {
            "section_evidence": {
                "1": {"coverage": [{"step_id": "1-1", "status": "missing", "reason": "缺少数据", "blocking_gap": True}]},
                "2": {"coverage": [{"step_id": "2-1", "status": "covered", "reason": "已有来源"}]},
            },
            "citation_registry": [],
        }
    )
    review = BriefEvidenceReview.model_validate(
        {
            "writing_guidance": {"report_strategy": "内部策略，不应出现在流中"},
            "blocking_gaps": [{"step_id": "1-1", "status": "missing", "reason": "缺少数据", "blocking_gap": True}],
        }
    )
    session = _BriefSession()
    session.values["search_context.brief_state"] = BriefWorkflowState(
        outline=outline, collection=collection,
    ).model_dump()
    stream = AsyncMock()
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "review_brief_evidence", AsyncMock(return_value=review))
    monkeypatch.setattr(brief_nodes, "custom_stream_output", stream)

    result = await BriefEvidenceReviewNode()._do_invoke({}, session, None)

    assert result["next_node"] == NodeId.BRIEF_INFO_COLLECTOR.value
    _session, _stream_id, content, agent_name = stream.await_args.args
    payload = json.loads(content)
    assert agent_name == NodeId.BRIEF_EVIDENCE_REVIEWER.value
    assert payload["supplement_required"] is True
    assert payload["blocking_gaps"] == [{"section_id": "1", "step_id": "1-1", "reason": "缺少数据"}]
    assert "writing_guidance" not in payload


@pytest.mark.asyncio
async def test_brief_main_nodes_emit_lifecycle_logs(monkeypatch, caplog):
    """遗漏任一 Brief 阶段的进入或退出日志时，此完整主链回归必须失败。"""
    outline = _brief_outline()
    collection = BriefCollectionResult(
        section_evidence={}, citation_registry=[]
    )
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "generate_brief_outline", AsyncMock(return_value=outline))
    monkeypatch.setattr(
        brief_nodes,
        "generate_brief_queries",
        AsyncMock(return_value=[BriefQuery(query="测试", section_ids=["1"], step_ids=["1-1"])]),
    )
    monkeypatch.setattr(brief_nodes, "_search_brief_queries", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        brief_nodes,
        "collect_initial_brief_evidence",
        AsyncMock(return_value=(collection, BriefCollectionContext())),
    )
    monkeypatch.setattr(
        brief_nodes,
        "review_brief_evidence",
        AsyncMock(return_value=BriefEvidenceReview()),
    )
    monkeypatch.setattr(
        brief_nodes,
        "write_brief_chapters",
        AsyncMock(return_value=[BriefChapter(section_id="1", raw_markdown="## 范围\n\n正文")]),
    )
    monkeypatch.setattr(brief_nodes, "generate_brief_summary", AsyncMock(return_value="- 核心结论"))
    monkeypatch.setattr(
        brief_nodes,
        "assemble_brief_report",
        lambda _: BriefReportAssembly(
            report_content="# 测试 Brief\n\n- 核心结论",
            merged_trace_source_datas=[],
        ),
    )
    monkeypatch.setattr(
        brief_nodes,
        "generate_brief_html_report",
        AsyncMock(return_value="<!DOCTYPE html><html><head></head><body></body></html>"),
    )
    session = _BriefSession()

    with caplog.at_level(logging.INFO, logger=brief_nodes.__name__):
        assert (await BriefOutlineNode()._do_invoke({}, session, None))["next_node"] == NodeId.BRIEF_INFO_COLLECTOR.value
        assert (await BriefInfoCollectorNode()._do_invoke({}, session, None))["next_node"] == NodeId.BRIEF_EVIDENCE_REVIEWER.value
        assert (await BriefEvidenceReviewNode()._do_invoke({}, session, None))["next_node"] == NodeId.BRIEF_SUB_REPORTER.value
        assert (await BriefSubReporterNode()._do_invoke({}, session, None))["next_node"] == NodeId.BRIEF_REPORTER.value
        assert (await BriefReporterNode()._do_invoke({}, session, None))["next_node"] == NodeId.BRIEF_REPORT_ASSEMBLER.value
        assert (await BriefReportAssemblerNode()._do_invoke({}, session, None))["next_node"] == NodeId.BRIEF_SOURCE_TRACER.value
        assert (await BriefHtmlReporterNode()._do_invoke({}, session, None))["next_node"] == NodeId.END.value

    messages = [record.getMessage() for record in caplog.records]
    for node_name in ("BriefOutlineNode", "BriefInfoCollectorNode", "BriefEvidenceReviewNode", "BriefSubReporterNode", "BriefReporterNode", "BriefReportAssemblerNode", "BriefHtmlReporterNode"):
        assert f"[{node_name}] Start {node_name}." in messages
        assert any(message.startswith(f"[{node_name}] End {node_name}, next_node=") for message in messages)
    assert any("[BriefOutlineNode] Generated outline: {'title': '测试 Brief'" in message for message in messages)
    assert any("[BriefInfoCollectorNode] Collected evidence:" in message for message in messages)
    assert any("[BriefEvidenceReviewNode] Evidence review:" in message for message in messages)
    assert any("[BriefSubReporterNode] Generated chapters:" in message for message in messages)
    assert any("[BriefReporterNode] Generated executive summary:" in message for message in messages)
    assert any("[BriefReportAssemblerNode] Assembled final report:" in message for message in messages)
    assert any("[BriefHtmlReporterNode] Generated html report:" in message for message in messages)


@pytest.mark.asyncio
async def test_brief_review_routes_blocking_gaps_to_one_supplement(monkeypatch):
    """审阅节点只能把有阻断缺口的首轮证据送入唯一一次补搜。"""
    outline = _brief_outline()
    first_collection = BriefCollectionResult.model_validate(
        {
            "section_evidence": {
                "1": {
                    "coverage": [
                        {"step_id": "1-1", "status": "missing", "reason": "缺数据", "blocking_gap": True},
                        {"step_id": "1-2", "status": "covered", "reason": "已有"},
                    ]
                }
            },
            "citation_registry": [],
        }
    )
    supplemented_collection = BriefCollectionResult(section_evidence={}, citation_registry=[])
    review = BriefEvidenceReview.model_validate(
        {
            "blocking_gaps": [
                {"step_id": "1-1", "status": "missing", "reason": "缺数据", "blocking_gap": True}
            ]
        }
    )
    session = _BriefSession()
    session.values["search_context.brief_state"] = BriefWorkflowState(
        outline=outline,
        collection=first_collection,
        collection_context=BriefCollectionContext(executed_queries=["首轮查询"]),
    ).model_dump()
    supplement = AsyncMock(return_value=(supplemented_collection, BriefCollectionContext(executed_queries=["首轮查询", "补搜查询"])))
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "review_brief_evidence", AsyncMock(return_value=review))
    monkeypatch.setattr(
        brief_nodes,
        "generate_brief_queries",
        AsyncMock(return_value=[BriefQuery(query="补搜", section_ids=["1"], step_ids=["1-1"])]),
    )
    monkeypatch.setattr(brief_nodes, "_search_brief_queries", AsyncMock(return_value=[]))
    monkeypatch.setattr(brief_nodes, "supplement_brief_evidence", supplement)

    review_result = await BriefEvidenceReviewNode()._do_invoke({}, session, None)
    supplement_result = await BriefInfoCollectorNode()._do_invoke({}, session, None)

    assert review_result["next_node"] == NodeId.BRIEF_INFO_COLLECTOR.value
    assert supplement_result["next_node"] == NodeId.BRIEF_SUB_REPORTER.value
    assert supplement.await_count == 1


@pytest.mark.asyncio
async def test_brief_organizes_citations_once_in_assembler_and_converts_to_html(monkeypatch):
    """拼装只在 Assembler 执行一次；HTML 节点消费溯源结果并写回 HTML 产物。"""
    outline = _brief_outline()
    collection = BriefCollectionResult(section_evidence={}, citation_registry=[])
    session = _BriefSession()
    session.values["search_context.brief_state"] = BriefWorkflowState(
        outline=outline,
        collection=collection,
        executive_summary="- 结论",
        chapters=[BriefChapter(section_id="1", raw_markdown="## 范围\n\n正文")],
    ).model_dump()
    assembled_requests = []

    def assemble(request):
        assembled_requests.append(request)
        return BriefReportAssembly(
            report_content="# 测试 Brief\n\n## 核心摘要\n\n- 结论\n\n## 范围\n\n正文\n\n## 参考文章",
            merged_trace_source_datas=[],
        )

    html_calls = []

    async def generate_html(*, llm, markdown, language):
        html_calls.append(markdown)
        return "<!DOCTYPE html><html><head></head><body><h1>x</h1></body></html>"

    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "generate_brief_summary", AsyncMock(return_value="- 结论"))
    monkeypatch.setattr(brief_nodes, "assemble_brief_report", assemble)
    monkeypatch.setattr(brief_nodes, "generate_brief_html_report", generate_html)

    assert (await BriefReporterNode()._do_invoke({}, session, None))["next_node"] == NodeId.BRIEF_REPORT_ASSEMBLER.value
    assert assembled_requests == []

    assert (await BriefReportAssemblerNode()._do_invoke({}, session, None))["next_node"] == NodeId.BRIEF_SOURCE_TRACER.value
    assert len(assembled_requests) == 1
    current_report = session.get_global_state("search_context.current_report")
    assert current_report.report_content.startswith("# 测试 Brief")

    current_report.checked_trace_source_report_content = "# 测试 Brief\n\n正文"
    session.update_global_state({"search_context.current_report": current_report})
    assert (await BriefHtmlReporterNode()._do_invoke({}, session, None))["next_node"] == NodeId.END.value
    assert html_calls == ["# 测试 Brief\n\n正文"]
    assert session.get_global_state("search_context.final_result.response_content").startswith("<!DOCTYPE html>")
    assert session.get_global_state("search_context.final_result.response_content_type") == "text/html"
    assert session.get_global_state("search_context.current_report").report_html.startswith("<!DOCTYPE html>")


@pytest.mark.asyncio
async def test_brief_html_reporter_falls_back_to_markdown_on_generation_failure(monkeypatch, caplog):
    """HTML 转写重试耗尽必须降级保留 markdown 产物，而不是让报告整体失败。"""
    from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Report

    current_report = Report(report_content="# 测试 Brief\n\n正文")
    current_report.checked_trace_source_report_content = "# 测试 Brief\n\n正文"
    session = _BriefSession()
    session.values["search_context.final_result.response_content"] = "# 测试 Brief\n\n正文"
    session.values["search_context.final_result.response_content_type"] = "text/markdown"
    session.update_global_state({"search_context.current_report": current_report})
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(
        brief_nodes,
        "generate_brief_html_report",
        AsyncMock(side_effect=ValueError("brief html report generation failed: sup_citation_sequence_mismatch")),
    )

    with caplog.at_level(logging.WARNING, logger=brief_nodes.__name__):
        result = await BriefHtmlReporterNode()._do_invoke({}, session, None)

    # 正常结束而非 REPORT_GENERATE_ERROR；产物保持 SourceTracer 写入的 markdown。
    assert result == {"next_node": NodeId.END.value}
    assert session.get_global_state("search_context.final_result.response_content") == "# 测试 Brief\n\n正文"
    assert session.get_global_state("search_context.final_result.response_content_type") == "text/markdown"
    assert session.get_global_state("search_context.final_result.exception_info") is None
    warning_info = session.get_global_state("search_context.final_result.warning_info")
    assert "fallback to markdown" in warning_info
    assert "sup_citation_sequence_mismatch" in warning_info
    fallback_log = next(
        record for record in caplog.records
        if "fallback to markdown report" in record.getMessage()
    )
    assert fallback_log.levelno == logging.WARNING


@pytest.mark.asyncio
async def test_brief_sub_reporter_ends_with_structured_error_on_chapter_failure(monkeypatch, caplog):
    """章节写作不可恢复失败必须记录业务错误并结束，而非交给工作流泛化处理。"""
    collection = BriefCollectionResult(
        section_evidence={}, citation_registry=[]
    )
    session = _BriefSession()
    session.values["search_context.brief_state"] = BriefWorkflowState(
        outline=_brief_outline(), collection=collection
    ).model_dump()
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(
        brief_nodes,
        "write_brief_chapters",
        AsyncMock(side_effect=ValueError("unclosed_code_fence")),
    )

    with caplog.at_level(logging.ERROR, logger=brief_nodes.__name__):
        result = await BriefSubReporterNode()._do_invoke({}, session, None)

    assert result == {"next_node": NodeId.END.value}
    assert session.get_global_state("search_context.final_result.exception_info") == (
        f"[{StatusCode.SUB_REPORT_GENERATE_ERROR.code}]"
        "Error when generate sub report, error: unclosed_code_fence"
    )
    failure = next(
        record for record in caplog.records
        if "[BriefSubReporterNode] Chapter writing failed: unclosed_code_fence" in record.getMessage()
    )
    assert failure.exc_info is not None


@pytest.mark.asyncio
async def test_brief_outline_failure_uses_outliner_error_code(monkeypatch):
    """大纲失败必须在 Brief 节点记录专业版同类错误码，而不是抛到工作流兜底。"""
    session = _BriefSession()
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(
        brief_nodes,
        "generate_brief_outline",
        AsyncMock(side_effect=RuntimeError("outline response malformed")),
    )

    result = await BriefOutlineNode()._do_invoke({}, session, None)

    assert result == {"next_node": NodeId.END.value}
    assert session.get_global_state("search_context.final_result.exception_info") == (
        f"[{StatusCode.OUTLINER_GENERATE_ERROR.code}]"
        "Error when Outliner generate an outline: outline response malformed"
    )


@pytest.mark.asyncio
async def test_brief_collector_failure_uses_info_collecting_error_code(monkeypatch):
    """首轮采集失败必须以信息收集错误结束，而不是丢失具体失败阶段。"""
    session = _BriefSession()
    session.values["search_context.brief_state"] = BriefWorkflowState(outline=_brief_outline()).model_dump()
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(
        brief_nodes,
        "generate_brief_queries",
        AsyncMock(side_effect=RuntimeError("query generation failed")),
    )

    result = await BriefInfoCollectorNode()._do_invoke({}, session, None)

    assert result == {"next_node": NodeId.END.value}
    assert session.get_global_state("search_context.final_result.exception_info") == (
        f"[{StatusCode.INFO_COLLECTING_EMPTY.code}]"
        "Info collecting exists Abnormal, No doc infos found.: query generation failed"
    )


@pytest.mark.asyncio
async def test_brief_sub_reporter_ends_with_structured_error_when_all_chapters_fail(monkeypatch):
    """所有并行章节均降级失败时，不能把空章节当作成功继续后续报告节点。"""
    session = _BriefSession()
    session.values["search_context.brief_state"] = BriefWorkflowState(
        outline=_brief_outline(),
        collection=BriefCollectionResult(section_evidence={}, citation_registry=[]),
    ).model_dump()
    monkeypatch.setattr(brief_nodes, "_llm", lambda *_: object())
    monkeypatch.setattr(brief_nodes, "write_brief_chapters", AsyncMock(return_value=[]))

    result = await BriefSubReporterNode()._do_invoke({}, session, None)

    assert result == {"next_node": NodeId.END.value}
    assert session.get_global_state("search_context.final_result.exception_info") == (
        f"[{StatusCode.SUB_REPORT_GENERATE_ERROR.code}]"
        "Error when generate sub report, error: No Brief chapters were generated."
    )


def test_brief_branch_workflow_order_is_rewritten():
    """brief 分支编排：Reporter→Assembler→SourceTracer→HtmlReporter→END，无 Mermaid。"""
    import inspect

    from openjiuwen_deepsearch.framework.openjiuwen.agent import workflow as workflow_module

    source = inspect.getsource(workflow_module._add_brief_branch)
    assert "BRIEF_REPORT_ASSEMBLER" in source
    assert "BRIEF_HTML_REPORTER" in source
    assert "BRIEF_MERMAID_GENERATOR" not in source
    assert "BriefReportAssemblerNode" in source
    assert "BriefHtmlReporterNode" in source
    assert "BriefMermaidGeneratorNode" not in source


def test_brief_mermaid_pipeline_module_is_removed():
    """brief 专用 Mermaid 链路模块必须整体删除。"""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("openjiuwen_deepsearch.algorithm.brief_report.visualization")
