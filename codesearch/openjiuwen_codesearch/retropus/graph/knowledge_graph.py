# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-memory repository knowledge graph (files, AST nodes, text chunks)."""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from openjiuwen_codesearch.retropus.graph.file_graph_builder import FileGraphBuilder
from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    ASTNodeDict,
    FileNode,
    FileNodeDict,
    HasASTEdge,
    HasFileEdge,
    HasTextEdge,
    InheritsEdge,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
    NextChunkEdge,
    ParentOfEdge,
    TextNode,
    TextNodeDict,
)
from openjiuwen_codesearch.retropus.graph.imports import build_imports_edges
from openjiuwen_codesearch.retropus.graph.inherits import build_inherits_edges, inheritance_neighbors
from openjiuwen_codesearch.utils.log_utils import get_logger


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
        self._root_node = root_node
        self._knowledge_graph_nodes: List[KnowledgeGraphNode] = (
            list(knowledge_graph_nodes) if knowledge_graph_nodes is not None else []
        )
        self._knowledge_graph_edges: List[KnowledgeGraphEdge] = (
            list(knowledge_graph_edges) if knowledge_graph_edges is not None else []
        )
        self._next_node_id = root_node_id + len(self._knowledge_graph_nodes)
        self._file_graph_builder = FileGraphBuilder(max_ast_depth, chunk_size, chunk_overlap)
        self._logger = get_logger(__name__)
        self._reset_caches()

    # ------------------------------------------------------------------ caches
    def _reset_caches(self) -> None:
        self._file_nodes: Optional[List[KnowledgeGraphNode]] = None
        self._ast_nodes: Optional[List[KnowledgeGraphNode]] = None
        self._text_nodes: Optional[List[KnowledgeGraphNode]] = None
        self._edges_by_type: Optional[
            Mapping[KnowledgeGraphEdgeType, List[KnowledgeGraphEdge]]
        ] = None
        self._parent_children: Optional[Mapping[int, List[KnowledgeGraphNode]]] = None
        self._prev_chunk: Optional[Mapping[int, KnowledgeGraphNode]] = None
        self._text_owner: Optional[Mapping[int, KnowledgeGraphNode]] = None
        self._ast_owner: Optional[Mapping[int, KnowledgeGraphNode]] = None
        self._ast_file_pairs: Optional[
            List[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]]
        ] = None
        self._inherits_out: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        self._inherits_in: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        self._class_owner: Optional[Dict[int, KnowledgeGraphNode]] = None
        self._imports_out: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        self._imports_in: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        self._imports_labels: Dict[Tuple[int, int], str] = {}

    def _invalidate_indexes(self) -> None:
        """Drop lazy indexes after the vertex/edge sets change."""
        labels = self._imports_labels
        self._reset_caches()
        # Preserve labels unless caller clears them explicitly during rebuild.
        self._imports_labels = labels

    def _partition_nodes(self) -> None:
        if self._file_nodes is not None:
            return
        files: List[KnowledgeGraphNode] = []
        asts: List[KnowledgeGraphNode] = []
        texts: List[KnowledgeGraphNode] = []
        for kg in self._knowledge_graph_nodes:
            payload = kg.node
            if isinstance(payload, FileNode):
                files.append(kg)
            elif isinstance(payload, ASTNode):
                asts.append(kg)
            elif isinstance(payload, TextNode):
                texts.append(kg)
        self._file_nodes = files
        self._ast_nodes = asts
        self._text_nodes = texts

    def _ensure_node_indexes(self) -> None:
        self._partition_nodes()

    def _build_edge_indexes(self) -> None:
        if self._edges_by_type is not None:
            return

        buckets: dict[KnowledgeGraphEdgeType, List[KnowledgeGraphEdge]] = {
            kind: [] for kind in KnowledgeGraphEdgeType
        }
        for edge in self._knowledge_graph_edges:
            buckets[edge.type].append(edge)
        self._edges_by_type = buckets

        children: dict[int, List[KnowledgeGraphNode]] = {}
        for edge in buckets[KnowledgeGraphEdgeType.parent_of]:
            children.setdefault(edge.source.node_id, []).append(edge.target)
        self._parent_children = children

        self._prev_chunk = {
            e.target.node_id: e.source
            for e in buckets[KnowledgeGraphEdgeType.next_chunk]
        }
        self._text_owner = {
            e.target.node_id: e.source
            for e in buckets[KnowledgeGraphEdgeType.has_text]
        }

        # One DFS per file AST → (file, non-root ast) pairs for BM25.
        owners: dict[int, KnowledgeGraphNode] = {}
        pairs: List[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]] = []
        for edge in buckets[KnowledgeGraphEdgeType.has_ast]:
            file_kg = edge.source
            pending = list(children.get(edge.target.node_id, ()))
            while pending:
                cur = pending.pop()
                owners[cur.node_id] = file_kg
                pairs.append((file_kg, cur))
                kids = children.get(cur.node_id)
                if kids:
                    pending.extend(kids)
        self._ast_owner = owners
        self._ast_file_pairs = pairs

        out_inh: Dict[int, List[KnowledgeGraphNode]] = {}
        in_inh: Dict[int, List[KnowledgeGraphNode]] = {}
        for edge in buckets[KnowledgeGraphEdgeType.inherits]:
            out_inh.setdefault(edge.source.node_id, []).append(edge.target)
            in_inh.setdefault(edge.target.node_id, []).append(edge.source)
        self._inherits_out = out_inh
        self._inherits_in = in_inh

        class_files: Dict[int, KnowledgeGraphNode] = {}
        for edge in buckets[KnowledgeGraphEdgeType.inherits]:
            src_f = owners.get(edge.source.node_id)
            tgt_f = owners.get(edge.target.node_id)
            if src_f is not None:
                class_files[edge.source.node_id] = src_f
            if tgt_f is not None:
                class_files[edge.target.node_id] = tgt_f
        self._class_owner = class_files

        out_imp: Dict[int, List[KnowledgeGraphNode]] = {}
        in_imp: Dict[int, List[KnowledgeGraphNode]] = {}
        for edge in buckets[KnowledgeGraphEdgeType.imports]:
            out_imp.setdefault(edge.source.node_id, []).append(edge.target)
            in_imp.setdefault(edge.target.node_id, []).append(edge.source)
        self._imports_out = out_imp
        self._imports_in = in_imp

    def _ensure_edge_indexes(self) -> None:
        self._build_edge_indexes()

    # ------------------------------------------------------------------ build
    async def build_graph(self, root_dir: Path) -> None:
        """Build the graph for ``root_dir`` on a worker thread."""
        await asyncio.to_thread(self._build_graph, root_dir)

    def _build_graph(self, root_dir: Path) -> None:
        import igittigitt  # guarded: retropus extra
        from tqdm import tqdm  # guarded: build-time progress only

        self._invalidate_indexes()
        self._imports_labels = {}
        root_dir = root_dir.absolute()
        started = time.perf_counter()
        self._logger.info("KG: scanning repository tree under %s", root_dir)

        ignore = igittigitt.IgnoreParser()
        ignore.parse_rule_files(root_dir)
        ignore.add_rule(".git", root_dir)

        root_payload = FileNode(basename=root_dir.name, relative_path=".")
        root_kg = KnowledgeGraphNode(self._next_node_id, root_payload)
        self._next_node_id += 1
        self._knowledge_graph_nodes.append(root_kg)
        self._root_node = root_kg

        walk: deque[tuple[Path, KnowledgeGraphNode]] = deque([(root_dir, root_kg)])
        to_parse: List[Tuple[Path, KnowledgeGraphNode]] = []

        while walk:
            path, parent_kg = walk.pop()
            if path.is_dir():
                self._logger.debug("Processing directory %s", path)
                for child in sorted(path.iterdir()):
                    if not child.exists():
                        continue
                    if child.is_file() and not self._file_graph_builder.supports_file(child):
                        continue
                    if ignore.match(child):
                        continue
                    child_payload = FileNode(
                        basename=child.name,
                        relative_path=child.relative_to(root_dir).as_posix(),
                    )
                    child_kg = KnowledgeGraphNode(self._next_node_id, child_payload)
                    self._next_node_id += 1
                    self._knowledge_graph_nodes.append(child_kg)
                    self._knowledge_graph_edges.append(
                        KnowledgeGraphEdge(
                            parent_kg, child_kg, KnowledgeGraphEdgeType.has_file
                        )
                    )
                    walk.append((child, child_kg))
            else:
                to_parse.append((path, parent_kg))

        self._logger.info("KG: parsing %d files with tree-sitter", len(to_parse))
        for path, file_kg in tqdm(to_parse, desc="KG parse", unit="file", leave=False):
            self._logger.debug("Processing file %s", path)
            try:
                self._next_node_id, kg_nodes, kg_edges = (
                    self._file_graph_builder.build_file_graph(
                        file_kg, path, self._next_node_id
                    )
                )
            except (ValueError, OSError) as exc:
                self._logger.warning("Skipping %s: %s", path, exc)
                continue
            self._knowledge_graph_nodes.extend(kg_nodes)
            self._knowledge_graph_edges.extend(kg_edges)

        index_t0 = time.perf_counter()
        self._partition_nodes()
        self._build_edge_indexes()

        inherit_t0 = time.perf_counter()
        inherit_edges = build_inherits_edges(self._ast_file_pairs or ())
        inherit_s = time.perf_counter() - inherit_t0

        imports_t0 = time.perf_counter()
        import_edges, import_labels = build_imports_edges(
            self._file_nodes or (), repo_root=root_dir
        )
        imports_s = time.perf_counter() - imports_t0

        if inherit_edges or import_edges:
            if inherit_edges:
                self._knowledge_graph_edges.extend(inherit_edges)
            if import_edges:
                self._knowledge_graph_edges.extend(import_edges)
                self._imports_labels = import_labels
            # Force edge-index rebuild so new relation types are visible.
            self._edges_by_type = None
            self._inherits_out = None
            self._inherits_in = None
            self._class_owner = None
            self._imports_out = None
            self._imports_in = None
            self._build_edge_indexes()

        self._logger.info(
            "KG: INHERITS edges=%d (%.1fs); IMPORTS edges=%d (%.1fs); "
            "indexes ready (searchable_ast=%d, %.1fs)",
            len(inherit_edges),
            inherit_s,
            len(import_edges),
            imports_s,
            len(self._ast_file_pairs or ()),
            time.perf_counter() - index_t0,
        )
        self._logger.info(
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
        return self._root_node

    def get_all_nodes(self) -> Sequence[KnowledgeGraphNode]:
        return list(self._knowledge_graph_nodes)

    def get_all_edges(self) -> Sequence[KnowledgeGraphEdge]:
        return list(self._knowledge_graph_edges)

    def get_imports_labels_map(self) -> Dict[Tuple[int, int], str]:
        return dict(self._imports_labels)

    def set_imports_labels_map(self, labels: Mapping[Tuple[int, int], str]) -> None:
        self._imports_labels = dict(labels)

    def get_file_tree(self, max_depth: int = 5, max_lines: int = 5000) -> str:
        """Render a box-drawing tree of ``HAS_FILE`` children under the root."""
        adjacency = self._file_adjacency()
        lines: List[str] = []
        # stack: (node, depth, prefix_before_connector, is_last)
        stack: deque[
            tuple[KnowledgeGraphNode, int, str, bool | None]
        ] = deque([(self._root_node, 0, "", None)])  # type: ignore[list-item]

        indent_blank = "    "
        indent_pipe = "|   "
        connector_mid = "├── "
        connector_end = "└── "

        while stack and len(lines) < max_lines:
            node, depth, prefix, is_last = stack.pop()
            if node is None or depth > max_depth:
                continue
            if depth == 0:
                lines.append(node.node.basename)
            else:
                tip = connector_end if is_last else connector_mid
                lines.append(f"{prefix}{tip}{node.node.basename}")

            kids = sorted(adjacency[node], key=lambda n: n.node.basename)
            for idx in range(len(kids) - 1, -1, -1):
                last_child = idx == len(kids) - 1
                child_prefix = ""
                if depth > 0:
                    child_prefix = prefix + (indent_blank if is_last else indent_pipe)
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

    def get_file_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._partition_nodes()
        assert self._file_nodes is not None
        return self._file_nodes

    def get_ast_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._partition_nodes()
        assert self._ast_nodes is not None
        return self._ast_nodes

    def get_text_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._partition_nodes()
        assert self._text_nodes is not None
        return self._text_nodes

    def _edges_of_type(self, edge_type: KnowledgeGraphEdgeType) -> Sequence[KnowledgeGraphEdge]:
        self._build_edge_indexes()
        assert self._edges_by_type is not None
        return self._edges_by_type[edge_type]

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
        return self._imports_labels.get((source_id, target_id), "")

    def get_import_neighbors(
        self, file_node: KnowledgeGraphNode
    ) -> Sequence[KnowledgeGraphNode]:
        self._build_edge_indexes()
        assert self._imports_out is not None and self._imports_in is not None
        merged: List[KnowledgeGraphNode] = []
        seen = {file_node.node_id}
        for n in self._imports_out.get(file_node.node_id, ()) + self._imports_in.get(
            file_node.node_id, ()
        ):
            if n.node_id not in seen:
                seen.add(n.node_id)
                merged.append(n)
        return merged

    def get_inheritance_neighbors(
        self, class_ast: KnowledgeGraphNode
    ) -> Sequence[KnowledgeGraphNode]:
        self._build_edge_indexes()
        assert self._inherits_out is not None and self._inherits_in is not None
        return inheritance_neighbors(
            class_ast.node_id, self._inherits_out, self._inherits_in
        )

    def get_file_for_ast(self, ast_node: KnowledgeGraphNode) -> Optional[KnowledgeGraphNode]:
        self._build_edge_indexes()
        assert self._ast_owner is not None
        return self._ast_owner.get(ast_node.node_id)

    def get_ast_to_file_map(self) -> Mapping[int, KnowledgeGraphNode]:
        self._build_edge_indexes()
        assert self._ast_owner is not None
        return self._ast_owner

    def get_ast_file_pairs(self) -> Sequence[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]]:
        self._build_edge_indexes()
        assert self._ast_file_pairs is not None
        return self._ast_file_pairs

    def find_file_node_for_text_node(self, text_node: KnowledgeGraphNode) -> KnowledgeGraphNode:
        """Walk ``NEXT_CHUNK`` backward to the first chunk, then ``HAS_TEXT``."""
        self._build_edge_indexes()
        assert self._prev_chunk is not None and self._text_owner is not None
        nid = text_node.node_id
        while nid in self._prev_chunk:
            nid = self._prev_chunk[nid].node_id
        owner = self._text_owner.get(nid)
        if owner is None:
            raise KeyError(f"no file node for text node id {nid}")
        return owner

    def get_file_node_dicts(self) -> Sequence[FileNodeDict]:
        return [n.to_dict() for n in self.get_file_nodes()]  # type: ignore[misc]

    def get_ast_node_dicts(self) -> Sequence[ASTNodeDict]:
        return [n.to_dict() for n in self.get_ast_nodes()]  # type: ignore[misc]

    def get_text_node_dicts(self) -> Sequence[TextNodeDict]:
        return [n.to_dict() for n in self.get_text_nodes()]  # type: ignore[misc]

    def get_has_ast_edge_dicts(self) -> Sequence[HasASTEdge]:
        return [e.to_edge_dict() for e in self.get_has_ast_edges()]  # type: ignore[misc]

    def get_has_file_edge_dicts(self) -> Sequence[HasFileEdge]:
        return [e.to_edge_dict() for e in self.get_has_file_edges()]  # type: ignore[misc]

    def get_has_text_edge_dicts(self) -> Sequence[HasTextEdge]:
        return [e.to_edge_dict() for e in self.get_has_text_edges()]  # type: ignore[misc]

    def get_next_chunk_edge_dicts(self) -> Sequence[NextChunkEdge]:
        return [e.to_edge_dict() for e in self.get_next_chunk_edges()]  # type: ignore[misc]

    def get_parent_of_edge_dicts(self) -> Sequence[ParentOfEdge]:
        return [e.to_edge_dict() for e in self.get_parent_of_edges()]  # type: ignore[misc]

    def get_inherits_edge_dicts(self) -> Sequence[InheritsEdge]:
        return [e.to_edge_dict() for e in self.get_inherits_edges()]  # type: ignore[misc]

    def get_parent_to_children_map(self) -> Mapping[int, Sequence[KnowledgeGraphNode]]:
        self._build_edge_indexes()
        assert self._parent_children is not None
        return self._parent_children

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, KnowledgeGraph):
            return False
        left_nodes = sorted(self._knowledge_graph_nodes, key=lambda n: n.node_id)
        right_nodes = sorted(other._knowledge_graph_nodes, key=lambda n: n.node_id)
        if any(a != b for a, b in itertools.zip_longest(left_nodes, right_nodes)):
            return False

        def _edge_key(e: KnowledgeGraphEdge):
            return (e.source.node_id, e.target.node_id, e.type)

        left_edges = sorted(self._knowledge_graph_edges, key=_edge_key)
        right_edges = sorted(other._knowledge_graph_edges, key=_edge_key)
        return all(a == b for a, b in itertools.zip_longest(left_edges, right_edges))
