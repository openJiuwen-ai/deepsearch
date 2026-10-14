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
    monkeypatch.setattr(VIZ_MODULE + ".apply_system_prompt", lambda *a, **k: [])
    with pytest.raises(StopRequested):
        await _make_viz()._extract_data_from_text({"section_idx": 1})
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_visualization_compliance_stop_not_retried(monkeypatch):
    """合规校验重试循环遇停止必须立即上抛，不得烧完重试预算。"""
    invoke = _stop_mock()
    monkeypatch.setattr(VIZ_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(VIZ_MODULE + ".apply_system_prompt", lambda *a, **k: [])
    with pytest.raises(StopRequested):
        await _make_viz()._validate_chart_compliance("{}", 1, "大纲", 3)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_visualization_traceability_stop_not_retried(monkeypatch):
    """溯源校验重试循环遇停止必须立即上抛，不得烧完重试预算。"""
    invoke = _stop_mock()
    monkeypatch.setattr(VIZ_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(VIZ_MODULE + ".apply_system_prompt", lambda *a, **k: [])
    with pytest.raises(StopRequested):
        await _make_viz()._validate_chart_traceability("{}", "原文", 1, 3)
    assert invoke.await_count == 1


@pytest.mark.asyncio
async def test_visualization_gather_reraises_stop_before_aggregation(monkeypatch):
    """return_exceptions=True 把停止压平成结果项后必须先于汇总分流重抛，
    不得置空内容吞成"无图表"假完成。"""
    invoke = _stop_mock()
    monkeypatch.setattr(VIZ_MODULE + ".ainvoke_llm_with_stats", invoke)
    monkeypatch.setattr(VIZ_MODULE + ".apply_system_prompt", lambda *a, **k: [])
    viz = _make_viz()
    current_inputs = {
        "section_idx": 1,
        "sub_section_outline": "图表大纲",
        "section_task": "1. 测试章节",
        "classified_content": [
            {"data_density": 0.95, "passage_text": "内容甲"},
            {"data_density": 0.95, "passage_text": "内容乙"},
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
    monkeypatch.setattr(WE_MODULE + ".apply_system_prompt", lambda *a, **k: [])
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
    monkeypatch.setattr(WE_MODULE + ".apply_system_prompt", lambda *a, **k: [])
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
    monkeypatch.setattr(CG_UTILS_MODULE + ".apply_system_prompt", lambda *a, **k: [])
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
