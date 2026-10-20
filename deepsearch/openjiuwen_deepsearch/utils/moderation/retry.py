"""Carry recovery policy across an existing retry loop; never issue requests."""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from uuid import uuid4


@dataclass
class ModerationRetryPolicy:
    mask_input: bool = False
    constrain_output: bool = False
    attempts: int = 0
    pending_recovery: bool = False
    call_id: str = field(default_factory=lambda: uuid4().hex)

    def fork(self):
        return ModerationRetryPolicy(
            mask_input=self.mask_input,
            constrain_output=self.constrain_output,
            pending_recovery=self.pending_recovery,
        )

    def prepare(self, messages: list) -> list:
        from openjiuwen_deepsearch.utils.moderation import mask as moderation_mask

        prepared = messages
        if self.mask_input:
            prepared, _, _ = moderation_mask.mask_messages(prepared, 1, evidence_only=True)
        if self.constrain_output:
            already_present = any(
                (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
                and (m.get("content") if isinstance(m, dict) else getattr(m, "content", None))
                == moderation_mask.RETRY_FEEDBACK_PROMPT
                for m in prepared
            )
            if not already_present:
                prepared = moderation_mask.append_retry_feedback(prepared)
        return prepared

    async def prepare_async(self, messages: list) -> list:
        """Offload input recovery, including cold resource loading and rendering.

        The matcher is Python code, so worker scans release the GIL regularly.
        Cancelling the await does not stop the worker; it only builds a copy and
        never invokes the model or mutates retry state.
        """
        if self.mask_input:
            return await asyncio.to_thread(self.prepare, messages)
        return self.prepare(messages)


@dataclass
class _RetryScope:
    owner: object
    policies: dict = field(default_factory=dict)


_CURRENT_RETRY: ContextVar[_RetryScope | None] = ContextVar("moderation_retry_scope", default=None)


def moderation_retry_scope(function):
    """Scope policy to a logical operation without changing its loop or budget.

    Recursive batch splitting inherits a copy, so siblings never share mutations.
    Nested, different operations always start fresh; context is reset on any exit.
    """
    @wraps(function)
    async def wrapped(*args, **kwargs):
        parent = _CURRENT_RETRY.get()
        policies = {
            key: policy.fork() for key, policy in parent.policies.items()
        } if parent is not None and parent.owner is function else {}
        token = _CURRENT_RETRY.set(_RetryScope(owner=function, policies=policies))
        try:
            return await function(*args, **kwargs)
        finally:
            _CURRENT_RETRY.reset(token)
    return wrapped


def get_moderation_retry_policy(llm: dict, agent_name: str, request_key=None):
    scope = _CURRENT_RETRY.get()
    if scope is None:
        return None
    # Separate models, phases and parallel HTML sections within the same operation.
    key = (id(llm), agent_name, request_key)
    if key not in scope.policies:
        scope.policies[key] = ModerationRetryPolicy()
    return scope.policies[key]
