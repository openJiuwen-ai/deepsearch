"""Tests for Mermaid visualization runtime metrics."""

from openjiuwen_deepsearch.algorithm.report.visualization_metrics import (
    VisualizationTaskMetrics,
    build_visualization_generation_summary,
    build_visualization_insert_summary,
)


def test_visualization_task_metrics_records_stage_and_retry():
    metrics = VisualizationTaskMetrics(section_idx=2)

    metrics.stage_durations_ms["extract_data"] = 12
    metrics.record_retry("extract_data")
    metrics.record_retry("extract_data")
    result = metrics.finish(False, "extract_data_failed")

    assert result["section_idx"] == 2
    assert result["rs_success"] is False
    assert result["error_msg"] == "extract_data_failed"
    assert result["retry_counts"] == {"extract_data": 2}
    assert result["stage_durations_ms"]["extract_data"] == 12
    assert result["stage_durations_ms"]["candidate_total"] >= 0


def test_build_visualization_generation_summary_aggregates_candidates():
    task_metrics = [
        {
            "rs_success": True,
            "error_msg": "",
            "stage_durations_ms": {"extract_data": 100, "candidate_total": 150},
            "retry_counts": {"extract_data": 1},
        },
        {
            "rs_success": False,
            "error_msg": "normalize_failed",
            "stage_durations_ms": {"normalize_units": 40, "candidate_total": 70},
            "retry_counts": {"normalize_units": 2},
        },
    ]

    summary = build_visualization_generation_summary(
        section_idx=3,
        source_candidate_count=5,
        pre_budget_candidate_count=4,
        candidate_budget=2,
        selected_candidate_count=2,
        generated_mermaid_count=1,
        task_metrics=task_metrics,
        exception_count=1,
        wall_time_ms=220,
    )

    assert summary["section_idx"] == 3
    assert summary["source_candidate_count"] == 5
    assert summary["pre_budget_candidate_count"] == 4
    assert summary["candidate_budget"] == 2
    assert summary["selected_candidate_count"] == 2
    assert summary["attempted_candidate_count"] == 3
    assert summary["successful_candidate_count"] == 1
    assert summary["generated_mermaid_count"] == 1
    assert summary["failure_reasons"] == {"normalize_failed": 1, "exception": 1}
    assert summary["retry_counts"] == {"extract_data": 1, "normalize_units": 2}
    assert summary["stage_durations_ms"] == {
        "extract_data": 100,
        "candidate_total": 220,
        "normalize_units": 40,
    }
    assert summary["wall_time_ms"] == 220


def test_build_visualization_insert_summary_keeps_log_safe_counts():
    summary = build_visualization_insert_summary(
        section_idx=4,
        visualization_count=3,
        valid_mermaid_count=2,
        planned_insertion_count=2,
        inserted_mermaid_count=2,
        wall_time_ms=80,
        planner_mode="local_single_chart",
    )

    assert summary == {
        "section_idx": 4,
        "visualization_count": 3,
        "valid_mermaid_count": 2,
        "planned_insertion_count": 2,
        "inserted_mermaid_count": 2,
        "wall_time_ms": 80,
        "planner_mode": "local_single_chart",
    }
