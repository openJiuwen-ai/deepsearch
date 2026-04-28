from __future__ import annotations

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
