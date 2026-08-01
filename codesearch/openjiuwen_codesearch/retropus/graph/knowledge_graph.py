"""The in-memory knowledge graph representation of a codebase (copied from Prometheus).

Node types:
* FileNode: Represent a file/dir
* ASTNode: Represent a tree-sitter node
* TextNode: Represent a string

Edge types:
* HAS_FILE, HAS_AST, HAS_TEXT, PARENT_OF, NEXT_CHUNK, INHERITS

The graph is built fully in memory (Retropus has no Neo4j dependency).
"""

import asyncio
import itertools
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import igittigitt
from tqdm import tqdm

from openjiuwen_codesearch.retropus.graph.file_graph_builder import FileGraphBuilder
from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    FileNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
    Neo4jASTNode,
    Neo4jFileNode,
    Neo4jHasASTEdge,
    Neo4jHasFileEdge,
    Neo4jHasTextEdge,
    Neo4jInheritsEdge,
    Neo4jNextChunkEdge,
    Neo4jParentOfEdge,
    Neo4jTextNode,
    TextNode,
)
from openjiuwen_codesearch.retropus.graph.inherits import build_inherits_edges, inheritance_neighbors
from openjiuwen_codesearch.utils.log_utils import get_logger


class KnowledgeGraph:
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
        """Initializes the knowledge graph."""
        self.max_ast_depth = max_ast_depth
        self.root_node_id = root_node_id
        self._root_node = root_node
        self._knowledge_graph_nodes = (
            knowledge_graph_nodes if knowledge_graph_nodes is not None else []
        )
        self._knowledge_graph_edges = (
            knowledge_graph_edges if knowledge_graph_edges is not None else []
        )
        self._next_node_id = root_node_id + len(self._knowledge_graph_nodes)

        self._file_graph_builder = FileGraphBuilder(max_ast_depth, chunk_size, chunk_overlap)
        self._logger = get_logger(__name__)

        # Lazy indexes — rebuilt on first access after the graph is populated.
        self._cached_file_nodes: Optional[List[KnowledgeGraphNode]] = None
        self._cached_ast_nodes: Optional[List[KnowledgeGraphNode]] = None
        self._cached_text_nodes: Optional[List[KnowledgeGraphNode]] = None
        self._cached_edges_by_type: Optional[
            Mapping[KnowledgeGraphEdgeType, List[KnowledgeGraphEdge]]
        ] = None
        self._cached_parent_to_children: Optional[Mapping[int, List[KnowledgeGraphNode]]] = None
        self._cached_next_chunk_reverse: Optional[Mapping[int, KnowledgeGraphNode]] = None
        self._cached_text_to_file: Optional[Mapping[int, KnowledgeGraphNode]] = None
        self._cached_ast_to_file: Optional[Mapping[int, KnowledgeGraphNode]] = None
        self._cached_ast_file_pairs: Optional[
            List[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]]
        ] = None
        self._cached_inherits_out: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        self._cached_inherits_in: Optional[Dict[int, List[KnowledgeGraphNode]]] = None
        # class AST node_id → owning FileNode
        self._cached_class_to_file: Optional[Dict[int, KnowledgeGraphNode]] = None

    def _invalidate_indexes(self) -> None:
        self._cached_file_nodes = None
        self._cached_ast_nodes = None
        self._cached_text_nodes = None
        self._cached_edges_by_type = None
        self._cached_parent_to_children = None
        self._cached_next_chunk_reverse = None
        self._cached_text_to_file = None
        self._cached_ast_to_file = None
        self._cached_ast_file_pairs = None
        self._cached_inherits_out = None
        self._cached_inherits_in = None
        self._cached_class_to_file = None

    def _ensure_node_indexes(self) -> None:
        if self._cached_file_nodes is not None:
            return
        files: List[KnowledgeGraphNode] = []
        asts: List[KnowledgeGraphNode] = []
        texts: List[KnowledgeGraphNode] = []
        for kg_node in self._knowledge_graph_nodes:
            node = kg_node.node
            if isinstance(node, FileNode):
                files.append(kg_node)
            elif isinstance(node, ASTNode):
                asts.append(kg_node)
            elif isinstance(node, TextNode):
                texts.append(kg_node)
        self._cached_file_nodes = files
        self._cached_ast_nodes = asts
        self._cached_text_nodes = texts

    def _ensure_edge_indexes(self) -> None:
        if self._cached_edges_by_type is not None:
            return
        by_type: dict[KnowledgeGraphEdgeType, List[KnowledgeGraphEdge]] = {
            edge_type: [] for edge_type in KnowledgeGraphEdgeType
        }
        for edge in self._knowledge_graph_edges:
            by_type[edge.type].append(edge)
        self._cached_edges_by_type = by_type

        parent_to_children: dict[int, List[KnowledgeGraphNode]] = {}
        for edge in by_type[KnowledgeGraphEdgeType.parent_of]:
            parent_to_children.setdefault(edge.source.node_id, []).append(edge.target)
        self._cached_parent_to_children = parent_to_children

        self._cached_next_chunk_reverse = {
            edge.target.node_id: edge.source
            for edge in by_type[KnowledgeGraphEdgeType.next_chunk]
        }
        self._cached_text_to_file = {
            edge.target.node_id: edge.source
            for edge in by_type[KnowledgeGraphEdgeType.has_text]
        }

        # Walk each file's AST once so BM25 collection is O(N) with no repeated scans.
        # Pairs are (file_node, non-root ast_node) — the file-level AST root is skipped.
        ast_to_file: dict[int, KnowledgeGraphNode] = {}
        ast_file_pairs: List[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]] = []
        parent_to_children_map = parent_to_children
        for edge in by_type[KnowledgeGraphEdgeType.has_ast]:
            file_node = edge.source
            root_ast = edge.target
            stack = list(parent_to_children_map.get(root_ast.node_id, ()))
            while stack:
                current = stack.pop()
                ast_to_file[current.node_id] = file_node
                ast_file_pairs.append((file_node, current))
                children = parent_to_children_map.get(current.node_id)
                if children:
                    stack.extend(children)
        self._cached_ast_to_file = ast_to_file
        self._cached_ast_file_pairs = ast_file_pairs

        inherits_out: Dict[int, List[KnowledgeGraphNode]] = {}
        inherits_in: Dict[int, List[KnowledgeGraphNode]] = {}
        for edge in by_type[KnowledgeGraphEdgeType.inherits]:
            inherits_out.setdefault(edge.source.node_id, []).append(edge.target)
            inherits_in.setdefault(edge.target.node_id, []).append(edge.source)
        self._cached_inherits_out = inherits_out
        self._cached_inherits_in = inherits_in

        class_to_file: Dict[int, KnowledgeGraphNode] = {}
        for edge in by_type[KnowledgeGraphEdgeType.inherits]:
            src_file = ast_to_file.get(edge.source.node_id)
            tgt_file = ast_to_file.get(edge.target.node_id)
            if src_file is not None:
                class_to_file[edge.source.node_id] = src_file
            if tgt_file is not None:
                class_to_file[edge.target.node_id] = tgt_file
        self._cached_class_to_file = class_to_file

    async def build_graph(self, root_dir: Path):
        """Asynchronously builds knowledge graph for a codebase at a location."""
        await asyncio.to_thread(self._build_graph, root_dir)

    def _build_graph(self, root_dir: Path):
        """Builds knowledge graph for a codebase at a location."""
        self._invalidate_indexes()
        root_dir = root_dir.absolute()
        t0 = time.perf_counter()
        self._logger.info("KG: scanning repository tree under %s", root_dir)
        gitignore_parser = igittigitt.IgnoreParser()
        gitignore_parser.parse_rule_files(root_dir)
        gitignore_parser.add_rule(".git", root_dir)

        # The root node for the whole graph
        root_dir_node = FileNode(basename=root_dir.name, relative_path=".")
        kg_root_dir_node = KnowledgeGraphNode(self._next_node_id, root_dir_node)
        self._next_node_id += 1
        self._knowledge_graph_nodes.append(kg_root_dir_node)
        self._root_node = kg_root_dir_node

        file_stack = deque()
        file_stack.append((root_dir, kg_root_dir_node))
        files_to_parse: List[Tuple[Path, KnowledgeGraphNode]] = []

        # Walk the tree first so parsing can show a determinate progress bar.
        while file_stack:
            file, kg_file_path_node = file_stack.pop()

            # If the file is a directory, we create FileNode for all supported children files.
            if file.is_dir():
                self._logger.debug(f"Processing directory {file}")
                for child_file in sorted(file.iterdir()):
                    # Skip if the file does not exist (broken symlink).
                    if not child_file.exists():
                        continue

                    # Skip if the child is not a file or it is not supported by the file graph builder.
                    if child_file.is_file() and not self._file_graph_builder.supports_file(
                        child_file
                    ):
                        continue

                    if gitignore_parser.match(child_file):
                        continue

                    child_file_node = FileNode(
                        basename=child_file.name,
                        relative_path=child_file.relative_to(root_dir).as_posix(),
                    )
                    kg_child_file_node = KnowledgeGraphNode(self._next_node_id, child_file_node)
                    self._next_node_id += 1
                    self._knowledge_graph_nodes.append(kg_child_file_node)
                    self._knowledge_graph_edges.append(
                        KnowledgeGraphEdge(
                            kg_file_path_node,
                            kg_child_file_node,
                            KnowledgeGraphEdgeType.has_file,
                        )
                    )

                    file_stack.append((child_file, kg_child_file_node))
            else:
                files_to_parse.append((file, kg_file_path_node))

        self._logger.info("KG: parsing %d files with tree-sitter", len(files_to_parse))
        for file, kg_file_path_node in tqdm(
            files_to_parse,
            desc="KG parse",
            unit="file",
            leave=False,
        ):
            self._logger.debug(f"Processing file {file}")
            try:
                next_node_id, kg_nodes, kg_edges = self._file_graph_builder.build_file_graph(
                    kg_file_path_node, file, self._next_node_id
                )
            except (UnicodeDecodeError, ValueError, OSError) as exc:
                self._logger.warning(f"Skipping {file}: {exc}")
                continue
            self._next_node_id = next_node_id
            self._knowledge_graph_nodes.extend(kg_nodes)
            self._knowledge_graph_edges.extend(kg_edges)

        # First-pass indexes (needed to walk AST→file for inheritance resolution).
        index_t0 = time.perf_counter()
        self._ensure_node_indexes()
        self._ensure_edge_indexes()

        inherit_t0 = time.perf_counter()
        inherit_edges = build_inherits_edges(self._cached_ast_file_pairs or ())
        if inherit_edges:
            self._knowledge_graph_edges.extend(inherit_edges)
            # Rebuild edge indexes so INHERITS adjacency is available.
            self._cached_edges_by_type = None
            self._cached_inherits_out = None
            self._cached_inherits_in = None
            self._cached_class_to_file = None
            self._ensure_edge_indexes()
        self._logger.info(
            "KG: INHERITS edges=%d (%.1fs); indexes ready (searchable_ast=%d, %.1fs)",
            len(inherit_edges),
            time.perf_counter() - inherit_t0,
            len(self._cached_ast_file_pairs or ()),
            time.perf_counter() - index_t0,
        )
        self._logger.info(
            "KG: ready (files=%d ast=%d text=%d inherits=%d, %.1fs)",
            len(self.get_file_nodes()),
            len(self.get_ast_nodes()),
            len(self.get_text_nodes()),
            len(inherit_edges),
            time.perf_counter() - t0,
        )

    def get_file_tree(self, max_depth: int = 5, max_lines: int = 5000) -> str:
        """Generate a tree-like string representation of the file structure."""
        file_node_adjacency_dict = self._get_file_node_adjacency_dict()

        # Each stack entry contains: (current_node, depth, prefix_string, is_last_child)
        stack = deque()
        stack.append((self._root_node, 0, "", None))
        result_lines = []

        # Box-drawing characters and indentation constants
        SPACE = "    "  # Indentation for levels without children
        BRANCH = "|   "  # Vertical line for intermediate children
        TEE = "├── "  # Entry for a non-final child
        LAST = "└── "  # Entry for the last child

        while stack and (len(result_lines)) < max_lines:
            file_node, depth, prefix, is_last = stack.pop()

            # Skip if we've exceeded max_depth
            if depth > max_depth:
                continue

            # Choose the connector character depending on whether this is the last child
            pointer = LAST if is_last else TEE
            line_prefix = "" if depth == 0 else prefix + pointer

            # Add the current file or directory to the result lines
            result_lines.append(line_prefix + file_node.node.basename)

            # Get the current node's children and sort them alphabetically by name
            sorted_children_file_node = sorted(
                file_node_adjacency_dict[file_node], key=lambda x: x.node.basename
            )

            # Traverse the children in reverse order to maintain the correct tree shape
            for i in range(len(sorted_children_file_node) - 1, -1, -1):
                extension = SPACE if is_last else BRANCH  # Update prefix for children
                new_prefix = "" if depth == 0 else prefix + extension
                stack.append(
                    (
                        sorted_children_file_node[i],
                        depth + 1,
                        new_prefix,
                        i == len(sorted_children_file_node) - 1,  # True if last child
                    )
                )

        # Join all lines into a single string for output
        return "\n".join(result_lines)

    def get_all_ast_node_types(self) -> Sequence[str]:
        ast_node_types = set()
        for ast_node in self.get_ast_nodes():
            ast_node_types.add(ast_node.node.type)
        return list(ast_node_types)

    def _get_file_node_adjacency_dict(
        self,
    ) -> Mapping[KnowledgeGraphNode, Sequence[KnowledgeGraphNode]]:
        file_node_adjacency_dict = defaultdict(list)
        for has_file_edge in self.get_has_file_edges():
            file_node_adjacency_dict[has_file_edge.source].append(has_file_edge.target)
        return file_node_adjacency_dict

    def get_file_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._ensure_node_indexes()
        assert self._cached_file_nodes is not None
        return self._cached_file_nodes

    def get_ast_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._ensure_node_indexes()
        assert self._cached_ast_nodes is not None
        return self._cached_ast_nodes

    def get_text_nodes(self) -> Sequence[KnowledgeGraphNode]:
        self._ensure_node_indexes()
        assert self._cached_text_nodes is not None
        return self._cached_text_nodes

    def _edges_of_type(self, edge_type: KnowledgeGraphEdgeType) -> Sequence[KnowledgeGraphEdge]:
        self._ensure_edge_indexes()
        assert self._cached_edges_by_type is not None
        return self._cached_edges_by_type[edge_type]

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

    def get_inheritance_neighbors(
        self, class_ast: KnowledgeGraphNode
    ) -> Sequence[KnowledgeGraphNode]:
        """1-hop superclass and subclass neighbors of a class AST node."""
        self._ensure_edge_indexes()
        assert self._cached_inherits_out is not None
        assert self._cached_inherits_in is not None
        return inheritance_neighbors(
            class_ast.node_id, self._cached_inherits_out, self._cached_inherits_in
        )

    def get_file_for_ast(self, ast_node: KnowledgeGraphNode) -> Optional[KnowledgeGraphNode]:
        """Owning FileNode for a non-root AST node, if known."""
        self._ensure_edge_indexes()
        assert self._cached_ast_to_file is not None
        return self._cached_ast_to_file.get(ast_node.node_id)

    def get_ast_to_file_map(self) -> Mapping[int, KnowledgeGraphNode]:
        """Map non-root AST node_id → owning FileNode (built once)."""
        self._ensure_edge_indexes()
        assert self._cached_ast_to_file is not None
        return self._cached_ast_to_file

    def get_ast_file_pairs(self) -> Sequence[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]]:
        """Cached ``(file_node, non_root_ast_node)`` pairs for retrieval indexing."""
        self._ensure_edge_indexes()
        assert self._cached_ast_file_pairs is not None
        return self._cached_ast_file_pairs

    def find_file_node_for_text_node(self, text_node: KnowledgeGraphNode) -> KnowledgeGraphNode:
        """Owning FileNode for a TextNode, using cached NEXT_CHUNK / HAS_TEXT maps."""
        self._ensure_edge_indexes()
        assert self._cached_next_chunk_reverse is not None
        assert self._cached_text_to_file is not None
        current_id = text_node.node_id
        while current_id in self._cached_next_chunk_reverse:
            current_id = self._cached_next_chunk_reverse[current_id].node_id
        return self._cached_text_to_file[current_id]

    def get_neo4j_file_nodes(self) -> Sequence[Neo4jFileNode]:
        return [kg_node.to_neo4j_node() for kg_node in self.get_file_nodes()]

    def get_neo4j_ast_nodes(self) -> Sequence[Neo4jASTNode]:
        return [kg_node.to_neo4j_node() for kg_node in self.get_ast_nodes()]

    def get_neo4j_text_nodes(self) -> Sequence[Neo4jTextNode]:
        return [kg_node.to_neo4j_node() for kg_node in self.get_text_nodes()]

    def get_neo4j_has_ast_edges(self) -> Sequence[Neo4jHasASTEdge]:
        return [kg_edge.to_neo4j_edge() for kg_edge in self.get_has_ast_edges()]

    def get_neo4j_has_file_edges(self) -> Sequence[Neo4jHasFileEdge]:
        return [kg_edge.to_neo4j_edge() for kg_edge in self.get_has_file_edges()]

    def get_neo4j_has_text_edges(self) -> Sequence[Neo4jHasTextEdge]:
        return [kg_edge.to_neo4j_edge() for kg_edge in self.get_has_text_edges()]

    def get_neo4j_next_chunk_edges(self) -> Sequence[Neo4jNextChunkEdge]:
        return [kg_edge.to_neo4j_edge() for kg_edge in self.get_next_chunk_edges()]

    def get_neo4j_parent_of_edges(self) -> Sequence[Neo4jParentOfEdge]:
        return [kg_edge.to_neo4j_edge() for kg_edge in self.get_parent_of_edges()]

    def get_neo4j_inherits_edges(self) -> Sequence[Neo4jInheritsEdge]:
        return [kg_edge.to_neo4j_edge() for kg_edge in self.get_inherits_edges()]

    def get_parent_to_children_map(self) -> Mapping[int, Sequence[KnowledgeGraphNode]]:
        """Returns a mapping from parent AST node IDs to their child AST nodes."""
        self._ensure_edge_indexes()
        assert self._cached_parent_to_children is not None
        return self._cached_parent_to_children

    def __eq__(self, other: "KnowledgeGraph") -> bool:
        if not isinstance(other, KnowledgeGraph):
            return False

        self._knowledge_graph_nodes.sort(key=lambda x: x.node_id)
        other._knowledge_graph_nodes.sort(key=lambda x: x.node_id)

        for self_kg_node, other_kg_node in itertools.zip_longest(
            self._knowledge_graph_nodes, other._knowledge_graph_nodes, fillvalue=None
        ):
            if self_kg_node != other_kg_node:
                return False

        self._knowledge_graph_edges.sort(key=lambda x: (x.source.node_id, x.target.node_id, x.type))
        other._knowledge_graph_edges.sort(
            key=lambda x: (x.source.node_id, x.target.node_id, x.type)
        )
        for self_kg_edge, other_kg_edge in itertools.zip_longest(
            self._knowledge_graph_edges, other._knowledge_graph_edges, fillvalue=None
        ):
            if self_kg_edge != other_kg_edge:
                return False

        return True
