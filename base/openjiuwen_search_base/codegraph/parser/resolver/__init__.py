"""Node resolution pipeline -- resolves cross-references between parsed symbols."""

from .indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from .pipeline import resolve_graph
from .types import EdgeType, ResolvedEdge

__all__ = [
    "ClassMethodIndex",
    "EdgeType",
    "ImportIndex",
    "ResolvedEdge",
    "SymbolIndex",
    "resolve_graph",
]
