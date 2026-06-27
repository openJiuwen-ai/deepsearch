from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from pathlib import Path

import pytest

from openjiuwen_deepsearch.algorithm.search_agent.action_pool import ActionPool

pytestmark = pytest.mark.unit
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Result


def test_action_pool_sample_prioritizes_immediate_queue(base_action) -> None:
    pool = ActionPool()
    regular = base_action.model_copy(update={"id": "regular"})
    immediate = base_action.model_copy(update={"id": "immediate"})
    pool.add([regular])
    pool.immediate_queue = deque([immediate])

    sampled = pool.sample(1)

    assert [a.id for a in sampled] == ["immediate"]
    assert [a.id for a in pool.running_actions] == ["immediate"]
    assert [a.id for a in pool._pool] == ["regular"]


def test_action_pool_record_completed_handles_missing_running(base_action) -> None:
    pool = ActionPool()

    pool.record_completed(base_action, None)

    assert len(pool.completed_actions) == 1
    assert pool.completed_actions[0][0].id == base_action.id
    assert pool.completed_actions[0][1] is None


def test_action_pool_get_best_guess_returns_highest_candidate(base_action) -> None:
    pool = ActionPool()
    weak = base_action.model_copy(
        update={
            "id": "weak",
            "state": base_action.state.model_copy(
                update={
                    "state": [
                        base_action.state.state[0].model_copy(
                            update={"candidate": "Lyon", "candidate_strength": 0.4}
                        )
                    ]
                }
            ),
        }
    )
    strong = base_action.model_copy(
        update={
            "id": "strong",
            "state": base_action.state.model_copy(
                update={
                    "state": [
                        base_action.state.state[0].model_copy(
                            update={"candidate": "Paris", "candidate_strength": 0.9}
                        )
                    ]
                }
            ),
        }
    )
    pool.completed_actions = [(weak, None), (strong, None)]

    best = pool.get_best_guess()

    assert best is not None
    action, result, candidate = best
    assert action.id == "strong"
    assert result is None
    assert candidate == "Paris"


def test_action_pool_add_and_sample_updates_json_snapshot(
    tmp_log_dir: Path, base_action
) -> None:
    pool = ActionPool()
    pool.log_dir = str(tmp_log_dir)
    pool.add([base_action])

    sampled = pool.sample(1)

    assert len(sampled) == 1
    assert (tmp_log_dir / "action_pool.json").exists()


@pytest.mark.asyncio
async def test_action_pool_flush_snapshot_persists_latest_async_state(
    tmp_log_dir: Path, base_action
) -> None:
    pool = ActionPool()
    pool.log_dir = str(tmp_log_dir)
    pool.add([base_action])

    sampled = pool.sample(1)
    assert len(sampled) == 1

    result = Result(messages=[], new_states=[], found_answer=None, previous_action_id=sampled[0].id)
    pool.record_completed(sampled[0], result)

    await pool.flush_snapshot()

    snapshot = json.loads((tmp_log_dir / "action_pool.json").read_text(encoding="utf-8"))
    assert snapshot["pending"] == []
    assert snapshot["running"] == []
    assert len(snapshot["completed"]) == 1
    assert snapshot["completed"][0]["id"] == sampled[0].id
    assert snapshot["completed"][0]["has_result"] is True


@pytest.mark.asyncio
async def test_action_pool_flush_snapshot_handles_write_failure(
    tmp_log_dir: Path, base_action, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pool = ActionPool()
    pool.log_dir = str(tmp_log_dir)

    emitted = False

    def fail_write(snapshot: dict) -> None:
        raise OSError("boom")

    def mark_emitted(snapshot: dict) -> None:
        nonlocal emitted
        emitted = True

    monkeypatch.setattr(pool, "_write_pool_json_sync", fail_write)
    monkeypatch.setattr(pool, "_emit_pool_snapshot", mark_emitted)

    caplog.set_level("ERROR")
    pool.add([base_action])

    await pool.flush_snapshot()

    assert pool._snapshot_task is None
    assert not emitted
    assert not (tmp_log_dir / "action_pool.json").exists()
    assert "failed: boom" in caplog.text


@pytest.mark.asyncio
async def test_action_pool_save_pool_json_coalesces_rapid_async_updates(
    tmp_log_dir: Path, base_action, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = ActionPool()
    pool.log_dir = str(tmp_log_dir)

    snapshots: list[dict] = []
    first_write_started = threading.Event()
    release_first_write = threading.Event()

    def capture_write(snapshot: dict) -> None:
        snapshots.append(json.loads(json.dumps(snapshot)))
        if len(snapshots) == 1:
            first_write_started.set()
            assert release_first_write.wait(timeout=1)

    monkeypatch.setattr(pool, "_write_pool_json_sync", capture_write)
    monkeypatch.setattr(pool, "_emit_pool_snapshot", lambda snapshot: None)

    action_two = base_action.model_copy(update={"id": "action-2"})
    action_three = base_action.model_copy(update={"id": "action-3"})

    pool.add([base_action])
    await asyncio.to_thread(first_write_started.wait, 1)

    pool.add([action_two])
    pool.add([action_three])
    release_first_write.set()

    await pool.flush_snapshot()

    assert len(snapshots) == 2
    assert [item["id"] for item in snapshots[-1]["pending"]] == [
        base_action.id,
        action_two.id,
        action_three.id,
    ]
    assert pool._snapshot_task is None


@pytest.mark.asyncio
async def test_action_pool_flush_snapshot_supports_concurrent_waiters(
    tmp_log_dir: Path, base_action, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = ActionPool()
    pool.log_dir = str(tmp_log_dir)

    snapshots: list[dict] = []
    first_write_started = threading.Event()
    release_first_write = threading.Event()

    def capture_write(snapshot: dict) -> None:
        snapshots.append(json.loads(json.dumps(snapshot)))
        if len(snapshots) == 1:
            first_write_started.set()
            assert release_first_write.wait(timeout=1)

    monkeypatch.setattr(pool, "_write_pool_json_sync", capture_write)
    monkeypatch.setattr(pool, "_emit_pool_snapshot", lambda snapshot: None)

    pool.add([base_action])
    await asyncio.to_thread(first_write_started.wait, 1)

    sampled = pool.sample(1)
    result = Result(messages=[], new_states=[], found_answer=None, previous_action_id=sampled[0].id)
    pool.record_completed(sampled[0], result)

    waiters = [asyncio.create_task(pool.flush_snapshot()) for _ in range(3)]
    await asyncio.sleep(0)
    release_first_write.set()
    await asyncio.gather(*waiters)

    assert len(snapshots) == 2
    assert snapshots[-1]["pending"] == []
    assert snapshots[-1]["running"] == []
    assert len(snapshots[-1]["completed"]) == 1
    assert snapshots[-1]["completed"][0]["id"] == sampled[0].id
    assert snapshots[-1]["completed"][0]["has_result"] is True
    assert pool._snapshot_task is None
