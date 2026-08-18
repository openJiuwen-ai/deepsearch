# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Recursive character text splitter (LangChain-compatible behaviour, no langchain deps).

Separator preference: ``["\\n\\n", "\\n", " ", ""]``. Separator is kept at the
start of the following piece; chunks are stripped of leading/trailing whitespace.
"""

from __future__ import annotations

import re
from typing import List, Sequence

_DEFAULT_SEPARATORS: Sequence[str] = ("\n\n", "\n", " ", "")


def _check_limits(*, size: int, overlap: int) -> None:
    """Validate chunk sizing before splitting."""
    if size < 1:
        raise ValueError(f"invalid chunk_size {size}: expected a positive integer")
    if overlap < 0:
        raise ValueError(f"invalid chunk_overlap {overlap}: expected a non-negative integer")
    if overlap > size:
        raise ValueError(
            f"invalid chunk_overlap {overlap}: cannot exceed chunk_size {size}"
        )


def split_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    separators: Sequence[str] = _DEFAULT_SEPARATORS,
) -> List[str]:
    """Split ``text`` into overlapping chunks preferring coarser separators."""
    _check_limits(size=chunk_size, overlap=chunk_overlap)
    if not text:
        return []
    return _recursive_split(
        text,
        list(separators),
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


def _keep_separator_split(text: str, separator: str) -> List[str]:
    """Split so each separator stays glued to the piece that follows it."""
    if not separator:
        return list(text)
    tokens = re.split(f"({re.escape(separator)})", text)
    out: List[str] = []
    if tokens and tokens[0]:
        out.append(tokens[0])
    for i in range(1, len(tokens), 2):
        sep = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        out.append(sep + nxt)
    return [p for p in out if p]


def _emit(parts: List[str], joiner: str) -> str | None:
    text = joiner.join(parts).strip()
    return text or None


def _pack_windows(
    pieces: List[str],
    joiner: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> List[str]:
    """Accumulate pieces into sized windows; retain ``chunk_overlap`` chars."""
    jlen = len(joiner)
    result: List[str] = []
    window: List[str] = []
    used = 0

    for piece in pieces:
        add = len(piece) + (jlen if window else 0)
        if used + add > chunk_size and window:
            chunk = _emit(window, joiner)
            if chunk is not None:
                result.append(chunk)
            # Trim from the left until overlap budget + new piece fit.
            while window and (
                used > chunk_overlap
                or used + len(piece) + (jlen if window else 0) > chunk_size
            ):
                head = window.pop(0)
                used -= len(head) + (jlen if window else 0)
        window.append(piece)
        used += len(piece) + (jlen if len(window) > 1 else 0)

    chunk = _emit(window, joiner)
    if chunk is not None:
        result.append(chunk)
    return result


def _pick_separator(text: str, separators: List[str]) -> tuple[str, List[str]]:
    for i, sep in enumerate(separators):
        if not sep:
            return sep, []
        if re.search(re.escape(sep), text):
            return sep, separators[i + 1:]
    return separators[-1], []


def _recursive_split(
    text: str,
    separators: List[str],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> List[str]:
    sep, finer = _pick_separator(text, separators)
    parts = _keep_separator_split(text, sep)
    # Separators already live inside ``parts`` → pack with empty joiner.
    joiner = ""

    out: List[str] = []
    pending: List[str] = []

    def flush() -> None:
        nonlocal pending
        if pending:
            out.extend(
                _pack_windows(
                    pending,
                    joiner,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            )
            pending = []

    for part in parts:
        if len(part) < chunk_size:
            pending.append(part)
            continue
        flush()
        if finer:
            out.extend(
                _recursive_split(
                    part,
                    finer,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            )
        else:
            out.append(part)
    flush()
    return out
