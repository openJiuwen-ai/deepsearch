"""openjiuwen_search_base.codegraph.parser -- public surface of the parsing library."""

from .chunker import Chunk, ChunkEdge, chunks_from_file, chunks_from_file_nodes
from .constants import FILENAME_PATTERN, EdgeType, NodeType, detect_language
from .custom_types import ModuleInfo, Parameter, SignatureProvider, SourceSpan
from .languages import BaseLanguageParser, LanguageRegistry, get_default_registry, register_builtins
from .loader import parse_file, parse_files
from .models import (
    AnnotationNode,
    BaseNode,
    CallNode,
    ClassNode,
    CodeBlockNode,
    DuckTypeNode,
    EnumNode,
    FileNode,
    FolderNode,
    FunctionNode,
    ImportNode,
    InterfaceNode,
    ModuleNode,
    PropertyNode,
    StructNode,
    TypeAliasNode,
    UnionNode,
)
from .resolver import ResolvedEdge, resolve_graph

__all__ = [
    # API
    "parse_file",
    "parse_files",
    "chunks_from_file",
    "chunks_from_file_nodes",
    "Chunk",
    "ChunkEdge",
    # Registry
    "BaseLanguageParser",
    "LanguageRegistry",
    "get_default_registry",
    "register_builtins",
    # Enums / types
    "NodeType",
    "EdgeType",
    "ModuleInfo",
    "Parameter",
    "SignatureProvider",
    "SourceSpan",
    "FILENAME_PATTERN",
    "detect_language",
    # Models
    "BaseNode",
    "CallNode",
    "ClassNode",
    "CodeBlockNode",
    "DuckTypeNode",
    "FunctionNode",
    "ImportNode",
    "InterfaceNode",
    "PropertyNode",
    "FileNode",
    "FolderNode",
    "EnumNode",
    "StructNode",
    "ModuleNode",
    "TypeAliasNode",
    "AnnotationNode",
    "UnionNode",
    # Resolver
    "ResolvedEdge",
    "resolve_graph",
]
