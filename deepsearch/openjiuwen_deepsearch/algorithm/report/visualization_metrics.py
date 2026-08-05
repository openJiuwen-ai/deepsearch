# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Runtime metrics helpers for report visualization generation."""

from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any


def elapsed_ms(started_at: float) -> int:
    """Return non-negative elapsed milliseconds from a perf_counter timestamp."""
    return max(int((perf_counter() - started_at) * 1000), 0)


def _merge_counter(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        if not isinstance(value, int):
            continue
        target[key] = target.get(key, 0) + value


@dataclass
class VisualizationTaskMetrics:
    """Collect metrics for one visualization candidate."""

    section_idx: int
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)
    rs_success: bool = False
    error_msg: str = ""
    _started_at: float = field(default_factory=perf_counter, repr=False)

    def record_stage(self, stage: str, started_at: float) -> None:
        self.stage_durations_ms[stage] = (
            self.stage_durations_ms.get(stage, 0) + elapsed_ms(started_at)
        )

    def record_retry(self, stage: str, count: int = 1) -> None:
        self.retry_counts[stage] = self.retry_counts.get(stage, 0) + count

    def finish(self, rs_success: bool, error_msg: str = "") -> dict[str, Any]:
        self.rs_success = bool(rs_success)
        self.error_msg = str(error_msg or "")
        self.record_stage("candidate_total", self._started_at)
        return self.as_log_dict()

    def as_log_dict(self) -> dict[str, Any]:
        return {
            "section_idx": self.section_idx,
            "rs_success": self.rs_success,
            "error_msg": self.error_msg,
            "stage_durations_ms": dict(self.stage_durations_ms),
            "retry_counts": dict(self.retry_counts),
        }


def build_visualization_generation_summary(
    *,
    section_idx: int,
    source_candidate_count: int,
    selected_candidate_count: int,
    generated_mermaid_count: int,
    task_metrics: list[dict[str, Any]],
    exception_count: int,
    wall_time_ms: int,
    pre_budget_candidate_count: int | None = None,
    candidate_budget: int | None = None,
) -> dict[str, Any]:
    """Build a log-safe summary for one section's visualization generation."""
    failure_reasons: Counter[str] = Counter()
    stage_durations_ms: dict[str, int] = {}
    retry_counts: dict[str, int] = {}
    successful_candidate_count = 0

    for item in task_metrics:
        if item.get("rs_success"):
            successful_candidate_count += 1
        else:
            reason = str(item.get("error_msg") or "unknown")
            failure_reasons[reason] += 1
        _merge_counter(stage_durations_ms, item.get("stage_durations_ms", {}))
        _merge_counter(retry_counts, item.get("retry_counts", {}))

    if exception_count:
        failure_reasons["exception"] += exception_count

    summary = {
        "section_idx": section_idx,
        "source_candidate_count": source_candidate_count,
        "selected_candidate_count": selected_candidate_count,
        "attempted_candidate_count": len(task_metrics) + exception_count,
        "successful_candidate_count": successful_candidate_count,
        "generated_mermaid_count": generated_mermaid_count,
        "failure_reasons": dict(failure_reasons),
        "retry_counts": retry_counts,
        "stage_durations_ms": stage_durations_ms,
        "wall_time_ms": wall_time_ms,
    }
    if pre_budget_candidate_count is not None:
        summary["pre_budget_candidate_count"] = pre_budget_candidate_count
    if candidate_budget is not None:
        summary["candidate_budget"] = candidate_budget
    return summary


def build_visualization_insert_summary(
    *,
    section_idx: int,
    visualization_count: int,
    valid_mermaid_count: int,
    planned_insertion_count: int,
    inserted_mermaid_count: int,
    wall_time_ms: int,
    planner_mode: str | None = None,
) -> dict[str, Any]:
    """Build a log-safe summary for one section's visualization insertion."""
    summary = {
        "section_idx": section_idx,
        "visualization_count": visualization_count,
        "valid_mermaid_count": valid_mermaid_count,
        "planned_insertion_count": planned_insertion_count,
        "inserted_mermaid_count": inserted_mermaid_count,
        "wall_time_ms": wall_time_ms,
    }
    if planner_mode:
        summary["planner_mode"] = planner_mode
    return summary
