# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-memory repository knowledge graph (files, AST nodes, text chunks)."""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from openjiuwen_codesearch.retropus.graph.file_graph_builder import FileGraphBuilder
from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    FileNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
    TextNode,
)
from openjiuwen_codesearch.retropus.graph.imports import build_imports_edges
from openjiuwen_codesearch.retropus.graph.inherits import (
    build_inherits_edges,
    inheritance_neighbors,
)
from openjiuwen_codesearch.utils.log_utils import get_logger

if TYPE_CHECKING:
    from openjiuwen_codesearch.retropus.graph.graph_types import (
        ASTNodeDict,
        FileNodeDict,
        HasASTEdge,
        HasFileEdge,
        HasTextEdge,
        InheritsEdge,
        NextChunkEdge,
        ParentOfEdge,
        TextNodeDict,
    )

_Log = get_logger(__name__)


def _as_list(seq: Optional[Sequence[KnowledgeGraphNode]]) -> List[KnowledgeGraphNode]:
    return list(seq) if seq is not None else []


def _as_edge_list(
    seq: Optional[Sequence[KnowledgeGraphEdge]],
) -> List[KnowledgeGraphEdge]:
    return list(seq) if seq is not None else []


class KnowledgeGraph:
    """File / AST / text graph for one repository, with lazy adjacency indexes."""

    def __init__(
        self,
        max_ast_depth: int,
        chunk_size: int,
        chunk_overlap: int,
        root_node_id: int,
        root_node: Optional[KnowledgeGraphNode] = None,
        knowledge_graph_nodes: Optional[Sequence[KnowledgeGraphNode]] = None,
        knowledge_graph_edges: Optional[Sequence[KnowledgeGraphEdge]] = None,
    ):
        self.max_ast_depth = max_ast_depth
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.root_node_id = root_node_id

        self._repo_root = root_node
        self._vertices = _as_list(knowledge_graph_nodes)
        self._arcs = _as_edge_list(knowledge_graph_edges)
        self._id_seq = root_node_id + len(self._vertices)
        self._file_builder = FileGraphBuilder(max_ast_depth, chunk_size, chunk_overlap)
        self._log = _Log
        self._clear_lazy_maps()

    # ------------------------------------------------------------------ caches
    def _clear_lazy_maps(self) -> None:
        self._files: Optional[List[KnowledgeGraphNode]] = None
        self._asts: Optional[List[KnowledgeGraphNode]] = None
        self._texts: Optional[List[KnowledgeGraphNode]] = None
        self._arcs_by_kind: Optional[
            Mapping[KnowledgeGraphEdgeType, List[KnowledgeGraphEdge]]
        ] = None
        self._ast_kids: Optional[Mapping[int, List[KnowledgeGraphNode]]] = None
        self._chunk_prev: Optional[Mapping[int, KnowledgeGraphNode]] = None
        self._text_file: Optional[Mapping[int, KnowledgeGraphNode]] = None
        self._ast_file: Optional[Mapping[int, KnowledgeGraphNode]] = None
        self._pairs: Optional[List[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]]] = None
        self._inh_out: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        self._inh_in: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        self._cls_file: Optional[Dict[int, KnowledgeGraphNode]] = None
        self._imp_out: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        self._imp_in: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        self._imp_labels: Dict[Tuple[int, int], str] = {}

    def _invalidate_indexes(self) -> None:
        """Drop lazy indexes after the vertex/edge sets change."""
        kept = self._imp_labels
        self._clear_lazy_maps()
        self._imp_labels = kept

    def _partition_nodes(self) -> None:
        if self._files is not None:
            return
        files: List[KnowledgeGraphNode] = []
        asts: List[KnowledgeGraphNode] = []
        texts: List[KnowledgeGraphNode] = []
        for vertex in self._vertices:
            payload = vertex.node
            if isinstance(payload, FileNode):
                files.append(vertex)
            elif isinstance(payload, ASTNode):
                asts.append(vertex)
            elif isinstance(payload, TextNode):
                texts.append(vertex)
        self._files = files
        self._asts = asts
        self._texts = texts

    def _ensure_node_indexes(self) -> None:
        self._partition_nodes()

    def _rebuild_arc_maps(self) -> None:
        if self._arcs_by_kind is not None:
            return

        buckets: dict[KnowledgeGraphEdgeType, List[KnowledgeGraphEdge]] = {
            kind: [] for kind in KnowledgeGraphEdgeType
        }
        for arc in self._arcs:
            buckets[arc.type].append(arc)
        self._arcs_by_kind = buckets

        kids: dict[int, List[KnowledgeGraphNode]] = {}
        for arc in buckets[KnowledgeGraphEdgeType.parent_of]:
            kids.setdefault(arc.source.node_id, []).append(arc.target)
        self._ast_kids = kids

        self._chunk_prev = {
            arc.target.node_id: arc.source
            for arc in buckets[KnowledgeGraphEdgeType.next_chunk]
        }
        self._text_file = {
            arc.target.node_id: arc.source
            for arc in buckets[KnowledgeGraphEdgeType.has_text]
        }

        owners: dict[int, KnowledgeGraphNode] = {}
        pairs: List[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]] = []
        for arc in buckets[KnowledgeGraphEdgeType.has_ast]:
            file_kg = arc.source
            pending = list(kids.get(arc.target.node_id, ()))
            while pending:
                cur = pending.pop()
                owners[cur.node_id] = file_kg
                pairs.append((file_kg, cur))
                more = kids.get(cur.node_id)
                if more:
                    pending.extend(more)
        self._ast_file = owners
        self._pairs = pairs

        out_inh: Dict[int, List[KnowledgeGraphNode]] = {}
        in_inh: Dict[int, List[KnowledgeGraphNode]] = {}
        for arc in buckets[KnowledgeGraphEdgeType.inherits]:
            out_inh.setdefault(arc.source.node_id, []).append(arc.target)
            in_inh.setdefault(arc.target.node_id, []).append(arc.source)
        self._inh_out = out_inh
        self._inh_in = in_inh

        cls_files: Dict[int, KnowledgeGraphNode] = {}
        for arc in buckets[KnowledgeGraphEdgeType.inherits]:
            src_f = owners.get(arc.source.node_id)
            tgt_f = owners.get(arc.target.node_id)
            if src_f is not None:
                cls_files[arc.source.node_id] = src_f
            if tgt_f is not None:
                cls_files[arc.target.node_id] = tgt_f
        self._cls_file = cls_files

        out_imp: Dict[int, List[KnowledgeGraphNode]] = {}
        in_imp: Dict[int, List[KnowledgeGraphNode]] = {}
        for arc in buckets[KnowledgeGraphEdgeType.imports]:
            out_imp.setdefault(arc.source.node_id, []).append(arc.target)
            in_imp.setdefault(arc.target.node_id, []).append(arc.source)
        self._imp_out = out_imp
        self._imp_in = in_imp

    def _ensure_edge_indexes(self) -> None:
        self._rebuild_arc_maps()

    def _build_edge_indexes(self) -> None:
        self._rebuild_arc_maps()

    # ------------------------------------------------------------------ build
    async def build_graph(self, root_dir: Path) -> None:
        """Build the graph for ``root_dir`` on a worker thread."""
        await asyncio.to_thread(self._populate_from_repo, root_dir)

    def _build_graph(self, root_dir: Path) -> None:
        """Compat alias used by older call sites / tests."""
        self._populate_from_repo(root_dir)

    def _populate_from_repo(self, root_dir: Path) -> None:
        import igittigitt  # guarded: retropus extra
        from tqdm import tqdm  # guarded: build-time progress only

        self._invalidate_indexes()
        self._imp_labels = {}
        repo = root_dir.absolute()
        t0 = time.perf_counter()
        self._log.info("KG: scanning repository tree under %s", repo)

        ignore = igittigitt.IgnoreParser()
        ignore.parse_rule_files(repo)
        ignore.add_rule(".git", repo)

        self._seed_repo_root(repo)
        pending_files = self._collect_file_entries(repo, ignore)

        self._log.info("KG: parsing %d files with tree-sitter", len(pending_files))
        for path, file_kg in tqdm(pending_files, desc="KG parse", unit="file", leave=False):
            self._log.debug("Processing file %s", path)
            try:
                self._id_seq, kg_nodes, kg_edges = self._file_builder.build_file_graph(
                    file_kg, path, self._id_seq
                )
            except (ValueError, OSError) as exc:
                self._log.warning("Skipping %s: %s", path, exc)
                continue
            self._vertices.extend(kg_nodes)
            self._arcs.extend(kg_edges)

        self._attach_semantic_edges(repo, t0)

    def _seed_repo_root(self, repo: Path) -> None:
        payload = FileNode(basename=repo.name, relative_path=".")
        root_kg = KnowledgeGraphNode(self._id_seq, payload)
        self._id_seq += 1
        self._vertices.append(root_kg)
        self._repo_root = root_kg

    def _collect_file_entries(
        self, repo: Path, ignore: object
    ) -> List[Tuple[Path, KnowledgeGraphNode]]:
        if self._repo_root is None:
            raise RuntimeError("repository root vertex missing")
        match = getattr(ignore, "match")
        walk: deque[tuple[Path, KnowledgeGraphNode]] = deque([(repo, self._repo_root)])
        to_parse: List[Tuple[Path, KnowledgeGraphNode]] = []

        while walk:
            path, parent_kg = walk.pop()
            if not path.is_dir():
                to_parse.append((path, parent_kg))
                continue
            self._log.debug("Processing directory %s", path)
            for child in sorted(path.iterdir()):
                if not child.exists():
                    continue
                if child.is_file() and not self._file_builder.supports_file(child):
                    continue
                if match(child):
                    continue
                child_kg = KnowledgeGraphNode(
                    self._id_seq,
                    FileNode(
                        basename=child.name,
                        relative_path=child.relative_to(repo).as_posix(),
                    ),
                )
                self._id_seq += 1
                self._vertices.append(child_kg)
                self._arcs.append(
                    KnowledgeGraphEdge(
                        parent_kg, child_kg, KnowledgeGraphEdgeType.has_file
                    )
                )
                walk.append((child, child_kg))
        return to_parse

    def _attach_semantic_edges(self, repo: Path, started: float) -> None:
        index_t0 = time.perf_counter()
        self._partition_nodes()
        self._rebuild_arc_maps()

        inherit_t0 = time.perf_counter()
        inherit_edges = build_inherits_edges(self._pairs or ())
        inherit_s = time.perf_counter() - inherit_t0

        imports_t0 = time.perf_counter()
        import_edges, import_labels = build_imports_edges(
            self._files or (), repo_root=repo
        )
        imports_s = time.perf_counter() - imports_t0

        if inherit_edges or import_edges:
            if inherit_edges:
                self._arcs.extend(inherit_edges)
            if import_edges:
                self._arcs.extend(import_edges)
                self._imp_labels = import_labels
            self._arcs_by_kind = None
            self._inh_out = None
            self._inh_in = None
            self._cls_file = None
            self._imp_out = None
            self._imp_in = None
            self._rebuild_arc_maps()

        self._log.info(
            "KG: INHERITS edges=%d (%.1fs); IMPORTS edges=%d (%.1fs); "
            "indexes ready (searchable_ast=%d, %.1fs)",
            len(inherit_edges),
            inherit_s,
            len(import_edges),
            imports_s,
            len(self._pairs or ()),
            time.perf_counter() - index_t0,
        )
        self._log.info(
            "KG: ready (files=%d ast=%d text=%d inherits=%d imports=%d, %.1fs)",
            len(self.get_file_nodes()),
            len(self.get_ast_nodes()),
            len(self.get_text_nodes()),
            len(inherit_edges),
            len(import_edges),
            time.perf_counter() - started,
        )

    # --------------------------------------------------------------- accessors
    @property
    def root_node(self) -> Optional[KnowledgeGraphNode]:
        return self._repo_root

    def get_all_nodes(self) -> Sequence[KnowledgeGraphNode]:
        return list(self._vertices)

    def get_all_edges(self) -> Sequence[KnowledgeGraphEdge]:
        return list(self._arcs)

    def get_imports_labels_map(self) -> Dict[Tuple[int, int], str]:
        return dict(self._imp_labels)

    def set_imports_labels_map(self, labels: Mapping[Tuple[int, int], str]) -> None:
        self._imp_labels = dict(labels)

    def get_file_tree(self, max_depth: int = 5, max_lines: int = 5000) -> str:
        """Render a box-drawing tree of ``HAS_FILE`` children under the root."""
        if self._repo_root is None:
            return ""
        adjacency = self._file_adjacency()
        blank, pipe, mid, end = "    ", "|   ", "├── ", "└── "
        lines: List[str] = []
        stack: deque[tuple[KnowledgeGraphNode, int, str, bool | None]] = deque(
            [(self._repo_root, 0, "", None)]
        )

        while stack and len(lines) < max_lines:
            node, depth, prefix, is_last = stack.pop()
            if depth > max_depth:
                continue
            if depth == 0:
                lines.append(node.node.basename)
            else:
                tip = end if is_last else mid
                lines.append(f"{prefix}{tip}{node.node.basename}")

            kids = sorted(adjacency[node], key=lambda n: n.node.basename)
            for idx in range(len(kids) - 1, -1, -1):
                last_child = idx == len(kids) - 1
                child_prefix = ""
                if depth > 0:
                    child_prefix = prefix + (blank if is_last else pipe)
                stack.append((kids[idx], depth + 1, child_prefix, last_child))

        return "\n".join(lines)

    def get_all_ast_node_types(self) -> Sequence[str]:
        return list({n.node.type for n in self.get_ast_nodes()})

    def _file_adjacency(
        self,
    ) -> Mapping[KnowledgeGraphNode, Sequence[KnowledgeGraphNode]]:
        adj: dict[KnowledgeGraphNode, list[KnowledgeGraphNode]] = defaultdict(list)
        for edge in self.get_has_file_edges():
            adj[edge.source].append(edge.target)
        return adj

    def _get_file_node_adjacency_dict(
        self,
    ) -> Mapping[KnowledgeGraphNode, Sequence[KnowledgeGraphNode]]:
        return self._file_adjacency()

    def _require(self, value: Optional[object], message: str) -> object:
        if value is None:
            raise RuntimeError(message)
        return value

    def get_file_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._partition_nodes()
        return self._require(self._files, "file node index not built")  # type: ignore[return-value]

    def get_ast_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._partition_nodes()
        return self._require(self._asts, "ast node index not built")  # type: ignore[return-value]

    def get_text_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._partition_nodes()
        return self._require(self._texts, "text node index not built")  # type: ignore[return-value]

    def _edges_of_type(self, edge_type: KnowledgeGraphEdgeType) -> Sequence[KnowledgeGraphEdge]:
        self._rebuild_arc_maps()
        buckets = self._require(self._arcs_by_kind, "edge-type index not built")
        return buckets[edge_type]  # type: ignore[index]

    def get_has_ast_edges(self) -> Sequence[KnowledgeGraphEdge]:
        return self._edges_of_type(KnowledgeGraphEdgeType.has_ast)

    def get_has_file_edges(self) -> Sequence[KnowledgeGraphEdge]:
        return self._edges_of_type(KnowledgeGraphEdgeType.has_file)

    def get_has_text_edges(self) -> Sequence[KnowledgeGraphEdge]:
        return self._edges_of_type(KnowledgeGraphEdgeType.has_text)

    def get_next_chunk_edges(self) -> Sequence[KnowledgeGraphEdge]:
        return self._edges_of_type(KnowledgeGraphEdgeType.next_chunk)

    def get_parent_of_edges(self) -> Sequence[KnowledgeGraphEdge]:
        return self._edges_of_type(KnowledgeGraphEdgeType.parent_of)

    def get_inherits_edges(self) -> Sequence[KnowledgeGraphEdge]:
        return self._edges_of_type(KnowledgeGraphEdgeType.inherits)

    def get_imports_edges(self) -> Sequence[KnowledgeGraphEdge]:
        return self._edges_of_type(KnowledgeGraphEdgeType.imports)

    def get_imports_label(self, source_id: int, target_id: int) -> str:
        return self._imp_labels.get((source_id, target_id), "")

    def get_import_neighbors(
        self, file_node: KnowledgeGraphNode
    ) -> Sequence[KnowledgeGraphNode]:
        self._rebuild_arc_maps()
        if self._imp_out is None or self._imp_in is None:
            raise RuntimeError("imports adjacency index not built")
        merged: List[KnowledgeGraphNode] = []
        seen = {file_node.node_id}
        for n in self._imp_out.get(file_node.node_id, ()) + self._imp_in.get(
            file_node.node_id, ()
        ):
            if n.node_id not in seen:
                seen.add(n.node_id)
                merged.append(n)
        return merged

    def get_inheritance_neighbors(
        self, class_ast: KnowledgeGraphNode
    ) -> Sequence[KnowledgeGraphNode]:
        self._rebuild_arc_maps()
        if self._inh_out is None or self._inh_in is None:
            raise RuntimeError("inherits adjacency index not built")
        return inheritance_neighbors(class_ast.node_id, self._inh_out, self._inh_in)

    def get_file_for_ast(self, ast_node: KnowledgeGraphNode) -> Optional[KnowledgeGraphNode]:
        self._rebuild_arc_maps()
        owners = self._require(self._ast_file, "ast-to-file index not built")
        return owners.get(ast_node.node_id)  # type: ignore[union-attr]

    def get_ast_to_file_map(self) -> Mapping[int, KnowledgeGraphNode]:
        self._rebuild_arc_maps()
        return self._require(self._ast_file, "ast-to-file index not built")  # type: ignore[return-value]

    def get_ast_file_pairs(self) -> Sequence[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]]:
        self._rebuild_arc_maps()
        return self._require(self._pairs, "ast-file pairs index not built")  # type: ignore[return-value]

    def find_file_node_for_text_node(self, text_node: KnowledgeGraphNode) -> KnowledgeGraphNode:
        """Walk ``NEXT_CHUNK`` backward to the first chunk, then ``HAS_TEXT``."""
        self._rebuild_arc_maps()
        if self._chunk_prev is None or self._text_file is None:
            raise RuntimeError("text-to-file index not built")
        nid = text_node.node_id
        while nid in self._chunk_prev:
            nid = self._chunk_prev[nid].node_id
        owner = self._text_file.get(nid)
        if owner is None:
            raise KeyError(f"no file node for text node id {nid}")
        return owner

    def get_file_node_dicts(self) -> Sequence["FileNodeDict"]:
        return [n.to_dict() for n in self.get_file_nodes()]  # type: ignore[misc]

    def get_ast_node_dicts(self) -> Sequence["ASTNodeDict"]:
        return [n.to_dict() for n in self.get_ast_nodes()]  # type: ignore[misc]

    def get_text_node_dicts(self) -> Sequence["TextNodeDict"]:
        return [n.to_dict() for n in self.get_text_nodes()]  # type: ignore[misc]

    def get_has_ast_edge_dicts(self) -> Sequence["HasASTEdge"]:
        return [e.to_edge_dict() for e in self.get_has_ast_edges()]  # type: ignore[misc]

    def get_has_file_edge_dicts(self) -> Sequence["HasFileEdge"]:
        return [e.to_edge_dict() for e in self.get_has_file_edges()]  # type: ignore[misc]

    def get_has_text_edge_dicts(self) -> Sequence["HasTextEdge"]:
        return [e.to_edge_dict() for e in self.get_has_text_edges()]  # type: ignore[misc]

    def get_next_chunk_edge_dicts(self) -> Sequence["NextChunkEdge"]:
        return [e.to_edge_dict() for e in self.get_next_chunk_edges()]  # type: ignore[misc]

    def get_parent_of_edge_dicts(self) -> Sequence["ParentOfEdge"]:
        return [e.to_edge_dict() for e in self.get_parent_of_edges()]  # type: ignore[misc]

    def get_inherits_edge_dicts(self) -> Sequence["InheritsEdge"]:
        return [e.to_edge_dict() for e in self.get_inherits_edges()]  # type: ignore[misc]

    def get_parent_to_children_map(self) -> Mapping[int, Sequence[KnowledgeGraphNode]]:
        self._rebuild_arc_maps()
        return self._require(self._ast_kids, "parent-to-children index not built")  # type: ignore[return-value]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, KnowledgeGraph):
            return False
        left_nodes = sorted(self._vertices, key=lambda n: n.node_id)
        right_nodes = sorted(other._vertices, key=lambda n: n.node_id)
        if any(a != b for a, b in itertools.zip_longest(left_nodes, right_nodes)):
            return False

        def _edge_key(e: KnowledgeGraphEdge):
            return (e.source.node_id, e.target.node_id, e.type)

        left_edges = sorted(self._arcs, key=_edge_key)
        right_edges = sorted(other._arcs, key=_edge_key)
        return all(a == b for a, b in itertools.zip_longest(left_edges, right_edges))
