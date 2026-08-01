# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retropus prompt templates, ordered for KV / prompt-cache hits.

Providers (OpenAI, Anthropic, OpenRouter, …) cache by **exact prefix match**.
Any change early in the serialized prompt invalidates the cache for everything
after it. Therefore every template below follows:

    [static instructions / labels]  →  [semi-stable metadata]  →  [variable payload]

Cross-instance reuse: keep the system prompt byte-identical across runs that
share the same improvement flags, and put the issue text only at the end of
the first user message.

Templates live as ``algorithm/prompts/*.md`` and are loaded via ``load_prompt``.
"""

from __future__ import annotations

from openjiuwen_codesearch.algorithm.prompts import load_prompt


def _header(name: str) -> str:
    """Load a tool-observation header and keep a trailing newline for concat."""
    return load_prompt(name) + "\n"


# Lazy module-level accessors keep the public constant names used by tools/agent.
# Values are cached by ``load_prompt``.


def _system_prompt() -> str:
    return load_prompt("system")


def _inherits_appendix() -> str:
    return load_prompt("inherits")


# --------------------------------------------------------------------------- #
# Tool observation headers (static instruction first; paths/payload last)
# --------------------------------------------------------------------------- #

SEARCH_CODE_HEADER = _header("search_code_header")
EXPAND_DEFS_HEADER = _header("expand_defs_header")
EXPAND_INHERITANCE_HEADER = _header("expand_inheritance_header")
READ_FILE_HEADER = _header("read_file_header")
NUDGE_NO_SPANS_PROMPT = load_prompt("nudge_no_spans")


def build_system_prompt(
    *,
    inherits_expand: bool = False,
) -> str:
    """Return the fully static system prompt for the current flag set."""
    prompt = _system_prompt()
    if inherits_expand:
        prompt += "\n" + _inherits_appendix()
    return prompt


def build_issue_user_message(issue_text: str) -> str:
    """User message with the variable issue text last (cache-friendly)."""
    return load_prompt("issue").format(issue_text=issue_text or "")
