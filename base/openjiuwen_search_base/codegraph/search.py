"""Viewer-syntax search over in-memory node and edge dicts.

Parses the same query language as the graph viewer search bar: free-text
substrings plus ``{field:glob}`` predicates.  See the README section
"Viewer Search Syntax".

Matches are sorted with fixed relevance rules.  ``limit`` only truncates
``matches``; ``total`` and tag stats cover the full hit set.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

EDGE_FIELDS: frozenset[str] = frozenset({"relation", "confidence", "resolved_by", "source", "target"})

_NODE_REGEX_FIELDS: tuple[str, ...] = ("name", "signature", "id", "path", "type", "node_type")
_EDGE_REGEX_FIELDS: tuple[str, ...] = ("relation", "resolved_by", "source", "target")

_NODE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "type": ("type", "node_type"),
}

_PRED_RE = re.compile(r"\{(\w+):([^}]+)\}")

_EMPTY_RESULT_COUNTS: dict[str, int] = {}
_TOP_TAG_COMBOS: int = 10
_LRU_CACHE_SIZE: int = 2048


@dataclass(frozen=True)
class ParsedSearchQuery:
    """Parsed free text and field predicates from a viewer search query.

    Attributes:
        text: Lowercased free-text segment (may be empty).
        predicates: ``(field, compiled_glob)`` pairs.  A compiled pattern
            of ``None`` means the glob was invalid and never matches.
    """

    text: str
    predicates: tuple[tuple[str, re.Pattern[str] | None], ...]


_EMPTY_PARSED = ParsedSearchQuery("", ())


@dataclass(frozen=True)
class SearchResult:
    """Sorted search hits plus full-set tag facet counts.

    Attributes:
        matches: Sorted hit dicts, truncated by ``limit`` when set.
        total: Full hit count before ``limit`` truncation (always set).
        tag_counts: Individual tag frequencies over all hits.
        tag_combo_counts: Top full tag-set combinations over all hits
            (prefer longer sets, then higher count; at most 10 entries).
    """

    matches: list[dict]
    total: int
    tag_counts: dict[str, int]
    tag_combo_counts: dict[str, int]


@lru_cache(maxsize=_LRU_CACHE_SIZE)
def _glob_to_regex(pattern: str) -> re.Pattern[str] | None:
    """Compile a case-insensitive anchored glob; return ``None`` if invalid."""
    escaped = re.escape(pattern).replace(r"\*", ".*")
    try:
        return re.compile(f"^{escaped}$", re.IGNORECASE)
    except re.error:
        return None


@lru_cache(maxsize=_LRU_CACHE_SIZE)
def parse_search_query(query: str) -> ParsedSearchQuery | None:
    """Parse a viewer search query into free text and field predicates.

    Returns ``None`` when *query* is empty or whitespace-only.
    """
    trimmed = query.strip()
    if not trimmed:
        return None

    predicates: list[tuple[str, re.Pattern[str] | None]] = []
    text_parts: list[str] = []
    last = 0
    for match in _PRED_RE.finditer(trimmed):
        before = trimmed[last : match.start()].strip()
        if before:
            text_parts.append(before)
        last = match.end()
        key = match.group(1)
        predicates.append((key, _glob_to_regex(match.group(2).strip())))
    tail = trimmed[last:].strip()
    if tail:
        text_parts.append(tail)

    text = " ".join(text_parts).lower()
    return ParsedSearchQuery(text=text, predicates=tuple(predicates))


def _field_matches(value: Any, pattern: re.Pattern[str] | None) -> bool:
    if pattern is None or value is None:
        return False
    return pattern.fullmatch(str(value)) is not None


def _node_predicate_matches(node: Mapping[str, Any], key: str, pattern: re.Pattern[str] | None) -> bool:
    fields = _NODE_FIELD_ALIASES.get(key, (key,))
    return any(_field_matches(node.get(field), pattern) for field in fields)


def _node_matches(node: Mapping[str, Any], parsed: ParsedSearchQuery) -> bool:
    if parsed.text:
        name = str(node.get("name") or "").lower()
        signature = str(node.get("signature") or "").lower()
        if parsed.text not in name and parsed.text not in signature:
            return False

    for key, pattern in parsed.predicates:
        if key in EDGE_FIELDS:
            continue
        if not _node_predicate_matches(node, key, pattern):
            return False
    return True


def _edge_free_text_haystack(edge: Mapping[str, Any]) -> str:
    parts = [
        str(edge.get("relation") or ""),
        str(edge.get("resolved_by") or ""),
        str(edge.get("source") or ""),
        str(edge.get("target") or ""),
    ]
    return " ".join(parts).lower()


def _edge_matches(edge: Mapping[str, Any], parsed: ParsedSearchQuery) -> bool:
    if parsed.text and parsed.text not in _edge_free_text_haystack(edge):
        return False

    for key, pattern in parsed.predicates:
        if key not in EDGE_FIELDS:
            continue
        if not _field_matches(edge.get(key), pattern):
            return False
    return True


def _has_node_criteria(parsed: ParsedSearchQuery) -> bool:
    if parsed.text:
        return True
    return any(key not in EDGE_FIELDS for key, _ in parsed.predicates)


def _has_edge_criteria(parsed: ParsedSearchQuery) -> bool:
    if parsed.text:
        return True
    return any(key in EDGE_FIELDS for key, _ in parsed.predicates)


def _empty_result() -> SearchResult:
    return SearchResult([], 0, dict(_EMPTY_RESULT_COUNTS), dict(_EMPTY_RESULT_COUNTS))


def _sorted_count_dict(counter: Counter[str]) -> dict[str, int]:
    """Order by count descending, then key ascending."""
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _top_tag_combo_dict(counter: Counter[str], *, limit: int = _TOP_TAG_COMBOS) -> dict[str, int]:
    """Keep the top combos, preferring more tags, then higher count."""
    ranked = sorted(
        counter.items(),
        key=lambda item: (-(item[0].count("|") + 1), -item[1], item[0]),
    )
    return dict(ranked[:limit])


def _accumulate_tags(counter: Counter[str], combo_counter: Counter[str], tags: Sequence[str]) -> None:
    """Count individual tags and the full tag-set as one combination."""
    unique = sorted({str(tag) for tag in tags if tag})
    for tag in unique:
        counter[tag] += 1
    if len(unique) >= 2:
        combo_counter["|".join(unique)] += 1


def _tag_stats_from_nodes(nodes: Sequence[Mapping[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    tag_counts: Counter[str] = Counter()
    combo_counts: Counter[str] = Counter()
    for node in nodes:
        raw = node.get("tags") or []
        if isinstance(raw, (list, tuple)):
            _accumulate_tags(tag_counts, combo_counts, raw)
    return _sorted_count_dict(tag_counts), _top_tag_combo_dict(combo_counts)


def _tag_stats_from_edge_endpoints(
    edges: Sequence[Mapping[str, Any]],
    nodes: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    tags_by_id: dict[str, list[str]] = {}
    for node in nodes:
        node_id = node.get("id")
        if node_id is None:
            continue
        raw = node.get("tags") or []
        if isinstance(raw, (list, tuple)):
            tags_by_id[str(node_id)] = [str(tag) for tag in raw if tag]

    tag_counts: Counter[str] = Counter()
    combo_counts: Counter[str] = Counter()
    for edge in edges:
        for endpoint in (edge.get("source"), edge.get("target")):
            if endpoint is None:
                continue
            tags = tags_by_id.get(str(endpoint))
            if tags:
                _accumulate_tags(tag_counts, combo_counts, tags)
    return _sorted_count_dict(tag_counts), _top_tag_combo_dict(combo_counts)


def _node_relevance(node: Mapping[str, Any], text: str) -> int:
    """Lower is better: exact name, name contains, signature-only, no free text."""
    if not text:
        return 3
    name = str(node.get("name") or "").lower()
    if name == text:
        return 0
    if text in name:
        return 1
    return 2


def _node_sort_key(node: Mapping[str, Any], parsed: ParsedSearchQuery) -> tuple:
    return (
        _node_relevance(node, parsed.text),
        str(node.get("node_type") or "").casefold(),
        str(node.get("name") or "").casefold(),
        str(node.get("path") or "").casefold(),
        str(node.get("id") or "").casefold(),
    )


def _edge_confidence(edge: Mapping[str, Any]) -> float:
    value = edge.get("confidence")
    if value is None:
        return 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _edge_sort_key(edge: Mapping[str, Any]) -> tuple:
    return (
        -_edge_confidence(edge),
        str(edge.get("relation") or "").casefold(),
        str(edge.get("source") or "").casefold(),
        str(edge.get("target") or "").casefold(),
    )


def _apply_match_limit(hits: list[dict], limit: int | None) -> list[dict]:
    """Truncate *hits* for ``matches``.

    ``None`` or a negative *limit* (e.g. ``-1``) means return all hits.
    """
    if limit is None or limit < 0:
        return hits
    return hits[:limit]


def search_nodes(
    nodes: Sequence[Mapping[str, Any]],
    query: str,
    *,
    limit: int | None = None,
) -> SearchResult:
    """Return matching nodes sorted by relevance, with full-set tag stats.

    Free text matches ``name`` and ``signature`` (case-insensitive substring).
    ``{field:pattern}`` predicates use case-insensitive globs; ``{type:…}``
    matches ``type`` or ``node_type``.  Edge fields
    (``relation``, ``confidence``, ``resolved_by``, ``source``, ``target``)
    in the query are ignored.

    Sort order: free-text relevance (exact name, name contains, signature-only),
    then ``node_type``, ``name``, ``path``, ``id``.

    An empty or whitespace-only *query* returns an empty :class:`SearchResult`.
    Edge-only queries (no free text and only edge-field predicates) also return
    empty.

    Args:
        nodes: Exported node dicts (graph JSONL shape).
        query: Viewer search syntax string.
        limit: Max matches in ``matches``. ``None`` or a negative value (e.g.
            ``-1``) returns all hits. ``total`` and tag counts always cover the
            full hit set.
    """
    parsed = parse_search_query(query)
    if parsed is None or not _has_node_criteria(parsed):
        return _empty_result()

    hits = [dict(node) for node in nodes if _node_matches(node, parsed)]
    tag_counts, tag_combo_counts = _tag_stats_from_nodes(hits)
    hits.sort(key=lambda node: _node_sort_key(node, parsed))
    total = len(hits)
    return SearchResult(_apply_match_limit(hits, limit), total, tag_counts, tag_combo_counts)


def search_edges(
    edges: Sequence[Mapping[str, Any]],
    query: str,
    *,
    limit: int | None = None,
    nodes: Sequence[Mapping[str, Any]] | None = None,
) -> SearchResult:
    """Return matching edges sorted by confidence, with optional endpoint tag stats.

    Free text matches ``relation``, ``resolved_by``, ``source``, and
    ``target`` (case-insensitive substring).  Only edge-field predicates
    (``relation``, ``confidence``, ``resolved_by``, ``source``, ``target``)
    are applied; node-only fields such as ``{type:…}`` are ignored.

    Sort order: ``confidence`` descending (missing treated as ``1.0``), then
    ``relation``, ``source``, ``target``.

    When *nodes* is provided, tag stats are aggregated from each matched edge's
    source and target node tags (each endpoint counted once per edge).
    Otherwise tag counts are empty.

    An empty or whitespace-only *query* returns an empty :class:`SearchResult`.
    Node-only queries (no free text and only non-edge predicates) also return
    empty.

    Args:
        edges: Exported edge dicts (graph JSONL shape).
        query: Viewer search syntax string.
        limit: Max matches in ``matches``. ``None`` or a negative value (e.g.
            ``-1``) returns all hits. ``total`` and tag counts always cover the
            full hit set.
        nodes: Optional node dicts used to resolve endpoint tags for stats.
    """
    parsed = parse_search_query(query)
    if parsed is None or not _has_edge_criteria(parsed):
        return _empty_result()

    hits = [dict(edge) for edge in edges if _edge_matches(edge, parsed)]
    if nodes is None:
        tag_counts: dict[str, int] = {}
        tag_combo_counts: dict[str, int] = {}
    else:
        tag_counts, tag_combo_counts = _tag_stats_from_edge_endpoints(hits, nodes)
    hits.sort(key=_edge_sort_key)
    total = len(hits)
    return SearchResult(_apply_match_limit(hits, limit), total, tag_counts, tag_combo_counts)


@lru_cache(maxsize=_LRU_CACHE_SIZE)
def _compile_search_regex(pattern: str, ignore_case: bool) -> re.Pattern[str]:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc


def _mapping_matches_regex(item: Mapping[str, Any], compiled: re.Pattern[str], fields: Sequence[str]) -> bool:
    return any(compiled.search(str(item.get(field) or "")) is not None for field in fields)


def search_regex(
    pattern: str,
    *,
    target: Literal["nodes", "edges"] = "nodes",
    nodes: Sequence[Mapping[str, Any]] | None = None,
    edges: Sequence[Mapping[str, Any]] | None = None,
    limit: int | None = None,
    ignore_case: bool = True,
) -> SearchResult:
    """Search nodes or edges with a Python regular expression.

    Node fields searched: ``name``, ``signature``, ``id``, ``path``, ``type``,
    ``node_type``.  Edge fields searched: ``relation``, ``resolved_by``,
    ``source``, ``target``.

    Uses the same sort rules and tag stats as :func:`search_nodes` /
    :func:`search_edges`.  When ``target="edges"`` and *nodes* is provided,
    endpoint tag stats are included.

    Args:
        pattern: Regular expression (not viewer search syntax).
        target: ``"nodes"`` or ``"edges"``.
        nodes: Node dicts (required when ``target="nodes"``; optional for edge
            tag stats when ``target="edges"``).
        edges: Edge dicts (required when ``target="edges"``).
        limit: Max matches in ``matches``. ``None`` or negative = all.
        ignore_case: Compile with ``re.IGNORECASE`` when true.

    Raises:
        ValueError: If *pattern* is invalid, *target* is unknown, or the
            required item sequence is missing.
    """
    if not pattern or not pattern.strip():
        return _empty_result()

    compiled = _compile_search_regex(pattern, ignore_case)

    if target == "nodes":
        if nodes is None:
            raise ValueError("nodes is required when target='nodes'")
        hits = [dict(node) for node in nodes if _mapping_matches_regex(node, compiled, _NODE_REGEX_FIELDS)]
        tag_counts, tag_combo_counts = _tag_stats_from_nodes(hits)
        hits.sort(key=lambda node: _node_sort_key(node, _EMPTY_PARSED))
        total = len(hits)
        return SearchResult(_apply_match_limit(hits, limit), total, tag_counts, tag_combo_counts)

    if target == "edges":
        if edges is None:
            raise ValueError("edges is required when target='edges'")
        hits = [dict(edge) for edge in edges if _mapping_matches_regex(edge, compiled, _EDGE_REGEX_FIELDS)]
        if nodes is None:
            tag_counts = {}
            tag_combo_counts = {}
        else:
            tag_counts, tag_combo_counts = _tag_stats_from_edge_endpoints(hits, nodes)
        hits.sort(key=_edge_sort_key)
        total = len(hits)
        return SearchResult(_apply_match_limit(hits, limit), total, tag_counts, tag_combo_counts)

    raise ValueError("target must be 'nodes' or 'edges'")
