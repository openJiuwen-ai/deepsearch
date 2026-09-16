"""Main resolution orchestrator that runs all passes."""

from collections.abc import Callable

from ..languages import LanguageHooks, get_default_registry
from ..models.core import BaseNode
from ..models.structural import FileNode
from .indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from .passes import (
    resolve_calls,
    resolve_decorators,
    resolve_duck_types,
    resolve_imports,
    resolve_indirect_calls,
    resolve_inheritance,
    resolve_inherited_methods,
    resolve_overrides,
    resolve_types,
)
from .types import ResolvedEdge

try:
    from warnings import filterwarnings

    from tqdm.rich import tqdm
    from tqdm.std import TqdmExperimentalWarning

    filterwarnings("ignore", category=TqdmExperimentalWarning)
except ImportError:
    from tqdm.auto import tqdm


def resolve_graph(
    file_nodes: list[FileNode],
    node_id_fn: Callable[[str, BaseNode], str] | None = None,
    *,
    show_progress: bool = True,
) -> tuple[list[ResolvedEdge], list[dict], list[dict]]:
    """Run all resolution passes and return combined edges plus synthesised nodes."""
    if node_id_fn is None:
        from ..ids import node_id as _node_id

        node_id_fn = _node_id

    registry = get_default_registry()
    hooks_map: dict[str, LanguageHooks] = {}
    for fn in file_nodes:
        if fn.language not in hooks_map:
            hooks_map[fn.language] = registry.get_hooks(fn.language)

    symbol_idx = SymbolIndex(file_nodes, node_id_fn)
    import_idx = ImportIndex(file_nodes, node_id_fn)
    class_method_idx = ClassMethodIndex(file_nodes, node_id_fn)

    all_synth_nodes: list[dict] = []
    all_synth_edges: list[dict] = []
    edges: list[ResolvedEdge] = []
    with tqdm(total=10, desc="Resolving", unit="pass", disable=not show_progress) as progress:
        edges.extend(resolve_imports(file_nodes, symbol_idx, node_id_fn))
        progress.update(1)

        inheritance = resolve_inheritance(file_nodes, symbol_idx, import_idx, node_id_fn, hooks_map)
        edges.extend(inheritance)
        progress.update(1)

        edges.extend(resolve_overrides(file_nodes, inheritance, symbol_idx, node_id_fn))
        progress.update(1)

        edges.extend(resolve_decorators(file_nodes, symbol_idx, import_idx, node_id_fn, hooks_map))
        progress.update(1)

        edges.extend(resolve_calls(file_nodes, symbol_idx, import_idx, class_method_idx, node_id_fn, hooks_map))
        progress.update(1)

        im_nodes, im_edges = resolve_inherited_methods(
            file_nodes,
            symbol_idx,
            import_idx,
            class_method_idx,
            node_id_fn,
        )
        all_synth_nodes.extend(im_nodes)
        all_synth_edges.extend(im_edges)
        progress.update(1)

        # Second calls pass: picks up guessed methods added by
        # resolve_inherited_methods (e.g. SmartCache.get from dict).
        seen = {(e.source_id, e.target_id, e.relation) for e in edges}
        for edge in resolve_calls(file_nodes, symbol_idx, import_idx, class_method_idx, node_id_fn, hooks_map):
            key = (edge.source_id, edge.target_id, edge.relation)
            if key not in seen:
                edges.append(edge)
                seen.add(key)
        progress.update(1)

        # Indirect calls: resolve method calls on subscripted/dereferenced
        # receivers (e.g. objects[i]->method(), self.items[0].update()).
        for edge in resolve_indirect_calls(
            file_nodes, symbol_idx, import_idx, class_method_idx, node_id_fn, hooks_map, seen
        ):
            key = (edge.source_id, edge.target_id, edge.relation)
            if key not in seen:
                edges.append(edge)
                seen.add(key)
        progress.update(1)

        edges.extend(resolve_types(file_nodes, symbol_idx, import_idx, node_id_fn, hooks_map))
        progress.update(1)

        dt_edges, dt_nodes, dt_edges_synth = resolve_duck_types(
            file_nodes,
            class_method_idx,
            symbol_idx,
            import_idx,
            node_id_fn,
            hooks_map,
        )
        edges.extend(dt_edges)
        all_synth_nodes.extend(dt_nodes)
        all_synth_edges.extend(dt_edges_synth)
        progress.update(1)

    return edges, all_synth_nodes, all_synth_edges
