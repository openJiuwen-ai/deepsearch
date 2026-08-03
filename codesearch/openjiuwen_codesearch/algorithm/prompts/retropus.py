# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retropus prompt templates, ordered for KV / prompt-cache hits.

Providers (OpenAI, Anthropic, OpenRouter, …) cache by **exact prefix match**.
Any change early in the serialized prompt invalidates the cache for everything
after it. Therefore every template below follows:

    [static instructions / labels]  →  [semi-stable metadata]  →  [variable payload]

Cross-instance reuse: keep the system prompt byte-identical across runs that
share the same feature flags, and put the issue text only at the end of
the first user message.

Templates live as ``algorithm/prompts/*.md`` and are loaded via ``load_prompt``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from openjiuwen_codesearch.algorithm.prompts import load_prompt


def stable_prompt_cache_key(
    system: str, tools: Optional[list[dict[str, Any]]] = None
) -> str:
    """Hash the static system+tools prefix for OpenAI/OpenRouter prompt caching."""
    payload = json.dumps(
        {"system": system, "tools": tools or []},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"retropus:{digest}"


def _header(name: str) -> str:
    """Load a tool-observation header and keep a trailing newline for concat."""
    return load_prompt(name) + "\n"


# Lazy module-level accessors keep the public constant names used by tools/agent.
# Values are cached by ``load_prompt``.


def _system_prompt() -> str:
    """Load the base Retropus system prompt (no optional appendices)."""
    return load_prompt("system")


def _inherits_appendix() -> str:
    """Load the system-prompt appendix for ``expand_inheritance``."""
    return load_prompt("inherits")


def _expand_imports_appendix() -> str:
    """Load the system-prompt appendix for ``expand_imports``."""
    return load_prompt("expand_imports")


# --------------------------------------------------------------------------- #
# Tool observation headers (static instruction first; paths/payload last)
# --------------------------------------------------------------------------- #

SEARCH_CODE_HEADER = _header("search_code_header")
EXPAND_DEFS_HEADER = _header("expand_defs_header")
EXPAND_INHERITANCE_HEADER = _header("expand_inheritance_header")
EXPAND_IMPORTS_HEADER = _header("expand_imports_header")
READ_FILE_HEADER = _header("read_file_header")
NUDGE_NO_SPANS_PROMPT = load_prompt("nudge_no_spans")


def build_system_prompt(
    *,
    inherits_expand: bool = False,
    expand_imports: bool = False,
) -> str:
    """Return the fully static system prompt for the current flag set."""
    prompt = _system_prompt()
    if inherits_expand:
        prompt += "\n" + _inherits_appendix()
    if expand_imports:
        prompt += "\n" + _expand_imports_appendix()
    return prompt


def build_issue_user_message(issue_text: str) -> str:
    """User message with the variable issue text last (cache-friendly)."""
    return load_prompt("issue").format(issue_text=issue_text or "")
