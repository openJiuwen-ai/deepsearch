"""Resolved edge dataclass and re-export of EdgeType."""

from dataclasses import dataclass

from ..constants import EdgeType

__all__ = ["EdgeType", "ResolvedEdge"]


@dataclass(frozen=True, slots=True)
class ResolvedEdge:
    """A semantic edge discovered during resolution."""

    source_id: str
    target_id: str
    relation: EdgeType
    confidence: float = 1.0
    resolved_by: str = ""
