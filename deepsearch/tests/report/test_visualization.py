import asyncio
import json
from unittest.mock import patch, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.algorithm.report.report_common import (
    MAX_CONCURRENT_VISUALIZATION_TASKS,
)


def _visualization_reporter() -> Reporter:
    reporter = Reporter.__new__(Reporter)
    reporter._llm = object()
    return reporter


def test_select_visualization_selects_high_data_density():
    selected = Reporter._select_visualization_from_classified_content([
        {
            "title": "high density",
            "data_density": 0.9,
        },
        {
            "title": "low density",
            "data_density": 0.5,
        },
    ])

    assert [item["title"] for item in selected] == ["high density"]


def test_select_visualization_uses_eight_point_fallback_when_no_high_density_docs():
    """data_density >= 0.9 优先, 不足时回退到 >= 0.8 的项。
    """
    selected = Reporter._select_visualization_from_classified_content([
        {
            "title": "fallback density",
            "data_density": 0.8,
        },
        {
            "title": "too sparse",
            "data_density": 0.7,
        },
    ])

    assert [item["title"] for item in selected] == ["fallback density"]


def test_infer_desired_chart_type_uses_explicit_and_year_sequence_hints_only():
    assert Reporter._infer_desired_chart_type(
        "请使用柱状图展示不同模型的性能指标",
    ) == "bar"
    assert Reporter._infer_desired_chart_type(
        "年度吞吐量规模与延迟变化"
    ) == ""
    assert Reporter._infer_desired_chart_type(
        "比较 2022—2024 年同一口径指标"
    ) == "line"
    assert Reporter._infer_desired_chart_type(
        "不同模型、区域或策略的结果对比"
    ) == ""


@pytest.mark.asyncio
async def test_visualization_extraction_retries_empty_json_and_accepts_fenced_json():
    chart_payload = {
        "image_title": "2024 Vehicle Sales Comparison",
        "image_type": "bar",
        "records": [
            ["A", "120", "vehicles"],
            ["B", "95", "vehicles"],
            ["C", "80", "vehicles"],
        ],
    }
    llm_responses = [
        {"content": "{}"},
        {"content": f"```json\n{json.dumps(chart_payload)}\n```"},
        {"content": '```json\n{"valid":true,"error_msg":""}\n```'},
        {"content": '```json\n{"valid":true,"error_msg":""}\n```'},
    ]

    with patch(
        "openjiuwen_deepsearch.algorithm.report.visualization.ainvoke_llm_with_stats",
        new=AsyncMock(side_effect=llm_responses),
    ) as mocked_llm:
        ok, result, extracted = (
            await _visualization_reporter()._extract_visualization_data(
                visualization_dict={
                    "section_idx": 1,
                    "language": "en",
                    "section_outline": "Vehicle market sales comparison",
                    "origin_content": (
                        "A sold 120 vehicles, B sold 95 vehicles, "
                        "C sold 80 vehicles."
                    ),
                },
                visualization_content={"rs_success": True},
                max_attempt_num=3,
                section_idx=1,
            )
        )

    assert ok is True
    assert extracted == chart_payload
    assert result["sub_section_visualization_content"] == json.dumps(
        chart_payload, ensure_ascii=False
    )
    assert mocked_llm.await_count == 4


@pytest.mark.asyncio
async def test_visualization_extraction_retries_chart_type_mismatch():
    wrong_chart_payload = {
        "image_title": "2022-2024 NEV sales trend",
        "image_type": "bar",
        "records": [
            ["2022年", "688.7", "万辆"],
            ["2023年", "949.5", "万辆"],
            ["2024年", "1286.6", "万辆"],
        ],
    }
    corrected_chart_payload = {
        **wrong_chart_payload,
        "image_type": "line",
    }
    llm_responses = [
        {"content": json.dumps(wrong_chart_payload, ensure_ascii=False)},
        {"content": '{"valid":true,"error_msg":""}'},
        {
            "content": (
                '{"valid":false,"error_msg":"Bar chart uses time-series '
                'X-axis values; use line instead."}'
            )
        },
        {"content": json.dumps(corrected_chart_payload, ensure_ascii=False)},
        {"content": '{"valid":true,"error_msg":""}'},
        {"content": '{"valid":true,"error_msg":""}'},
    ]

    with patch(
        "openjiuwen_deepsearch.algorithm.report.visualization.ainvoke_llm_with_stats",
        new=AsyncMock(side_effect=llm_responses),
    ) as mocked_llm:
        ok, result, extracted = (
            await _visualization_reporter()._extract_visualization_data(
                visualization_dict={
                    "section_idx": 1,
                    "language": "zh-CN",
                    "section_title": "中国新能源汽车年度销量趋势",
                    "section_outline": "1 中国新能源汽车年度销量趋势\n1.1 年度销量与增速",
                    "origin_content": (
                        "2022年销量688.7万辆，2023年销量949.5万辆，"
                        "2024年销量1286.6万辆。"
                    ),
                    "desired_chart_type": "line",
                },
                visualization_content={"rs_success": True},
                max_attempt_num=3,
                section_idx=1,
            )
        )

    assert ok is True
    assert extracted == corrected_chart_payload
    assert extracted["image_type"] == "line"
    assert json.loads(result["sub_section_visualization_content"])["image_type"] == "line"
    assert mocked_llm.await_count == 6


@pytest.mark.asyncio
async def test_visualization_normalization_uses_local_same_unit_fast_path():
    reporter = _visualization_reporter()
    visualization_content = {"rs_success": True}
    extracted_obj = {
        "image_title": "New energy vehicle sales trend",
        "image_type": "line",
        "records": [
            ["2021", "352.1", "万辆"],
            ["2022", "688.7", "万辆"],
            ["2023", "949.5", "万辆"],
            ["2024", "1,286.6", "万辆"],
        ],
    }

    with patch(
        "openjiuwen_deepsearch.algorithm.report.visualization.ainvoke_llm_with_stats",
        new_callable=AsyncMock,
    ) as mocked_llm:
        normalized = await reporter._normalize_visualization_content(
            visualization_content=visualization_content,
            extracted_obj=extracted_obj,
            visualization_dict={"language": "zh-CN"},
            max_attempt_num=3,
            section_idx=1,
        )

    assert normalized is True
    mocked_llm.assert_not_awaited()
    assert json.loads(visualization_content["sub_section_visualization_content"]) == {
        "image_title": "New energy vehicle sales trend",
        "image_type": "line",
        "unit": "万辆",
        "records": [
            ["2021", 352.1],
            ["2022", 688.7],
            ["2023", 949.5],
            ["2024", 1286.6],
        ],
    }


def test_local_same_unit_normalization_scales_large_chinese_wan_values():
    normalized = Reporter._normalize_same_unit_records_locally(
        [
            ["万达电影", "647690", "万元"],
            ["横店院线", "164226", "万元"],
            ["上海星轶", "112586", "万元"],
        ],
        "bar",
    )

    assert normalized == {
        "unit": "亿元",
        "records": [
            ["万达电影", 64.769],
            ["横店院线", 16.4226],
            ["上海星轶", 11.2586],
        ],
    }


# ── Semaphore concurrency tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_content_for_visualization_limits_concurrency():
    """生产路径验证：_generate_content_for_visualization 的并发不超过 MAX_CONCURRENT_VISUALIZATION_TASKS。"""
    current = 0
    max_observed = 0

    async def mock_process_task(visualization_dict):
        nonlocal current, max_observed
        current += 1
        max_observed = max(max_observed, current)
        await asyncio.sleep(0.05)  # 模拟 LLM 调用耗时，制造并发重叠窗口
        current -= 1
        return dict(
            rs_success=True,
            sub_section_visualization_content="",
            mermaid_content="",
        )

    num_tasks = MAX_CONCURRENT_VISUALIZATION_TASKS + 3  # 8 > 5，超限可观测
    classified_content = [
        {"title": f"doc-{i}", "data_density": 0.95, "passage_text": f"content {i}"}
        for i in range(num_tasks)
    ]
    reporter = _visualization_reporter()

    with patch.object(reporter, "_process_visualization_task", new=mock_process_task):
        result = await reporter._generate_content_for_visualization(
            {
                "section_idx": 1,
                "section_task": "市场对比",
                "sub_section_outline": "1 市场对比\n1.1 销量",
                "classified_content": classified_content,
            }
        )

    assert result["rs_success"] is True
    assert max_observed <= MAX_CONCURRENT_VISUALIZATION_TASKS
    assert len(result["visualization_content"]) == num_tasks
    # 所有 task 的结果都被正确回填
    for item in result["visualization_content"]:
        assert item["mermaid_content"] == ""


@pytest.mark.asyncio
async def test_generate_content_for_visualization_all_tasks_complete():
    """生产路径验证：信号量不丢失任何 task，结果按序回填。"""
    num_tasks = 12

    async def mock_process_task(visualization_dict):
        await asyncio.sleep(0.01)
        title = visualization_dict["title"]
        return dict(
            rs_success=True,
            sub_section_visualization_content=f"data for {title}",
            mermaid_content=f"mermaid for {title}",
        )

    classified_content = [
        {"title": f"doc-{i}", "data_density": 0.95, "passage_text": f"content {i}"}
        for i in range(num_tasks)
    ]
    reporter = _visualization_reporter()

    with patch.object(reporter, "_process_visualization_task", new=mock_process_task):
        result = await reporter._generate_content_for_visualization(
            {
                "section_idx": 1,
                "section_task": "市场对比",
                "sub_section_outline": "1 市场对比\n1.1 销量",
                "classified_content": classified_content,
            }
        )

    assert result["rs_success"] is True
    visualization_content = result["visualization_content"]
    assert len(visualization_content) == num_tasks
    for i, item in enumerate(visualization_content):
        assert item["sub_section_visualization_content"] == f"data for doc-{i}"
        assert item["mermaid_content"] == f"mermaid for doc-{i}"


@pytest.mark.asyncio
async def test_generate_content_for_visualization_task_exception_not_blocking():
    """生产路径验证：单个 task 抛异常不影响其他 task，异常 task 结果置空。"""
    num_ok_tasks = 7

    async def ok_task(visualization_dict):
        await asyncio.sleep(0.01)
        return dict(
            rs_success=True,
            sub_section_visualization_content="ok",
            mermaid_content="ok-mermaid",
        )

    async def fail_task(visualization_dict):
        raise RuntimeError("LLM call failed")

    call_count = {"n": 0}

    async def dispatch(visualization_dict):
        call_count["n"] += 1
        if call_count["n"] == num_ok_tasks + 1:  # 最后一个 task 失败
            return await fail_task(visualization_dict)
        return await ok_task(visualization_dict)

    classified_content = [
        {"title": f"doc-{i}", "data_density": 0.95, "passage_text": f"content {i}"}
        for i in range(num_ok_tasks + 1)
    ]
    reporter = _visualization_reporter()

    with patch.object(reporter, "_process_visualization_task", new=dispatch):
        result = await reporter._generate_content_for_visualization(
            {
                "section_idx": 1,
                "section_task": "市场对比",
                "sub_section_outline": "1 市场对比\n1.1 销量",
                "classified_content": classified_content,
            }
        )

    assert result["rs_success"] is True
    visualization_content = result["visualization_content"]
    assert len(visualization_content) == num_ok_tasks + 1
    # 前 7 个 task 正常
    for item in visualization_content[:num_ok_tasks]:
        assert item["mermaid_content"] == "ok-mermaid"
    # 最后 1 个异常 task 被置空
    failed = visualization_content[num_ok_tasks]
    assert failed["sub_section_visualization_content"] == ""
    assert failed["mermaid_content"] == ""
