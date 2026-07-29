from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.search_nodes.utils import _save_result
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Result

pytestmark = pytest.mark.unit


def _result_files(log_dir: Path) -> list[Path]:
    return sorted((log_dir / "Result").glob("*.json"))


def test_save_result_writes_error_file_and_increments_fail_count(
    tmp_log_dir: Path, base_action
) -> None:
    config = {"log_dir": str(tmp_log_dir), "fail_count": 0}
    result_dict = {
        "question": base_action.question,
        "messages": [{"role": "assistant", "content": "failed"}],
        "termination": "Error: boom",
    }

    updated = _save_result(config, base_action, result_dict, time_taken=0.5)

    files = _result_files(tmp_log_dir)
    assert updated["fail_count"] == 1
    assert len(files) == 1
    assert files[0].name.startswith("error_result_")

    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["previous_action"] == base_action.proposal.direction
    assert payload["result"]["previous_action_id"] == base_action.id


def test_save_result_skips_early_termination_dict(tmp_log_dir: Path, base_action) -> None:
    config = {"log_dir": str(tmp_log_dir), "fail_count": 3}
    result_dict = {
        "question": base_action.question,
        "messages": [],
        "termination": "Early termination: context",
    }

    updated = _save_result(config, base_action, result_dict, time_taken=0.1)

    assert updated["fail_count"] == 3
    assert _result_files(tmp_log_dir) == []


def test_save_result_writes_regular_result_file(tmp_log_dir: Path, base_action) -> None:
    config = {"log_dir": str(tmp_log_dir), "fail_count": 0}
    result = Result(
        previous_action_id=base_action.id,
        messages=[{"role": "assistant", "content": "new state"}],
        new_states=[],
        found_answer=None,
    )

    _save_result(config, base_action, result, time_taken=0.8)

    files = _result_files(tmp_log_dir)
    assert len(files) == 1
    assert files[0].name.startswith("result_")


def test_save_result_writes_answer_result_file(tmp_log_dir: Path, base_action) -> None:
    config = {"log_dir": str(tmp_log_dir), "fail_count": 0}
    answer_result = Result(
        previous_action_id=base_action.id,
        messages=[{"role": "assistant", "content": "final"}],
        new_states=[],
        found_answer="Paris",
    )

    _save_result(config, base_action, answer_result, time_taken=0.2)

    files = _result_files(tmp_log_dir)
    assert len(files) == 1
    assert files[0].name.startswith("answer_result_")
