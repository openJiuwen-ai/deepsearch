# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Node / edge value types for the in-memory Retropus knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, TypedDict, Union


@dataclass(frozen=True)
class FileNode:
    """Filesystem entry (file or directory) relative to the repo root."""

    basename: str
    relative_path: str


@dataclass(frozen=True)
class ASTNode:
    """One tree-sitter syntax node with 1-based inclusive line bounds."""

    type: str
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True)
class TextNode:
    """A contiguous text chunk (markdown / plain text) with line bounds."""

    text: str
    start_line: int
    end_line: int


Payload = Union[FileNode, ASTNode, TextNode]


@dataclass(frozen=True)
class KnowledgeGraphNode:
    """Graph vertex: stable integer id + typed payload."""

    node_id: int
    node: Payload

    def to_dict(self) -> Union["FileNodeDict", "ASTNodeDict", "TextNodeDict"]:
        """Flatten into a JSON-serializable property dict."""
        serializer = _NODE_SERIALIZERS.get(type(self.node))
        if serializer is None:
            raise ValueError(f"unsupported payload type: {type(self.node)!r}")
        return serializer(self)

    @classmethod
    def from_file_node_dict(cls, data: "FileNodeDict") -> "KnowledgeGraphNode":
        return cls(
            node_id=data["node_id"],
            node=FileNode(basename=data["basename"], relative_path=data["relative_path"]),
        )

    @classmethod
    def from_ast_node_dict(cls, data: "ASTNodeDict") -> "KnowledgeGraphNode":
        return cls(
            node_id=data["node_id"],
            node=ASTNode(
                type=data["type"],
                start_line=data["start_line"],
                end_line=data["end_line"],
                text=data["text"],
            ),
        )

    @classmethod
    def from_text_node_dict(cls, data: "TextNodeDict") -> "KnowledgeGraphNode":
        return cls(
            node_id=data["node_id"],
            node=TextNode(
                text=data["text"],
                start_line=data["start_line"],
                end_line=data["end_line"],
            ),
        )


class KnowledgeGraphEdgeType(StrEnum):
    """Directed relationship labels between knowledge-graph nodes."""

    parent_of = "PARENT_OF"  # AST → AST
    has_file = "HAS_FILE"  # dir File → child File
    has_ast = "HAS_AST"  # File → AST root
    has_text = "HAS_TEXT"  # File → Text chunk
    next_chunk = "NEXT_CHUNK"  # Text → Text
    inherits = "INHERITS"  # subtype AST → supertype AST
    imports = "IMPORTS"  # importer File → imported File


@dataclass(frozen=True)
class KnowledgeGraphEdge:
    """Directed edge between two ``KnowledgeGraphNode`` vertices."""

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
        """Serialize as a typed edge dict (source/target already flattened)."""
        builder = _EDGE_SERIALIZERS.get(self.type)
        if builder is None:
            raise ValueError(f"unknown edge type: {self.type}")
        return builder(self.source.to_dict(), self.target.to_dict())


def _serialize_file(kg: KnowledgeGraphNode) -> "FileNodeDict":
    n = kg.node
    if not isinstance(n, FileNode):
        raise TypeError(f"expected FileNode payload, got {type(n)!r}")
    return FileNodeDict(
        node_id=kg.node_id,
        basename=n.basename,
        relative_path=n.relative_path,
    )


def _serialize_ast(kg: KnowledgeGraphNode) -> "ASTNodeDict":
    n = kg.node
    if not isinstance(n, ASTNode):
        raise TypeError(f"expected ASTNode payload, got {type(n)!r}")
    return ASTNodeDict(
        node_id=kg.node_id,
        type=n.type,
        start_line=n.start_line,
        end_line=n.end_line,
        text=n.text,
    )


def _serialize_text(kg: KnowledgeGraphNode) -> "TextNodeDict":
    n = kg.node
    if not isinstance(n, TextNode):
        raise TypeError(f"expected TextNode payload, got {type(n)!r}")
    return TextNodeDict(
        node_id=kg.node_id,
        text=n.text,
        start_line=n.start_line,
        end_line=n.end_line,
    )


_NODE_SERIALIZERS: dict[type, Callable[[KnowledgeGraphNode], object]] = {
    FileNode: _serialize_file,
    ASTNode: _serialize_ast,
    TextNode: _serialize_text,
}


def _edge_has_file(src, tgt) -> "HasFileEdge":
    return HasFileEdge(source=src, target=tgt)


def _edge_has_ast(src, tgt) -> "HasASTEdge":
    return HasASTEdge(source=src, target=tgt)


def _edge_parent_of(src, tgt) -> "ParentOfEdge":
    return ParentOfEdge(source=src, target=tgt)


def _edge_has_text(src, tgt) -> "HasTextEdge":
    return HasTextEdge(source=src, target=tgt)


def _edge_next_chunk(src, tgt) -> "NextChunkEdge":
    return NextChunkEdge(source=src, target=tgt)


def _edge_inherits(src, tgt) -> "InheritsEdge":
    return InheritsEdge(source=src, target=tgt)


def _edge_imports(src, tgt) -> "ImportsEdge":
    return ImportsEdge(source=src, target=tgt)


_EDGE_SERIALIZERS = {
    KnowledgeGraphEdgeType.has_file: _edge_has_file,
    KnowledgeGraphEdgeType.has_ast: _edge_has_ast,
    KnowledgeGraphEdgeType.parent_of: _edge_parent_of,
    KnowledgeGraphEdgeType.has_text: _edge_has_text,
    KnowledgeGraphEdgeType.next_chunk: _edge_next_chunk,
    KnowledgeGraphEdgeType.inherits: _edge_inherits,
    KnowledgeGraphEdgeType.imports: _edge_imports,
}


# ---- Wire / persistence shapes (unchanged field names for dump compatibility) ----


class MetadataNode(TypedDict):
    """Repo-level metadata attached when serializing a graph."""

    codebase_source: str
    local_path: str
    https_url: str
    commit_id: str


class FileNodeDict(TypedDict):
    node_id: int
    basename: str
    relative_path: str


class ASTNodeDict(TypedDict):
    node_id: int
    type: str
    start_line: int
    end_line: int
    text: str


class TextNodeDict(TypedDict):
    node_id: int
    text: str
    start_line: int
    end_line: int


class HasFileEdge(TypedDict):
    source: FileNodeDict
    target: FileNodeDict


class HasASTEdge(TypedDict):
    source: FileNodeDict
    target: ASTNodeDict


class ParentOfEdge(TypedDict):
    source: ASTNodeDict
    target: ASTNodeDict


class HasTextEdge(TypedDict):
    source: FileNodeDict
    target: TextNodeDict


class NextChunkEdge(TypedDict):
    source: TextNodeDict
    target: TextNodeDict


class InheritsEdge(TypedDict):
    source: ASTNodeDict
    target: ASTNodeDict


class ImportsEdge(TypedDict):
    source: FileNodeDict
    target: FileNodeDict
