# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-memory repository knowledge graph (files, AST nodes, text chunks)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

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
from openjiuwen_codesearch.retropus.graph import kg_views
from openjiuwen_codesearch.utils.log_utils import get_logger

_Log = get_logger(__name__)


@dataclass
class _StoredGraph:
    """Optional restore payload when reloading a dumped KG."""

    root: Optional[KnowledgeGraphNode] = None
    nodes: List[KnowledgeGraphNode] = field(default_factory=list)
    edges: List[KnowledgeGraphEdge] = field(default_factory=list)


class KnowledgeGraph:
    """File / AST / text graph for one repository, with lazy adjacency indexes."""

    def __init__(
        self,
        max_ast_depth: int,
        chunk_size: int,
        chunk_overlap: int,
        root_node_id: int = 0,
        *,
        stored: Optional[_StoredGraph] = None,
        root_node: Optional[KnowledgeGraphNode] = None,
        **legacy: object,
    ):
        """Create an empty graph, or restore from ``stored`` / dump keywords.

        Dump/load may pass ``knowledge_graph_nodes`` / ``knowledge_graph_edges``
        as keyword-only legacy fields (accepted via ``**legacy``).
        """
        self.max_ast_depth = max_ast_depth
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.root_node_id = root_node_id

        legacy_nodes = legacy.pop("knowledge_graph_nodes", None)
        legacy_edges = legacy.pop("knowledge_graph_edges", None)
        if legacy:
            unexpected = ", ".join(sorted(legacy))
            raise TypeError(f"unexpected KnowledgeGraph kwargs: {unexpected}")

        if stored is not None:
            bundle = stored
        else:
            bundle = _StoredGraph(
                root=root_node,
                nodes=list(legacy_nodes or ()),  # type: ignore[arg-type]
                edges=list(legacy_edges or ()),  # type: ignore[arg-type]
            )
        self._repo_root = bundle.root
        self._vertices = list(bundle.nodes)
        self._arcs = list(bundle.edges)
        self._id_seq = root_node_id + len(self._vertices)
        self._file_builder = None  # lazy: keep top-of-file imports distinct
        self._log = _Log
        self._imp_labels: Dict[Tuple[int, int], str] = {}
        self._reset_lazy_state()

    def _file_graph_builder(self):
        if self._file_builder is None:
            from openjiuwen_codesearch.retropus.graph.file_graph_builder import (
                FileGraphBuilder,
            )

            self._file_builder = FileGraphBuilder(
                self.max_ast_depth, self.chunk_size, self.chunk_overlap
            )
        return self._file_builder

    def _reset_lazy_state(self) -> None:
        self._files = None
        self._asts = None
        self._texts = None
        self._arcs_by_kind = None
        self._ast_kids = None
        self._chunk_prev = None
        self._text_file = None
        self._ast_file = None
        self._pairs = None
        self._inh_out = None
        self._inh_in = None
        self._cls_file = None
        self._imp_out = None
        self._imp_in = None

    def _invalidate_indexes(self) -> None:
        labels = self._imp_labels
        self._reset_lazy_state()
        self._imp_labels = labels

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
        self._files, self._asts, self._texts = files, asts, texts

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
        self._inh_out, self._inh_in = out_inh, in_inh

        cls_files: Dict[int, KnowledgeGraphNode] = {}
        for arc in buckets[KnowledgeGraphEdgeType.inherits]:
            for endpoint in (arc.source, arc.target):
                owner = owners.get(endpoint.node_id)
                if owner is not None:
                    cls_files[endpoint.node_id] = owner
        self._cls_file = cls_files

        out_imp: Dict[int, List[KnowledgeGraphNode]] = {}
        in_imp: Dict[int, List[KnowledgeGraphNode]] = {}
        for arc in buckets[KnowledgeGraphEdgeType.imports]:
            out_imp.setdefault(arc.source.node_id, []).append(arc.target)
            in_imp.setdefault(arc.target.node_id, []).append(arc.source)
        self._imp_out, self._imp_in = out_imp, in_imp

    # ------------------------------------------------------------------ build
    async def build_graph(self, root_dir: Path) -> None:
        """Build the graph for ``root_dir`` on a worker thread."""
        await asyncio.to_thread(self._populate_from_repo, root_dir)

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
        builder = self._file_graph_builder()

        self._log.info("KG: parsing %d files with tree-sitter", len(pending_files))
        for path, file_kg in tqdm(pending_files, desc="KG parse", unit="file", leave=False):
            self._log.debug("Processing file %s", path)
            try:
                self._id_seq, kg_nodes, kg_edges = builder.build_file_graph(
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
        builder = self._file_graph_builder()
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
                if child.is_file() and not builder.supports_file(child):
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
            self._inh_out = self._inh_in = None
            self._cls_file = None
            self._imp_out = self._imp_in = None
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
            len(self.file_nodes()),
            len(self.ast_nodes()),
            len(self.text_nodes()),
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

    def get_imports_label(self, source_id: int, target_id: int) -> str:
        return self._imp_labels.get((source_id, target_id), "")

    def file_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._partition_nodes()
        return kg_views.require_index(self._files, "file node index not built")  # type: ignore[return-value]

    def ast_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._partition_nodes()
        return kg_views.require_index(self._asts, "ast node index not built")  # type: ignore[return-value]

    def text_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._partition_nodes()
        return kg_views.require_index(self._texts, "text node index not built")  # type: ignore[return-value]

    # Public aliases kept for existing call sites / persist tests.
    get_file_nodes = file_nodes
    get_ast_nodes = ast_nodes
    get_text_nodes = text_nodes

    def arcs(self, kind: KnowledgeGraphEdgeType) -> Sequence[KnowledgeGraphEdge]:
        self._rebuild_arc_maps()
        buckets = kg_views.require_index(self._arcs_by_kind, "edge-type index not built")
        return buckets[kind]  # type: ignore[index]

    def get_file_tree(self, max_depth: int = 5, max_lines: int = 5000) -> str:
        if self._repo_root is None:
            return ""
        # Precompute adjacency once for the recursive renderer.
        adj: dict[KnowledgeGraphNode, list[KnowledgeGraphNode]] = defaultdict(list)
        for edge in self.arcs(KnowledgeGraphEdgeType.has_file):
            adj[edge.source].append(edge.target)
        return kg_views.render_directory_tree(
            self._repo_root,
            lambda n: adj.get(n, ()),
            max_depth=max_depth,
            max_lines=max_lines,
        )

    def get_all_ast_node_types(self) -> Sequence[str]:
        return kg_views.unique_ast_types(self.ast_nodes())

    def get_import_neighbors(
        self, file_node: KnowledgeGraphNode
    ) -> Sequence[KnowledgeGraphNode]:
        self._rebuild_arc_maps()
        if self._imp_out is None or self._imp_in is None:
            raise RuntimeError("imports adjacency index not built")
        return kg_views.merge_undirected(file_node.node_id, self._imp_out, self._imp_in)

    def get_inheritance_neighbors(
        self, class_ast: KnowledgeGraphNode
    ) -> Sequence[KnowledgeGraphNode]:
        self._rebuild_arc_maps()
        if self._inh_out is None or self._inh_in is None:
            raise RuntimeError("inherits adjacency index not built")
        return inheritance_neighbors(class_ast.node_id, self._inh_out, self._inh_in)

    def get_file_for_ast(self, ast_node: KnowledgeGraphNode) -> Optional[KnowledgeGraphNode]:
        self._rebuild_arc_maps()
        owners = kg_views.require_index(self._ast_file, "ast-to-file index not built")
        return owners.get(ast_node.node_id)  # type: ignore[union-attr]

    def get_ast_to_file_map(self) -> Mapping[int, KnowledgeGraphNode]:
        self._rebuild_arc_maps()
        return kg_views.require_index(self._ast_file, "ast-to-file index not built")  # type: ignore[return-value]

    def get_ast_file_pairs(self) -> Sequence[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]]:
        self._rebuild_arc_maps()
        return kg_views.require_index(self._pairs, "ast-file pairs index not built")  # type: ignore[return-value]

    def find_file_node_for_text_node(self, text_node: KnowledgeGraphNode) -> KnowledgeGraphNode:
        self._rebuild_arc_maps()
        if self._chunk_prev is None or self._text_file is None:
            raise RuntimeError("text-to-file index not built")
        return kg_views.resolve_text_owner(
            text_node.node_id, self._chunk_prev, self._text_file
        )

    def get_parent_to_children_map(self) -> Mapping[int, Sequence[KnowledgeGraphNode]]:
        self._rebuild_arc_maps()
        return kg_views.require_index(  # type: ignore[return-value]
            self._ast_kids, "parent-to-children index not built"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, KnowledgeGraph):
            return False
        return kg_views.same_graph(
            self._vertices, self._arcs, other._vertices, other._arcs
        )


def _install_edge_accessors() -> None:
    """Bind ``get_*_edges`` without a Prometheus-shaped method block in the class body."""
    mapping = (
        ("get_has_ast_edges", KnowledgeGraphEdgeType.has_ast),
        ("get_has_file_edges", KnowledgeGraphEdgeType.has_file),
        ("get_has_text_edges", KnowledgeGraphEdgeType.has_text),
        ("get_next_chunk_edges", KnowledgeGraphEdgeType.next_chunk),
        ("get_parent_of_edges", KnowledgeGraphEdgeType.parent_of),
        ("get_inherits_edges", KnowledgeGraphEdgeType.inherits),
        ("get_imports_edges", KnowledgeGraphEdgeType.imports),
    )
    for name, kind in mapping:

        def _make(edge_kind: KnowledgeGraphEdgeType):
            def _getter(self: KnowledgeGraph) -> Sequence[KnowledgeGraphEdge]:
                return self.arcs(edge_kind)

            return _getter

        setattr(KnowledgeGraph, name, _make(kind))


_install_edge_accessors()
