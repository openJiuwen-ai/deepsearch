"""Recursive character text splitter (LangChain-compatible, no langchain deps).

Matches ``langchain_text_splitters.RecursiveCharacterTextSplitter`` defaults:
separators ``["\\n\\n", "\\n", " ", ""]``, ``keep_separator=True`` (attach at
start), and ``strip_whitespace=True``.
"""

import re
from typing import List, Sequence


_DEFAULT_SEPARATORS: Sequence[str] = ("\n\n", "\n", " ", "")


def split_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    separators: Sequence[str] = _DEFAULT_SEPARATORS,
) -> List[str]:
    """Split ``text`` into overlapping chunks preferring coarser separators."""
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
    if chunk_overlap < 0:
        raise ValueError(f"chunk_overlap must be >= 0, got {chunk_overlap}")
    if chunk_overlap > chunk_size:
        raise ValueError(
            f"chunk_overlap ({chunk_overlap}) must be <= chunk_size ({chunk_size})"
        )
    return _split_text(text, list(separators), chunk_size=chunk_size, chunk_overlap=chunk_overlap)


def _split_with_separator(text: str, separator: str) -> List[str]:
    """Split ``text`` keeping the separator attached to the following piece."""
    if not separator:
        return [ch for ch in text if ch]
    parts = re.split(f"({re.escape(separator)})", text)
    # parts: [pre, sep, mid, sep, mid, ..., post]
    merged = [parts[i] + parts[i + 1] for i in range(1, len(parts), 2)]
    if len(parts) % 2 == 0:
        merged.append(parts[-1])
    return [s for s in [parts[0], *merged] if s]


def _join_docs(docs: List[str], separator: str) -> str | None:
    """Join split pieces with ``separator``, returning ``None`` for empty/whitespace."""
    text = separator.join(docs).strip()
    return text or None


def _merge_splits(
    splits: List[str],
    separator: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> List[str]:
    """Greedily merge ``splits`` into chunks of at most ``chunk_size`` with overlap."""
    separator_len = len(separator)
    docs: List[str] = []
    current_doc: List[str] = []
    total = 0
    for piece in splits:
        piece_len = len(piece)
        projected = total + piece_len + (separator_len if current_doc else 0)
        if projected > chunk_size and current_doc:
            doc = _join_docs(current_doc, separator)
            if doc is not None:
                docs.append(doc)
            while total > chunk_overlap or (
                total + piece_len + (separator_len if current_doc else 0) > chunk_size
                and total > 0
            ):
                total -= len(current_doc[0]) + (separator_len if len(current_doc) > 1 else 0)
                current_doc = current_doc[1:]
        current_doc.append(piece)
        total += piece_len + (separator_len if len(current_doc) > 1 else 0)
    doc = _join_docs(current_doc, separator)
    if doc is not None:
        docs.append(doc)
    return docs


def _split_text(
    text: str,
    separators: List[str],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> List[str]:
    """Recursively split ``text`` with the coarsest matching separator, then merge."""
    separator = separators[-1]
    new_separators: List[str] = []
    for i, candidate in enumerate(separators):
        if not candidate:
            separator = candidate
            break
        if re.search(re.escape(candidate), text):
            separator = candidate
            new_separators = separators[i + 1:]
            break

    splits = _split_with_separator(text, separator)
    # Separators are already attached to splits, so merge with "".
    merge_separator = ""
    final_chunks: List[str] = []
    good_splits: List[str] = []
    for piece in splits:
        if len(piece) < chunk_size:
            good_splits.append(piece)
            continue
        if good_splits:
            final_chunks.extend(
                _merge_splits(
                    good_splits,
                    merge_separator,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            )
            good_splits = []
        if not new_separators:
            final_chunks.append(piece)
        else:
            final_chunks.extend(
                _split_text(
                    piece,
                    new_separators,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            )
    if good_splits:
        final_chunks.extend(
            _merge_splits(
                good_splits,
                merge_separator,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        )
    return final_chunks
