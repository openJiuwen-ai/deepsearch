"""Lightweight type aliases shared across the parser package."""

from dataclasses import dataclass
from typing import NamedTuple, Protocol, runtime_checkable


class SourceSpan(NamedTuple):
    """Half-open source range (1-indexed lines, 0-indexed columns)."""

    line_start: int
    line_end: int
    col_start: int = 0
    col_end: int = 0


@dataclass(frozen=True, slots=True)
class Parameter:
    """A function parameter with optional type info."""

    name: str
    type_annotation: str | None = None
    default: str | None = None


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    """Describes a detected module/package in a folder."""

    name: str
    language: str
    path: str


@runtime_checkable
class SignatureProvider(Protocol):
    """Nodes that can produce a one-line signature for chunking/embedding."""

    @property
    def signature(self) -> str:
        ...
