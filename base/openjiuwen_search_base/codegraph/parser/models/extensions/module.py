"""Module / namespace node type."""

from dataclasses import dataclass

from ..core import BaseNode


@dataclass(frozen=True, slots=True)
class ModuleNode(BaseNode):
    """A named module or namespace."""

    exports: tuple[str, ...] = ()
