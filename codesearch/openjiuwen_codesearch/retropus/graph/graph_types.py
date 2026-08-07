"""Type definition for nodes and edges in the knowledge graph (based on Prometheus)."""

import dataclasses
import enum
from typing import TypedDict, Union


@dataclasses.dataclass(frozen=True)
class FileNode:
    """A node representing a file/dir.

    Attributes:
      basename: The basename of a file/dir, like 'bar.py' or 'foo'.
      relative_path: The relative path from the root path, like 'foo/bar/baz.java'.
    """

    basename: str
    relative_path: str


@dataclasses.dataclass(frozen=True)
class ASTNode:
    """A node representing a tree-sitter node.

    Attributes:
      type: The tree-sitter node type.
      start_line: The starting line number. 1-indexed and inclusive.
      end_line: The ending line number.  1-indexed and inclusive.
      text: The source code correcpsonding to the node.
    """

    type: str
    start_line: int
    end_line: int
    text: str


@dataclasses.dataclass(frozen=True)
class TextNode:
    """A node representing a piece of text.

    Attributes:
      text: A string.
      start_line: The starting line number.
      end_line: The ending line number.
    """

    text: str
    start_line: int
    end_line: int


@dataclasses.dataclass(frozen=True)
class KnowledgeGraphNode:
    """A node in the knowledge graph.

    Attributes:
      node_id: A id that uniquely identifies a node in the graph.
      node: The node itself, can be a FileNode, ASTNode or TextNode.
    """

    node_id: int
    node: Union[FileNode, ASTNode, TextNode]

    def to_dict(self) -> Union["FileNodeDict", "ASTNodeDict", "TextNodeDict"]:
        """Convert the KnowledgeGraphNode into a serializable dict."""
        match self.node:
            case FileNode():
                return FileNodeDict(
                    node_id=self.node_id,
                    basename=self.node.basename,
                    relative_path=self.node.relative_path,
                )
            case ASTNode():
                return ASTNodeDict(
                    node_id=self.node_id,
                    type=self.node.type,
                    start_line=self.node.start_line,
                    end_line=self.node.end_line,
                    text=self.node.text,
                )
            case TextNode():
                return TextNodeDict(
                    node_id=self.node_id,
                    text=self.node.text,
                    start_line=self.node.start_line,
                    end_line=self.node.end_line,
                )
            case _:
                raise ValueError("Unknown KnowledgeGraphNode.node type")

    @classmethod
    def from_file_node_dict(cls, node: "FileNodeDict") -> "KnowledgeGraphNode":
        """Rebuild a ``KnowledgeGraphNode`` wrapping a ``FileNode`` from dict fields."""
        return cls(
            node_id=node["node_id"],
            node=FileNode(
                basename=node["basename"],
                relative_path=node["relative_path"],
            ),
        )

    @classmethod
    def from_ast_node_dict(cls, node: "ASTNodeDict") -> "KnowledgeGraphNode":
        """Rebuild a ``KnowledgeGraphNode`` wrapping an ``ASTNode`` from dict fields."""
        return cls(
            node_id=node["node_id"],
            node=ASTNode(
                type=node["type"],
                start_line=node["start_line"],
                end_line=node["end_line"],
                text=node["text"],
            ),
        )

    @classmethod
    def from_text_node_dict(cls, node: "TextNodeDict") -> "KnowledgeGraphNode":
        """Rebuild a ``KnowledgeGraphNode`` wrapping a ``TextNode`` from dict fields."""
        return cls(
            node_id=node["node_id"],
            node=TextNode(
                text=node["text"],
                start_line=node["start_line"],
                end_line=node["end_line"],
            ),
        )


class KnowledgeGraphEdgeType(enum.StrEnum):
    """Enum of all knowledge graph edge types"""

    parent_of = "PARENT_OF"  # ASTNode -> ASTNode
    has_file = "HAS_FILE"  # FileNode -> FileNode
    has_ast = "HAS_AST"  # FileNode -> ASTNode
    has_text = "HAS_TEXT"  # FileNode -> TextNode
    next_chunk = "NEXT_CHUNK"  # TextNode -> TextNode
    inherits = "INHERITS"  # ASTNode (subclass) -> ASTNode (superclass)
    imports = "IMPORTS"  # FileNode (importer) -> FileNode (imported module)


@dataclasses.dataclass(frozen=True)
class KnowledgeGraphEdge:
    """An edge in the knowledge graph.

    Attributes:
      source: The source knowledge graph node.
      target: The target knowledge graph node.
      type: The knowledge graph edge type.
    """

    source: KnowledgeGraphNode
    target: KnowledgeGraphNode
    type: KnowledgeGraphEdgeType

    def to_edge_dict(
        self,
    ) -> Union[
        "HasFileEdge",
        "HasASTEdge",
        "ParentOfEdge",
        "HasTextEdge",
        "NextChunkEdge",
        "InheritsEdge",
        "ImportsEdge",
    ]:
        """Convert the KnowledgeGraphEdge into a serializable edge dict."""
        match self.type:
            case KnowledgeGraphEdgeType.has_file:
                return HasFileEdge(
                    source=self.source.to_dict(),
                    target=self.target.to_dict(),
                )
            case KnowledgeGraphEdgeType.has_ast:
                return HasASTEdge(
                    source=self.source.to_dict(),
                    target=self.target.to_dict(),
                )
            case KnowledgeGraphEdgeType.parent_of:
                return ParentOfEdge(
                    source=self.source.to_dict(),
                    target=self.target.to_dict(),
                )
            case KnowledgeGraphEdgeType.has_text:
                return HasTextEdge(
                    source=self.source.to_dict(),
                    target=self.target.to_dict(),
                )
            case KnowledgeGraphEdgeType.next_chunk:
                return NextChunkEdge(
                    source=self.source.to_dict(),
                    target=self.target.to_dict(),
                )
            case KnowledgeGraphEdgeType.inherits:
                return InheritsEdge(
                    source=self.source.to_dict(),
                    target=self.target.to_dict(),
                )
            case KnowledgeGraphEdgeType.imports:
                return ImportsEdge(
                    source=self.source.to_dict(),
                    target=self.target.to_dict(),
                )
            case _:
                raise ValueError(f"Unknown edge type: {self.type}")


###############################################################################
#                         Serializable dict types                             #
###############################################################################


class MetadataNode(TypedDict):
    """Repo-level metadata attached when serializing a graph."""

    codebase_source: str
    local_path: str
    https_url: str
    commit_id: str


class FileNodeDict(TypedDict):
    """Serializable property dict for a ``FileNode``."""

    node_id: int
    basename: str
    relative_path: str


class ASTNodeDict(TypedDict):
    """Serializable property dict for an ``ASTNode``."""

    node_id: int
    type: str
    start_line: int
    end_line: int
    text: str


class TextNodeDict(TypedDict):
    """Serializable property dict for a ``TextNode``."""

    node_id: int
    text: str
    start_line: int
    end_line: int


class HasFileEdge(TypedDict):
    """Serializable edge dict for ``HAS_FILE`` (directory → child file/dir)."""

    source: FileNodeDict
    target: FileNodeDict


class HasASTEdge(TypedDict):
    """Serializable edge dict for ``HAS_AST`` (file → AST root)."""

    source: FileNodeDict
    target: ASTNodeDict


class ParentOfEdge(TypedDict):
    """Serializable edge dict for ``PARENT_OF`` (AST parent → AST child)."""

    source: ASTNodeDict
    target: ASTNodeDict


class HasTextEdge(TypedDict):
    """Serializable edge dict for ``HAS_TEXT`` (file → text chunk)."""

    source: FileNodeDict
    target: TextNodeDict


class NextChunkEdge(TypedDict):
    """Serializable edge dict for ``NEXT_CHUNK`` (text chunk → next chunk)."""

    source: TextNodeDict
    target: TextNodeDict


class InheritsEdge(TypedDict):
    """Serializable edge dict for ``INHERITS`` (subtype AST → supertype AST)."""

    source: ASTNodeDict
    target: ASTNodeDict


class ImportsEdge(TypedDict):
    """Serializable edge dict for ``IMPORTS`` (importer file → imported file)."""

    source: FileNodeDict
    target: FileNodeDict
