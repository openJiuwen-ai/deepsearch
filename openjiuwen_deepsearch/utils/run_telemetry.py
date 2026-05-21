# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
HTTP telemetry for long-running search jobs — **explicit configuration**, no env vars required.

Typical use
-----------
Wrap the CLI entry (or a worker) with :func:`run_telemetry_session` and pass a
:class:`RunTelemetryConfig`::

    from jiuwen_deepsearch.utils.run_telemetry import RunTelemetryConfig, run_telemetry_session
    import main as main_mod

    cfg = RunTelemetryConfig(url="http://127.0.0.1:8089/events", run_id="exp-1")
    with run_telemetry_session(cfg):
        main_mod.main(argv=[...])  # or asyncio.run(...)

:func:`emit` POSTs JSON from a daemon thread (non-blocking). Configuration is read from
the current context on the **calling** thread and passed into the worker — ``ContextVar`` is
not inherited by new threads, so the worker must not call :func:`_effective_config` again.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterator, List, Mapping, Optional

logger = logging.getLogger(__name__)

_MAX_JSON_SAFE_DEPTH = 48

_lock = threading.Lock()
_seq = 0

_telemetry_ctx: ContextVar[Optional["RunTelemetryConfig"]] = ContextVar(
    "run_telemetry", default=None
)


@dataclass(frozen=True)
class RunTelemetryConfig:
    """HTTP sink and auth for run telemetry (all fields explicit)."""

    url: str
    """POST endpoint; must be non-empty after strip."""

    run_id: Optional[str] = None
    """Correlates all events for one logical run."""

    token: Optional[str] = None
    """If set, sent as ``Authorization: Bearer <token>``."""

    extra_headers: Dict[str, str] = field(default_factory=dict)
    """Additional request headers (string keys and values only)."""

    timeout_sec: float = 2.0
    """Per-request timeout for urllib."""

    def __post_init__(self) -> None:
        u = (self.url or "").strip()
        if not u:
            raise ValueError("RunTelemetryConfig.url must be a non-empty string")
        object.__setattr__(self, "url", u)
        rid = (self.run_id or "").strip() or None
        object.__setattr__(self, "run_id", rid)
        tok = (self.token or "").strip() or None
        object.__setattr__(self, "token", tok)
        eh = dict(self.extra_headers) if self.extra_headers else {}
        for k, v in list(eh.items()):
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("RunTelemetryConfig.extra_headers must be str -> str")
        object.__setattr__(self, "extra_headers", eh)
        if self.timeout_sec <= 0:
            raise ValueError("RunTelemetryConfig.timeout_sec must be positive")

    def to_worker_dict(self) -> Dict[str, Any]:
        """Pickle-friendly dict for :func:`multiprocessing` workers."""
        return asdict(self)

    @classmethod
    def from_worker_dict(
        cls, data: Mapping[str, Any] | None
    ) -> Optional["RunTelemetryConfig"]:
        """Rebuild config in a child process (e.g. batch worker)."""
        if not data:
            return None
        try:
            return cls(
                url=str(data["url"]),
                run_id=data.get("run_id"),
                token=data.get("token"),
                extra_headers=dict(data.get("extra_headers") or {}),
                timeout_sec=float(data.get("timeout_sec", 2.0)),
            )
        except Exception as e:
            logger.debug("[run_telemetry] invalid worker dict: %s", e)
            return None


def _effective_config() -> Optional[RunTelemetryConfig]:
    return _telemetry_ctx.get()


@contextmanager
def run_telemetry_session(
    config: Optional[RunTelemetryConfig],
) -> Iterator[None]:
    """Install ``config`` for the current context (task/async task); ``None`` = no telemetry."""
    if config is None:
        yield
        return
    tok = _telemetry_ctx.set(config)
    try:
        yield
    finally:
        _telemetry_ctx.reset(tok)


def telemetry_enabled() -> bool:
    c = _effective_config()
    return c is not None and bool(c.url)


def _next_seq() -> int:
    global _seq
    with _lock:
        _seq += 1
        return _seq


def _is_sensitive() -> bool:
    try:
        from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

        return LogManager.is_sensitive()
    except Exception:
        return False


def runtime_correlation_from(runtime: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if runtime is None:
        return out
    getter = getattr(runtime, "get_global_state", None)
    if callable(getter):
        cid = getter("conversation_id")
        if cid:
            out["conversation_id"] = cid
    return out


def telemetry_action_id_from_runtime(runtime: Any) -> Optional[str]:
    """Best-effort action id from workflow global state (search mode)."""
    if runtime is None:
        return None
    try:
        action = runtime.get_global_state("action") or {}
        aid = action.get("id")
        if aid is None:
            return None
        return str(aid)
    except Exception:
        return None


def _normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except Exception:
        return str(content)


def _message_content_entry(index_1based: int, m: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "index": index_1based,
        "role": m.get("role"),
        "content": _normalize_message_content(m.get("content")),
    }


def message_contents_for_telemetry(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """When count is 1–3, include every message; when count > 3, include messages 1, 2, and last."""
    n = len(msgs)
    if n == 0:
        return []
    if n <= 3:
        return [_message_content_entry(i + 1, msgs[i]) for i in range(n)]
    return [
        _message_content_entry(n, msgs[-1]),
    ]


def messages_payload_for_telemetry(messages: Optional[List[Any]]) -> Dict[str, Any]:
    """Build the ``messages`` object for telemetry: count, full ``message_contents``, ``last_content``, tool names.

    When sensitive logging is on, only ``count`` is included.
    """
    if not messages:
        return {"count": 0}
    msgs = [m for m in messages if isinstance(m, dict)]
    out: Dict[str, Any] = {"count": len(msgs)}
    if _is_sensitive():
        return out
    out["message_contents"] = message_contents_for_telemetry(msgs)
    last = msgs[-1] if msgs else None
    if last is not None:
        lc = _normalize_message_content(last.get("content"))
        if lc:
            out["last_content"] = lc
        if last.get("tool_calls"):
            tcs = last.get("tool_calls") or []
            if isinstance(tcs, list):
                out["last_assistant_tool_call_names"] = [
                    (tc.get("name") or (tc.get("function") or {}).get("name") or "?")
                    for tc in tcs
                    if isinstance(tc, dict)
                ][:16]
    return out


def _finalize_payload(
    payload: Optional[Dict[str, Any]],
    *,
    source: str,
    action_id: Optional[str],
) -> Dict[str, Any]:
    """Attach required ``source`` and ``action_execution`` to every telemetry payload."""
    pl = dict(payload or {})
    pl.pop("source", None)
    pl.pop("action_execution", None)
    pl["source"] = source
    pl["action_execution"] = {"action_id": action_id}
    return pl


def _non_serializable_placeholder(obj: Any) -> str:
    mod = getattr(type(obj), "__module__", "") or ""
    name = type(obj).__name__
    return f"<non-serializable {mod}.{name}>"


def json_safe_for_telemetry(obj: Any, *, _depth: int = 0) -> Any:
    """Recursively coerce ``obj`` to JSON-serializable data (no locks, clients, etc.)."""
    if _depth > _MAX_JSON_SAFE_DEPTH:
        return "<max depth exceeded>"
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return "***"
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return json_safe_for_telemetry(obj.value, _depth=_depth + 1)
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return json_safe_for_telemetry(model_dump(), _depth=_depth + 1)
    if isinstance(obj, dict):
        return {
            str(k): json_safe_for_telemetry(v, _depth=_depth + 1) for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple, set)):
        return [json_safe_for_telemetry(v, _depth=_depth + 1) for v in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError, OverflowError):
        return _non_serializable_placeholder(obj)


def _build_envelope(
    event: str, payload: Optional[Dict[str, Any]], cfg: RunTelemetryConfig
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": cfg.run_id,
        "seq": _next_seq(),
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "event": event,
        "payload": json_safe_for_telemetry(payload or {}),
    }


def _post_sync(body: Dict[str, Any], cfg: RunTelemetryConfig) -> None:
    """
    Run in a worker thread. ``cfg`` must be captured in the caller's context.
    New threads do not inherit :class:`contextvars.ContextVar`
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg.url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    if cfg.token:
        req.add_header("Authorization", f"Bearer {cfg.token}")
    for k, v in cfg.extra_headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        logger.warning(
            "[run_telemetry] POST %s failed HTTP %s: %s", cfg.url, e.code, e.reason
        )
    except Exception as e:
        logger.warning("[run_telemetry] POST %s failed: %s", cfg.url, e)


def emit(
    event: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    source: str = "unknown",
    action_id: Optional[Any] = None,
) -> None:
    """Queue a non-blocking POST (no-op if no :class:`RunTelemetryConfig` is active).

    Every payload is normalized with ``source`` and ``action_execution`` (``action_id`` may be null).
    """
    cfg = _effective_config()
    if cfg is None or not cfg.url:
        return
    aid: Optional[str] = None if action_id is None else str(action_id)
    body = _build_envelope(
        event, _finalize_payload(payload, source=source, action_id=aid), cfg
    )
    threading.Thread(target=_post_sync, args=(body, cfg), daemon=True).start()


def emit_messages_updated(
    *,
    source: str,
    messages: Optional[List[Any]],
    runtime: Any = None,
    action_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    aid = action_id
    if aid is None and runtime is not None:
        aid = telemetry_action_id_from_runtime(runtime)
    pl: Dict[str, Any] = {
        "messages": messages_payload_for_telemetry(messages),
        **runtime_correlation_from(runtime),
    }
    if extra:
        pl.update(extra)
    emit("messages_updated", pl, source=source, action_id=aid)


def state_snapshot_for_telemetry(state_dict: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": state_dict}


def emit_state_created(
    *,
    source: str,
    origin: str,
    states: List[Dict[str, Any]],
    runtime: Any = None,
    action_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit one ``state_created`` event for one or more states (e.g. initial or action patch)."""
    if not states:
        return
    snaps = [state_snapshot_for_telemetry(s) for s in states if isinstance(s, dict)]
    if not snaps:
        return
    pl: Dict[str, Any] = {
        "origin": origin,
        "count": len(snaps),
        "states": snaps,
        **runtime_correlation_from(runtime),
    }
    if extra:
        pl.update(extra)
    aid = action_id
    if aid is None and runtime is not None:
        aid = telemetry_action_id_from_runtime(runtime)
    emit("state_created", pl, source=source, action_id=aid)
