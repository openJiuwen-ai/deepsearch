# -*- coding: UTF-8 -*-
"""专业版主链用户停止（StopRequested）传播的行为测试。

实机事故延续（2026-10-14 停止恢复系列）：修复 10 在 llm_utils 注入停止
检查点后，StopRequested 开始流入专业版旧代码未设防的 ``except Exception``
与 ``gather(return_exceptions=True)`` 分流路径——停止被吞成"局部失败降级"
或"空内容假完成"，图引擎误判正常完成清除 checkpoint，resume 只能从头重跑。
本文件逐点验证：

1. stop_control.raise_if_stop_requested_in_results 分流检查；
2. visualization / chart_generator / figure_placeholders / data_collector /
   webpage_enrichment 的停止穿透与 gather 分流；
3. evaluation / citation_verify_research 的 gather_with_stop 无孤儿收口。
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent import stop_control
from openjiuwen_deepsearch.framework.openjiuwen.agent.stop_control import (
    STOP_REQUESTED_MARKER,
    StopRequested,
    clear_stop,
    gather_results_with_stop,
    raise_if_stop_requested_in_results,
    request_stop,
)

pytestmark = pytest.mark.unit

_STOP = StopRequested("deepresearch task stopped by user")

VIZ_MODULE = "openjiuwen_deepsearch.algorithm.report.visualization"
CG_MODULE = "openjiuwen_deepsearch.algorithm.chart_generation.chart_generator"
CG_UTILS_MODULE = "openjiuwen_deepsearch.algorithm.chart_generation.utils"
FP_MODULE = "openjiuwen_deepsearch.algorithm.chart_generation.figure_placeholders"
DC_MODULE = "openjiuwen_deepsearch.algorithm.chart_generation.data_collector"
WE_MODULE = (
    "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment"
)
EV_MODULE = "openjiuwen_deepsearch.algorithm.brief_report.evaluation"


@pytest.fixture(autouse=True)
def _ensure_stop_flag_clean():
    """每个用例前后清零停止标志，避免全局单例跨用例泄漏。"""
    clear_stop()
    yield
    clear_stop()


def _stop_mock() -> AsyncMock:
    """每次调用都抛 StopRequested 的异步桩；配合 await_count 断言停止不空转重试。"""
    return AsyncMock(side_effect=_STOP)


# ---------------------------------------------------------------------------
# stop_control：gather(return_exceptions=True) 结果分流检查
# ---------------------------------------------------------------------------


def test_raise_if_stop_requested_in_results_reraises_stop():
    results = [ValueError("普通失败"), _STOP, {"ok": 1}]
    with pytest.raises(StopRequested):
        raise_if_stop_requested_in_results(results)


def test_raise_if_stop_requested_in_results_ignores_other_exceptions():
    """非 StopRequested 异常项不做分流拦截，交由调用方的通用分流处理。"""
    raise_if_stop_requested_in_results([ValueError("x"), None, {"ok": 1}])


def test_raise_if_stop_requested_in_results_empty_ok():
    raise_if_stop_requested_in_results([])


# ---------------------------------------------------------------------------
# visualization：LLM 产码/校验链停止穿透 + gather 分流
# ---------------------------------------------------------------------------

from openjiuwen_deepsearch.algorithm.report.visualization import VisualizationMixin


def _make_viz() -> VisualizationMixin:
    viz = VisualizationMixin()
    viz._llm = object()
    # MarkdownProcessorMixin 依赖补齐（本测试只测 VisualizationMixin 自身）
    viz.strip_leading_number = lambda s: s
    return viz


@pytest.mark.asyncio
async def test_visualization_extract_stop_not_swallowed_as_failure(monkeypatch):
    """数据提取链停止必须上抛，不得降级为 rs_success=False 的失败返回。"""
    invoke = _stop_mock()
    monkeypatch.setattr(VIZ_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(VIZ_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    with pytest.raises(StopRequested):
        await _make_viz()._extract_data_from_text({"section_idx": 1})
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_visualization_compliance_stop_not_retried(monkeypatch):
    """合规校验重试循环遇停止必须立即上抛，不得烧完重试预算。"""
    invoke = _stop_mock()
    monkeypatch.setattr(VIZ_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(VIZ_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    with pytest.raises(StopRequested):
        await _make_viz()._validate_chart_compliance("{}", 1, "大纲", 3)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_visualization_traceability_stop_not_retried(monkeypatch):
    """溯源校验重试循环遇停止必须立即上抛，不得烧完重试预算。"""
    invoke = _stop_mock()
    monkeypatch.setattr(VIZ_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(VIZ_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    with pytest.raises(StopRequested):
        await _make_viz()._validate_chart_traceability("{}", "原文", 1, 3)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_visualization_gather_reraises_stop_before_aggregation(monkeypatch):
    """return_exceptions=True 把停止压平成结果项后必须先于汇总分流重抛，
    不得置空内容吞成"无图表"假完成。"""
    invoke = _stop_mock()
    monkeypatch.setattr(VIZ_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(VIZ_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    viz = _make_viz()
    current_inputs = {
        "section_idx": 1,
        "sub_section_outline": "图表大纲",
        "section_task": "1. 测试章节",
        "classified_content": [
            {"is_fulltext": True, "data_density": 0.95, "passage_text": "内容甲"},
            {"is_fulltext": True, "data_density": 0.95, "passage_text": "内容乙"},
        ],
    }
    with pytest.raises(StopRequested):
        await viz.generate_content_for_visualization(current_inputs)
    # 两个任务各触发一次 LLM 后即停止，无重试空转
    assert invoke.await_count == 2


# ---------------------------------------------------------------------------
# chart_generator：产码/VLM 链停止穿透 + 双层 gather 分流
# ---------------------------------------------------------------------------

from openjiuwen_deepsearch.algorithm.chart_generation.chart_generator import (
    ChartGenerator,
)


def _chart_tasks() -> dict:
    return {
        1: [
            {
                "chart_title": "测试图",
                "description": "测试图描述",
                "chart_type": "bar",
                "data": {"a": 1, "b": 2, "c": 3},
                "section_index": 1,
                "chart_id_in_section": 1,
            }
        ]
    }


@pytest.mark.asyncio
async def test_chart_generator_stop_propagates_from_generate_charts(
    monkeypatch, tmp_path
):
    """产码 LLM 链停止必须逐层上抛：_generate_chart_code → _generate_single_chart
    → 章节级/全局级 gather 分流 → generate_charts，不得包装成
    CustomValueException 或降级为"无图表"。"""
    invoke = _stop_mock()
    monkeypatch.setattr(CG_MODULE + ".call_model", invoke)
    gen = ChartGenerator("mock_model", str(tmp_path))
    with pytest.raises(StopRequested):
        await gen.generate_charts(_chart_tasks())
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_chart_generator_vlm_iterate_stop_not_wrapped(monkeypatch, tmp_path):
    """VLM 迭代链停止必须原样上抛，不得包装成 CustomValueException
    被 _generate_single_chart 当普通失败降级。"""
    invoke = _stop_mock()
    monkeypatch.setattr(CG_MODULE + ".call_model", invoke)
    gen = ChartGenerator("mock_model", str(tmp_path), vlm_model_name="mock_vlm")
    with pytest.raises(StopRequested):
        await gen._vlm_iterate("base64", {"chart_title": "测试图"}, [])
    assert invoke.await_count == 1


# ---------------------------------------------------------------------------
# figure_placeholders：占位符识别链停止穿透 + gather 分流
# ---------------------------------------------------------------------------

from openjiuwen_deepsearch.algorithm.chart_generation.figure_placeholders import (
    FigurePlaceholderGenerator,
)


@pytest.mark.asyncio
async def test_figure_placeholder_run_stop_propagates(monkeypatch):
    """占位符识别全链停止必须上抛：_process_section_h2 → _process_section_h1
    分流 → generate_chart_tasks 分流 → run，不得包装成业务异常。"""
    invoke = _stop_mock()
    monkeypatch.setattr(FP_MODULE + ".call_model", invoke)
    gen = FigurePlaceholderGenerator("mock_model")
    report = (
        "# 报告标题\n\n"
        "# 1. 第一章\n\n## 1.1 小节\n\n第一章正文\n\n"
        "# 2. 第二章\n\n## 2.1 小节\n\n第二章正文\n"
    )
    with pytest.raises(StopRequested):
        await gen.run(report)
    # 两个二级章节各调用一次 LLM 后即停止，无重试空转
    assert invoke.await_count == 2


# ---------------------------------------------------------------------------
# data_collector：数据收集链停止穿透 + gather 分流
# ---------------------------------------------------------------------------

from openjiuwen_deepsearch.algorithm.chart_generation.data_collector import (
    ChartDataCollector,
)


@pytest.mark.asyncio
async def test_data_collector_run_stop_propagates(monkeypatch):
    """批次收集 LLM 链停止必须上抛：_collect_batch_tasks → 章节级/全局级
    gather 分流 → run，不得包装成业务异常或降级为空数据。"""
    invoke = _stop_mock()
    monkeypatch.setattr(DC_MODULE + ".call_model", invoke)
    collector = ChartDataCollector("mock_model")
    meta_collect_tasks = {1: [{"collection_tasks": ["task-1", "task-2"]}]}
    all_classified_contents = [[{"original_content": "信息源内容"}]]
    with pytest.raises(StopRequested):
        await collector.run(meta_collect_tasks, all_classified_contents)
    assert invoke.await_count == 1


# ---------------------------------------------------------------------------
# webpage_enrichment：候选选择/正文压缩链停止穿透 + gather 分流
# ---------------------------------------------------------------------------

from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment import (
    WebPageEnrichmentNode,
)


def _make_enrichment_node() -> WebPageEnrichmentNode:
    node = WebPageEnrichmentNode()
    node.llm = object()
    return node


@pytest.mark.asyncio
async def test_webpage_enrichment_select_stop_propagates(monkeypatch):
    """候选选择链停止必须上抛，不得降级为空列表的"正常完成"。"""
    invoke = _stop_mock()
    monkeypatch.setattr(WE_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(WE_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    node = _make_enrichment_node()
    state = {
        "candidates": [{"url": "https://example.com", "doc_id": "d1"}],
        "max_urls": 3,
        "section_idx": 1,
        "step_title": "测试步骤",
    }
    with pytest.raises(StopRequested):
        await node._select_candidate_indexes(state)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_webpage_enrichment_compress_stop_propagates(monkeypatch):
    """正文压缩链停止必须上抛，不得降级为 None 的"正常完成"。"""
    invoke = _stop_mock()
    monkeypatch.setattr(WE_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(WE_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    node = _make_enrichment_node()
    state = {"section_idx": 1, "step_title": "测试步骤"}
    doc_info = {"query": "查询", "title": "标题", "doc_id": "d1", "url": "https://example.com"}
    fetched = {"content": "抓取正文内容", "title": "标题"}
    with pytest.raises(StopRequested):
        await node._compress_content(state, doc_info, fetched)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_webpage_enrichment_gather_reraises_stop_before_result_loop(monkeypatch):
    """候选增强 gather 分流：停止必须先于结果循环重抛，不得被逐项 continue 吞掉。"""
    monkeypatch.setattr(
        WE_MODULE + ".WebPageEnrichmentNode._enrich_candidate",
        AsyncMock(side_effect=_STOP),
    )
    node = _make_enrichment_node()
    state = {
        "new_doc_infos_current_loop": [{"doc_id": "d1"}],
        "doc_infos": [],
        "source_store": {},
        "history_queries": [],
        "section_idx": 1,
        "step_title": "测试步骤",
    }
    with pytest.raises(StopRequested):
        await node._enrich_selected_candidates(state, [0, 1])


# ---------------------------------------------------------------------------
# gather_with_stop：停止先取消全部兄弟协程再上抛（无孤儿任务）
# ---------------------------------------------------------------------------

from openjiuwen_deepsearch.algorithm.brief_report.evaluation import (
    evaluate_brief_sections,
)
from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefOutline,
    BriefSearchResult,
)
from openjiuwen_deepsearch.algorithm.source_trace.citation_verify_research import (
    CitationVerifyResearch,
)


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


def _search_result() -> BriefSearchResult:
    return BriefSearchResult(
        source_id="s1",
        title="数据来源",
        url="https://example.com/data",
        snippet="证据摘要",
        search_rank=1,
        section_ids=["1"],
    )


@pytest.mark.asyncio
async def test_evaluation_stop_cancels_remaining_sections(monkeypatch):
    """分章节评估 gather_with_stop：停止章节抛出后其余章节的评估协程
    必须被取消，不得孤儿化继续烧 LLM。"""
    completed = []
    calls = {"n": 0}

    async def fake_invoke(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _STOP
        await asyncio.sleep(0.25)
        completed.append(args)
        return {"content": "{}"}

    monkeypatch.setattr(EV_MODULE + ".ainvoke_llm_with_stats", fake_invoke)
    with pytest.raises(StopRequested):
        await evaluate_brief_sections(
            object(),
            _outline(),
            {"1": [_search_result()], "2": [_search_result()]},
        )
    assert calls["n"] >= 1
    # 停止重抛前另一章节的慢评估协程已被取消，未跑完落地
    assert completed == []


@pytest.mark.asyncio
async def test_citation_split_retry_stop_cancels_sibling_branch(monkeypatch):
    """二分重试分支 gather_with_stop：停止先取消另一分支再上抛，
    不孤儿化继续烧 LLM 重试。"""
    verifier = CitationVerifyResearch("mock_model")
    completed = []
    calls = {"n": 0}

    async def fake_once(handle_datas, semaphore):
        calls["n"] += 1
        if calls["n"] == 1:
            # 根批次普通失败，触发二分重试
            raise ValueError("invocation failure")
        if calls["n"] == 2:
            raise _STOP
        await asyncio.sleep(0.25)
        completed.append(list(handle_datas))
        return [{} for _ in handle_datas]

    monkeypatch.setattr(verifier, "extract_messages_once", fake_once)
    handle_datas = [
        {"domain": f"d{i}", "citation_content": "c", "fact": "f"} for i in range(4)
    ]
    with pytest.raises(StopRequested):
        await verifier.extract_messages_with_split_retry(
            handle_datas, attempt=1, lineage="root", semaphore=asyncio.Semaphore(2)
        )
    assert calls["n"] >= 2
    # 兄弟分支被取消，未跑完落地
    assert completed == []


# ---------------------------------------------------------------------------
# chart 公共包装器（call_model）真实穿透：停止不得落入通用重试分支
# ---------------------------------------------------------------------------

from openjiuwen_deepsearch.algorithm.chart_generation.utils import (
    CallModelInput,
    call_model as real_call_model,
)


@pytest.mark.asyncio
async def test_chart_call_model_real_wrapper_stop_propagates_without_retry(monkeypatch):
    """保留真实 call_model 包装器（非桩替换），从底层 ainvoke_llm_with_stats
    抛停止：不得被通用 except Exception 当可重试错误烧完 3 次预算后降级
    返回 []（产码/占位/采集/VLM 外层的停止透传将全部不可达）。"""
    invoke = _stop_mock()
    monkeypatch.setattr(CG_UTILS_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(CG_UTILS_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    monkeypatch.setattr(
        CG_UTILS_MODULE + ".llm_context",
        SimpleNamespace(get=lambda: {"": object()}),
    )

    with pytest.raises(StopRequested):
        await real_call_model(CallModelInput())

    # 停止只尝试一次，不空转烧重试预算
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_vlm_chart_generator_node_stop_not_downgraded_to_error_msg(monkeypatch):
    """VLMChartGeneratorNode 停止必须上抛，不得被通用 Exception 分支
    吞成 error_msg 的"正常完成"继续转 source_tracer 并清除 checkpoint。"""
    from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import (
        VLMChartGeneratorNode,
    )

    node = VLMChartGeneratorNode()
    monkeypatch.setattr(
        VLMChartGeneratorNode,
        "_pre_handle",
        lambda self, inputs, session, context: {
            "vlm_chart_generator_enable": True,
            "visualization_enable": False,
        },
    )

    async def _raise_stop(inputs):
        raise _STOP

    monkeypatch.setattr(node, "_run_vlm_chart_generator_handle", _raise_stop)

    with pytest.raises(StopRequested):
        await node._do_invoke(inputs={}, session=object(), context=object())


# ---------------------------------------------------------------------------
# gather_results_with_stop：保留普通失败压平语义 + 停止秒级取消慢兄弟
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gather_results_with_stop_keeps_plain_failure_flattened(monkeypatch):
    """普通异常仍压平为结果项返回（保留 return_exceptions 分流语义），
    不中断其余协程。"""
    monkeypatch.setattr(stop_control, "_GATHER_STOP_POLL_INTERVAL", 0.05)

    async def _plain_failure():
        raise ValueError("普通候选失败")

    async def _success():
        return {"ok": 1}

    results = await asyncio.wait_for(
        gather_results_with_stop(_plain_failure(), _success()), timeout=5
    )

    assert isinstance(results[0], ValueError)
    assert results[1] == {"ok": 1}


@pytest.mark.asyncio
async def test_gather_results_with_stop_cancels_slow_sibling_on_stop(monkeypatch):
    """停止命中后慢协程（如长网络抓取）必须被取消收口，不等待其自然
    结束（原生 gather 会拖满抓取预算才让停止可见）。"""
    monkeypatch.setattr(stop_control, "_GATHER_STOP_POLL_INTERVAL", 0.05)
    completed = []

    async def _fast_stop():
        # 模拟压缩阶段看到停止标志：置位后立即抛出
        request_stop()
        raise _STOP

    async def _slow_fetch():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        completed.append("slow")
        return {"ok": 1}

    started = time.monotonic()
    with pytest.raises(StopRequested):
        await asyncio.wait_for(
            gather_results_with_stop(_fast_stop(), _slow_fetch()), timeout=10
        )

    # 秒级传播（未等满 30s 抓取），慢协程被取消未落地
    assert time.monotonic() - started < 10
    assert completed == []


@pytest.mark.asyncio
async def test_webpage_enrichment_stop_cancels_slow_fetch_sibling(monkeypatch):
    """候选增强混合并发（一条压缩停止 + 一条慢抓取）：停止必须秒级传播
    并取消慢抓取候选，不等待其跑完全程。"""
    monkeypatch.setattr(stop_control, "_GATHER_STOP_POLL_INTERVAL", 0.05)
    completed = []

    async def _fake_enrich_candidate(self, *, state, loop_docs, candidate_index):
        if candidate_index == 0:
            # 压缩阶段命中停止：置位后立即抛出
            request_stop()
            raise _STOP
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        completed.append(candidate_index)
        return None

    monkeypatch.setattr(
        WE_MODULE + ".WebPageEnrichmentNode._enrich_candidate",
        _fake_enrich_candidate,
    )
    node = _make_enrichment_node()
    state = {
        "new_doc_infos_current_loop": [{"doc_id": "d1"}],
        "doc_infos": [],
        "source_store": {},
        "history_queries": [],
        "section_idx": 1,
        "step_title": "测试步骤",
    }

    started = time.monotonic()
    with pytest.raises(StopRequested):
        await asyncio.wait_for(
            node._enrich_selected_candidates(state, [0, 1]), timeout=10
        )

    assert time.monotonic() - started < 10
    assert completed == []


# ---------------------------------------------------------------------------
# 实机事故 2026-10-20：professional 模式调研链路 retry 循环吞停止
# ---------------------------------------------------------------------------

PLANNER_MODULE = "openjiuwen_deepsearch.algorithm.query_understanding.planner"
OUTLINER_MODULE = "openjiuwen_deepsearch.algorithm.query_understanding.outliner"
INTERPRETER_MODULE = "openjiuwen_deepsearch.algorithm.query_understanding.interpreter"
OMR_MODULE = "openjiuwen_deepsearch.algorithm.query_understanding.outline_mode_router"
IR_MODULE = "openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition"
GB_MODULE = (
    "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder"
)
IC_MODULE = (
    "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector"
)
SM_MODULE = "openjiuwen_deepsearch.algorithm.source_trace.source_matcher"
CA_MODULE = "openjiuwen_deepsearch.algorithm.source_trace.content_analyzer"


def _fake_llm_registry(monkeypatch, module: str):
    """打桩模块级 llm_context，避免依赖真实 contextvar 注册表。"""
    monkeypatch.setattr(
        module + ".llm_context", SimpleNamespace(get=lambda: {"mock_llm": object()})
    )


@pytest.mark.asyncio
async def test_planner_retry_stop_not_swallowed_as_error_msg(monkeypatch):
    """实机事故铁证路径：planner retry 循环遇停止必须立即上抛，
    不得烧完 3 次重试预算后把停止写入 error_msg 数据"正常"返回。"""
    _fake_llm_registry(monkeypatch, PLANNER_MODULE)
    monkeypatch.setattr(PLANNER_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    monkeypatch.setattr(PLANNER_MODULE + ".build_runtime_api_tools", lambda *a, **k: [])
    monkeypatch.setattr(
        PLANNER_MODULE + ".merge_runtime_api_tools", lambda tools, extra: tools
    )
    invoke = _stop_mock()
    monkeypatch.setattr(PLANNER_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.query_understanding.planner import (
        Planner,
        PlannerConfig,
    )

    planner = Planner(PlannerConfig(max_retry_num=3))
    with pytest.raises(StopRequested):
        await planner.generate_plan({"section_idx": 1, "plan_executed_num": 0})
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_outliner_stop_not_swallowed_as_error_msg(monkeypatch):
    """大纲生成 retry 循环遇停止必须上抛，不得降级为 error_msg 数据返回。"""
    _fake_llm_registry(monkeypatch, OUTLINER_MODULE)
    monkeypatch.setattr(OUTLINER_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    monkeypatch.setattr(OUTLINER_MODULE + ".build_runtime_api_tools", lambda *a, **k: [])
    monkeypatch.setattr(
        OUTLINER_MODULE + ".merge_runtime_api_tools", lambda tools, extra: tools
    )
    invoke = _stop_mock()
    monkeypatch.setattr(OUTLINER_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.query_understanding.outliner import Outliner

    outliner = Outliner("mock_llm", "outliner")
    with pytest.raises(StopRequested):
        await outliner.generate_outline({"section_num": 5})
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_query_interpreter_stop_not_swallowed_as_exception_info(monkeypatch):
    """问题澄清生成遇停止必须上抛，不得降级为 exception_info 数据返回。"""
    _fake_llm_registry(monkeypatch, INTERPRETER_MODULE)
    monkeypatch.setattr(INTERPRETER_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    invoke = _stop_mock()
    monkeypatch.setattr(INTERPRETER_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.query_understanding.interpreter import (
        query_interpreter,
    )

    with pytest.raises(StopRequested):
        await query_interpreter({"query": "q", "llm_model_name": "mock_llm"})
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_outline_mode_router_stop_not_defaulted_to_parallel(monkeypatch):
    """大纲模式路由遇停止必须上抛，不得静默降级为 parallel 默认模式。"""
    _fake_llm_registry(monkeypatch, OMR_MODULE)
    invoke = _stop_mock()
    monkeypatch.setattr(OMR_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.query_understanding.outline_mode_router import (
        route_outline_execution_method,
    )

    with pytest.raises(StopRequested):
        await route_outline_execution_method("研究问题", "mock_llm")
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_intent_recognition_stop_not_swallowed_as_fallback(monkeypatch):
    """意图识别遇停止必须上抛，不得降级为 _default_fallback 数据返回。"""
    async def _stop_intent(*args, **kwargs):
        raise _STOP

    monkeypatch.setattr(IR_MODULE + "._invoke_llm_for_intent", _stop_intent)

    from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import (
        classify_and_recognize_intent,
    )

    with pytest.raises(StopRequested):
        await classify_and_recognize_intent(
            {"original_query": "研究主题", "llm_model_name": "mock_llm"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("node_cls,method,args", [
    ("GenerateQueryNode", "_invoke_llm_with_retry", ([], 1, "t", 3)),
    ("SupervisorNode", "_invoke_llm_with_retry", ([], 1, "t", 3)),
])
async def test_graph_builder_retry_stop_not_swallowed_as_fallback(
    monkeypatch, node_cls, method, args
):
    """collector 子图 LLM retry 循环遇停止必须上抛，不得降级为
    fallback 查询列表 / Reflection(is_sufficient=True) 等正常返回。"""
    invoke = _stop_mock()
    monkeypatch.setattr(GB_MODULE + ".ainvoke_llm_with_stats", invoke)

    import importlib

    gb = importlib.import_module(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder"
    )
    node = getattr(gb, node_cls)()
    node.llm = object()
    with pytest.raises(StopRequested):
        await getattr(node, method)(*args)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_summary_node_retry_stop_not_swallowed(monkeypatch):
    """SummaryNode retry 循环遇停止必须上抛，不得降级为空 Summary。"""
    invoke = _stop_mock()
    monkeypatch.setattr(GB_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder import (
        SummaryNode,
    )

    node = SummaryNode()
    node.llm = object()
    with pytest.raises(StopRequested):
        await node._invoke_llm_with_retry([], {"section_idx": 1, "step_title": "t"}, [])
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_info_retrieval_retry_stop_not_swallowed(monkeypatch):
    """InfoRetrievalNode retry 循环遇停止必须上抛，不得降级为 None 继续。"""
    invoke = _stop_mock()
    monkeypatch.setattr(IC_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import (
        InfoRetrievalNode,
    )

    node = InfoRetrievalNode()
    node.llm = object()
    with pytest.raises(StopRequested):
        await node._invoke_llm_with_retry([], [], {"section_idx": 1, "step_title": "t"})
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_match_sources_stop_not_swallowed_as_empty_list(monkeypatch):
    """溯源匹配遇停止必须上抛：降级为空列表会让 source_tracer 正常返回，
    绕过 SourceTracerNode 的节点级停止穿透。"""
    _fake_llm_registry(monkeypatch, SM_MODULE)
    invoke = _stop_mock()
    monkeypatch.setattr(SM_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.source_trace.source_matcher import match_sources

    with pytest.raises(StopRequested):
        await match_sources(
            ["待溯源句"],
            {"web": [{"title": "标题", "content": "内容"}]},
            10,
            "mock_llm",
        )
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_recognize_content_to_cite_stop_not_swallowed(monkeypatch):
    """引用内容识别遇停止必须上抛，不得降级为空列表。"""
    _fake_llm_registry(monkeypatch, CA_MODULE)
    invoke = _stop_mock()
    monkeypatch.setattr(CA_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.source_trace.content_analyzer import (
        recognize_content_to_cite,
    )

    with pytest.raises(StopRequested):
        await recognize_content_to_cite("报告正文", 0.8, "mock_llm")
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_generate_sub_report_visualization_stop_not_swallowed():
    """三层连续吞链第一层：generate_sub_report 可视化段遇停止必须上抛，
    不得降级为 visualization_result=[] 继续写报告。"""
    from openjiuwen_deepsearch.algorithm.report.report import Reporter

    reporter = Reporter.__new__(Reporter)
    reporter._get_background_knowledge_contents = lambda x: []
    reporter._prepare_evidence = AsyncMock(return_value=(True, None, []))
    reporter._generate_outline_with_retry = AsyncMock(return_value=(True, None))
    reporter._generate_content_for_visualization = _stop_mock()

    current_inputs = {
        "passages": ["p"],
        "visualization_enable": True,
        "max_generate_retry_num": 3,
        "section_idx": 1,
    }
    with pytest.raises(StopRequested):
        await reporter.generate_sub_report(current_inputs)
    assert reporter._generate_content_for_visualization.await_count == 1


@pytest.mark.asyncio
async def test_post_process_subsection_visualization_stop_not_swallowed():
    """三层连续吞链第二层：可视化插图段遇停止必须上抛，
    不得"警告后使用原内容继续"。"""
    from openjiuwen_deepsearch.algorithm.report.report import Reporter

    reporter = Reporter.__new__(Reporter)
    reporter._clean_internal_callback_labels = lambda s: s
    reporter._contains_mermaid_source = lambda s: False
    reporter._insert_visualization = _stop_mock()

    current_inputs = {
        "sub_report_content": "章节正文",
        "visualization_enable": True,
        "section_idx": 1,
    }
    with pytest.raises(StopRequested):
        await reporter._post_process_subsection(current_inputs)
    assert reporter._insert_visualization.await_count == 1


# ---------------------------------------------------------------------------
# init_state 停止传播：停止不得被包装为 AGENT_INIT_STATE_ERROR（那会遮蔽
# 停止标记，外层按标记识别的兜底失效误删快照）。实机链路中 init_state_1
# 子工作流节点抛出的 StopRequested 会被 openjiuwen vertex 层包装成
# BaseError[100054]（reason=str(e) 保留标记但类型丢失），故裸异常与包装
# 异常两种形态都必须原样上抛。
# ---------------------------------------------------------------------------

WF_MODULE = "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow"


def _make_search_agent_and_context(tmp_path):
    from openjiuwen_deepsearch.config.config import AgentConfig, SearchWorkflowConfig
    from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepSearchAgent

    agent = DeepSearchAgent()
    agent_config = AgentConfig()
    per_question_params = agent_config.search_workflow_per_question_params.model_copy(
        update={
            "max_workers": 1,
            "retry_count_on_empty_action_space": 0,
            "time_limit": 120,
            "actions_explored_limit": 0,
            "fail_limit": 0,
            "answer_mode_top_k": 1,
            "provide_best_guess": False,
        }
    )
    run_context = agent._create_run_context(
        agent_config=agent_config,
        per_question_params=per_question_params,
        search_config=SearchWorkflowConfig(),
        query="capital question",
        log_dir=str(tmp_path),
        time_limit=120,
        tool_map={},
    )
    return agent, run_context


def _raise_in_init_state(monkeypatch, exc: BaseException):
    """打桩 Runner.run_workflow：init_state_1 抛给定异常，其余工作流报错。"""

    async def _fake_run_workflow(*, workflow, inputs):
        if workflow == "init_state_1":
            raise exc
        raise AssertionError(f"unexpected workflow: {workflow}")

    monkeypatch.setattr(f"{WF_MODULE}.Runner.run_workflow", _fake_run_workflow)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(
            StopRequested("deepresearch task stopped by user"),
            id="raw-stop-requested",
        ),
        pytest.param(
            RuntimeError(
                "[100054] component 'initialize_state' execute 'invoke' error, "
                "reason='deepresearch task stopped by user', workflow='init_state'"
            ),
            id="vertex-wrapped-stop",
        ),
    ],
)
async def test_init_state_stop_not_wrapped_as_init_state_error(
    monkeypatch, tmp_path, exc
):
    """init_state 遇停止（裸 / vertex 包装两形态）必须原样上抛，
    不得包装为 AGENT_INIT_STATE_ERROR 遮蔽停止标记。"""
    agent, run_context = _make_search_agent_and_context(tmp_path)
    _raise_in_init_state(monkeypatch, exc)

    with pytest.raises(type(exc)) as exc_info:
        await agent._run_internal(run_context)
    assert STOP_REQUESTED_MARKER in str(exc_info.value)


@pytest.mark.asyncio
async def test_init_state_ordinary_error_still_wrapped(monkeypatch, tmp_path):
    """防回归：普通异常（不含停止标记）仍照常包装为 AGENT_INIT_STATE_ERROR，
    防止标记识别过度匹配把真错误当停止上抛。"""
    from openjiuwen_deepsearch.common.exception import CustomValueException
    from openjiuwen_deepsearch.common.status_code import StatusCode

    agent, run_context = _make_search_agent_and_context(tmp_path)
    _raise_in_init_state(monkeypatch, ValueError("boom"))

    with pytest.raises(CustomValueException) as exc_info:
        await agent._run_internal(run_context)
    assert exc_info.value.error_code == StatusCode.AGENT_INIT_STATE_ERROR.code


# ---------------------------------------------------------------------------
# reviewer 核实 2026-10-21（P1 修订）：终检兜底无效，图引擎在
# CompiledGraph._invoke 内部的 post_workflow_execute 正常完成分支中已清除
# checkpoint，本层跳过 release 保不住已被删除的快照。修订方案：
# 1. report 链摘要/结论段补穿透 + gather_with_stop 并发收口；
# 2. ReporterNode 失败分支返回 END 前节点级停止检查（对照实验注入位置）；
# 3. 第三档吞没点全量穿透。
# ---------------------------------------------------------------------------

RP_MODULE = "openjiuwen_deepsearch.algorithm.report.report"
PARTS_MODULE = "openjiuwen_deepsearch.algorithm.report.report_parts"
SSO_MODULE = "openjiuwen_deepsearch.algorithm.report.sub_section_outline"
VI_MODULE = "openjiuwen_deepsearch.algorithm.report.visualization_insertion"
EV_REPORT_MODULE = "openjiuwen_deepsearch.algorithm.report.evidence"
MGN_MODULE = "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes"


def _make_reporter():
    from openjiuwen_deepsearch.algorithm.report.report import Reporter

    reporter = Reporter.__new__(Reporter)
    reporter._llm = object()
    reporter.strip_leading_number = lambda s: s
    return reporter


@pytest.mark.asyncio
async def test_generate_report_abstract_stop_propagates_and_cancels_conclusion():
    """摘要/结论段停止穿透 + 并发收口：abstract 抛停止时 conclusion 孤儿
    任务必须被取消（gather_with_stop），不得降级为 (False, error) 数据返回
    ——那会让图引擎按正常完成清 checkpoint。"""
    from openjiuwen_deepsearch.algorithm.report.report import Reporter

    reporter = Reporter.__new__(Reporter)
    reporter._set_context_variables = lambda x: True
    reporter.gen_report_context = {"language": "zh-CN"}

    async def _fake_process_sub_report():
        return {
            "sub_reports_content": "章节内容",
            "refreshed_all_classified_contents": [],
            "sub_references": "",
        }

    reporter._process_sub_report = _fake_process_sub_report
    reporter._build_reporter_compact_context = lambda target: ""

    completed = []

    async def _stop_abstract(content):
        raise _STOP

    async def _slow_conclusion(content):
        try:
            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        completed.append("conclusion")
        return "结论"

    reporter.generate_abstract = _stop_abstract
    reporter.generate_conclusion = _slow_conclusion

    with pytest.raises(StopRequested):
        await reporter.generate_report({"language": "zh-CN"})
    # conclusion 孤儿任务被取消，未跑完落地
    assert completed == []


@pytest.mark.asyncio
async def test_write_subsection_reports_stop_not_swallowed(monkeypatch):
    """章节正文生成遇停止必须上抛，不得降级为 retry 反馈数据
    （那会驱动重试循环跑完整个报告阶段）。"""
    monkeypatch.setattr(RP_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    invoke = _stop_mock()
    monkeypatch.setattr(RP_MODULE + ".ainvoke_llm_with_stats", invoke)

    reporter = _make_reporter()
    reporter._get_background_knowledge_contents = lambda x: []
    reporter.gen_report_context = {}

    current_inputs = {
        "section_idx": 1,
        "section_task": "1. 测试章节",
        "sub_section_outline": "小节大纲",
        "classified_content": [{"passage_text": "内容"}],
        "language": "zh-CN",
    }
    with pytest.raises(StopRequested):
        await reporter._write_subsection_reports(current_inputs)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_reporter_node_failed_report_with_stop_raises_before_end():
    """ReporterNode 失败分支返回 END 前的节点级停止检查（reviewer 对照
    实验注入位置）：停止标志置位时必须抛 StopRequested 走异常保存分支
    （写入 reporter pending_node），不得返回 END 让图正常完成清快照。"""
    from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import (
        ReporterNode,
    )
    from types import SimpleNamespace

    node = ReporterNode()
    session = SimpleNamespace(
        get_global_state=lambda key: None,
        update_global_state=lambda state: None,
    )
    request_stop()
    try:
        with pytest.raises(StopRequested):
            node._post_handle(
                inputs={},
                algorithm_output={"success": False, "report_str": "[x] mock error"},
                session=session,
                context=object(),
            )
    finally:
        clear_stop()


@pytest.mark.asyncio
async def test_reporter_node_failed_report_without_stop_still_returns_end():
    """防回归：未停止时报告失败仍照常返回 END（error 语义不变）。"""
    from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import (
        ReporterNode,
    )
    from types import SimpleNamespace

    node = ReporterNode()
    session = SimpleNamespace(
        get_global_state=lambda key: None,
        update_global_state=lambda state: None,
    )
    result = node._post_handle(
        inputs={},
        algorithm_output={"success": False, "report_str": "[x] mock error"},
        session=session,
        context=object(),
    )
    from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

    assert result["next_node"] == NodeId.END.value


# --- 第三档吞没点：report_parts / sub_section_outline /
# --- visualization_insertion / evidence / search 模式 3 节点


@pytest.mark.asyncio
async def test_sub_report_transaction_stop_not_swallowed(monkeypatch):
    """过渡句生成遇停止必须上抛，不得降级为原 content 返回。"""
    monkeypatch.setattr(PARTS_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    invoke = _stop_mock()
    monkeypatch.setattr(PARTS_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.report.report_parts import ReportPartsMixin

    parts = ReportPartsMixin.__new__(ReportPartsMixin)
    parts._llm = object()
    current_inputs = {
        "section_idx": 1,
        "content": "正文",
        "summary_prev": "上一章摘要",
        "summary_next": "下一章摘要",
    }
    with pytest.raises(StopRequested):
        await parts._add_sub_report_transaction(current_inputs)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_sub_report_summary_stop_not_swallowed(monkeypatch):
    """章节摘要生成遇停止必须上抛，不得降级为 (rs_success=False, "")。"""
    monkeypatch.setattr(PARTS_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    invoke = _stop_mock()
    monkeypatch.setattr(PARTS_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.report.report_parts import ReportPartsMixin

    parts = ReportPartsMixin.__new__(ReportPartsMixin)
    parts._llm = object()
    current_inputs = {"section_idx": 1, "sub_report_content": "章节正文"}
    with pytest.raises(StopRequested):
        await parts._generate_sub_report_summary(current_inputs)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_sub_report_sidecar_stop_not_swallowed(monkeypatch):
    """章节 sidecar 生成遇停止必须上抛，不得降级为 fallback sidecar /
    进入重试循环烧完预算。"""
    monkeypatch.setattr(PARTS_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    invoke = _stop_mock()
    monkeypatch.setattr(PARTS_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.report.report_parts import ReportPartsMixin

    parts = ReportPartsMixin.__new__(ReportPartsMixin)
    parts._llm = object()
    current_inputs = {"section_idx": 1, "sub_report_content": "章节正文"}
    with pytest.raises(StopRequested):
        await parts._generate_sub_report_sidecar(current_inputs)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_sub_section_outline_stop_not_swallowed(monkeypatch):
    """小节大纲生成遇停止必须上抛，不得降级为纠错反馈数据返回。"""
    monkeypatch.setattr(SSO_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    invoke = _stop_mock()
    monkeypatch.setattr(SSO_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.report.sub_section_outline import (
        SubSectionOutlineMixin,
    )

    mixin = SubSectionOutlineMixin.__new__(SubSectionOutlineMixin)
    mixin._llm = object()
    mixin.strip_leading_number = lambda s: s
    current_inputs = {
        "section_idx": 1,
        "section_task": "1. 测试章节",
        "sub_section_core_content": [{"content": "核心信息"}],
    }
    with pytest.raises(StopRequested):
        await mixin._generate_sub_section_outline(current_inputs)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_visualization_insert_plan_stop_not_swallowed(monkeypatch):
    """可视化插图规划 LLM 调用遇停止必须上抛，不得降级为
    (rs_success=False, 原报告) 返回。"""
    monkeypatch.setattr(VI_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    invoke = _stop_mock()
    monkeypatch.setattr(VI_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.report.visualization_insertion import (
        VisualizationInsertionMixin,
        VisualizationInsertPlanContext,
    )

    mixin = VisualizationInsertionMixin.__new__(VisualizationInsertionMixin)
    mixin._llm = object()
    context = VisualizationInsertPlanContext(
        numbered_report="1 正文",
        visualization_data="[]",
        current_inputs={"section_idx": 1, "language": "zh-CN"},
        report_lines=["1 正文"],
        invalid_rows=set(),
        mermaid_map={},
        original_report="正文",
    )
    with pytest.raises(StopRequested):
        await mixin._request_visualization_insert_plan(context)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_section_rationales_stop_not_swallowed(monkeypatch):
    """章节 rationale 生成遇停止必须上抛，不得进入重试循环
    烧完 max_retries 次再降级返回。"""
    monkeypatch.setattr(EV_REPORT_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    invoke = _stop_mock()
    monkeypatch.setattr(EV_REPORT_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.report.evidence import EvidenceMixin

    mixin = EvidenceMixin.__new__(EvidenceMixin)
    mixin._llm = object()
    mixin.strip_leading_number = lambda s: s
    current_inputs = {"section_idx": 1, "section_task": "1. 测试章节"}
    with pytest.raises(StopRequested):
        await mixin._generate_section_rationales(current_inputs)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_extract_batch_stop_not_swallowed(monkeypatch):
    """passages 批次提取遇停止必须上抛，不得进入重试循环
    烧完 max_retries 次再降级返回。"""
    monkeypatch.setattr(EV_REPORT_MODULE + ".build_prompt_messages", lambda *a, **k: [])
    invoke = _stop_mock()
    monkeypatch.setattr(EV_REPORT_MODULE + ".ainvoke_llm_with_stats", invoke)

    from openjiuwen_deepsearch.algorithm.report.evidence import EvidenceMixin

    mixin = EvidenceMixin.__new__(EvidenceMixin)
    mixin._llm = object()
    batch_docs = [{"original_content": "文档内容"}]
    section_ctx = {"section_idx": 1, "section_task": "任务", "section_description": ""}
    with pytest.raises(StopRequested):
        await mixin._extract_batch(batch_docs, 0, "rationales", section_ctx)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_search_mode_nodes_stop_not_swallowed(monkeypatch):
    """search 模式 3 节点（FindActionSpace / RunAction / ValidateNewState）
    遇停止必须上抛，不得降级为 (success=False, END) fallback ——那会让
    search 图正常完成到 END，引擎清 checkpoint，续跑从头重跑。"""
    import importlib
    from types import SimpleNamespace

    mgn = importlib.import_module(MGN_MODULE)

    session = SimpleNamespace(
        get_global_state=lambda key: None,
        update_global_state=lambda state: None,
    )

    # FindActionSpaceNode
    monkeypatch.setattr(
        mgn, "run_find_action_space", AsyncMock(side_effect=_STOP)
    )
    node = mgn.FindActionSpaceNode()
    monkeypatch.setattr(
        mgn.FindActionSpaceNode,
        "_pre_handle",
        lambda self, i, s, c: {"llm_config": {"general": {}}},
    )
    with pytest.raises(StopRequested):
        await node._do_invoke(inputs={}, session=session, context=object())

    # RunActionNode
    monkeypatch.setattr(mgn, "run_action", AsyncMock(side_effect=_STOP))
    node = mgn.RunActionNode()
    monkeypatch.setattr(
        mgn.RunActionNode,
        "_pre_handle",
        lambda self, i, s, c: {
            "action_start_time": 0.0,
            "config": {},
            "llm_config": {"general": {}},
            "validate_new_states": True,
            "validate_answer": True,
            "retrieval_settings": {},
            "new_found_evidence_ids": [],
            "retrieval_tool_only": False,
            "llm_budget": 10,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "messages": [],
            "action": {},
            "query": "q",
            "state": {},
        },
    )
    with pytest.raises(StopRequested):
        await node._do_invoke(inputs={}, session=session, context=object())

    # ValidateNewStateNode
    monkeypatch.setattr(mgn, "run_validations", AsyncMock(side_effect=_STOP))
    node = mgn.ValidateNewStateNode()
    monkeypatch.setattr(
        mgn.ValidateNewStateNode,
        "_pre_handle",
        lambda self, i, s, c: {
            "config": {},
            "validator_config": {},
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "new_states": [],
            "query": "q",
            "new_found_evidence_ids": [],
            "answer": None,
            "messages": [],
            "mode": "state_creation",
            "action": {"id": "a1", "state": {}},
        },
    )
    with pytest.raises(StopRequested):
        await node._do_invoke(inputs={}, session=session, context=object())


# ---------------------------------------------------------------------------
# 真实持久化契约（reviewer P1 修订要求）：引擎 post_workflow_execute 的
# 异常保存 / 正常完成清快照语义，是"节点返回前抛 StopRequested 可续跑、
# 吞成数据必回 stage1"机制成立的前提。直接对 agent-core
# PersistenceCheckpointer 用真实 KV 存储断言该契约，防止上游重构
# 静默破坏停止恢复机制。
# ---------------------------------------------------------------------------


class _DictKVStore:
    """与 agent-core MockKVStore 等价的最小同步字典 KV 实现。"""

    def __init__(self):
        self._store = {}

    async def set(self, key, value):
        self._store[key] = value

    async def get(self, key):
        return self._store.get(key)

    async def delete(self, key):
        self._store.pop(key, None)

    async def exclusive_set(self, key, value, expiry=None):
        if key in self._store:
            return False
        self._store[key] = value
        return True

    async def exists(self, key):
        return key in self._store

    async def get_by_prefix(self, prefix):
        return {k: v for k, v in self._store.items() if k.startswith(prefix)}

    async def delete_by_prefix(self, prefix, batch_size=None):
        for k in [k for k in self._store if k.startswith(prefix)]:
            del self._store[k]

    async def mget(self, keys):
        return [self._store.get(k) for k in keys]

    async def batch_delete(self, keys, batch_size=None):
        n = sum(1 for k in keys if k in self._store)
        for k in keys:
            self._store.pop(k, None)
        return n

    def pipeline(self):
        from openjiuwen.core.foundation.store.base_kv_store import (
            BasedKVStorePipeline,
        )

        async def execute(operations):
            results = []
            for operation in operations:
                if operation[0] == "set":
                    self._store[operation[1]] = operation[2]
                    results.append(None)
                elif operation[0] == "get":
                    results.append(self._store.get(operation[1]))
                elif operation[0] == "exists":
                    results.append(operation[1] in self._store)
            return results

        return BasedKVStorePipeline(execute)


@pytest.mark.asyncio
async def test_persistence_checkpoint_contract_save_on_exception_clear_on_completion():
    """契约：异常（StopRequested 上抛）→ workflow state 保存；
    正常完成（无 interrupt）→ graph state 删除 + workflow storage 清空。
    该契约是终检兜底降级为观测告警的依据：正常完成路径上引擎清理先于
    workflow.py finally 终检，吞成数据的停止无法在 agent 层保住快照。"""
    from openjiuwen.core.graph.store import create_state
    from openjiuwen.core.session.checkpointer.persistence import (
        PersistenceCheckpointer,
    )
    from openjiuwen.core.session.config.base import Config
    from openjiuwen.core.session.internal.agent import AgentSession
    from openjiuwen.core.session.internal.workflow import WorkflowSession

    kv = _DictKVStore()
    checkpointer = PersistenceCheckpointer(kv)
    parent = AgentSession(session_id="stop-contract", config=Config())
    session = WorkflowSession(
        workflow_id="wf-stop", parent=parent, session_id="stop-contract"
    )

    # 异常路径：StopRequested 上抛 → 保存 workflow state 后原样重抛
    with pytest.raises(StopRequested):
        await checkpointer.post_workflow_execute(session, {}, _STOP)
    assert await checkpointer._workflow_storage.exists(session) is True

    # 正常完成路径（无 interrupt）→ graph state 删除 + workflow storage 清空
    await checkpointer._graph_state.save(
        "stop-contract", "wf-stop", create_state("wf-stop", 3, {"chan": "v"})
    )
    await checkpointer.post_workflow_execute(session, {}, None)
    assert await checkpointer._workflow_storage.exists(session) is False
    graph_keys = await kv.get_by_prefix("stop-contract")
    assert graph_keys == {}
