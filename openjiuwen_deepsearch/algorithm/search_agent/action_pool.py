# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import logging
import os
import random
from collections import deque
from typing import Deque, List, Optional

from openjiuwen_deepsearch.config.config import ActionSamplingConfig
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Action, Result
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.run_telemetry import emit

logger = logging.getLogger(__name__)

# Depth-based scoring: soft cap on state depth
MAX_STATE_DEPTH = 12
DEPTH_PENALTY_THRESHOLD = 5
DEPTH_PENALTY_DENOMINATOR = 8
SHALLOW_DEPTH_THRESHOLD = 2
SHALLOW_DEPTH_BONUS = 100
MULTIPLE_STATES_DIVISOR_THRESHOLD = 1


class ActionPool:
    """
    Pool of actions for the DeepSearch agent. Used from a single coroutine only
    (no thread-safety; not needed under asyncio).

    _pool: pending actions
    immediate_queue: actions placed here are served first (FIFO) when sample()
    is called, bypassing the scored sampling logic entirely.
    """

    def __init__(self):
        self._pool: List[Action] = []
        self.running_actions: List[Action] = []
        self.completed_actions: List[tuple[Action, Optional[Result]]] = []
        self.successfully_completed_actions: List[tuple[Action, Result]] = []
        self.immediate_queue: Deque[Action] = deque()
        self.config: ActionSamplingConfig = ActionSamplingConfig()
        self.log_dir: str = ""
        self._score_cache: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _state_hash(action: Action) -> str:
        return "".join(str(v.candidate).lower() for v in action.state.state)

    def _compute_base_score(self, action: Action) -> float:
        """Full base score: proposal score + candidate strengths + depth adjustments.

        The pool-relative promote_unique_states divisor is intentionally excluded
        because it is a sampling weight, not an intrinsic action property.
        """
        score = action.proposal.score
        for v in action.state.state:
            if v.candidate is not None:
                score += v.candidate_strength or 0.0
        depth = action.state.depth
        if self.config.depth_weight:
            if depth > DEPTH_PENALTY_THRESHOLD:
                score *= (MAX_STATE_DEPTH - depth) / DEPTH_PENALTY_DENOMINATOR
            if depth < SHALLOW_DEPTH_THRESHOLD:
                score += SHALLOW_DEPTH_BONUS
        return score

    def _get_score(self, action: Action) -> float:
        """Return the cached base score, computing and storing it on first access."""
        if action.id not in self._score_cache:
            self._score_cache[action.id] = self._compute_base_score(action)
        return self._score_cache[action.id]

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _action_snapshot(self, action: Action) -> dict:
        return {
            "id": action.id,
            "direction": action.proposal.direction,
            "score": self._get_score(action),
            "depth": action.state.depth,
        }

    def _save_pool_json(self) -> None:
        """Write a live snapshot of the pool state to action_pool.json in log_dir."""
        if not self.log_dir:
            return
        try:
            snapshot = {
                "pending": [self._action_snapshot(a) for a in self._pool],
                "running": [self._action_snapshot(a) for a in self.running_actions],
                "completed": [
                    {**self._action_snapshot(a), "has_result": r is not None}
                    for a, r in self.completed_actions
                ],
            }
            path = os.path.join(self.log_dir, "action_pool.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
            emit(
                "action_pool_snapshot",
                {
                    "pending_count": len(snapshot["pending"]),
                    "running_count": len(snapshot["running"]),
                    "completed_count": len(snapshot["completed"]),
                    "snapshot": snapshot,
                },
                source="action_pool._save_pool_json",
                action_id=None,
            )
        except Exception as e:
            logger.exception("[ActionPool] _save_pool_json failed: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add(self, actions: List[Action]) -> None:
        if not actions:
            return
        try:
            for action in actions:
                self._get_score(action)  # pre-warm cache before extending pool
            self._pool.extend(actions)
            self._save_pool_json()
        except Exception as e:
            logger.exception(
                "[ActionPool] add failed, pool unchanged: %s", e, exc_info=True
            )

    def size(self) -> int:
        """Return the number of actions in the pool."""
        try:
            return len(self._pool)
        except Exception as e:
            logger.exception("[ActionPool] size failed: %s", e, exc_info=True)
            return 0

    def record_completed(self, action: Action, result: Optional[Result]) -> None:
        """Register an action that has finished executing, together with its result."""
        try:
            idx = next(i for i, a in enumerate(self.running_actions) if a.id == action.id)
            self.running_actions.pop(idx)
        except StopIteration:
            logger.warning(
                "[ActionPool] record_completed: action_id=%s not in running_actions "
                "(possible double completion or pool desync); still recording in completed.",
                "***" if LogManager.is_sensitive() else getattr(action, "id", "?"),
            )
        self.completed_actions.append((action, result))
        self._save_pool_json()

    def record_successful_answer(self, action: Action, result: Result) -> None:
        """Register an action whose result contains a found answer (top-k mode)."""
        self.successfully_completed_actions.append((action, result))

    def successful_answer_count(self) -> int:
        """Return the number of answers collected so far in top-k mode."""
        return len(self.successfully_completed_actions)

    def get_best_answer(self) -> Optional[tuple[Action, Result]]:
        """Return the (action, result) pair whose answer variable has the highest candidate_strength.

        Returns None if no successful answers have been collected.
        """
        if not self.successfully_completed_actions:
            return None

        def _score(pair: tuple[Action, Result]) -> float:
            action, _ = pair
            for v in action.state.state:
                if v.id == action.state.answer_variable:
                    return v.candidate_strength or 0.0
            return 0.0

        return max(self.successfully_completed_actions, key=_score)

    def get_best_guess(self) -> Optional[tuple[Action, Optional[Result], str]]:
        candidates: list[tuple[Action, Optional[Result], str, float]] = []
        for action, result in self.completed_actions:
            try:
                answer_var = next(
                    (v for v in action.state.state if v.id == action.state.answer_variable),
                    None,
                )
            except AttributeError:
                continue
            if answer_var is None or answer_var.candidate is None:
                continue
            strength = answer_var.candidate_strength or 0.0
            candidates.append((action, result, answer_var.candidate, strength))

        if not candidates:
            return None

        best = max(candidates, key=lambda t: t[3])
        return best[:3]

    def sample(self, k: int) -> List[Action]:
        try:
            sampled = self._sample_impl(k)
            self.running_actions.extend(sampled)
            self._save_pool_json()
            return sampled
        except Exception as e:
            logger.exception(
                "[ActionPool] sample failed, returning empty list: %s", e, exc_info=True
            )
            return []

    def _sample_impl(self, k: int) -> List[Action]:
        # Actions in the immediate queue are always served first, in FIFO order,
        # without going through the scoring logic.
        immediate: List[Action] = []
        while len(immediate) < k and self.immediate_queue:
            immediate.append(self.immediate_queue.popleft())
        remaining = k - len(immediate)
        if remaining == 0:
            return immediate

        if self.config.random_sample:
            scored = random.sample(self._pool, min(remaining, len(self._pool)))
            for a in scored:
                self._pool.remove(a)
            return immediate + scored

        sampled_actions: List[Action] = []
        for _ in range(remaining):
            if not self._pool:
                continue

            # Base scores from cache.
            action_scores = [self._get_score(a) for a in self._pool]

            # Pool-relative adjustment: down-weight over-represented states.
            if self.config.promote_unique_states:
                unique_states: dict[str, int] = {}
                for action_item in self._pool:
                    h = self._state_hash(action_item)
                    unique_states[h] = unique_states.get(h, 0) + 1
                for j, action_item in enumerate(self._pool):
                    h = self._state_hash(action_item)
                    if unique_states[h] > MULTIPLE_STATES_DIVISOR_THRESHOLD:
                        action_scores[j] /= unique_states[h]

            total = sum(action_scores)
            if total <= 0:
                continue
            normal_action_scores = [score / total for score in action_scores]
            sample_idx = random.choices(
                range(len(self._pool)),
                weights=normal_action_scores,
                k=1,
            )[0]
            sampled_actions.append(self._pool.pop(sample_idx))
        return immediate + sampled_actions
