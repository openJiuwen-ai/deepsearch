"""JSONL and ``.jcp`` graph export backend."""

import gzip
import json
from pathlib import Path

from .base import ExportArtifacts

_JCP_MARKER = {"marker": "break"}


def build_file_records(paths: list[str | Path], root: str | Path | None = None) -> list[dict]:
    """Collect file contents for the ``.jcp`` bundle."""
    root_path = Path(root).resolve() if root else None
    seen_paths: set[str] = set()
    file_records: list[dict] = []

    for path in paths:
        abs_path = Path(path).resolve()
        key = str(abs_path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        try:
            content = abs_path.read_text(errors="replace", encoding="utf-8")
        except OSError:
            continue
        rel = str(abs_path.relative_to(root_path)) if root_path else str(abs_path)
        file_records.append({"path": rel, "content": content})

    return file_records


def write_jsonl_jcp(
    output_dir: str | Path,
    nodes: list[dict],
    edges: list[dict],
    file_records: list[dict],
) -> ExportArtifacts:
    """Write JSONL node/edge files plus the compressed ``.jcp`` bundle."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = output_dir / "nodes.jsonl"
    edges_path = output_dir / "edges.jsonl"
    jcp_path = output_dir / "graph.jcp"

    with open(nodes_path, "w", encoding="utf-8") as file_obj:
        for node in nodes:
            file_obj.write(json.dumps(node, ensure_ascii=False) + "\n")

    with open(edges_path, "w", encoding="utf-8") as file_obj:
        for edge in edges:
            file_obj.write(json.dumps(edge, ensure_ascii=False) + "\n")

    with gzip.open(jcp_path, "wt", encoding="utf-8") as file_obj:
        for node in nodes:
            file_obj.write(json.dumps(node, ensure_ascii=False) + "\n")
        file_obj.write(json.dumps(_JCP_MARKER, ensure_ascii=False) + "\n")
        for edge in edges:
            file_obj.write(json.dumps(edge, ensure_ascii=False) + "\n")
        file_obj.write(json.dumps(_JCP_MARKER, ensure_ascii=False) + "\n")
        for record in file_records:
            file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")

    return ExportArtifacts(nodes_path=nodes_path, edges_path=edges_path, jcp_path=jcp_path)
