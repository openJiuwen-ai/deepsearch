#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
# Telemetry event server

FastAPI app for **telemetry ingestion** and **background search runs**.

## Endpoints

- **`POST {event_path}`** — append each JSON body as one line to the JSONL log (if enabled).
- **`POST /runs`** — start **search / react** graph workflow via `main.run_jiuwen_workflow` in a background task.
- **`GET /telemetry/recent`**, **`GET /telemetry/range`** — read parsed JSONL.
- **`POST /runs/{run_id}/cancel`** — cancel a run.

## Telemetry wiring

Uses `RunTelemetryConfig` and `run_telemetry_session` (same idea as `run_main_with_telemetry`).
In-process `emit()` POSTs to `{public_base}{event_path}`.

## Defaults

- JSONL file: `output/telemetry_logs/telemetry.jsonl`

## `POST /runs`

Requires a non-empty `llm` object (`model_name`, `base_url`, `api_key`).

The **workflow** generates its own `conversation_id` for agent logs and `run_started` telemetry.
The API’s optional `conversation_id` is only for the HTTP response and **server** lifecycle
events (`run_completed`, `run_failed`, `run_cancelled`).

## Run

From the project root:

```bash
uv run python -m server.telemetry_event_server
```
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from openjiuwen_deepsearch.algorithm.search_nodes.utils import ensure_api_keys_bytearray
from openjiuwen_deepsearch.utils.validation_utils.param_validation import (
    SAFE_CONVERSATION_ID_PATTERN,
)
from openjiuwen_deepsearch.config.config import (
    Config,
    LLMConfig,
    MilvusConfig,
    PerQuestionParams,
    WebFetchProviderConfig,
    WebSearchEngineConfig,
)
from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api import (
    normalize_fetch_provider_name,
    supported_fetch_providers,
)
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import cancel_context
from openjiuwen_deepsearch.utils.run_telemetry import RunTelemetryConfig, emit, run_telemetry_session

import main as main_workflow

logger = logging.getLogger(__name__)

# Default: <project_root>/output/telemetry_logs/telemetry.jsonl
_DEFAULT_LOG_FILE = (
    Path(__file__).resolve().parent.parent / "output" / "telemetry_logs" / "telemetry.jsonl"
)
_write_lock = threading.Lock()
MAX_RECENT_N = 10_000
_LIFECYCLE_SOURCE = "server.telemetry_event_server._run_search_workflow"
_WILDCARD_BIND_HOSTS = {"0.0.0.0", "::", "[::]"}


@dataclass
class ServerState:
    """Process-wide settings: JSONL path, public URL, in-flight runs and cancel handles."""

    jsonl_path: str | None
    event_path: str
    public_base: str
    cancel_events: dict[str, asyncio.Event] = field(default_factory=dict)
    running_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# --- Pydantic: create run (search mode) ----------------------------------------------------------


def _bytearray_to_str(b: bytearray | bytes) -> str:
    """Decode secrets from config defaults for JSON-friendly Pydantic fields."""
    return b.decode("utf-8", errors="replace") if b else ""


# Field defaults are taken from openjiuwen_deepsearch.config.config (LLMConfig, MilvusConfig, …).
_LLM_CONFIG = LLMConfig()
_MIL_CONFIG = MilvusConfig()
_PQ_CONFIG = PerQuestionParams()
_AC_CONFIG = Config().agent_config


class GeneralLlmIn(BaseModel):
    """Request body aligned with `LLMConfig`; `api_key` is a JSON string.

    **Required**

    - `model_name`
    - `base_url`
    - `api_key`

    Empty values are rejected by `validate_llm_obj_params` / `create_llm_obj`.

    **Optional**

    Other fields default from `LLMConfig` (see module `_LLM_CONFIG`).
    """

    model_name: str = Field(
        ...,
        min_length=1,
        description="Required; see LLMConfig and validate_llm_obj_params",
    )
    model_type: Literal["openai", "siliconflow"] = Field(default=_LLM_CONFIG.model_type)
    base_url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    hyper_parameters: dict[str, Any] = Field(default_factory=lambda: dict(_LLM_CONFIG.hyper_parameters))
    extension: dict[str, Any] = Field(default_factory=lambda: dict(_LLM_CONFIG.extension))
    timeout: int = Field(default=_LLM_CONFIG.timeout)
    max_tries: int = Field(default=_LLM_CONFIG.max_tries)
    append_think_tags_to_messages: bool = Field(default=_LLM_CONFIG.append_think_tags_to_messages)


class MilvusFieldsIn(BaseModel):
    """Request fields aligned with `MilvusConfig`; `embedder_api_key` is a JSON string.

    Defaults match `MilvusConfig` (see module `_MIL_CONFIG`).
    """

    milvus_host: str = Field(default=_MIL_CONFIG.milvus_host)
    milvus_port: int = Field(default=_MIL_CONFIG.milvus_port)
    database_name: str = Field(default=_MIL_CONFIG.database_name)
    collection_name: str = Field(default=_MIL_CONFIG.collection_name)
    embedder_model_name: str = Field(default=_MIL_CONFIG.embedder_model_name)
    embedder_api_key: str = Field(default=_bytearray_to_str(_MIL_CONFIG.embedder_api_key))
    embedder_base_url: str = Field(default=_MIL_CONFIG.embedder_base_url)
    embedder_timeout: int = Field(default=_MIL_CONFIG.embedder_timeout)


class WebSearchEngineFieldsIn(BaseModel):
    """Request fields aligned with `WebSearchEngineConfig`; `search_api_key` is a JSON string."""

    search_engine_name: Literal[
        "tavily",
        "google",
        "xunfei",
        "petal",
        "custom",
        "bocha",
        "jina",
        "perplexity",
        "serper",
    ] = Field(default=_AC_CONFIG.web_search_engine_config.search_engine_name)
    search_api_key: str = Field(default=_bytearray_to_str(_AC_CONFIG.web_search_engine_config.search_api_key))
    search_url: str = Field(default=_AC_CONFIG.web_search_engine_config.search_url)
    max_web_search_results: int = Field(
        default=_AC_CONFIG.web_search_engine_config.max_web_search_results,
        ge=1,
        le=10,
    )
    extension: dict[str, Any] = Field(default_factory=lambda: dict(_AC_CONFIG.web_search_engine_config.extension))


class WebFetchProviderFieldsIn(BaseModel):
    """Request fields aligned with `WebFetchProviderConfig`; `api_key` is a JSON string."""

    provider_name: str = Field(default=_AC_CONFIG.web_fetch_provider_config.provider_name)
    api_key: str = Field(default=_bytearray_to_str(_AC_CONFIG.web_fetch_provider_config.api_key))
    base_url: str = Field(default=_AC_CONFIG.web_fetch_provider_config.base_url)
    extension: dict[str, Any] = Field(default_factory=lambda: dict(_AC_CONFIG.web_fetch_provider_config.extension))


class CreateSearchRunRequest(BaseModel):
    """Request body for **`POST /runs`** (DeepSearch graph: `search` or `react` only — not `research`)."""

    model_config = ConfigDict(extra="forbid")

    search_mode: Literal["search", "react"] = Field(
        default="search",
        description="search: DeepSearch graph; react: ReAct + same tools as search (see main.py --search_mode).",
    )
    enable_question_router: bool = Field(
        default=_AC_CONFIG.enable_question_router,
        description="When true and search_mode is search: LLM may route the question to react first.",
    )
    run_id: str | None = None
    query: str = Field(..., min_length=1)
    conversation_id: str | None = None
    llm: GeneralLlmIn
    tool_map: Literal["search_fetch", "retrieve"] = Field(default=_PQ_CONFIG.tool_map)
    web_search_engine_config: WebSearchEngineFieldsIn | None = Field(
        default=None,
        description="Preferred search provider config for search_fetch mode.",
    )
    web_fetch_provider_config: WebFetchProviderFieldsIn | None = Field(
        default=None,
        description="Required fetch provider config for search_fetch mode.",
    )
    milvus: MilvusFieldsIn = Field(default_factory=MilvusFieldsIn)
    search_workflow_per_question_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("conversation_id")
    @classmethod
    def _validate_optional_conversation_id(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        if not SAFE_CONVERSATION_ID_PATTERN.fullmatch(v):
            raise ValueError(
                "conversation_id must be 1–128 characters and use only ASCII letters, digits, "
                "underscore, or hyphen (^[A-Za-z0-9_-]{1,128}$)."
            )
        return v

    @model_validator(mode="after")
    def _validate_tool_keys(self) -> CreateSearchRunRequest:
        """Validate search/fetch config for `search_fetch`; embedder URL/key for `retrieve`.

        If `search_workflow_per_question_params` is set, validate merged shape against
        `PerQuestionParams`.
        """
        if self.tool_map == "search_fetch":
            if self.web_search_engine_config is None:
                raise ValueError("tool_map=search_fetch requires web_search_engine_config")
            if self.web_fetch_provider_config is None:
                raise ValueError("tool_map=search_fetch requires web_fetch_provider_config")
            _web_search_config_for_agent(self.web_search_engine_config)
            _web_fetch_provider_config_for_agent(self.web_fetch_provider_config)
        else:
            if not (self.milvus.embedder_api_key and self.milvus.embedder_base_url):
                raise ValueError("tool_map=retrieve requires milvus.embedder_api_key and milvus.embedder_base_url")
        if self.search_workflow_per_question_params:
            # Validate merge target shape (raises if invalid keys/types)
            base = _PQ_CONFIG.model_dump()
            merged = {**base, **self.search_workflow_per_question_params}
            PerQuestionParams.model_validate(merged)
        return self


def _merge_per_question(
    current_agent_config: dict[str, Any],
    req: CreateSearchRunRequest,
) -> None:
    """Overlay `search_workflow_per_question_params` and `tool_map` from the request."""
    pq = current_agent_config["search_workflow_per_question_params"]
    for k, v in req.search_workflow_per_question_params.items():
        pq[k] = v
    pq["tool_map"] = req.tool_map


def _general_llm_for_agent(req_llm: GeneralLlmIn) -> dict[str, Any]:
    """Merge request LLM fields with defaults and return an `LLMConfig`-valid dict."""
    from_request = req_llm.model_dump()
    combined: dict[str, Any] = {
        **LLMConfig().model_dump(),
        **from_request,
        "api_key": bytearray((from_request.get("api_key") or ""), "utf-8"),
    }
    return LLMConfig.model_validate(combined).model_dump()


def _milvus_config_for_agent(milvus_in: MilvusFieldsIn) -> dict[str, Any]:
    """Merge request Milvus fields with defaults and return a `MilvusConfig`-valid dict."""
    from_request = milvus_in.model_dump()
    combined: dict[str, Any] = {
        **MilvusConfig().model_dump(),
        **from_request,
        "embedder_api_key": bytearray((from_request.get("embedder_api_key") or ""), "utf-8"),
    }
    return MilvusConfig.model_validate(combined).model_dump()


def _web_search_config_for_agent(web_search_in: WebSearchEngineFieldsIn) -> dict[str, Any]:
    """Merge request web-search fields with defaults and return a `WebSearchEngineConfig`-valid dict."""
    from_request = web_search_in.model_dump()
    combined: dict[str, Any] = {
        **_AC_CONFIG.web_search_engine_config.model_dump(),
        **from_request,
        "search_api_key": bytearray((from_request.get("search_api_key") or ""), "utf-8"),
    }
    return WebSearchEngineConfig.model_validate(combined).model_dump()


def _web_fetch_provider_config_for_agent(fetch_in: WebFetchProviderFieldsIn) -> dict[str, Any]:
    """Merge request fetch-provider fields with defaults and return a validated dict."""
    from_request = fetch_in.model_dump()
    combined: dict[str, Any] = {
        **_AC_CONFIG.web_fetch_provider_config.model_dump(),
        **from_request,
        "api_key": bytearray((from_request.get("api_key") or ""), "utf-8"),
    }
    validated = WebFetchProviderConfig.model_validate(combined)
    provider_name = normalize_fetch_provider_name(validated.provider_name)
    if not provider_name:
        raise ValueError("web_fetch_provider_config.provider_name must be set explicitly")
    supported = supported_fetch_providers()
    if provider_name not in supported:
        raise ValueError(
            f"Unsupported web_fetch provider '{validated.provider_name}'. Supported providers: {', '.join(supported)}"
        )
    result = validated.model_dump()
    result["provider_name"] = provider_name
    return result


def build_agent_config_from_request(req: CreateSearchRunRequest) -> dict[str, Any]:
    """Build a full `AgentConfig` dict for `run_jiuwen_workflow` from `POST /runs` body."""
    current_agent_config: dict[str, Any] = Config().agent_config.model_dump()
    current_agent_config["llm_config"]["general"] = _general_llm_for_agent(req.llm)
    _merge_per_question(current_agent_config, req)
    current_agent_config["workflow_human_in_the_loop"] = False
    current_agent_config["outline_interaction_enabled"] = False
    current_agent_config["search_mode"] = req.search_mode
    current_agent_config["enable_question_router"] = req.enable_question_router

    if req.tool_map == "search_fetch":
        if req.web_search_engine_config is None or req.web_fetch_provider_config is None:
            raise ValueError("tool_map=search_fetch requires web_search_engine_config and web_fetch_provider_config")
        current_agent_config["web_search_engine_config"] = _web_search_config_for_agent(
            req.web_search_engine_config
        )
        current_agent_config["web_fetch_provider_config"] = _web_fetch_provider_config_for_agent(
            req.web_fetch_provider_config
        )
    else:
        current_agent_config["search_workflow_milvus_config"] = _milvus_config_for_agent(req.milvus)
    return ensure_api_keys_bytearray(current_agent_config)


_RUNTIME_ENV_DEFAULTS: dict[str, str] = {
    "LLM_SSL_VERIFY": "false",
    "LLM_SSL_CERT": "",
    "TOOL_SSL_VERIFY": "false",
    "TOOL_SSL_CERT": "",
}


def _apply_runtime_env_defaults() -> dict[str, str | None]:
    """Set SSL-related env vars to match `main.py` before running the workflow.

    Returns previous values for each key so `_restore_runtime_env` can undo the change.
    """
    previous: dict[str, str | None] = {k: os.environ.get(k) for k in _RUNTIME_ENV_DEFAULTS}
    for k, v in _RUNTIME_ENV_DEFAULTS.items():
        os.environ[k] = v
    return previous


def _restore_runtime_env(previous: dict[str, str | None]) -> None:
    """Restore environment after `_apply_runtime_env_defaults`."""
    for k, old in previous.items():
        if old is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = old


async def _run_search_workflow(
    run_id: str,
    query: str,
    conversation_id: str,
    agent_config: dict[str, Any],
    cancel_event: asyncio.Event,
) -> None:
    """Run `main.run_jiuwen_workflow` under `run_telemetry_session`.

    **Telemetry session**

    `RunTelemetryConfig.url` points at this server’s ingest path so nested `emit()` calls
    from the workflow are recorded.

    **Two different `conversation_id` values**

    - **Argument `conversation_id`:** used only in **this server’s** lifecycle events
      (`run_completed`, `run_failed`, `run_cancelled`).
    - **Inside `main.run_jiuwen_workflow`:** a **new** UUID is generated for the agent; that
      id appears in `run_started` and graph telemetry from the library.

    **Environment**

    Applies `_RUNTIME_ENV_DEFAULTS` (SSL flags) for parity with CLI `main`, then restores
    them in `finally`.
    """
    cfg = RunTelemetryConfig(
        url=_ingest_telemetry_url(),
        run_id=run_id,
        timeout_sec=2.0,
    )
    previous_runtime_env = _apply_runtime_env_defaults()
    try:
        with run_telemetry_session(cfg):
            token: object | None = None
            try:
                token = cancel_context.set(cancel_event)
                await main_workflow.run_jiuwen_workflow(query, agent_config, "")
            except asyncio.CancelledError:
                emit(
                    "run_cancelled",
                    {"reason": "task_cancelled", "conversation_id": conversation_id},
                    source=_LIFECYCLE_SOURCE,
                    action_id=None,
                )
                raise
            except Exception as e:
                logger.error("search run %s error: %s", run_id, e)
                emit(
                    "run_failed",
                    {"error": str(e), "conversation_id": conversation_id},
                    source=_LIFECYCLE_SOURCE,
                    action_id=None,
                )
                raise
            else:
                emit(
                    "run_completed",
                    {"conversation_id": conversation_id},
                    source=_LIFECYCLE_SOURCE,
                    action_id=None,
                )
            finally:
                if token is not None:
                    try:
                        cancel_context.reset(token)  # type: ignore[arg-type]
                    except (ValueError, RuntimeError):
                        pass
    finally:
        _restore_runtime_env(previous_runtime_env)


def _ingest_telemetry_url() -> str:
    """Full URL for `RunTelemetryConfig` (`public_base` + `event_path`)."""
    b = server_state.public_base.rstrip("/")
    ep = server_state.event_path
    ep = ep if ep.startswith("/") else f"/{ep}"
    return f"{b}{ep}"


def _default_public_base_from_bind(host: str, port: int) -> str:
    """Build a routable default public base URL from bind host/port."""
    h = (host or "").strip()
    if not h:
        return f"http://127.0.0.1:{port}"
    if h == "0.0.0.0":
        return f"http://127.0.0.1:{port}"
    if h in {"::", "[::]"}:
        return f"http://[::1]:{port}"
    if ":" in h and not (h.startswith("[") and h.endswith("]")):
        return f"http://[{h}]:{port}"
    return f"http://{h}:{port}"


def _append_jsonl_line(path: str, line: str) -> None:
    """Append one line to a JSONL file (thread-safe)."""
    with _write_lock:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _is_valid_telemetry_envelope(d: dict[str, Any]) -> bool:
    """True if ``d`` matches the envelope shape from :func:`run_telemetry.emit`.

    Rejects accidental POSTs (empty ``{}``, bare ``conversation_id`` / ``space_id``, and similar)
    that would otherwise pollute JSONL and dilute ``/telemetry/recent`` results.
    """
    ev = d.get("event")
    if not isinstance(ev, str) or not ev.strip():
        return False
    pl = d.get("payload")
    if pl is not None and not isinstance(pl, dict):
        return False
    return True


def _normalize_telemetry_row(d: dict[str, Any]) -> dict[str, Any]:
    """Shallow copy with a dict ``payload`` (default ``{}``)."""
    row = dict(d)
    if "payload" not in row or row["payload"] is None:
        row["payload"] = {}
    elif not isinstance(row["payload"], dict):
        row["payload"] = {}
    return row


def _read_all_parsed(path: str | None) -> list[dict[str, Any]]:
    """Read JSONL from disk and return parsed rows that pass :func:`_is_valid_telemetry_envelope`."""
    if not path or not Path(path).is_file():
        return []
    out: list[dict[str, Any]] = []
    with _write_lock:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning("skip bad JSONL line: %s", e)
                    continue
                if isinstance(d, dict) and _is_valid_telemetry_envelope(d):
                    out.append(_normalize_telemetry_row(d))
                elif isinstance(d, dict):
                    logger.debug("skip non-telemetry JSONL row (missing or invalid event): %s", line[:200])
    return out


def _make_app() -> FastAPI:
    """Construct the FastAPI application (routes bound to module `server_state`)."""
    st_ref = server_state

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for _, t in list(st_ref.running_tasks.items()):
            if t and not t.done():
                t.cancel()
        await asyncio.gather(
            *(t for t in st_ref.running_tasks.values() if not t.done()),
            return_exceptions=True,
        )

    fastapi_app = FastAPI(
        title="TelemetryEventServer",
        description=(
            "**Telemetry:** ingest `POST` bodies to JSONL.\n\n"
            "**Runs:** background `search` / `react` graph workflow + cancel.\n\n"
            "**Reads:** `/telemetry/recent` and `/telemetry/range` (single process)."
        ),
        lifespan=lifespan,
    )

    @fastapi_app.get(
        "/health",
        response_class=PlainTextResponse,
        summary="Health check (explicit path)",
        description=(
            "Lightweight liveness response for load balancers. Body is **plain text** (not JSON), "
            "e.g. `ok; append JSONL to …/telemetry.jsonl` or `ok (no JSONL file)` when file logging is off. "
            "This path is the conventional probe URL."
        ),
    )
    @fastapi_app.get(
        "/",
        response_class=PlainTextResponse,
        summary="Health check (root path)",
        description="Same behavior as `GET /health` — liveness and JSONL path hint, returned as plain text.",
    )
    def health() -> str:
        p = f"; append JSONL to {st_ref.jsonl_path}" if st_ref.jsonl_path else " (no JSONL file)"
        return f"ok{p}\n"

    @fastapi_app.post(
        st_ref.event_path,
        summary="Ingest a telemetry event",
        description=(
            "Accepts a **JSON object** body (e.g. the same envelope that `openjiuwen_deepsearch.utils."
            "run_telemetry` posts: `event`, `run_id`, `seq`, `ts`, `payload`, …). "
            "The server optionally appends the serialized JSON (one line) to the configured JSONL file. "
            "\n\n**Responses:** `204` on success; `400` if the body is not a JSON object; `422` when JSONL "
            "persistence is enabled and the body is not a valid telemetry envelope (non-empty `event`, "
            "optional object `payload`)."
        ),
    )
    async def post_event(request: Request) -> Response:
        raw = await request.body()
        if not raw:
            body: dict[str, Any] = {}
        else:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                logger.warning("invalid JSON body: %s", e)
                return Response(status_code=status.HTTP_400_BAD_REQUEST)
            if not isinstance(parsed, dict):
                return Response(status_code=status.HTTP_400_BAD_REQUEST)
            body = parsed
        if st_ref.jsonl_path:
            if not _is_valid_telemetry_envelope(body):
                logger.warning("invalid JSON body cannot be ingested: %s", body)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "JSONL persistence requires a telemetry envelope: non-empty string 'event', "
                        "and optional object 'payload'. Bare correlation fields (e.g. only "
                        "conversation_id / space_id) are not accepted."
                    ),
                )
            line = json.dumps(_normalize_telemetry_row(body), ensure_ascii=False)
            logger.info(
                "event=%s run_id=%s seq=%s", body.get("event"), body.get("run_id"), body.get("seq")
            )
            await run_in_threadpool(_append_jsonl_line, st_ref.jsonl_path, line)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @fastapi_app.post(
        "/runs",
        status_code=status.HTTP_201_CREATED,
        summary="Start a background search run",
        description=(
            "Validates the body, then schedules `main.run_jiuwen_workflow` for **`search` or `react`** "
            "(DeepSearch graph modes only, not `research`). "
            "The handler returns immediately; progress and errors appear via telemetry to the ingest "
            "path and in server lifecycle `emit` events (`run_completed` / `run_failed` / `run_cancelled`).\n\n"
            "**`search_mode` / `enable_question_router`:** same semantics as `main.py` for graph runs.\n\n"
            "**`tool_map`:** `search_fetch` requires explicit `web_search_engine_config` + "
            "`web_fetch_provider_config`; "
            "`retrieve` requires Milvus/embedder fields.\n"
            "\n**IDs:** the workflow generates its **own** `conversation_id` for on-disk logs; the `conversation_id` "
            "in the response (and in lifecycle emits) is the **API** id the client may correlate with. "
            "Do not reuse a client `run_id` until the previous run with that id has finished (otherwise **409**)."
        ),
    )
    async def create_run(req: CreateSearchRunRequest) -> dict[str, Any]:
        run_id = req.run_id or str(uuid.uuid4())
        conversation_id = req.conversation_id or str(uuid.uuid4())
        async with st_ref.run_lock:
            t_old = st_ref.running_tasks.get(run_id)
            if t_old and not t_old.done():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="run_id already in progress",
                )
        try:
            agent_config = build_agent_config_from_request(req)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

        ev = asyncio.Event()
        st_ref.cancel_events[run_id] = ev
        t = asyncio.create_task(
            _run_search_workflow(
                run_id=run_id,
                query=req.query,
                conversation_id=conversation_id,
                agent_config=agent_config,
                cancel_event=ev,
            ),
            name=f"search-run-{run_id}",
        )
        st_ref.running_tasks[run_id] = t

        def _run_task_done_callback(task: asyncio.Task[None]) -> None:
            try:
                if task.cancelled():
                    logger.info("search run %s cancelled (task)", run_id)
                else:
                    exc = task.exception()
                    if exc is not None:
                        logger.error("search run %s failed: %s", run_id, exc, exc_info=exc)
            finally:
                st_ref.running_tasks.pop(run_id, None)
                st_ref.cancel_events.pop(run_id, None)

        t.add_done_callback(_run_task_done_callback)
        return {"run_id": run_id, "status": "started", "conversation_id": conversation_id}

    @fastapi_app.post(
        "/runs/{run_id}/cancel",
        summary="Cancel a run",
        description=(
            "Signals the run’s `cancel_context` and **cancels** the asyncio task if the run is still in "
            "`running_tasks`. Best-effort: a **404** means the id is unknown or the run has already completed."
        ),
    )
    async def cancel_run(run_id: str) -> Response:
        async with st_ref.run_lock:
            ev = st_ref.cancel_events.get(run_id)
            t = st_ref.running_tasks.get(run_id)
        if not ev and not t:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="unknown or finished run_id",
            )
        if ev:
            ev.set()
        if t and not t.done():
            t.cancel()
        try:
            if t and not t.done():
                await t
        except asyncio.CancelledError:
            pass
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @fastapi_app.get(
        "/telemetry/recent",
        summary="Last N events (optionally for one run_id)",
        description=(
            "Reads the entire JSONL file from disk, optionally filters by `run_id`, then returns the **last** "
            "`n` matching records (by file order), capped at 10,000. Useful for a tail view in dashboards. "
            "The file is read under a lock; very large files can be slow on cold requests."
        ),
    )
    async def telemetry_recent(n: int, run_id: str | None = None) -> dict[str, Any]:
        n = min(max(1, n), MAX_RECENT_N)
        rows = await run_in_threadpool(_read_all_parsed, st_ref.jsonl_path)
        if run_id is not None:
            rows = [r for r in rows if r.get("run_id") == run_id]
        tail = rows[-n:] if len(rows) > n else rows
        return {"items": tail, "count": len(tail)}

    @fastapi_app.get(
        "/telemetry/range",
        summary="Events by run_id and seq range",
        description=(
            "Scans the full JSONL log and returns every event whose `run_id` matches **and** whose integer `seq` "
            "is between `start_seq` and `end_seq` (inclusive). Events without a usable `seq` are skipped. "
            "`start_seq` must be ≤ `end_seq` (otherwise **422**)."
        ),
    )
    async def telemetry_range(
        run_id: str, start_seq: int, end_seq: int
    ) -> dict[str, Any]:
        if start_seq > end_seq:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="start_seq must be <= end_seq",
            )
        rows = await run_in_threadpool(_read_all_parsed, st_ref.jsonl_path)
        picked: list[dict[str, Any]] = []
        for r in rows:
            if r.get("run_id") != run_id:
                continue
            seq = r.get("seq")
            if seq is None:
                continue
            if not isinstance(seq, int):
                try:
                    seq = int(seq)  # type: ignore[assignment]
                except (TypeError, ValueError):
                    continue
            if start_seq <= seq <= end_seq:
                picked.append(r)
        return {"items": picked, "count": len(picked)}

    return fastapi_app


# Default app for: ``uvicorn server.telemetry_event_server:app`` (from project root).
_default_log = str(_DEFAULT_LOG_FILE)
Path(_default_log).parent.mkdir(parents=True, exist_ok=True)
Path(_default_log).touch(exist_ok=True)
server_state = ServerState(
    jsonl_path=_default_log,
    event_path="/events",
    public_base="http://127.0.0.1:8089",
)
app: FastAPI = _make_app()


def main(argv: list[str] | None = None) -> int:
    """CLI: configure `server_state`, rebuild `app`, and start Uvicorn."""
    p = argparse.ArgumentParser(
        description="Telemetry ingestion + background search runs (FastAPI / Uvicorn).",
    )
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8089, help="Port (default 8089)")
    p.add_argument(
        "--path",
        default="/events",
        help="URL path for telemetry POST (default /events)",
    )
    p.add_argument(
        "--jsonl",
        default=str(_DEFAULT_LOG_FILE),
        metavar="FILE",
        help="JSONL file for events (default: output/telemetry_logs/telemetry.jsonl under project root)",
    )
    p.add_argument(
        "--no-jsonl",
        action="store_true",
        help="Do not write POST bodies to a file (HTTP only)",
    )
    p.add_argument(
        "--public-base-url",
        default="",
        help=(
            "Public base for RunTelemetryConfig.url = BASE + --path "
            "(default: derived from --host/--port; wildcard bind hosts use loopback)"
        ),
    )
    p.add_argument("--verbose", "-v", action="store_true", help="DEBUG logging")
    args = p.parse_args(argv)

    ep = args.path if args.path.startswith("/") else f"/{args.path}"
    public_base = (
        args.public_base_url or _default_public_base_from_bind(args.host, args.port)
    ).rstrip("/")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not args.public_base_url and args.host in _WILDCARD_BIND_HOSTS:
        logger.info(
            "bind host %s is wildcard; using internal telemetry base %s "
            "(override with --public-base-url)",
            args.host,
            public_base,
        )

    global server_state, app
    server_state = ServerState(
        jsonl_path=None if args.no_jsonl else args.jsonl,
        event_path=ep,
        public_base=public_base,
    )
    if not args.no_jsonl and args.jsonl:
        log_path = Path(args.jsonl)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
    app = _make_app()

    try:
        import uvicorn
    except ImportError as e:
        logger.error("uvicorn is required. Install: uv sync --group backend. %s", e)
        return 1
    telemetry_url = f"{public_base}{ep}"
    logger.info("Uvicorn http://%s:%s — telemetry: POST %s", args.host, args.port, telemetry_url)
    if server_state.jsonl_path:
        logger.info("Appending events to %s", os.path.abspath(server_state.jsonl_path))
    else:
        logger.info("JSONL file output disabled")
    logger.info("Runs: POST /runs, cancel: POST /runs/{run_id}/cancel")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="debug" if args.verbose else "info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
