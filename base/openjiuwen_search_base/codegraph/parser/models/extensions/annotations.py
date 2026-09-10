"""Type-system and annotation node types."""

from dataclasses import dataclass

from ..core import BaseNode


@dataclass(frozen=True, slots=True)
class TypeAliasNode(BaseNode):
    """A type alias (``type X = Y`` or ``X: TypeAlias = Y``)."""

    aliased_type: str = ""


@dataclass(frozen=True, slots=True)
class AnnotationNode(BaseNode):
    """A decorator / annotation that targets another symbol."""

    target: str | None = None


@dataclass(frozen=True, slots=True)
class UnionNode(BaseNode):
    """A union type (C/C++ ``union``)."""

    variants: tuple[str, ...] = ()
