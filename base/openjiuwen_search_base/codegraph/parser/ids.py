"""Stable node / folder ID helpers shared by graph export and the chunker."""

from .constants import NodeType
from .models.core import BaseNode, CodeBlockNode, FunctionNode


def node_id(file_path: str, node: BaseNode) -> str:
    """Generate a unique ID for a node.

    Format: ``{file_path}::{qualified_name}@L{line}``

    Lambda nodes already embed ``@L{line}@C{col}`` in their name, so the
    ID is ``{file_path}::{name}`` with no extra owner prefix or line suffix.
    """
    if isinstance(node, CodeBlockNode):
        return f"{file_path}::__code_block_L{node.span.line_start}"
    if isinstance(node, FunctionNode) and node.func_type == "lambda":
        return f"{file_path}::{node.name}"
    owner = getattr(node, "owner", None)
    name = f"{owner}.{node.name}" if owner else node.name
    if node.node_type is NodeType.TYPE_ALIAS:
        return f"{file_path}::{name}[type_alias]@L{node.span.line_start}"
    return f"{file_path}::{name}@L{node.span.line_start}"


def folder_id(rel_path: str) -> str:
    """ID for a synthesised folder node."""
    return f"folder::{rel_path}" if rel_path else "folder::."
