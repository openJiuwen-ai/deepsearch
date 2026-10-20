# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Normalize observed moderation signals without making requests or reading content."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError


Side = Literal["input", "output", "unknown"]
Source = Literal["exception", "response", "stream"]
_DATA_SIDE = re.compile(r"\b(input|output)(?: text)? data may contain inappropriate\b", re.I)
_SENSITIVE_INPUT = re.compile(r"\binput may contain sensitive information\b", re.I)
_INSPECTION_CODE = re.compile(r"\bdata_inspection_failed\b", re.I)
_MAX_ERROR_SIZE = 65536
_MAX_CHAIN_LENGTH = 16


@dataclass(frozen=True)
class ModerationSignal:
    side: Side
    code: str
    source: Source
    evidence: str


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _mapping(value: Any) -> Mapping | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and len(value) <= _MAX_ERROR_SIZE:
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            return None
        if isinstance(parsed, Mapping):
            return parsed
    return None


def _code(value: Any) -> str:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip().lower()
    return ""


def _message_sides(message: str) -> set[str]:
    sides = {match.group(1).lower() for match in _DATA_SIDE.finditer(message)}
    if _SENSITIVE_INPUT.search(message):
        sides.add("input")
    return sides


def _side(sides: set[str]) -> Side:
    return next(iter(sides)) if len(sides) == 1 else "unknown"


def _combine(signals: list[ModerationSignal]) -> ModerationSignal | None:
    if not signals:
        return None
    primary = next((s for s in signals if s.code != "content_filter"), signals[0])
    sides = {s.side for s in signals if s.side != "unknown"}
    conflict = len(sides) > 1 or any(s.evidence == "conflicting_side_fields" for s in signals)
    evidence = "conflicting_side_fields" if conflict else primary.evidence
    return ModerationSignal("unknown" if conflict else _side(sides), primary.code, primary.source, evidence)


def _error_signal(envelope: Mapping, source: Source, status: Any) -> ModerationSignal | None:
    error = _mapping(envelope.get("error")) or envelope
    code = _code(error.get("code"))
    message = error.get("message")
    message = message[:_MAX_ERROR_SIZE] if isinstance(message, str) else ""
    sides = _message_sides(message)
    evidence = "explicit_side_phrase" if sides else "error_code"
    if code == "1301":
        if not message:
            return None
        filters = envelope.get("contentFilter", error.get("contentFilter"))
        if isinstance(filters, list) and filters:
            roles = {_code(item.get("role")) for item in filters if isinstance(item, Mapping)}
            if len(roles) == 1 and roles == {"user"} and all(isinstance(x, Mapping) for x in filters):
                sides.add("input")
                evidence = "content_filter_user"
    elif code not in {"data_inspection_failed", "sensitivecontentdetected"}:
        if (
            _code(status or error.get("http_code")) == "422"
            and error.get("type") == "unprocessable_entity_error"
            and message == "input new_sensitive (1026)"
        ):
            return ModerationSignal("input", "minimax_input_sensitive", source, "minimax_422_input")
        if not _DATA_SIDE.search(message):
            return None
        code = "data_inspection_failed"
    if len(sides) > 1:
        evidence = "conflicting_side_fields"
    return ModerationSignal(_side(sides), code, source, evidence)


def detect_response(value: Any, source: Source = "response", status: Any = None) -> ModerationSignal | None:
    """Inspect only protocol fields, never message content or tool arguments."""
    signals = []
    envelope = _mapping(value)
    if envelope is not None:
        signal = _error_signal(envelope, source, status)
        if signal is not None:
            signals.append(signal)
    if _field(value, "finish_reason") == "content_filter":
        signals.append(ModerationSignal("unknown", "content_filter", source, "finish_reason"))
    choices = _field(value, "choices")
    if isinstance(choices, (list, tuple)):
        for choice in choices:
            if _field(choice, "finish_reason") == "content_filter":
                signals.append(ModerationSignal("unknown", "content_filter", source, "finish_reason"))
    return _combine(signals)


def detect_error(error: BaseException | None = None, detail: str = "") -> ModerationSignal | None:
    """Prefer structured SDK errors and full response envelopes over text fallback."""
    pending = [error] if error is not None else []
    visited = set()
    signals = []
    texts = [detail[:_MAX_ERROR_SIZE]] if isinstance(detail, str) else []
    parse_failure = False
    while pending and len(visited) < _MAX_CHAIN_LENGTH:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, (ValidationError, json.JSONDecodeError)):
            parse_failure = True
        attached = getattr(current, "moderation_signal", None)
        if isinstance(attached, ModerationSignal):
            signals.append(attached)
        response = getattr(current, "response", None)
        status = getattr(current, "status_code", None) or getattr(response, "status_code", None)
        # SDK .body may contain only inner error; role evidence lives in response.json().
        response_json = getattr(response, "json", None)
        if callable(response_json):
            try:
                envelope = response_json()
            except (ValueError, RuntimeError):
                envelope = None
            signal = detect_response(envelope, "exception", status)
            if signal is not None:
                signals.append(signal)
        signal = detect_response(getattr(current, "body", None), "exception", status)
        if signal is not None:
            signals.append(signal)
        texts.append(str(current)[:_MAX_ERROR_SIZE])
        cause = current.__cause__
        context = current.__context__
        if cause is not None:
            pending.append(cause)
        elif not current.__suppress_context__ and context is not None:
            pending.append(context)
    structured = _combine(signals)
    if structured is not None:
        can_use_text_side = not parse_failure and structured.evidence != "conflicting_side_fields"
        if (
            structured.side == "unknown" and can_use_text_side
            and structured.code in {"data_inspection_failed", "sensitivecontentdetected"}
        ):
            sides = set().union(*(_message_sides(text) for text in texts))
            if sides:
                return ModerationSignal(
                    _side(sides), structured.code, structured.source,
                    "conflicting_side_fields" if len(sides) > 1 else "text_side_fallback",
                )
        return structured
    # Validation errors may quote generated content that looks like an API error.
    if parse_failure:
        return None
    for text in texts:
        signal = detect_response(_mapping(text), "exception")
        if signal is not None:
            signals.append(signal)
        sides = _message_sides(text)
        if _DATA_SIDE.search(text) or _INSPECTION_CODE.search(text):
            signals.append(ModerationSignal(
                _side(sides), "data_inspection_failed", "exception",
                "conflicting_side_fields" if len(sides) > 1 else "text_fallback",
            ))
    return _combine(signals)
