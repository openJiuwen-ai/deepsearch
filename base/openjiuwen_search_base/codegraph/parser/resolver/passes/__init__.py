"""Resolution passes that produce semantic edges."""

from ._utils import contain_name, match_name
from .calls import resolve_calls
from .decorators import resolve_decorators
from .duck_types import resolve_duck_types
from .imports import resolve_imports
from .indirect_calls import resolve_indirect_calls
from .inheritance import resolve_inheritance
from .inherited_methods import resolve_inherited_methods
from .overrides import resolve_overrides
from .types import resolve_types

__all__ = [
    "contain_name",
    "match_name",
    "resolve_calls",
    "resolve_decorators",
    "resolve_duck_types",
    "resolve_imports",
    "resolve_indirect_calls",
    "resolve_inheritance",
    "resolve_inherited_methods",
    "resolve_overrides",
    "resolve_types",
]
