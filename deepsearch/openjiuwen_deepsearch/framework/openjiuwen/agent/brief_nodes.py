"""Brief 独立主链的粗粒度编排节点。"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.algorithm.brief_report.collector import (
    collect_initial_brief_evidence,
    generate_brief_queries,
    supplement_brief_evidence,
)
from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import generate_brief_html_report
from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefAssemblyRequest, BriefChapter, BriefCollectionResult, BriefCollectorRequest,
    BriefEvidenceReview, BriefOutline, BriefOutlineRequest, BriefReviewRequest,
    BriefQuery, BriefQueryRequest, BriefSearchResult, BriefSummaryRequest,
    BriefWorkflowState, BriefWritingRequest,
)
from openjiuwen_deepsearch.algorithm.brief_report.outline import generate_brief_outline
from openjiuwen_deepsearch.algorithm.brief_report.review import review_brief_evidence
from openjiuwen_deepsearch.algorithm.brief_report.search import normalize_brief_search_results
from openjiuwen_deepsearch.algorithm.brief_report.writer import (
    assemble_brief_report,
    generate_brief_summary,
    write_brief_chapters,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Report, ResearchIntent
from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_adapter import adapt_llm_model_name
from openjiuwen_deepsearch.algorithm.research_collector.collector_function import process_tool_result
from openjiuwen_deepsearch.framework.openjiuwen.tools.local_search import create_local_search_tool
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import create_web_search_tool
from openjiuwen_deepsearch.common.status_code import StatusCode, format_exception_info
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.constants_utils.search_engine_constants import LocalSearch, SearchEngine
from openjiuwen_deepsearch.utils.common_utils.stream_utils import (
    MessageType,
    StreamEvent,
    custom_stream_output,
    get_current_time,
)
from openjiuwen_deepsearch.utils.debug_utils.node_debug import NodeDebugData, NodeType, add_debug_log_wrapper
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager


logger = logging.getLogger(__name__)


def _log_node_failure(node_name: str, stage: str, exc: Exception) -> None:
    """统一记录 Brief 主节点失败，同时遵循全局敏感信息策略。"""
    logger.error(
        "[%s] %s failed: %s",
        node_name,
        stage,
        "<detail masked>" if LogManager.is_sensitive() else exc,
        exc_info=not LogManager.is_sensitive(),
    )


def _log_node_detail(node_name: str, label: str, value: object) -> None:
    """以专业版节点相同的策略输出完整运行时输入或结构化产物。"""
    logger.info("[%s] %s: %s", node_name, label, "*" if LogManager.is_sensitive() else value)


@dataclass(frozen=True)
class BriefNodeFailureContext:
    """不可恢复 Brief 节点失败的结构化上下文。"""

    node_id: NodeId
    node_name: str
    stage: str
    status_code: StatusCode


def _finish_brief_node_failure(
    session: Session,
    failure: BriefNodeFailureContext,
    exc: Exception,
) -> Output:
    """以专业版主节点相同的结构化错误完成不可恢复的 Brief 节点失败。"""
    _log_node_failure(failure.node_name, failure.stage, exc)
    detail = "Error when running Brief node." if LogManager.is_sensitive() else exc
    exception_info = format_exception_info(failure.status_code, detail)
    session.update_global_state({"search_context.final_result.exception_info": exception_info})
    add_debug_log_wrapper(
        session,
        NodeDebugData(failure.node_id.value, 0, NodeType.MAIN.value, output_content=exception_info),
    )
    next_node = NodeId.END.value
    logger.info("[%s] End %s, next_node=%s", failure.node_name, failure.node_name, next_node)
    return {"next_node": next_node}


def _state(session: Session) -> BriefWorkflowState:
    """从会话恢复 Brief 工作流状态。"""
    return BriefWorkflowState.model_validate(session.get_global_state("search_context.brief_state") or {})


def _llm(session: Session, node: NodeId) -> object:
    """按既有模型类别路由取得当前运行时 LLM。"""
    return llm_context.get()[adapt_llm_model_name(session, node.value)]


def _search_tools(session: Session) -> list[tuple[str, object, str]]:
    """按专业版规则创建当前 Brief 可用的既有搜索工具。"""
    method = session.get_global_state("config.info_collector_search_method") or "web"
    tools: list[tuple[str, object, str]] = []
    if method in {"web", "all"}:
        config = session.get_global_state("config.web_search_engine_config")
        engine = getattr(config, "search_engine_name", "") or SearchEngine.PETAL.value
        tools.append(("web_search_tool", create_web_search_tool(), engine))
    if method in {"local", "all"}:
        config = session.get_global_state("config.local_search_engine_config")
        engine = getattr(config, "search_engine_name", "") or LocalSearch.OPENAPI.value
        tools.append(("local_search_tool", create_local_search_tool(), engine))
    return tools


async def _search_brief_queries(
    session: Session,
    queries: list[BriefQuery],
    intent: ResearchIntent,
) -> list[BriefSearchResult]:
    """复用专业版搜索工具执行与结果处理链路，返回 Brief 标准证据。"""
    jobs = [
        (query, tool_name, tool, engine)
        for query in queries
        for tool_name, tool, engine in _search_tools(session)
    ]
    raw_results = await asyncio.gather(
        *(tool.invoke({"query": query.query, "search_engine_name": engine})
          for query, _tool_name, tool, engine in jobs),
        return_exceptions=True,
    )
    batches: list[tuple[BriefQuery, list[dict[str, Any]]]] = []
    for (query, tool_name, _tool, _engine), raw_result in zip(jobs, raw_results, strict=True):
        if isinstance(raw_result, BaseException):
            _log_node_failure("BriefInfoCollectorNode", f"Search tool {tool_name}", raw_result)
            continue
        agent_input = {
            "messages": [],
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": [],
            "research_intent": intent.model_dump(),
        }
        try:
            processed_results = process_tool_result(
                tool_name,
                json.dumps(raw_result, ensure_ascii=False),
                agent_input,
            )
        except Exception as exc:
            _log_node_failure("BriefInfoCollectorNode", f"Process search tool {tool_name}", exc)
            continue
        if isinstance(processed_results, list):
            batches.append((query, processed_results))
    results = normalize_brief_search_results(batches, intent)
    await _stream_brief_search_results(session, results, queries)
    return results


async def _stream_brief_search_results(
    session: Session,
    results: list[BriefSearchResult],
    queries: list[BriefQuery],
) -> None:
    """按专业版来源事件协议逐条输出已规范化的 Brief 搜索结果。"""
    query_by_step_id = {
        step_id: query.query
        for query in queries
        for step_id in query.step_ids
    }
    for result in results:
        query = next(
            (query_by_step_id[step_id] for step_id in result.step_ids if step_id in query_by_step_id),
            "",
        )
        payload = {
            "title": result.title,
            "url": result.url,
            "query": query,
        }
        await session.write_custom_stream(
            {
                "message_id": str(uuid.uuid4()),
                "section_ids": result.section_ids,
                "step_ids": result.step_ids,
                "agent": NodeId.BRIEF_INFO_COLLECTOR.value,
                "content": json.dumps(payload, ensure_ascii=False),
                "message_type": MessageType.MESSAGE_CHUNK.value,
                "event": StreamEvent.SUMMARY_RESPONSE.value,
                "created_time": get_current_time(),
            }
        )


def _brief_evidence_review_stream_payload(
    outline: BriefOutline,
    collection: BriefCollectionResult,
    review: BriefEvidenceReview,
) -> str:
    """构造面向用户的审阅结果，不把内部编辑指引作为运行时事件暴露。"""
    outline_by_step_id = {
        step.id: section.id
        for section in outline.sections
        for step in section.research_steps
    }
    gap_by_step_id = {
        gap.step_id: gap
        for gap in review.blocking_gaps
    }
    section_coverage = []
    for section in outline.sections:
        coverage = collection.section_evidence.get(section.id)
        statuses = {
            item.status.value
            for item in (coverage.coverage if coverage is not None else [])
        }
        section_coverage.append(
            {
                "section_id": section.id,
                "status": (
                    "missing" if "missing" in statuses
                    else "weak" if "weak" in statuses
                    else "covered" if statuses == {"covered"}
                    else "unknown"
                ),
            }
        )
    blocking_gaps = [
        {
            "section_id": outline_by_step_id.get(step_id, ""),
            "step_id": step_id,
            "reason": gap.reason,
        }
        for step_id, gap in gap_by_step_id.items()
    ]
    return json.dumps(
        {
            "supplement_required": bool(blocking_gaps),
            "section_coverage": section_coverage,
            "blocking_gaps": blocking_gaps,
        },
        ensure_ascii=False,
    )


class BriefOutlineNode(BaseNode):
    """生成并保存 Brief 精简大纲。"""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info("[BriefOutlineNode] Start BriefOutlineNode.")
        intent = session.get_global_state("search_context.research_intent") or {}
        request = BriefOutlineRequest(
            query=session.get_global_state("search_context.original_query") or "",
            language=session.get_global_state("search_context.language") or "zh-CN",
            research_intent=intent,
            audience_role=intent.get("audience_role", ""),
            tone=intent.get("tone", ""),
            clarification_questions=session.get_global_state("search_context.questions") or "",
            user_feedback=session.get_global_state("search_context.user_feedback") or "",
            report_template=session.get_global_state("search_context.report_template") or "",
        )
        _log_node_detail("BriefOutlineNode", "current_inputs", request.model_dump())
        return {"llm": _llm(session, NodeId.BRIEF_OUTLINE), "request": request}

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """调用大纲算法。"""
        try:
            pre_output = self._pre_handle(inputs, session, context)
            outline = await generate_brief_outline(pre_output["llm"], pre_output["request"])
            await custom_stream_output(
                session,
                str(uuid.uuid4()),
                outline.model_dump_json(),
                NodeId.BRIEF_OUTLINE.value,
            )
        except Exception as exc:
            return _finish_brief_node_failure(
                session,
                BriefNodeFailureContext(
                    node_id=NodeId.BRIEF_OUTLINE,
                    node_name="BriefOutlineNode",
                    stage="Outline generation",
                    status_code=StatusCode.OUTLINER_GENERATE_ERROR,
                ),
                exc,
            )
        return self._post_handle(inputs, {"outline": outline}, session, context)

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> Output:
        outline = algorithm_output["outline"]
        session.update_global_state({"search_context.brief_state": BriefWorkflowState(outline=outline).model_dump()})
        next_node = NodeId.BRIEF_INFO_COLLECTOR.value
        logger.info("[BriefOutlineNode] Generated outline sections=%d.", len(outline.sections))
        _log_node_detail("BriefOutlineNode", "Generated outline", outline.model_dump())
        logger.info("[BriefOutlineNode] End BriefOutlineNode, next_node=%s", next_node)
        return {"next_node": next_node}


class BriefInfoCollectorNode(BaseNode):
    """执行 Brief 报告级证据采集。"""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info("[BriefInfoCollectorNode] Start BriefInfoCollectorNode.")
        state = _state(session)
        if state.outline is None:
            logger.warning("[BriefInfoCollectorNode] Missing outline, skip evidence collection.")
            return {"skip": True}
        intent = session.get_global_state("search_context.research_intent") or {}
        _log_node_detail(
            "BriefInfoCollectorNode", "current_inputs",
            {"outline": state.outline.model_dump(), "research_intent": intent,
             "search_method": session.get_global_state("config.info_collector_search_method") or "web"},
        )
        return {
            "state": state,
            "request": BriefCollectorRequest(
                outline=state.outline,
                user_query=session.get_global_state("search_context.original_query") or "",
                research_intent=ResearchIntent.model_validate(intent).model_dump(),
                llm=_llm(session, NodeId.BRIEF_INFO_COLLECTOR),
            ),
        }

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """执行首轮采集或审阅决定的唯一一次补搜。"""
        try:
            pre_output = self._pre_handle(inputs, session, context)
            if pre_output.get("skip"):
                return self._post_handle(inputs, pre_output, session, context)
            state, request = pre_output["state"], pre_output["request"]
            if state.collection is None or state.collection_context is None:
                queries = await generate_brief_queries(
                    request.llm,
                    BriefQueryRequest(
                        outline=request.outline,
                        user_query=request.user_query,
                        research_intent=request.research_intent,
                    ),
                )
                search_results = await _search_brief_queries(
                    session,
                    queries,
                    ResearchIntent.model_validate(request.research_intent),
                )
                collection, collection_context = await collect_initial_brief_evidence(
                    request,
                    queries,
                    search_results,
                )
                next_node = NodeId.BRIEF_EVIDENCE_REVIEWER.value
                stage = "Initial evidence collection"
            else:
                review = state.evidence_review
                blocking_gaps = review.blocking_gaps if review is not None else []
                queries = await generate_brief_queries(
                    request.llm,
                    BriefQueryRequest(
                        outline=request.outline,
                        user_query=request.user_query,
                        research_intent=request.research_intent,
                        executed_queries=state.collection_context.executed_queries,
                        blocking_gaps=blocking_gaps,
                    ),
                ) if blocking_gaps else []
                search_results = await _search_brief_queries(
                    session,
                    queries,
                    ResearchIntent.model_validate(request.research_intent),
                )
                collection, collection_context = await supplement_brief_evidence(
                    request,
                    state.collection,
                    state.collection_context,
                    queries,
                    search_results,
                )
                if review is not None:
                    state.evidence_review = review.model_copy(update={"blocking_gaps": []})
                next_node = NodeId.BRIEF_SUB_REPORTER.value
                stage = "Supplementary evidence collection"
        except Exception as exc:
            return _finish_brief_node_failure(
                session,
                BriefNodeFailureContext(
                    node_id=NodeId.BRIEF_INFO_COLLECTOR,
                    node_name="BriefInfoCollectorNode",
                    stage="Evidence collection",
                    status_code=StatusCode.INFO_COLLECTING_EMPTY,
                ),
                exc,
            )
        return self._post_handle(
            inputs,
            {"state": state, "collection": collection, "collection_context": collection_context,
             "next_node": next_node, "stage": stage},
            session, context,
        )

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> Output:
        if algorithm_output.get("skip"):
            next_node = NodeId.END.value
            logger.info("[BriefInfoCollectorNode] End BriefInfoCollectorNode, next_node=%s", next_node)
            return {"next_node": next_node}
        state = algorithm_output["state"]
        collection, collection_context = algorithm_output["collection"], algorithm_output["collection_context"]
        next_node, stage = algorithm_output["next_node"], algorithm_output["stage"]
        state.collection = collection
        state.collection_context = collection_context
        session.update_global_state({"search_context.brief_state": state.model_dump()})
        logger.info(
            "[BriefInfoCollectorNode] %s sections=%d citations=%d.",
            stage,
            len(collection.section_evidence), len(collection.citation_registry),
        )
        _log_node_detail("BriefInfoCollectorNode", "Collected evidence", collection.model_dump())
        _log_node_detail("BriefInfoCollectorNode", "Collection context", collection_context.model_dump())
        logger.info("[BriefInfoCollectorNode] End BriefInfoCollectorNode, next_node=%s", next_node)
        return {"next_node": next_node}


class BriefEvidenceReviewNode(BaseNode):
    """审阅首轮证据，生成内部写作指引并决定是否允许一次补搜。"""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info("[BriefEvidenceReviewNode] Start BriefEvidenceReviewNode.")
        state = _state(session)
        if state.outline is None or state.collection is None:
            logger.warning("[BriefEvidenceReviewNode] Missing outline or collection, skip evidence review.")
            return {"skip": True}
        intent = session.get_global_state("search_context.research_intent") or {}
        _log_node_detail(
            "BriefEvidenceReviewNode", "current_inputs",
            {"outline": state.outline.model_dump(), "collection": state.collection.model_dump(),
             "collection_context": state.collection_context.model_dump() if state.collection_context else {},
             "audience_role": intent.get("audience_role", ""), "tone": intent.get("tone", ""),
             "user_format": session.get_global_state("search_context.report_template") or ""},
        )
        return {
            "state": state,
            "request": BriefReviewRequest(
                outline=state.outline, collection=state.collection,
                llm=_llm(session, NodeId.BRIEF_EVIDENCE_REVIEWER),
                audience_role=intent.get("audience_role", ""), tone=intent.get("tone", ""),
                user_format=session.get_global_state("search_context.report_template") or "",
            ),
        }

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """不改动大纲和证据，仅保存审阅后的编辑指引及阻断缺口。"""
        try:
            pre_output = self._pre_handle(inputs, session, context)
            if pre_output.get("skip"):
                return self._post_handle(inputs, pre_output, session, context)
            review = await review_brief_evidence(pre_output["request"])
            await custom_stream_output(
                session,
                str(uuid.uuid4()),
                _brief_evidence_review_stream_payload(
                    pre_output["state"].outline,
                    pre_output["state"].collection,
                    review,
                ),
                NodeId.BRIEF_EVIDENCE_REVIEWER.value,
            )
        except Exception as exc:
            return _finish_brief_node_failure(
                session,
                BriefNodeFailureContext(
                    node_id=NodeId.BRIEF_EVIDENCE_REVIEWER,
                    node_name="BriefEvidenceReviewNode",
                    stage="Evidence review",
                    status_code=StatusCode.INFO_COLLECTING_EMPTY,
                ),
                exc,
            )
        return self._post_handle(
            inputs, {"state": pre_output["state"], "review": review}, session, context,
        )

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> Output:
        if algorithm_output.get("skip"):
            next_node = NodeId.END.value
            logger.info("[BriefEvidenceReviewNode] End BriefEvidenceReviewNode, next_node=%s", next_node)
            return {"next_node": next_node}
        state, review = algorithm_output["state"], algorithm_output["review"]
        state.evidence_review = review
        session.update_global_state({"search_context.brief_state": state.model_dump()})
        next_node = (
            NodeId.BRIEF_INFO_COLLECTOR.value
            if review.blocking_gaps
            else NodeId.BRIEF_SUB_REPORTER.value
        )
        _log_node_detail("BriefEvidenceReviewNode", "Evidence review", review.model_dump())
        _log_node_detail("BriefEvidenceReviewNode", "Writing guidance", review.writing_guidance.model_dump())
        _log_node_detail(
            "BriefEvidenceReviewNode",
            "Cleaned blocking gaps",
            [gap.model_dump() for gap in review.blocking_gaps],
        )
        logger.info("[BriefEvidenceReviewNode] End BriefEvidenceReviewNode, next_node=%s", next_node)
        return {"next_node": next_node}


class BriefSubReporterNode(BaseNode):
    """并行生成 Brief 章节。"""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info("[BriefSubReporterNode] Start BriefSubReporterNode.")
        state = _state(session)
        if state.outline is None or state.collection is None:
            logger.warning("[BriefSubReporterNode] Missing outline or collection, skip chapter writing.")
            return {"skip": True}
        _log_node_detail(
            "BriefSubReporterNode", "current_inputs",
            {"outline": state.outline.model_dump(), "collection": state.collection.model_dump()},
        )
        intent = session.get_global_state("search_context.research_intent") or {}
        return {
            "state": state,
            "request": BriefWritingRequest(
                llm=_llm(session, NodeId.BRIEF_SUB_REPORTER), outline=state.outline, collection=state.collection,
                language=session.get_global_state("search_context.language") or "zh-CN",
                audience_role=intent.get("audience_role", ""), tone=intent.get("tone", ""),
                user_format=session.get_global_state("search_context.report_template") or "",
                writing_guidance=state.evidence_review.writing_guidance if state.evidence_review else None,
            ),
        }

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """写作每个章节一次后进入核心摘要。"""
        try:
            pre_output = self._pre_handle(inputs, session, context)
            if pre_output.get("skip"):
                return self._post_handle(inputs, pre_output, session, context)
            chapters = await write_brief_chapters(pre_output["request"])
        except Exception as exc:
            return _finish_brief_node_failure(
                session,
                BriefNodeFailureContext(
                    node_id=NodeId.BRIEF_SUB_REPORTER,
                    node_name="BriefSubReporterNode",
                    stage="Chapter writing",
                    status_code=StatusCode.SUB_REPORT_GENERATE_ERROR,
                ),
                exc,
            )
        return self._post_handle(
            inputs, {"state": pre_output["state"], "chapters": chapters}, session, context,
        )

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> Output:
        if algorithm_output.get("skip"):
            next_node = NodeId.END.value
            logger.info("[BriefSubReporterNode] End BriefSubReporterNode, next_node=%s", next_node)
            return {"next_node": next_node}
        state, chapters = algorithm_output["state"], algorithm_output["chapters"]
        if not chapters:
            return _finish_brief_node_failure(
                session,
                BriefNodeFailureContext(
                    node_id=NodeId.BRIEF_SUB_REPORTER,
                    node_name="BriefSubReporterNode",
                    stage="Chapter writing",
                    status_code=StatusCode.SUB_REPORT_GENERATE_ERROR,
                ),
                RuntimeError("No Brief chapters were generated."),
            )
        state.chapters = chapters
        session.update_global_state({"search_context.brief_state": state.model_dump()})
        next_node = NodeId.BRIEF_REPORTER.value
        logger.info("[BriefSubReporterNode] Generated chapters=%d.", len(chapters))
        _log_node_detail(
            "BriefSubReporterNode", "Generated chapters", [chapter.model_dump() for chapter in chapters],
        )
        logger.info("[BriefSubReporterNode] End BriefSubReporterNode, next_node=%s", next_node)
        return {"next_node": next_node}


class BriefReporterNode(BaseNode):
    """生成一次摘要并组装最终 Brief 报告。"""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info("[BriefReporterNode] Start BriefReporterNode.")
        state = _state(session)
        if state.outline is None or state.collection is None or not state.chapters:
            logger.warning("[BriefReporterNode] Missing outline, collection, or chapters, skip report assembly.")
            return {"skip": True}
        _log_node_detail(
            "BriefReporterNode", "current_inputs",
            {"outline": state.outline.model_dump(), "collection": state.collection.model_dump(),
             "chapters": [chapter.model_dump() for chapter in state.chapters]},
        )
        intent = session.get_global_state("search_context.research_intent") or {}
        return {
            "state": state,
            "report_task": session.get_global_state("search_context.original_query") or "",
            "request": BriefSummaryRequest(
                llm=_llm(session, NodeId.BRIEF_REPORTER), title=state.outline.title,
                language=session.get_global_state("search_context.language") or "zh-CN",
                audience_role=intent.get("audience_role", ""), tone=intent.get("tone", ""),
                user_format=session.get_global_state("search_context.report_template") or "",
                chapters=state.chapters, section_evidence=state.collection.section_evidence,
                citation_registry=state.collection.citation_registry,
                writing_guidance=state.evidence_review.writing_guidance if state.evidence_review else None,
            ),
        }

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """生成摘要后确定性拼装 Markdown 报告。"""
        try:
            pre_output = self._pre_handle(inputs, session, context)
            if pre_output.get("skip"):
                return self._post_handle(inputs, pre_output, session, context)
            summary = await generate_brief_summary(pre_output["request"])
            state = pre_output["state"]
            state.executive_summary = summary
            assembly = assemble_brief_report(
                BriefAssemblyRequest(
                    title=state.outline.title,
                    language=pre_output["request"].language,
                    executive_summary=summary,
                    chapters=state.chapters,
                    citation_registry=state.collection.citation_registry,
                    section_order={section.id: index for index, section in enumerate(state.outline.sections)},
                )
            )
        except Exception as exc:
            return _finish_brief_node_failure(
                session,
                BriefNodeFailureContext(
                    node_id=NodeId.BRIEF_REPORTER,
                    node_name="BriefReporterNode",
                    stage="Report generation",
                    status_code=StatusCode.REPORT_GENERATE_ERROR,
                ),
                exc,
            )
        return self._post_handle(
            inputs, {**pre_output, "state": state, "summary": summary, "assembly": assembly}, session, context,
        )

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> Output:
        if algorithm_output.get("skip"):
            next_node = NodeId.END.value
            logger.info("[BriefReporterNode] End BriefReporterNode, next_node=%s", next_node)
            return {"next_node": next_node}
        state = algorithm_output["state"]
        summary = algorithm_output["summary"]
        assembly = algorithm_output["assembly"]
        current_report = Report(
            report_task=algorithm_output["report_task"],
            report_content=assembly.report_content,
            merged_trace_source_datas=assembly.merged_trace_source_datas,
            all_classified_contents=[[item.model_dump() for item in state.collection.citation_registry]],
        )
        session.update_global_state(
            {
                "search_context.brief_state": state.model_dump(),
                "search_context.current_report": current_report,
            }
        )
        next_node = NodeId.BRIEF_SOURCE_TRACER.value
        logger.info(
            "[BriefReporterNode] Generated and assembled report chapters=%d citations=%d.",
            len(state.chapters), len(state.collection.citation_registry),
        )
        _log_node_detail(
            "BriefReporterNode",
            "Generated and assembled report",
            {"executive_summary": summary, **assembly.model_dump()},
        )
        logger.info("[BriefReporterNode] End BriefReporterNode, next_node=%s", next_node)
        return {"next_node": next_node}


class BriefHtmlReporterNode(BaseNode):
    """把溯源校验后的 Brief 报告 md 转写为自包含 HTML 并写回最终产物。"""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext) -> dict:
        logger.info("[BriefHtmlReporterNode] Start BriefHtmlReporterNode.")
        current_report = session.get_global_state("search_context.current_report")
        markdown = getattr(current_report, "checked_trace_source_report_content", "") or ""
        if not markdown:
            markdown = getattr(current_report, "report_content", "") or ""
        language = session.get_global_state("search_context.language") or "zh-CN"
        _log_node_detail(
            "BriefHtmlReporterNode", "current_inputs",
            {"markdown_chars": len(markdown), "language": language},
        )
        return {
            "llm": _llm(session, NodeId.BRIEF_HTML_REPORTER),
            "markdown": markdown,
            "language": language,
            "current_report": current_report,
        }

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        try:
            pre_output = self._pre_handle(inputs, session, context)
            html = await generate_brief_html_report(
                llm=pre_output["llm"],
                markdown=pre_output["markdown"],
                language=pre_output["language"],
            )
        except Exception as exc:
            return self._fallback_to_markdown(session, exc)
        return self._post_handle(inputs, {**pre_output, "html": html}, session, context)

    def _fallback_to_markdown(self, session: Session, exc: Exception) -> Output:
        """HTML 转写重试耗尽后降级保留 markdown 产物，不让报告整体失败。

        HTML 是增值产物：markdown 报告已由 SourceTracer 写入 final_result
        （response_content_type=text/markdown）。降级与专业版 VLM 图表生成
        失败的处理一致——写 warning_info、正常流转 END，不抛
        REPORT_GENERATE_ERROR。
        """
        detail = "Html report generation fallback." if LogManager.is_sensitive() else str(exc)
        logger.warning(
            "[BriefHtmlReporterNode] Html generation failed after retries, fallback to markdown report; error=%s.",
            detail,
        )
        session.update_global_state(
            {
                "search_context.final_result.warning_info": (
                    f"brief html report generation failed, fallback to markdown: {detail}"
                )
            }
        )
        next_node = NodeId.END.value
        logger.info("[BriefHtmlReporterNode] End BriefHtmlReporterNode, next_node=%s", next_node)
        return {"next_node": next_node}

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext) -> Output:
        html = algorithm_output["html"]
        current_report = algorithm_output["current_report"]
        if current_report is not None:
            current_report.report_html = html
        session.update_global_state(
            {
                "search_context.final_result.response_content": html,
                "search_context.final_result.response_content_type": "text/html",
                "search_context.current_report": current_report,
            }
        )
        next_node = NodeId.END.value
        _log_node_detail("BriefHtmlReporterNode", "Generated html report", {"chars": len(html)})
        logger.info("[BriefHtmlReporterNode] End BriefHtmlReporterNode, next_node=%s", next_node)
        return {"next_node": next_node}
