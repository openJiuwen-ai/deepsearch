"""Duck type resolution pass: EXPECTS, IMPLEMENTS, IS_SUBSET_OF, and synthesis."""

import os
from collections import defaultdict
from collections.abc import Callable

from ...constants import NodeType
from ...languages import LanguageHooks
from ...models.core import BaseNode, ClassNode, DuckTypeNode, FunctionNode, ImportNode, InterfaceNode
from ...models.structural import FileNode
from ..indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from ..types import EdgeType, ResolvedEdge


def resolve_duck_types(
    file_nodes: list[FileNode],
    class_method_index: ClassMethodIndex,
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    hooks_map: dict[str, LanguageHooks],
) -> tuple[list[ResolvedEdge], list[dict], list[dict]]:
    """Resolve duck type relationships globally.

    Returns (edges, synthesised_node_dicts, synthesised_containment_edges).
    """
    dt_by_methods: dict[frozenset[str], tuple[str, DuckTypeNode, str]] = {}
    dt_name_to_id: dict[str, str] = {}

    for fnode in file_nodes:
        fp = fnode.path
        for child in fnode.children:
            _collect_duck_types(child, fp, node_id_fn, dt_by_methods, dt_name_to_id)

    all_dts = [(nid, dt, src) for nid, dt, src in dt_by_methods.values()]

    edges: list[ResolvedEdge] = []

    # EXPECTS edges
    for fnode in file_nodes:
        fp = fnode.path
        _emit_expects(fnode, fp, node_id_fn, dt_name_to_id, edges)

    # Build file reachability and emit scoped IMPLEMENTS
    file_graph = _build_file_import_graph(file_nodes, symbol_index, import_index, hooks_map)
    _emit_implements_scoped(
        all_dts,
        class_method_index,
        file_graph,
        file_nodes,
        node_id_fn,
        edges,
    )

    # IS_SUBSET_OF edges between existing duck types
    _emit_subset(all_dts, edges)

    # Synthesize intermediate duck types
    synth_nodes, synth_edges, new_dts = _synthesize_intermediates(all_dts, dt_by_methods)

    for new_id, new_dt, _ in new_dts:
        for old_id, old_dt, _ in all_dts:
            if new_dt.methods < old_dt.methods:
                edges.append(
                    ResolvedEdge(
                        source_id=new_id,
                        target_id=old_id,
                        relation=EdgeType.IS_SUBSET_OF,
                        resolved_by="intersection_subset",
                    )
                )
            if old_dt.methods < new_dt.methods:
                edges.append(
                    ResolvedEdge(
                        source_id=old_id,
                        target_id=new_id,
                        relation=EdgeType.IS_SUBSET_OF,
                        resolved_by="intersection_subset",
                    )
                )

    # IMPLEMENTS for synthesized types (scoped to all files for simplicity)
    _emit_implements_scoped(
        new_dts,
        class_method_index,
        file_graph,
        file_nodes,
        node_id_fn,
        edges,
    )

    return edges, synth_nodes, synth_edges


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _collect_duck_types(
    node: BaseNode,
    file_path: str,
    node_id_fn: Callable[[str, BaseNode], str],
    dt_by_methods: dict[frozenset[str], tuple[str, DuckTypeNode, str]],
    dt_name_to_id: dict[str, str],
) -> None:
    """Recursively collect DuckTypeNodes, storing (id, node, source_file_path)."""
    if isinstance(node, DuckTypeNode):
        nid = node_id_fn(file_path, node)
        if node.methods not in dt_by_methods:
            dt_by_methods[node.methods] = (nid, node, file_path)
        dt_name_to_id[node.name] = dt_by_methods[node.methods][0]
    for child in node.children:
        _collect_duck_types(child, file_path, node_id_fn, dt_by_methods, dt_name_to_id)


# ---------------------------------------------------------------------------
# EXPECTS
# ---------------------------------------------------------------------------


def _emit_expects(
    node: BaseNode,
    file_path: str,
    node_id_fn: Callable[[str, BaseNode], str],
    dt_name_to_id: dict[str, str],
    edges: list[ResolvedEdge],
) -> None:
    """Emit EXPECTS edges from functions to their duck type refs."""
    if isinstance(node, FunctionNode) and node.duck_type_refs:
        fn_id = node_id_fn(file_path, node)
        for dt_name in node.duck_type_refs:
            dt_id = dt_name_to_id.get(dt_name)
            if dt_id:
                edges.append(
                    ResolvedEdge(
                        source_id=fn_id,
                        target_id=dt_id,
                        relation=EdgeType.EXPECTS,
                        resolved_by="duck_type_ref",
                    )
                )
    for child in node.children:
        _emit_expects(child, file_path, node_id_fn, dt_name_to_id, edges)


# ---------------------------------------------------------------------------
# Import-chain file reachability
# ---------------------------------------------------------------------------


def _build_file_import_graph(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    hooks_map: dict[str, LanguageHooks],
) -> dict[str, set[str]]:
    """Build a directed graph: source_file -> set of imported file paths.

    Uses resolved imports to map import names to their definition file paths.
    Also adds implicit edges to package init files (``__init__.py``,
    ``index.ts``, etc.) when an import target lives inside a package directory.
    """
    graph: dict[str, set[str]] = defaultdict(set)

    # Collect all init files from all hooks
    all_init_files: set[str] = set()
    _default = LanguageHooks()
    for fnode in file_nodes:
        hooks = hooks_map.get(fnode.language, _default)
        all_init_files.update(hooks.package_init_files)

    # dir_path -> init file path (for package-init linking)
    dir_to_init: dict[str, str] = {}
    for fnode in file_nodes:
        basename = os.path.basename(fnode.path)
        if basename in all_init_files:
            dir_to_init[os.path.dirname(fnode.path)] = fnode.path

    for fnode in file_nodes:
        fp = fnode.path
        hooks = hooks_map.get(fnode.language, _default)
        for child in fnode.children:
            if not isinstance(child, ImportNode):
                continue
            names = list(child.names) if child.names else []
            if child.alias:
                names = [child.alias]
            if not names and child.module:
                names = [child.module.rsplit(".", maxsplit=1)[-1]]

            for name in names:
                imp = import_index.resolve_name(fp, name)
                if imp is None:
                    continue
                _module, original_name, _imp_id = imp
                candidates = symbol_index.lookup(original_name)
                for target_id, _node in candidates:
                    target_file = target_id.split("::")[0]
                    if target_file and target_file != fp:
                        graph[fp].add(target_file)
                        if hooks.implicit_package_loading:
                            _add_package_init_edges(
                                fp,
                                target_file,
                                dir_to_init,
                                graph,
                            )

    return dict(graph)


def _add_package_init_edges(
    source_fp: str,
    target_fp: str,
    dir_to_init: dict[str, str],
    graph: dict[str, set[str]],
) -> None:
    """Walk up from *target_fp*'s directory, adding edges to any init files found."""
    d = os.path.dirname(target_fp)
    while d:
        init = dir_to_init.get(d)
        if init and init != source_fp:
            graph[source_fp].add(init)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent


def _reachable_files(start: str, graph: dict[str, set[str]]) -> set[str]:
    """BFS to find all files reachable from *start* via imports (multi-hop)."""
    visited: set[str] = set()
    queue = [start]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for neighbor in graph.get(current, set()):
            if neighbor not in visited:
                queue.append(neighbor)
    return visited


# ---------------------------------------------------------------------------
# IMPLEMENTS (import-chain scoped, superset-deduplicated)
# ---------------------------------------------------------------------------


def _unqualify_methods(methods: set[str]) -> set[str]:
    """Strip ``ClassName.`` prefix from qualified method names."""
    result: set[str] = set()
    for m in methods:
        result.add(m.rsplit(".", maxsplit=1)[-1] if "." in m else m)
    return result


def _emit_implements_scoped(
    duck_types: list[tuple[str, DuckTypeNode, str]],
    class_method_index: ClassMethodIndex,
    file_graph: dict[str, set[str]],
    file_nodes: list[FileNode],
    node_id_fn: Callable[[str, BaseNode], str],
    edges: list[ResolvedEdge],
) -> None:
    """Emit IMPLEMENTS edges, scoped to import-reachable files.

    For each duck type:
      1. Compute which files are reachable from the duck type's source file.
      2. Only consider classes defined in those files.
      3. Deduplicate: if a class implements both DT_big and DT_small where
         DT_small ⊂ DT_big, skip the IMPLEMENTS edge to DT_small since
         the IS_SUBSET_OF edge already connects them.
    """
    fp_to_fnode: dict[str, FileNode] = {f.path: f for f in file_nodes}

    # Group duck types by source file for efficient reachability computation
    by_source: dict[str, list[tuple[str, DuckTypeNode]]] = defaultdict(list)
    for dt_id, dt_node, src_file in duck_types:
        by_source[src_file].append((dt_id, dt_node))

    # Per-class accumulator: class_id -> list of (dt_id, dt_methods)
    class_dt_matches: dict[str, list[tuple[str, frozenset[str]]]] = defaultdict(list)

    for src_file, dts_in_file in by_source.items():
        reachable = _reachable_files(src_file, file_graph)

        for reachable_fp in reachable:
            fnode = fp_to_fnode.get(reachable_fp)
            if fnode is None:
                continue
            for child in fnode.children:
                if not isinstance(child, (ClassNode, InterfaceNode)):
                    continue
                cid = node_id_fn(reachable_fp, child)
                child_methods = _unqualify_methods(class_method_index.get_methods(child.name))
                if not child_methods:
                    continue

                for dt_id, dt_node in dts_in_file:
                    if not dt_node.methods:
                        continue
                    if dt_node.methods <= child_methods:
                        class_dt_matches[cid].append((dt_id, dt_node.methods))

    # Deduplicate: for each class, only keep maximal duck types
    for cid, matches in class_dt_matches.items():
        maximal = _keep_maximal(matches)
        for dt_id, _ in maximal:
            edges.append(
                ResolvedEdge(
                    source_id=cid,
                    target_id=dt_id,
                    relation=EdgeType.IMPLEMENTS,
                    confidence=1.0,
                    resolved_by="structural_match",
                )
            )


def _keep_maximal(
    matches: list[tuple[str, frozenset[str]]],
) -> list[tuple[str, frozenset[str]]]:
    """Filter out duck types whose method set is a strict subset of another matched duck type."""
    result: list[tuple[str, frozenset[str]]] = []
    for dt_id, methods in matches:
        is_subset = any(methods < other_methods for other_id, other_methods in matches if other_id != dt_id)
        if not is_subset:
            result.append((dt_id, methods))
    return result


# ---------------------------------------------------------------------------
# IS_SUBSET_OF
# ---------------------------------------------------------------------------


def _emit_subset(
    duck_types: list[tuple[str, DuckTypeNode, str]],
    edges: list[ResolvedEdge],
) -> None:
    """Emit IS_SUBSET_OF edges between duck types with strict subset method sets."""
    for i, (id_a, dt_a, _) in enumerate(duck_types):
        for j, (id_b, dt_b, _) in enumerate(duck_types):
            if i == j:
                continue
            if dt_a.methods < dt_b.methods:
                edges.append(
                    ResolvedEdge(
                        source_id=id_a,
                        target_id=id_b,
                        relation=EdgeType.IS_SUBSET_OF,
                        resolved_by="method_subset",
                    )
                )


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


def _synthesize_intermediates(
    existing: list[tuple[str, DuckTypeNode, str]],
    seen_methods: dict[frozenset[str], tuple[str, DuckTypeNode, str]],
) -> tuple[list[dict], list[dict], list[tuple[str, DuckTypeNode, str]]]:
    """Synthesize new intermediate duck types from intersections of existing ones.

    Only creates intermediates with >= 2 methods that don't already exist.
    """
    new_nodes: list[dict] = []
    new_edges: list[dict] = []
    new_dts: list[tuple[str, DuckTypeNode, str]] = []
    already_seen: set[frozenset[str]] = set(seen_methods.keys())

    for i, (_id_a, dt_a, _) in enumerate(existing):
        for j, (_id_b, dt_b, _) in enumerate(existing):
            if i >= j:
                continue
            intersection = dt_a.methods & dt_b.methods
            if len(intersection) < 2:
                continue
            if intersection in already_seen:
                continue
            if intersection == dt_a.methods or intersection == dt_b.methods:
                continue

            already_seen.add(intersection)
            dt_name = "DuckType{" + ", ".join(sorted(intersection)) + "}"
            synth_id = f"synth::duck_type::{dt_name}"

            synth_node = DuckTypeNode(
                node_type=NodeType.DUCK_TYPE,
                name=dt_name,
                span=(0, 0, 0, 0),
                methods=intersection,
            )

            node_dict = {
                "id": synth_id,
                "type": "DuckTypeNode",
                "name": dt_name,
                "node_type": NodeType.DUCK_TYPE.value,
                "path": "",
                "span": [0, 0, 0, 0],
                "methods": sorted(intersection),
                "tags": ["cat:core", "type:duck_type"],
            }
            new_nodes.append(node_dict)
            new_dts.append((synth_id, synth_node, ""))

    return new_nodes, new_edges, new_dts
