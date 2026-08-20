"""Build Jiuwen graphs and export them through storage backends."""

import dataclasses
from os.path import commonpath
from pathlib import Path

from ..backends import ExportArtifacts, LadybugWriteConfig, build_file_records, write_jsonl_jcp, write_ladybug_graph
from .constants import INTERNAL_NODES, EdgeType, NodeType, detect_language
from .custom_types import SignatureProvider
from .ids import folder_id as _folder_id
from .ids import node_id as _node_id
from .languages import get_default_registry
from .loader import parse_files
from .models.core import BaseNode, CodeBlockNode, FunctionNode
from .models.structural import FileNode
from .resolver import ResolvedEdge, resolve_graph

try:
    from warnings import filterwarnings

    from tqdm.rich import tqdm
    from tqdm.std import TqdmExperimentalWarning

    filterwarnings("ignore", category=TqdmExperimentalWarning)
except ImportError:
    from tqdm.auto import tqdm


def _module_id(rel_path: str) -> str:
    """ID for a synthesised module node."""
    return f"module::{rel_path}" if rel_path else "module::."


def _relative_to_root(file_path: str | Path, root: str | Path) -> Path:
    """Return *file_path* relative to *root*, never as an absolute path.

    ``Path.relative_to`` is lexical, so a resolved root (as in
    ``export_graph_to_backends``) can fail against an unresolved file path
    even when they are the same directory after following symlinks — macOS
    ``/var`` vs ``/private/var`` is the usual case.  A basename fallback
    keeps folder synthesis under the synthetic project root.
    """
    path = Path(file_path)
    root_path = Path(root)
    for child, base in ((path, root_path), (path.resolve(), root_path.resolve())):
        try:
            rel = child.relative_to(base)
        except ValueError:
            continue
        if not rel.is_absolute():
            return rel
    return Path(path.name)


def _ancestor_folder_rels(folder_rel: str) -> tuple[str, ...]:
    """Parent folder keys from the nearest ancestor up to ``""``.

    *folder_rel* is a root-relative key.  Absolute paths are not walked; only
    the synthetic root is returned so we never climb to the filesystem root.
    """
    if not folder_rel:
        return ()
    path = Path(folder_rel)
    if path.is_absolute():
        return ("",)
    parts = path.parts
    ancestors = [str(Path(*parts[:i])) for i in range(len(parts) - 1, 0, -1)]
    ancestors.append("")
    return tuple(ancestors)


def _code_block_name(file_path: str, line_start: int, root: str | None) -> str:
    """Build a stable path-and-line display name for a code block."""
    path = _relative_to_root(file_path, root) if root else Path(Path(file_path).name)
    return f"{path.as_posix()}@L{line_start}"


_STRUCTURAL_TYPES = frozenset({"folder", "file", "module"})
_CORE_TYPES = frozenset({"class", "duck_type", "interface", "function", "property", "code_block"})


def _build_tags(node_type: str, file_path: str, root: str | None, node: BaseNode) -> list[str]:
    """Build a list of tags for filtering in the viewer."""
    tags: list[str] = []

    if node_type in _STRUCTURAL_TYPES:
        tags.append("cat:structural")
    elif node_type in _CORE_TYPES:
        tags.append("cat:core")
    else:
        tags.append("cat:language-specific")

    tags.append(f"type:{node_type}")

    lang = getattr(node, "language", None)
    if lang:
        tags.append(f"lang:{lang}")
    elif file_path:
        detected = detect_language(Path(file_path).name)
        if detected:
            tags.append(f"lang:{detected}")

    if root and file_path:
        dir_parts = _relative_to_root(file_path, root).parts[:-1]
        if dir_parts:
            tags.append(f"dir:{'/'.join(dir_parts)}")

    if isinstance(node, FunctionNode) and node.func_type == "method-guessed":
        tags.append("guessed")

    return tags


def _node_to_dict(node: BaseNode, file_path: str, root: str | None = None) -> dict:
    """Serialize a node to a dict suitable for JSONL output."""
    nid = _node_id(file_path, node)
    name = _code_block_name(file_path, node.span.line_start, root) if isinstance(node, CodeBlockNode) else node.name
    d: dict = {
        "id": nid,
        "type": type(node).__name__,
        "name": name,
        "node_type": node.node_type.value,
        "path": file_path,
        "span": list(node.span),
    }
    if isinstance(node, SignatureProvider):
        d["signature"] = node.signature
    if node.docstring:
        d["docstring"] = node.docstring

    for f in dataclasses.fields(node):
        if f.name in ("node_type", "name", "span", "docstring", "source", "children"):
            continue
        val = getattr(node, f.name)
        if val is None or val == () or val == frozenset():
            continue
        if isinstance(val, frozenset):
            val = sorted(val)
        elif isinstance(val, tuple) and val and dataclasses.is_dataclass(val[0]):
            val = [dataclasses.asdict(item) for item in val]
        d[f.name] = val

    d["tags"] = _build_tags(d["node_type"], file_path, root, node)
    return d


def _walk_edges(
    parent_id: str,
    node: BaseNode,
    file_path: str,
    nodes_out: list[dict],
    edges_out: list[dict],
    *,
    root: str | None = None,
) -> None:
    """Recursively walk children, emitting nodes and CONTAINS edges.

    Node types in ``INTERNAL_NODES`` (intermediate date) are consumed by the resolver and excluded from output graph.
    """
    for child in node.children:
        if child.node_type in INTERNAL_NODES:
            continue
        child_id = _node_id(file_path, child)
        nodes_out.append(_node_to_dict(child, file_path, root))
        edges_out.append({"source": parent_id, "target": child_id, "relation": EdgeType.CONTAINS.value})
        _walk_edges(child_id, child, file_path, nodes_out, edges_out, root=root)


def _synthesise_folders(
    file_nodes: list[FileNode],
    root: str | None,
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Build folder nodes, module nodes, and CONTAINS edges.

    Returns (synthesised_node_dicts, edges, file_path_to_parent_id).
    Files in module folders are parented to the ModuleNode, otherwise to
    the FolderNode directly.
    """
    if not file_nodes:
        return [], [], {}

    if root is None:
        try:
            root = commonpath([fn.path for fn in file_nodes])
        except ValueError:
            root = ""

    root_path = Path(root).resolve() if root else Path(root)

    seen_folders: dict[str, dict] = {
        "": {
            "id": _folder_id(""),
            "type": "FolderNode",
            "name": root_path.name or ".",
            "node_type": NodeType.FOLDER.value,
            "path": str(root_path),
            "span": [0, 0, 0, 0],
            "tags": ["cat:structural", "type:folder"],
        }
    }
    edges: list[dict] = []
    file_parent_map: dict[str, str] = {}

    folder_files: dict[str, list[FileNode]] = {}

    for file_node in file_nodes:
        rel = _relative_to_root(file_node.path, root_path)
        parts = list(rel.parts[:-1])

        for depth, part in enumerate(parts):
            parts_slice = parts[: depth + 1]
            folder_path = Path(*parts_slice)
            folder_rel = str(folder_path)
            if folder_rel in seen_folders:
                continue
            fid = _folder_id(folder_rel)
            folder_tags = ["cat:structural", "type:folder", f"dir:{'/'.join(parts_slice)}"]
            seen_folders[folder_rel] = {
                "id": fid,
                "type": "FolderNode",
                "name": part,
                "node_type": NodeType.FOLDER.value,
                "path": str(root_path / folder_path),
                "span": [0, 0, 0, 0],
                "tags": folder_tags,
            }
            parent_rel = str(Path(*parts[:depth])) if depth > 0 else ""
            edges.append(
                {
                    "source": _folder_id(parent_rel),
                    "target": fid,
                    "relation": EdgeType.CONTAINS.value,
                }
            )

        folder_key = str(Path(*parts)) if parts else ""
        folder_files.setdefault(folder_key, []).append(file_node)

    # --- Add language tags to folders based on contained files ---
    folder_langs: dict[str, set[str]] = {}
    for folder_rel, files_in_folder in folder_files.items():
        langs: set[str] = set()
        for fn in files_in_folder:
            detected = detect_language(Path(fn.path).name)
            if detected:
                langs.add(detected)
        folder_langs[folder_rel] = langs

    # Propagate child languages up to ancestor folders
    for folder_rel in list(folder_langs):
        langs = folder_langs[folder_rel]
        for parent_rel in _ancestor_folder_rels(folder_rel):
            if parent_rel in seen_folders:
                folder_langs.setdefault(parent_rel, set()).update(langs)

    for folder_rel, folder_dict in seen_folders.items():
        langs = folder_langs.get(folder_rel, set())
        for lang in sorted(langs):
            folder_dict["tags"].append(f"lang:{lang}")

    # --- Module detection pass ---
    module_nodes: list[dict] = []
    module_folders: dict[str, str] = {}
    registry = get_default_registry()

    for folder_rel, files_in_folder in folder_files.items():
        file_basenames = frozenset(Path(fn.path).name for fn in files_in_folder)
        root_str = str(root_path)
        for lang in registry.registered_languages:
            hooks = registry.get_hooks(lang)
            for mod_info in hooks.detect_modules(folder_rel, file_basenames, root_str):
                mod_id = _module_id(folder_rel)
                mod_tags = ["cat:structural", "type:module", f"lang:{mod_info.language}"]
                if folder_rel:
                    mod_tags.append(f"dir:{Path(folder_rel).as_posix()}")
                module_nodes.append(
                    {
                        "id": mod_id,
                        "type": "ModuleNode",
                        "name": mod_info.name,
                        "node_type": NodeType.MODULE.value,
                        "path": mod_info.path,
                        "span": [0, 0, 0, 0],
                        "language": mod_info.language,
                        "tags": mod_tags,
                    }
                )
                edges.append(
                    {
                        "source": _folder_id(folder_rel) if folder_rel in seen_folders else _folder_id(""),
                        "target": mod_id,
                        "relation": EdgeType.CONTAINS.value,
                    }
                )
                module_folders[folder_rel] = mod_id
                break
            if folder_rel in module_folders:
                break

    # --- Connect child modules to nearest ancestor module ---
    for folder_rel, child_mod_id in module_folders.items():
        for parent_rel in _ancestor_folder_rels(folder_rel):
            if parent_rel in module_folders:
                edges.append(
                    {
                        "source": module_folders[parent_rel],
                        "target": child_mod_id,
                        "relation": EdgeType.CONTAINS.value,
                    }
                )
                break

    # --- Assign files to their parent (module if available, else folder) ---
    for folder_rel, files_in_folder in folder_files.items():
        parent_id = module_folders.get(folder_rel)
        if parent_id is None:
            if folder_rel in seen_folders:
                parent_id = _folder_id(folder_rel)
            else:
                parent_id = _folder_id("")
        for file_node in files_in_folder:
            file_parent_map[file_node.path] = parent_id

    all_synth = list(seen_folders.values()) + module_nodes
    return all_synth, edges, file_parent_map


def _resolved_edge_to_dict(edge: ResolvedEdge) -> dict:
    """Convert a ResolvedEdge to a serializable dict."""
    d: dict = {
        "source": edge.source_id,
        "target": edge.target_id,
        "relation": edge.relation.value,
    }
    if edge.confidence < 1.0:
        d["confidence"] = edge.confidence
    if edge.resolved_by:
        d["resolved_by"] = edge.resolved_by
    return d


def export_graph_from_file_nodes(
    file_nodes: list[FileNode],
    root: str | None = None,
    *,
    run_resolver: bool = True,
    show_progress: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Convert parsed FileNodes into node and edge dicts.

    :param root: Root directory for computing relative folder paths.
    :param run_resolver: Whether to run the resolution pipeline for semantic edges.
    :param show_progress: Whether to show tqdm progress bars (disable for MCP stdio).
    :return: (nodes_list, edges_list) ready for JSONL serialization.
    """
    all_nodes: list[dict] = []
    all_edges: list[dict] = []

    folder_nodes, folder_edges, file_parent_map = _synthesise_folders(file_nodes, root)
    all_nodes.extend(folder_nodes)
    all_edges.extend(folder_edges)

    for file_node in tqdm(file_nodes, desc="Parsing", unit="file", disable=not show_progress):
        file_path = file_node.path
        file_id = _node_id(file_path, file_node)
        all_nodes.append(_node_to_dict(file_node, file_path, root))

        parent_folder_id = file_parent_map.get(file_path)
        if parent_folder_id:
            all_edges.append({"source": parent_folder_id, "target": file_id, "relation": EdgeType.CONTAINS.value})

        for child in file_node.children:
            if child.node_type in INTERNAL_NODES:
                continue
            child_id = _node_id(file_path, child)
            all_nodes.append(_node_to_dict(child, file_path, root))
            all_edges.append({"source": file_id, "target": child_id, "relation": EdgeType.CONTAINS.value})
            _walk_edges(child_id, child, file_path, all_nodes, all_edges, root=root)

    if run_resolver:
        resolved, synth_nodes, synth_edges = resolve_graph(
            file_nodes,
            node_id_fn=_node_id,
            show_progress=show_progress,
        )
        all_nodes.extend(synth_nodes)
        all_edges.extend(synth_edges)
        node_ids = {n["id"] for n in all_nodes}
        for edge in resolved:
            if edge.source_id in node_ids and edge.target_id in node_ids:
                all_edges.append(_resolved_edge_to_dict(edge))

    return all_nodes, all_edges


async def export_graph(
    paths: list[str | Path],
    output_dir: str | Path,
    root: str | Path | None = None,
) -> tuple[Path, Path, Path, list[dict], list[dict]]:
    """Parse all files and write nodes.jsonl, edges.jsonl, and graph.jcp.

    :param paths: List of file paths to parse.
    :param output_dir: Directory where output files will be written.
    :param root: Root directory for folder hierarchy (auto-detected if None).
    :return: (nodes_path, edges_path, jcp_path, nodes, edges).
    """
    artifacts, nodes, edges = await export_graph_to_backends(paths, output_dir, root=root)
    if artifacts.nodes_path is None or artifacts.edges_path is None or artifacts.jcp_path is None:
        raise RuntimeError("JSONL export did not produce the expected files")
    return artifacts.nodes_path, artifacts.edges_path, artifacts.jcp_path, nodes, edges


async def export_graph_to_backends(
    paths: list[str | Path],
    output_dir: str | Path,
    *,
    root: str | Path | None = None,
    backend: str = "jsonl",
    ladybug_path: str | Path | None = None,
    node_batch_size: int = 1000,
    edge_batch_size: int = 5000,
    db_export_workers: int = 2,
    show_progress: bool = True,
) -> tuple[ExportArtifacts, list[dict], list[dict]]:
    """Parse files, build the graph, and write one or more backends.

    :param paths: List of file paths to parse.
    :param output_dir: Directory used for JSONL/``.jcp`` output and the default ``.lbug`` path.
    :param root: Root directory for folder hierarchy (auto-detected if None).
    :param backend: One of ``jsonl``, ``ladybug``, or ``both``.
    :param ladybug_path: Explicit output path for the Ladybug database file.
    :param node_batch_size: Number of nodes per Ladybug COPY batch.
    :param edge_batch_size: Number of edges per Ladybug COPY batch.
    :param db_export_workers: Worker count used while preparing Ladybug CSV batches.
    :param show_progress: Whether to show tqdm progress bars (disable for MCP stdio).
    :return: ``(artifacts, nodes, edges)``.
    """
    if backend not in {"jsonl", "ladybug", "both"}:
        raise ValueError("backend must be one of: jsonl, ladybug, both")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_nodes = await parse_files(paths)
    root_str = str(Path(root).resolve()) if root else None
    nodes, edges = export_graph_from_file_nodes(
        file_nodes,
        root=root_str,
        show_progress=show_progress,
    )

    artifacts = ExportArtifacts()
    if backend in {"jsonl", "both"}:
        file_records = build_file_records(paths, root)
        jsonl_artifacts = write_jsonl_jcp(output_dir, nodes, edges, file_records)
        artifacts.nodes_path = jsonl_artifacts.nodes_path
        artifacts.edges_path = jsonl_artifacts.edges_path
        artifacts.jcp_path = jsonl_artifacts.jcp_path

    if backend in {"ladybug", "both"}:
        db_path = Path(ladybug_path) if ladybug_path is not None else output_dir / "graph.lbug"
        ladybug_artifacts = write_ladybug_graph(
            nodes,
            edges,
            config=LadybugWriteConfig(
                path=db_path,
                node_batch_size=node_batch_size,
                edge_batch_size=edge_batch_size,
                csv_chunk_size=min(node_batch_size, edge_batch_size),
                export_workers=db_export_workers,
            ),
        )
        artifacts.ladybug_path = ladybug_artifacts.ladybug_path

    return artifacts, nodes, edges
