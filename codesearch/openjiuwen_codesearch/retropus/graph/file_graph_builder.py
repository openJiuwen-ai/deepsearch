"""Building knowledge graph for a single file (copied from Prometheus)."""

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
    TextNode,
)
from openjiuwen_codesearch.retropus.graph.text_splitter import split_text


@dataclass
class TextChunk:
    """A text-file chunk with optional line-span metadata."""

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class FileGraphBuilder:
    """A class for building knowledge graphs from individual files.

    This class processes files and creates knowledge graph representations using different
    strategies based on the file type. For source code files, it uses tree-sitter to
    create an Abstract Syntax Tree (AST) representation. For markdown files, it creates
    a chain of text nodes based on the document's structure.
    """

    def __init__(self, max_ast_depth: int, chunk_size: int, chunk_overlap: int):
        """Initialize the FileGraphBuilder.

        Args:
          max_ast_depth: Maximum depth to traverse in the AST when processing source code files.
          chunk_size: The chunk size for text files.
          chunk_overlap: The overlap size for text files.
        """
        self.max_ast_depth = max_ast_depth
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    @staticmethod
    def support_code_file(file: Path) -> bool:
        """True if ``file`` can be parsed into an AST with tree-sitter."""
        from openjiuwen_codesearch.retropus.parser import tree_sitter_parser  # guarded

        return tree_sitter_parser.supports_file(file)

    @staticmethod
    def support_text_file(file: Path) -> bool:
        """True if ``file`` is treated as plain text (markdown / rst / txt)."""
        return file.suffix in [".markdown", ".md", ".txt", ".rst"]

    def supports_file(self, file: Path) -> bool:
        """Checks if we support building knowledge graph for this file."""
        return self.support_code_file(file) or self.support_text_file(file)

    def build_file_graph(
        self, parent_node: KnowledgeGraphNode, file: Path, next_node_id: int
    ) -> Tuple[int, Sequence[KnowledgeGraphNode], Sequence[KnowledgeGraphEdge]]:
        """Build knowledge graph for a single file."""
        # In this case, it is a file that tree sitter can parse (source code)
        if self.support_code_file(file):
            return self._tree_sitter_file_graph(parent_node, file, next_node_id)
        # otherwise it is a text file that we can parse using the recursive splitter
        else:
            return self._text_file_graph(parent_node, file, next_node_id)

    def _tree_sitter_file_graph(
        self, parent_node: KnowledgeGraphNode, file: Path, next_node_id: int
    ) -> Tuple[int, Sequence[KnowledgeGraphNode], Sequence[KnowledgeGraphEdge]]:
        """Parse a file into a tree-sitter AST and build a corresponding knowledge subgraph."""

        # Store created AST KnowledgeGraphNodes and edges for this file
        tree_sitter_nodes = []
        tree_sitter_edges = []

        # Parse the file into a tree-sitter AST (lazy: retropus extra)
        from openjiuwen_codesearch.retropus.parser import tree_sitter_parser  # guarded

        tree = tree_sitter_parser.parse(file)
        if tree.root_node.has_error or tree.root_node.child_count == 0:
            # Return empty results if the file cannot be parsed properly
            return next_node_id, tree_sitter_nodes, tree_sitter_edges

        # Create the KnowledgeGraphNode for the root AST node
        ast_root_node = ASTNode(
            type=tree.root_node.type,
            start_line=tree.root_node.start_point[0] + 1,
            end_line=tree.root_node.end_point[0] + 1,
            text=tree.root_node.text.decode("utf-8"),
        )
        kg_ast_root_node = KnowledgeGraphNode(next_node_id, ast_root_node)
        next_node_id += 1
        tree_sitter_nodes.append(kg_ast_root_node)

        # Add the HAS_AST edge connecting the file node to its AST root node
        tree_sitter_edges.append(
            KnowledgeGraphEdge(parent_node, kg_ast_root_node, KnowledgeGraphEdgeType.has_ast)
        )

        # Use an explicit stack for depth-first traversal of the AST
        node_stack = deque()
        node_stack.append(
            (tree.root_node, kg_ast_root_node, 1)
        )  # (tree_sitter_node, kg_node, depth)
        while node_stack:
            tree_sitter_node, kg_node, depth = node_stack.pop()

            # Limit the maximum depth to self.max_ast_depth
            if depth > self.max_ast_depth:
                continue

            # Process all children of the current AST node
            for tree_sitter_child_node in tree_sitter_node.children:
                # Create KnowledgeGraphNode for the child AST node
                child_ast_node = ASTNode(
                    type=tree_sitter_child_node.type,
                    start_line=tree_sitter_child_node.start_point[0] + 1,
                    end_line=tree_sitter_child_node.end_point[0] + 1,
                    text=tree_sitter_child_node.text.decode("utf-8"),
                )
                kg_child_ast_node = KnowledgeGraphNode(next_node_id, child_ast_node)
                next_node_id += 1

                tree_sitter_nodes.append(kg_child_ast_node)
                # Add a PARENT_OF edge from the parent to this child
                tree_sitter_edges.append(
                    KnowledgeGraphEdge(kg_node, kg_child_ast_node, KnowledgeGraphEdgeType.parent_of)
                )

                # Add the child node to the stack to continue traversal
                node_stack.append((tree_sitter_child_node, kg_child_ast_node, depth + 1))

        # Return the updated next_node_id, all nodes, and all edges for this file's AST subgraph
        return next_node_id, tree_sitter_nodes, tree_sitter_edges

    def _text_file_graph(
        self, parent_node: KnowledgeGraphNode, file: Path, next_node_id: int
    ) -> Tuple[int, Sequence[KnowledgeGraphNode], Sequence[KnowledgeGraphEdge]]:
        """Split a text file into chunks and attach them as ``HAS_TEXT`` / ``NEXT_CHUNK``."""
        text = file.open(encoding="utf-8").read()
        chunk_texts = split_text(
            text, chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )
        chunks = [TextChunk(text=chunk) for chunk in chunk_texts]

        # Calculate line positions for the entire text
        lines = text.split("\n")
        line_positions = []
        current_pos = 0
        for line in lines:
            line_positions.append(current_pos)
            current_pos += len(line) + 1  # +1 for the newline character

        # Add line position metadata to each chunk
        current_pos = 0
        for chunk in chunks:
            chunk_text = chunk.text
            start_pos = text.find(chunk_text, current_pos)
            if start_pos == -1:
                # If not found, try from beginning
                start_pos = text.find(chunk_text)
                if start_pos == -1:
                    raise ValueError("Chunk text not found in original text.")

            end_pos = start_pos + len(chunk_text)
            current_pos = end_pos  # Update for next iteration

            # Find start line
            for i, pos in enumerate(line_positions):
                if pos > start_pos:
                    start_line = i  # Line numbers are 0-indexed
                    break
            else:
                start_line = len(line_positions)

            # Find end line
            for i, pos in enumerate(line_positions):
                if pos > end_pos:
                    end_line = i  # Line numbers are 0-indexed
                    break
            else:
                end_line = len(line_positions)

            chunk.metadata["start_line"] = start_line
            chunk.metadata["end_line"] = end_line

        return self._chunks_to_file_graph(chunks, parent_node, next_node_id)

    @staticmethod
    def _chunks_to_file_graph(
        chunks: Sequence[TextChunk],
        parent_node: KnowledgeGraphNode,
        next_node_id: int,
    ) -> Tuple[int, Sequence[KnowledgeGraphNode], Sequence[KnowledgeGraphEdge]]:
        """Convert text chunks to a knowledge graph."""
        document_nodes: List[KnowledgeGraphNode] = []
        document_edges: List[KnowledgeGraphEdge] = []

        previous_node = None
        for chunk in chunks:
            start_line = chunk.metadata.get("start_line", 0)
            end_line = chunk.metadata.get("end_line", 0)

            text_node = TextNode(
                text=chunk.text,
                start_line=start_line,
                end_line=end_line,
            )
            kg_text_node = KnowledgeGraphNode(next_node_id, text_node)
            next_node_id += 1
            document_nodes.append(kg_text_node)
            document_edges.append(
                KnowledgeGraphEdge(parent_node, kg_text_node, KnowledgeGraphEdgeType.has_text)
            )

            if previous_node:
                document_edges.append(
                    KnowledgeGraphEdge(
                        previous_node, kg_text_node, KnowledgeGraphEdgeType.next_chunk
                    )
                )

            previous_node = kg_text_node
        return next_node_id, document_nodes, document_edges
