"""Convert parsed :class:`FileNode` trees into top-level text chunks and chunk edges."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .constants import INTERNAL_NODES, EdgeType, NodeType
from .custom_types import SignatureProvider, SourceSpan
from .ids import node_id
from .models.core import BaseNode, PropertyNode
from .models.structural import FileNode
from .resolver import resolve_graph


@dataclass(frozen=True, slots=True)
class ChunkEdge:
    """An edge between chunks, preserving the original resolved node endpoints."""

    source_chunk_id: str
    target_chunk_id: str
    relation: EdgeType
    original_lhs: str
    original_rhs: str
    confidence: float = 1.0
    resolved_by: str = ""


@dataclass(frozen=True, slots=True)
class Chunk:
    """A top-level text chunk with provenance and remapped relations."""

    id: str
    text: str
    node_type: NodeType
    name: str
    span: SourceSpan
    path: str
    language: str
    context: tuple[str, ...] = ()
    signature: str | None = None
    relations: tuple[ChunkEdge, ...] = ()
    collapsed_names: tuple[str, ...] = ()


def chunks_from_file_nodes(
    file_nodes: list[FileNode],
    *,
    run_resolver: bool = True,
    include_source: bool = True,
    include_docstring: bool = True,
    include_signature: bool = True,
    min_chars: int = 1,
) -> tuple[list[Chunk], list[ChunkEdge]]:
    """Build top-level chunks and remapped edges from parsed file trees.

    Mirrors :func:`export_graph_from_file_nodes`: only file-distance-1 nodes
    (plus the file itself) become chunks; nested nodes collapse into their
    nearest top-level ancestor.  When *run_resolver* is true, runs
    :func:`resolve_graph` and remaps every resolvable edge onto chunk endpoints.

    :return: ``(chunks, chunk_edges)``.
    """
    chunks: list[Chunk] = []
    node_to_chunk: dict[str, str] = {}
    # (path, class_name) -> chunk_id for mapping synthesised method-guessed nodes
    class_chunk_by_path: dict[tuple[str, str], str] = {}
    structural_edges: list[ChunkEdge] = []

    for file_node in file_nodes:
        path = file_node.path
        language = file_node.language
        file_cid = node_id(path, file_node)
        node_to_chunk[file_cid] = file_cid

        file_text = _pick_text(file_node, include_source, include_docstring) or ""
        chunks.append(
            Chunk(
                id=file_cid,
                text=file_text if len(file_text) >= min_chars else "",
                node_type=NodeType.FILE,
                name=file_node.name,
                span=file_node.span,
                path=path,
                language=language,
            )
        )

        for child in file_node.children:
            if child.node_type in INTERNAL_NODES:
                node_to_chunk[node_id(path, child)] = file_cid
                continue

            text = _pick_text(child, include_source, include_docstring)
            if not text or len(text) < min_chars:
                # Still map descendants so edges can collapse, but skip emitting.
                cid = node_id(path, child)
                _map_subtree(child, path, cid, node_to_chunk, class_chunk_by_path)
                continue

            sig = child.signature if (include_signature and isinstance(child, SignatureProvider)) else None
            # Source usually already begins with the signature line (often with a trailing ':').
            signature_in_text = bool(sig) and (text == sig or text.startswith(sig))
            body = f"{sig}\n{text}" if (sig and not signature_in_text) else text

            collapsed: list[str] = []
            span = child.span
            spans = [child.span]
            _collect_collapse(child, collapsed, spans)
            for s in spans[1:]:
                span = _union_span(span, s)

            cid = node_id(path, child)
            _map_subtree(child, path, cid, node_to_chunk, class_chunk_by_path)

            chunks.append(
                Chunk(
                    id=cid,
                    text=body,
                    node_type=child.node_type,
                    name=child.name,
                    span=span,
                    path=path,
                    language=language,
                    context=(),
                    signature=sig,
                    collapsed_names=tuple(collapsed),
                )
            )
            structural_edges.append(
                ChunkEdge(
                    source_chunk_id=file_cid,
                    target_chunk_id=cid,
                    relation=EdgeType.CONTAINS,
                    original_lhs=file_cid,
                    original_rhs=cid,
                )
            )

    chunk_edges: list[ChunkEdge] = list(structural_edges)

    if run_resolver and file_nodes:
        resolved, synth_nodes, synth_edges = resolve_graph(file_nodes, node_id_fn=node_id)
        _map_synth_nodes(synth_nodes, node_to_chunk, class_chunk_by_path)

        for edge in resolved:
            remapped = _remap_edge(
                edge.source_id,
                edge.target_id,
                edge.relation,
                node_to_chunk,
                confidence=edge.confidence,
                resolved_by=edge.resolved_by,
            )
            if remapped is not None:
                chunk_edges.append(remapped)

        for se in synth_edges:
            rel_raw = se.get("relation", "")
            try:
                relation = EdgeType(rel_raw) if not isinstance(rel_raw, EdgeType) else rel_raw
            except ValueError:
                continue
            remapped = _remap_edge(
                se["source"],
                se["target"],
                relation,
                node_to_chunk,
            )
            if remapped is not None:
                chunk_edges.append(remapped)

    # Attach incident relations onto each chunk
    by_chunk: dict[str, list[ChunkEdge]] = defaultdict(list)
    for ce in chunk_edges:
        by_chunk[ce.source_chunk_id].append(ce)
        if ce.target_chunk_id != ce.source_chunk_id:
            by_chunk[ce.target_chunk_id].append(ce)

    chunks = [replace(c, relations=tuple(by_chunk.get(c.id, ()))) for c in chunks]
    return chunks, chunk_edges


def chunks_from_file(
    file_node: FileNode,
    *,
    run_resolver: bool = True,
    include_source: bool = True,
    include_docstring: bool = True,
    include_signature: bool = True,
    min_chars: int = 1,
) -> tuple[list[Chunk], list[ChunkEdge]]:
    """Convenience wrapper around :func:`chunks_from_file_nodes` for one file."""
    return chunks_from_file_nodes(
        [file_node],
        run_resolver=run_resolver,
        include_source=include_source,
        include_docstring=include_docstring,
        include_signature=include_signature,
        min_chars=min_chars,
    )


def _union_span(a: SourceSpan, b: SourceSpan) -> SourceSpan:
    """Return the smallest span covering both *a* and *b*."""
    if a.line_start < b.line_start or (a.line_start == b.line_start and a.col_start <= b.col_start):
        start_line, start_col = a.line_start, a.col_start
    else:
        start_line, start_col = b.line_start, b.col_start
    if a.line_end > b.line_end or (a.line_end == b.line_end and a.col_end >= b.col_end):
        end_line, end_col = a.line_end, a.col_end
    else:
        end_line, end_col = b.line_end, b.col_end
    return SourceSpan(start_line, end_line, start_col, end_col)


def _collect_collapse(node: BaseNode, names: list[str], spans: list[SourceSpan]) -> None:
    """Accumulate descendant names and spans (excluding *node* itself)."""
    for child in node.children:
        if child.node_type in INTERNAL_NODES:
            continue
        names.append(child.name)
        spans.append(child.span)
        _collect_collapse(child, names, spans)


def _map_subtree(
    node: BaseNode,
    path: str,
    chunk_id: str,
    node_to_chunk: dict[str, str],
    class_chunk_by_path: dict[tuple[str, str], str],
) -> None:
    """Map *node* and all descendants to *chunk_id*."""
    nid = node_id(path, node)
    node_to_chunk[nid] = chunk_id
    if node.node_type is NodeType.CLASS:
        class_chunk_by_path[(path, node.name)] = chunk_id
    for child in node.children:
        if child.node_type in INTERNAL_NODES:
            # Still map internal nodes so call sites can resolve if needed
            node_to_chunk[node_id(path, child)] = chunk_id
            continue
        _map_subtree(child, path, chunk_id, node_to_chunk, class_chunk_by_path)


def _map_synth_nodes(
    synth_nodes: Sequence[dict],
    node_to_chunk: dict[str, str],
    class_chunk_by_path: dict[tuple[str, str], str],
) -> None:
    """Map synthesised resolver nodes onto owning class chunks when possible."""
    for sn in synth_nodes:
        sid = sn.get("id")
        if not sid or sid in node_to_chunk:
            continue
        owner = sn.get("owner")
        path = sn.get("path", "")
        if owner and path:
            cid = class_chunk_by_path.get((path, owner))
            if cid is not None:
                node_to_chunk[sid] = cid


def _remap_edge(
    lhs: str,
    rhs: str,
    relation: EdgeType,
    node_to_chunk: dict[str, str],
    *,
    confidence: float = 1.0,
    resolved_by: str = "",
) -> ChunkEdge | None:
    src = node_to_chunk.get(lhs)
    tgt = node_to_chunk.get(rhs)
    if src is None or tgt is None:
        return None
    return ChunkEdge(
        source_chunk_id=src,
        target_chunk_id=tgt,
        relation=relation,
        original_lhs=lhs,
        original_rhs=rhs,
        confidence=confidence,
        resolved_by=resolved_by,
    )


def _pick_text(node: BaseNode, include_source: bool, include_docstring: bool) -> str | None:
    if include_source and node.source:
        return node.source
    if include_docstring and node.docstring:
        return node.docstring
    if isinstance(node, PropertyNode):
        return node.signature
    return None
