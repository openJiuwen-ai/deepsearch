"""Shared tree-sitter helpers used by multiple language parsers."""

from tree_sitter import Node

from ..constants import MAX_AST_DEPTH
from ..custom_types import SourceSpan


def text(node: Node) -> str:
    """Return the UTF-8 text of a tree-sitter node."""
    if not node.text:
        return ""
    try:
        return node.text.decode("utf-8")
    except UnicodeDecodeError:
        return node.text.decode("utf-8", errors="replace")


def span(node: Node) -> SourceSpan:
    """Convert a tree-sitter node's position to a :class:`SourceSpan`."""
    return SourceSpan(
        line_start=node.start_point.row + 1,
        line_end=node.end_point.row + 1,
        col_start=node.start_point.column,
        col_end=node.end_point.column,
    )


def first_child_of_type(node: Node, *types: str) -> Node | None:
    """Return the first child whose ``type`` is in *types*, or ``None``."""
    for c in node.children:
        if c.type in types:
            return c
    return None


def children_of_type(node: Node, *types: str) -> list[Node]:
    """Return all children whose ``type`` is in *types*."""
    return [c for c in node.children if c.type in types]


def has_child_type(node: Node, type_name: str) -> bool:
    """Return whether *node* has any child with the given type."""
    return any(c.type == type_name for c in node.children)


def complexity(node: Node) -> int:
    """Compute a simple cyclomatic-complexity proxy for a function body."""
    _branch = frozenset(
        {
            "if_statement",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "catch_clause",
            "switch_case",
            "ternary_expression",
        }
    )
    count = 1

    def _walk(n: Node, depth: int = 0) -> None:
        nonlocal count
        if depth > MAX_AST_DEPTH:
            return
        if n.type in _branch:
            count += 1
        for child in n.children:
            _walk(child, depth + 1)

    _walk(node)
    return count


def unwrap_exports(root: Node) -> list[Node]:
    """Yield effective top-level children, unwrapping ``export_statement`` wrappers."""
    result: list[Node] = []
    for child in root.children:
        if child.type == "export_statement":
            inner = [c for c in child.children if c.type not in ("export", "default", ";")]
            if inner:
                result.extend(inner)
            else:
                result.append(child)
        else:
            result.append(child)
    return result
