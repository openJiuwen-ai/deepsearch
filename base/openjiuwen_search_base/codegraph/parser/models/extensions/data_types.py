"""Data structure node types that affect runtime layout (enums, structs, macros)."""

from dataclasses import dataclass

from ..core import BaseNode, PropertyNode


@dataclass(frozen=True, slots=True)
class EnumNode(BaseNode):
    """An enumeration type."""

    members: tuple[str, ...] = ()
    bases: tuple[str, ...] = ()

    @property
    def signature(self) -> str:
        """Produce e.g. ``enum Color(Enum)``."""
        return f"enum {self.name}"


@dataclass(frozen=True, slots=True)
class StructNode(BaseNode):
    """A struct (C, Go, Rust, etc.)."""

    fields: tuple[PropertyNode, ...] = ()
    bases: tuple[str, ...] = ()

    @property
    def signature(self) -> str:
        """Produce e.g. ``struct Point``."""
        return f"struct {self.name}"


@dataclass(frozen=True, slots=True)
class MacroNode(BaseNode):
    """A preprocessor macro definition."""

    parameters: tuple[str, ...] = ()
    expansion: str = ""

    @property
    def signature(self) -> str:
        """Produce e.g. ``#define MAX(a, b)`` or ``#define PI``."""
        if self.parameters:
            return f"#define {self.name}({', '.join(self.parameters)})"
        return f"#define {self.name}"
