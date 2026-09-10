"""DECORATED_BY and METACLASS edge resolution pass."""

import re
from collections.abc import Callable

from ...constants import FILTER_BUILTIN_NAMES
from ...languages import LanguageHooks
from ...models.core import BaseNode, ClassNode, FunctionNode
from ...models.structural import FileNode
from ..indexes import ImportIndex, SymbolIndex
from ..types import EdgeType, ResolvedEdge

_DECORATOR_NAME_RE = re.compile(r"^@?([A-Za-z_][\w.]*)")


def _strip_decorator(raw: str) -> str:
    """Extract the callable name from a decorator string, e.g. ``@lru_cache(maxsize=128)`` -> ``lru_cache``."""
    m = _DECORATOR_NAME_RE.match(raw)
    if m:
        name = m.group(1)
        return name.split(".")[-1]
    return raw.lstrip("@").split("(")[0].split(".")[-1]


def resolve_decorators(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    hooks_map: dict[str, LanguageHooks],
) -> list[ResolvedEdge]:
    """Resolve decorators and metaclasses to their definitions."""
    edges: list[ResolvedEdge] = []
    _default = LanguageHooks()

    for fnode in file_nodes:
        hooks = hooks_map.get(fnode.language, _default)
        if not hooks.supports_decorators:
            continue
        fp = fnode.path
        for child in fnode.children:
            if isinstance(child, (ClassNode, FunctionNode)) and hasattr(child, "decorators"):
                child_id = node_id_fn(fp, child)
                for raw_dec in child.decorators:
                    dec_name = _strip_decorator(raw_dec)
                    if dec_name in hooks.builtins and not symbol_index.lookup(dec_name) and FILTER_BUILTIN_NAMES:
                        continue
                    target_id = _resolve_name(fp, dec_name, symbol_index, import_index)
                    if target_id is not None:
                        edges.append(
                            ResolvedEdge(
                                source_id=child_id,
                                target_id=target_id,
                                relation=EdgeType.DECORATED_BY,
                                confidence=1.0,
                                resolved_by="decorator_match",
                            )
                        )

            if isinstance(child, ClassNode) and child.metaclass and hooks.supports_metaclass:
                child_id = node_id_fn(fp, child)
                mc_name = child.metaclass
                if not (mc_name in hooks.builtins and not symbol_index.lookup(mc_name) and FILTER_BUILTIN_NAMES):
                    target_id = _resolve_name(fp, mc_name, symbol_index, import_index)
                    if target_id is not None:
                        edges.append(
                            ResolvedEdge(
                                source_id=child_id,
                                target_id=target_id,
                                relation=EdgeType.METACLASS,
                                confidence=1.0,
                                resolved_by="metaclass_match",
                            )
                        )

    return edges


def _resolve_name(
    file_path: str,
    name: str,
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
) -> str | None:
    """Resolve a name via import index then symbol index."""
    imp = import_index.resolve_name(file_path, name)
    if imp is not None:
        _module, original_name, _imp_id = imp
        candidates = symbol_index.lookup(original_name)
        if candidates:
            return candidates[0][0]

    candidates = symbol_index.lookup(name)
    if candidates:
        return candidates[0][0]

    return None
