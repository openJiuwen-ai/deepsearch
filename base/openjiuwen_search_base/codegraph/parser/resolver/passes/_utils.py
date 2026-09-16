"""Shared utilities for resolver passes."""

import functools
import re
from collections.abc import Collection

_SCOPE_SUFFIX_RE = re.compile(r"@L\d+@D\d+$")


def _strip_scope(name: str) -> str:
    """Strip @L<line>@D<depth> suffix used for block-scoped local variables."""
    return _SCOPE_SUFFIX_RE.sub("", name)


@functools.cache
def match_name(node_name: str, short: str, qualified: str | None = None) -> bool:
    """Check whether *node_name* equals *short* or *qualified*, tolerating overload and scope suffixes."""
    stripped = _strip_scope(node_name)
    if stripped == short or node_name == short:
        return True
    if qualified is not None:
        if node_name == qualified or stripped == qualified:
            return True
        return node_name.startswith(f"{qualified}(") or stripped.startswith(f"{qualified}(")
    return False


def contain_name(short: str, qualified: str | None, names: Collection[str]) -> bool:
    """Check whether *names* contains *short* or *qualified*, tolerating overload and scope suffixes."""
    if short in names:
        return True
    if any(_strip_scope(n) == short for n in names):
        return True
    if qualified is not None:
        if qualified in names:
            return True
        if any(_strip_scope(n) == qualified for n in names):
            return True
        prefix = f"{qualified}("
        return any(n.startswith(prefix) or _strip_scope(n).startswith(prefix) for n in names)
    return False
