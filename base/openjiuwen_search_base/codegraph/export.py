"""CLI entry point: ``python -m openjiuwen_search_base.codegraph.export``."""

import argparse
import asyncio
import fnmatch
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

from .backends.base import ExportArtifacts
from .parser.constants import detect_language
from .parser.graph_export import export_graph_to_backends
from .parser.resolver.passes._utils import match_name

_EXCLUDED_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "dist",
        "build",
        ".jiuwen_graph",
    }
)


async def index_directory(
    root: Path,
    *,
    output_dir: Path | None = None,
    backend: str = "jsonl",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    ladybug_path: Path | None = None,
    node_batch_size: int = 1000,
    edge_batch_size: int = 5000,
    db_export_workers: int = 2,
    quiet: bool = False,
) -> tuple[ExportArtifacts, list[dict], list[dict], list[Path]]:
    """Discover source files under *root*, parse them, and write export backends.

    :param root: Directory to parse (must exist).
    :param output_dir: Export directory (defaults to ``<root>/.jiuwen_graph``).
    :param backend: One of ``jsonl``, ``ladybug``, or ``both``.
    :param include: Optional relative glob allowlist (same semantics as the CLI).
    :param exclude: Optional relative glob blocklist.
    :param ladybug_path: Optional ``.lbug`` path when using the Ladybug backend.
    :param node_batch_size: Ladybug node COPY batch size.
    :param edge_batch_size: Ladybug edge COPY batch size.
    :param db_export_workers: Workers used while preparing Ladybug batches.
    :param quiet: Suppress ``.jiuwenignore`` messages and tqdm progress bars
        (required for MCP stdio so progress text does not corrupt JSON-RPC).
    :returns: ``(artifacts, nodes, edges, files)``.
    :raises NotADirectoryError: If *root* is not a directory.
    :raises FileNotFoundError: If no supported source files are found.
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")

    out = (output_dir or (root / ".jiuwen_graph")).expanduser().resolve()
    ignored = _load_jiuwenignore(root, quiet=quiet)
    include_patterns = [_normalize_glob(p, root) for p in include] if include else None
    exclude_patterns = [_normalize_glob(p, root) for p in exclude] if exclude else None
    files = _discover_files(root, ignored=ignored, include=include_patterns, exclude=exclude_patterns)
    if not files:
        raise FileNotFoundError(f"No supported files found in {root}")

    artifacts, nodes, edges = await export_graph_to_backends(
        files,
        out,
        root=root,
        backend=backend,
        ladybug_path=ladybug_path,
        node_batch_size=node_batch_size,
        edge_batch_size=edge_batch_size,
        db_export_workers=db_export_workers,
        show_progress=not quiet,
    )
    return artifacts, nodes, edges, files


def main() -> None:
    """Parse a directory and export graph data."""
    parser = argparse.ArgumentParser(
        description="Export a code directory to JSONL, Ladybug, or both.",
    )
    parser.add_argument("directory", type=Path, help="Root directory to parse.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output directory (defaults to <directory>/.jiuwen_graph).",
    )
    parser.add_argument(
        "-i",
        "--include",
        action="append",
        default=None,
        metavar="GLOB",
        help="Only include files matching this glob (repeatable). A bare name like 'src' expands to 'src/**/*'.",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        action="append",
        default=None,
        metavar="GLOB",
        help="Exclude files matching this glob (repeatable). A bare name like 'tests' expands to 'tests/**/*'.",
    )
    parser.add_argument(
        "--backend",
        choices=("jsonl", "ladybug", "both"),
        default="jsonl",
        help="Output backend to write (default: jsonl).",
    )
    parser.add_argument(
        "--ladybug-path",
        type=Path,
        default=None,
        help="Path to the output .lbug file (default: <output>/graph.lbug).",
    )
    parser.add_argument(
        "--node-batch-size",
        type=int,
        default=1000,
        help="Number of nodes per Ladybug COPY batch.",
    )
    parser.add_argument(
        "--edge-batch-size",
        type=int,
        default=5000,
        help="Number of edges per Ladybug COPY batch.",
    )
    parser.add_argument(
        "--db-export-workers",
        type=int,
        default=2,
        help="Worker count used while preparing Ladybug export batches.",
    )
    args = parser.parse_args()

    root: Path = args.directory.resolve()
    try:
        print(f"Parsing files from {root} ...")
        artifacts, nodes, edges, files = asyncio.run(
            index_directory(
                root,
                output_dir=args.output,
                backend=args.backend,
                include=args.include,
                exclude=args.exclude,
                ladybug_path=args.ladybug_path,
                node_batch_size=args.node_batch_size,
                edge_batch_size=args.edge_batch_size,
                db_export_workers=args.db_export_workers,
            )
        )
    except (NotADirectoryError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsed {len(files)} files from {root}")
    if artifacts.nodes_path is not None:
        print(f"Wrote {artifacts.nodes_path}")
    if artifacts.edges_path is not None:
        print(f"Wrote {artifacts.edges_path}")
    if artifacts.jcp_path is not None:
        print(f"Wrote {artifacts.jcp_path}")
    if artifacts.ladybug_path is not None:
        print(f"Wrote {artifacts.ladybug_path}")

    _print_stats(nodes, edges)

    match_name.cache_clear()


def _load_jiuwenignore(root: Path, *, quiet: bool = False) -> Callable[[str], bool] | None:
    """Load ``.jiuwenignore`` from *root* if it exists.

    Returns a callable that takes an absolute path string and returns
    ``True`` if the path should be ignored.
    """
    ignore_file = root / ".jiuwenignore"
    if not ignore_file.is_file():
        if not quiet:
            print(f"No .jiuwenignore file found at {root}")
        return None
    from gitignore_parser import parse_gitignore_str

    content = ignore_file.read_text(encoding="utf-8", errors="replace")
    if not quiet:
        print(f"Loaded .jiuwenignore file at {root}")
    return parse_gitignore_str(content, base_dir=str(root))


def _normalize_glob(pattern: str, root: Path) -> str:
    """Normalize a CLI glob pattern relative to *root*.

    - Absolute paths are made relative to *root*.
    - A bare directory name (no wildcards, no dots) is treated as a prefix match.
    """
    p = Path(pattern)
    if p.is_absolute():
        try:
            pattern = str(p.relative_to(root))
        except ValueError:
            pass
    return pattern.rstrip("/\\")


def _matches_any_pattern(rel_str: str, patterns: list[str]) -> bool:
    """Return whether *rel_str* matches any of the *patterns*.

    A pattern without wildcards is treated as a directory prefix.
    A pattern with wildcards is matched with :func:`fnmatch.fnmatch`.
    """
    for p in patterns:
        if "*" in p or "?" in p or "[" in p:
            if fnmatch.fnmatch(rel_str, p):
                return True
        else:
            if rel_str == p or rel_str.startswith(p + "/"):
                return True
    return False


def _discover_files(
    root: Path,
    *,
    ignored: Callable[[str], bool] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    _base: Path | None = None,
) -> list[Path]:
    """Recursively find all supported files under *root*, applying filters.

    Filter order:
    1. ``_EXCLUDED_DIRS`` (hard-coded fast skip)
    2. ``.jiuwenignore`` (gitignore rules via *ignored*)
    3. ``--include`` allowlist (if provided)
    4. ``--exclude`` blocklist (if provided)
    5. ``detect_language`` (must be a supported file type)
    """
    base = _base or root
    files: list[Path] = []

    for child in root.iterdir():
        if child.is_dir():
            if child.name.startswith(".") or child.name in _EXCLUDED_DIRS:
                continue
            if ignored is not None and ignored(str(child)):
                continue
            files.extend(_discover_files(child, ignored=ignored, include=include, exclude=exclude, _base=base))
        elif child.is_file():
            if ignored is not None and ignored(str(child)):
                continue
            if detect_language(child.name) is None:
                continue
            rel_str = str(child.relative_to(base))
            if include and not _matches_any_pattern(rel_str, include):
                continue
            if exclude and _matches_any_pattern(rel_str, exclude):
                continue
            files.append(child)

    return sorted(files)


def _print_stats(nodes: list[dict], edges: list[dict]) -> None:
    """Print a summary table of node and edge counts."""
    node_counts = Counter(n["type"] for n in nodes)
    edge_counts = Counter(e["relation"] for e in edges)

    print(f"\n{'─' * 38}")
    print(f"  {'Nodes':30s} {len(nodes):>5}")
    print(f"{'─' * 38}")
    for ntype, count in node_counts.most_common():
        print(f"    {ntype:28s} {count:>5}")

    print(f"\n{'─' * 38}")
    print(f"  {'Edges':30s} {len(edges):>5}")
    print(f"{'─' * 38}")
    for rel, count in edge_counts.most_common():
        print(f"    {rel:28s} {count:>5}")
    print()


if __name__ == "__main__":
    main()
