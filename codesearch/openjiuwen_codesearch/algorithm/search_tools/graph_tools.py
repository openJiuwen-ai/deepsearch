# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retropus KG expand tools — ToolSpec edge + GraphExpandTools mixin.

Schemas/executors follow the same shape as ``memory_tools`` / ``expand_context``.
Heavy state stays on ``RetrievalTools`` (via mixin); executors call ``env.tools``.
Never register these into CodeSearch ``build_default_registry``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

from openjiuwen_codesearch.algorithm.search_tools.registry import ToolOutcome, ToolSpec

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]+\.(py|js|ts|java|go|rb|rs)$|"
    r"(^|/)[^/]+_test\.(py|js|ts|java|go|rb|rs)$|(^|/)conftest\.py$",
    re.IGNORECASE,
)


def normalize_rel_path(path: str) -> str:
    """Normalize a predicted path to a clean repo-relative POSIX path."""
    p = (path or "").strip().replace("\\", "/")
    for prefix in ("/testbed/", "/workspace/", "/repo/", "testbed/", "workspace/", "repo/"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def is_test_path(rel: str) -> bool:
    return bool(_TEST_PATH_RE.search(rel.replace("\\", "/")))


EXPAND_FILE_DEFS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "expand_file_defs",
        "description": (
            "List ranked class/function definitions inside one already-selected "
            "file (or a candidate file). Use this to find additional edit sites "
            "in the same file before finishing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative file.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional query to re-rank defs in the file.",
                },
            },
            "required": ["path"],
        },
    },
}

EXPAND_INHERITANCE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "expand_inheritance",
        "description": (
            "List superclass and subclass definitions linked by INHERITS edges "
            "to classes overlapping the currently selected spans (or a given "
            "file). Recommended when a selected span is inside a class — "
            "related edit sites often live in parent/child classes — but not "
            "required before finish."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Optional repo-relative file to seed from. "
                        "Defaults to all currently selected files."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Optional query to re-rank inheritance neighbors.",
                },
            },
        },
    },
}

EXPAND_IMPORTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "expand_imports",
        "description": (
            "List in-repo import neighbors of selected files (or a given "
            "path) using IMPORTS edges in the knowledge graph (Python, Java, "
            "JS/TS, Go, Rust, C/C++): modules this file imports, and other repo "
            "files that import it. Use to find related production modules before "
            "finish — then read_file / add_context on relevant hits. Suggest-only; "
            "not required before finish."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Optional repo-relative file to seed from. "
                        "Defaults to all currently selected files."
                    ),
                },
                "direction": {
                    "type": "string",
                    "description": (
                        "Which edges to follow: 'both' (default), 'out'/'imports' "
                        "(what this file imports), or 'in'/'importers' "
                        "(who imports this file)."
                    ),
                },
                "depth": {
                    "type": "integer",
                    "description": (
                        "Hop depth for BFS over the import graph (default 1, max 3)."
                    ),
                },
            },
        },
    },
}


async def execute_expand_file_defs(env: Any, args: dict) -> ToolOutcome:
    observation = await asyncio.to_thread(
        env.tools.dispatch, "expand_file_defs", args or {}
    )
    return ToolOutcome(message=observation)


async def execute_expand_inheritance(env: Any, args: dict) -> ToolOutcome:
    observation = await asyncio.to_thread(
        env.tools.dispatch, "expand_inheritance", args or {}
    )
    return ToolOutcome(message=observation)


async def execute_expand_imports(env: Any, args: dict) -> ToolOutcome:
    observation = await asyncio.to_thread(
        env.tools.dispatch, "expand_imports", args or {}
    )
    return ToolOutcome(message=observation)


EXPAND_FILE_DEFS_SPEC = ToolSpec(
    name="expand_file_defs",
    schema=EXPAND_FILE_DEFS_SCHEMA,
    executor=execute_expand_file_defs,
)
EXPAND_INHERITANCE_SPEC = ToolSpec(
    name="expand_inheritance",
    schema=EXPAND_INHERITANCE_SCHEMA,
    executor=execute_expand_inheritance,
)
EXPAND_IMPORTS_SPEC = ToolSpec(
    name="expand_imports",
    schema=EXPAND_IMPORTS_SCHEMA,
    executor=execute_expand_imports,
)

EXPAND_SPECS: Dict[str, ToolSpec] = {
    EXPAND_FILE_DEFS_SPEC.name: EXPAND_FILE_DEFS_SPEC,
    EXPAND_INHERITANCE_SPEC.name: EXPAND_INHERITANCE_SPEC,
    EXPAND_IMPORTS_SPEC.name: EXPAND_IMPORTS_SPEC,
}


class GraphExpandTools:
    """KG-backed ``expand_*`` tools mixed into Retropus ``RetrievalTools``.

    Expects the host instance to provide: ``kg``, ``retriever``, ``repo_dir``,
    ``config``, ``issue_text``, ``_issue_about_tests``, ``_file_nodes``,
    ``_all_spans``, ``_defs_by_file``, ``_expanded_files``,
    ``_second_file_probed``, ``_inheritance_expanded``, ``_import_index``,
    ``_rel_to_file_node``, and ``selected_files()``.
    """

    def expand_file_defs(self, path: str, query: Optional[str] = None) -> str:
        from openjiuwen_codesearch.algorithm.prompts.retropus import (  # noqa: PLC0415
            EXPAND_DEFS_HEADER,
        )
        from openjiuwen_codesearch.retropus.retrievers.bm25 import (  # noqa: PLC0415
            definition_label,
        )

        rel = normalize_rel_path(path)
        if not rel:
            return "expand_file_defs: empty path."
        if (self.repo_dir / rel).is_file() is False:
            return f"expand_file_defs: file not found: {rel}"
        self._expanded_files.add(rel)
        if self.config.imp_second_file_probe:
            self._second_file_probed = True

        q = (query or "").strip() or self.issue_text[:500]
        defs = self._definitions_in_file(rel)
        if not defs:
            return f"No definition AST nodes found in {rel}."

        # Re-score defs in this file with the retriever if possible.
        scored: List[Tuple[float, int, int, str]] = []
        try:
            ranked = self.retriever.score_files_and_defs(q, top_k=50, max_defs_per_file=40)
        except Exception:
            ranked = []
        score_map: Dict[Tuple[int, int], float] = {}
        for entry in ranked:
            if entry["file_node"].node.relative_path != rel:
                continue
            for def_node, score in entry["defs"]:
                score_map[(def_node.node.start_line, def_node.node.end_line)] = float(score)

        for start, end, node in defs:
            label = definition_label(node) if definition_label else node.node.type
            scored.append((score_map.get((start, end), 0.0), start, end, label))
        scored.sort(key=lambda t: (-t[0], t[1]))

        lines = [EXPAND_DEFS_HEADER.format(path=rel).rstrip("\n")]
        for score, start, end, label in scored[:30]:
            lines.append(f"  {rel}:{start}-{end}  {label}  (score {score:.2f})")
        return "\n".join(lines)

    def _class_defs_overlapping_spans(
        self, files: List[str]
    ) -> List[Tuple[str, int, int, Any]]:
        """Return (rel, start, end, kg_node) for class defs overlapping recorded spans."""
        from openjiuwen_codesearch.retropus.graph.inherits import (  # noqa: PLC0415
            is_class_ast_type,
        )

        out: List[Tuple[str, int, int, Any]] = []
        seen: set[Tuple[str, int, int]] = set()
        spans_by_file: Dict[str, List[Tuple[int, int]]] = {}
        for span in self._all_spans:
            spans_by_file.setdefault(span["file"], []).append((span["start"], span["end"]))

        for rel in files:
            defs = self._definitions_in_file(rel)
            file_spans = spans_by_file.get(rel)
            for start, end, node in defs:
                if not is_class_ast_type(node.node.type):
                    continue
                # If we have spans for this file, require overlap; else keep all classes.
                if file_spans is not None:
                    if not any(not (end < s or start > e) for s, e in file_spans):
                        continue
                key = (rel, start, end)
                if key in seen:
                    continue
                seen.add(key)
                out.append((rel, start, end, node))
        return out

    def _pending_inheritance_targets(self) -> List[str]:
        """Neighbor files linked by INHERITS that are not yet selected."""
        selected = set(self.selected_files())
        if not selected:
            return []
        get_edges = getattr(self.kg, "get_inherits_edges", None)
        if get_edges is None or not get_edges():
            return []

        pending: set[str] = set()
        for _rel, _s, _e, class_node in self._class_defs_overlapping_spans(sorted(selected)):
            try:
                neighbors = self.kg.get_inheritance_neighbors(class_node)
            except Exception:
                continue
            for neighbor in neighbors:
                file_node = self.kg.get_file_for_ast(neighbor)
                if file_node is None:
                    continue
                nrel = file_node.node.relative_path
                if (
                    self.config.imp_ban_tests
                    and is_test_path(nrel)
                    and not self._issue_about_tests
                ):
                    continue
                if nrel not in selected:
                    pending.add(nrel)
        return sorted(pending)

    def expand_inheritance(
        self, path: Optional[str] = None, query: Optional[str] = None
    ) -> str:
        """List INHERITS neighbors for classes overlapping selected spans (or ``path``)."""
        from openjiuwen_codesearch.algorithm.prompts.retropus import (  # noqa: PLC0415
            EXPAND_INHERITANCE_HEADER,
        )
        from openjiuwen_codesearch.retropus.graph.inherits import (  # noqa: PLC0415
            is_class_ast_type,
        )
        from openjiuwen_codesearch.retropus.retrievers.bm25 import (  # noqa: PLC0415
            definition_label,
        )

        self._inheritance_expanded = True
        if self.config.imp_second_file_probe:
            self._second_file_probed = True

        if path:
            seed_files = [normalize_rel_path(str(path))]
        else:
            seed_files = self.selected_files()
        seed_files = [f for f in seed_files if f]
        if not seed_files:
            return (
                "expand_inheritance: no selected spans yet. "
                "add_context on a class first, then call expand_inheritance."
            )

        get_edges = getattr(self.kg, "get_inherits_edges", None)
        if get_edges is None or not get_edges():
            return "No INHERITS edges in the knowledge graph for this repository."

        class_seeds = self._class_defs_overlapping_spans(seed_files)
        if not class_seeds:
            # Fall back to all class defs in seed files when spans don't overlap a class.
            class_seeds = []
            for rel in seed_files:
                for start, end, node in self._definitions_in_file(rel):
                    if is_class_ast_type(node.node.type):
                        class_seeds.append((rel, start, end, node))

        if not class_seeds:
            return (
                f"No class definitions found in {', '.join(seed_files)}. "
                "INHERITS expansion only applies to classes."
            )

        q = (query or "").strip() or self.issue_text[:500]
        score_map: Dict[Tuple[str, int, int], float] = {}
        try:
            ranked = self.retriever.score_files_and_defs(q, top_k=40, max_defs_per_file=30)
        except Exception:
            ranked = []
        for entry in ranked:
            rel = entry["file_node"].node.relative_path
            for def_node, score in entry["defs"]:
                score_map[
                    (rel, def_node.node.start_line, def_node.node.end_line)
                ] = float(score)

        lines = [EXPAND_INHERITANCE_HEADER.rstrip("\n")]
        seen_neighbor: set[Tuple[str, int, int]] = set()
        rows: List[Tuple[float, str]] = []

        for seed_rel, seed_s, seed_e, seed_node in class_seeds:
            seed_label = definition_label(seed_node) if definition_label else seed_node.node.type
            try:
                neighbors = list(self.kg.get_inheritance_neighbors(seed_node))
            except Exception:
                neighbors = []
            # Determine direction labels from edge endpoints when possible.
            parents = []
            children = []
            try:
                for edge in self.kg.get_inherits_edges():
                    if edge.source.node_id == seed_node.node_id:
                        parents.append(edge.target)
                    elif edge.target.node_id == seed_node.node_id:
                        children.append(edge.source)
            except Exception:
                parents = neighbors
                children = []

            if not parents and not children and neighbors:
                parents = neighbors

            for direction, group in (("superclass", parents), ("subclass", children)):
                for neighbor in group:
                    file_node = self.kg.get_file_for_ast(neighbor)
                    if file_node is None:
                        continue
                    nrel = file_node.node.relative_path
                    if (
                        self.config.imp_ban_tests
                        and is_test_path(nrel)
                        and not self._issue_about_tests
                    ):
                        continue
                    key = (nrel, neighbor.node.start_line, neighbor.node.end_line)
                    if key in seen_neighbor:
                        continue
                    seen_neighbor.add(key)
                    label = definition_label(neighbor) if definition_label else neighbor.node.type
                    score = score_map.get(key, 0.0)
                    rows.append(
                        (
                            score,
                            f"  {nrel}:{neighbor.node.start_line}-{neighbor.node.end_line}  "
                            f"{label}  [{direction} of {seed_rel} {seed_label}]  "
                            f"(score {score:.2f})",
                        )
                    )

        if not rows:
            return (
                EXPAND_INHERITANCE_HEADER.rstrip("\n")
                + "\nNo inheritance neighbors found for classes in "
                + ", ".join(seed_files)
                + "."
            )

        rows.sort(key=lambda t: -t[0])
        lines.extend(row for _, row in rows[:40])
        return "\n".join(lines)

    def _ensure_import_index(self):
        from openjiuwen_codesearch.retropus.graph.imports import (  # noqa: PLC0415
            build_import_index,
            collect_kg_file_paths,
            import_index_from_kg,
        )

        if self._import_index is not None:
            return self._import_index
        # Prefer IMPORTS edges already materialised on the knowledge graph.
        if hasattr(self.kg, "get_imports_edges"):
            index = import_index_from_kg(self.kg)
            if index.outgoing or index.incoming or list(self.kg.get_imports_edges()):
                self._import_index = index
                return self._import_index
        # Fallback for lightweight test KGs without IMPORTS edges.
        paths = collect_kg_file_paths(self._file_nodes)
        if not paths:
            paths = sorted(self._rel_to_file_node.keys())
        self._import_index = build_import_index(self.repo_dir, paths)
        return self._import_index

    def expand_imports(
        self,
        path: Optional[str] = None,
        direction: str = "both",
        depth: int = 1,
    ) -> str:
        """List in-repo import targets and/or importers for selected files."""
        from openjiuwen_codesearch.algorithm.prompts.retropus import (  # noqa: PLC0415
            EXPAND_IMPORTS_HEADER,
        )

        if self.config.imp_second_file_probe:
            self._second_file_probed = True

        if path:
            seed_files = [normalize_rel_path(str(path))]
        else:
            seed_files = self.selected_files()
        seed_files = [f for f in seed_files if f]
        if not seed_files:
            return (
                "expand_imports: no selected spans yet. "
                "add_context on a file first, then call expand_imports "
                "(or pass path=)."
            )

        index = self._ensure_import_index()
        lines = [EXPAND_IMPORTS_HEADER.rstrip("\n")]
        lines.append(
            f"Seeds: {', '.join(seed_files)}  "
            f"(direction={direction or 'both'}, depth={max(1, min(int(depth or 1), 3))})"
        )

        seen_rows: set[Tuple[str, str, str, str]] = set()
        rows: List[str] = []
        for seed in seed_files:
            if seed not in index.known_files and not (self.repo_dir / seed).is_file():
                rows.append(f"  [skip] {seed} — not in knowledge graph / worktree")
                continue
            neighbors = index.neighbors(
                seed,
                direction=direction or "both",
                depth=depth,
                max_nodes=80,
            )
            if not neighbors:
                rows.append(f"  [none] {seed}")
                continue
            for other, name, edge_dir in neighbors:
                if (
                    self.config.imp_ban_tests
                    and is_test_path(other)
                    and not self._issue_about_tests
                ):
                    continue
                key = (seed, other, name, edge_dir)
                if key in seen_rows:
                    continue
                seen_rows.add(key)
                if edge_dir == "imports":
                    rows.append(f"  [{seed}] imports {other}  (as {name})")
                else:
                    rows.append(f"  [{seed}] imported_by {other}  (as {name})")

        if not any((" imports " in r or " imported_by " in r) for r in rows):
            return (
                "\n".join(lines)
                + "\nNo in-repo import neighbors found for: "
                + ", ".join(seed_files)
                + "."
            )

        lines.extend(rows[:60])
        if len(rows) > 60:
            lines.append(f"... [{len(rows) - 60} more omitted]")
        return "\n".join(lines)

    def _ensure_defs_index(self) -> Dict[str, List[Tuple[int, int, Any]]]:
        from openjiuwen_codesearch.retropus.graph.graph_types import (  # noqa: PLC0415
            ASTNode,
        )
        from openjiuwen_codesearch.retropus.retrievers.bm25 import (  # noqa: PLC0415
            is_definition_ast_type,
        )

        if self._defs_by_file is not None:
            return self._defs_by_file
        by_file: Dict[str, List[Tuple[int, int, Any]]] = {}
        for file_node, ast_node in self.retriever._iter_ast_candidates(self._file_nodes):
            if not isinstance(ast_node.node, ASTNode):
                continue
            if not is_definition_ast_type(ast_node.node.type):
                continue
            rel = file_node.node.relative_path
            by_file.setdefault(rel, []).append(
                (ast_node.node.start_line, ast_node.node.end_line, ast_node)
            )
        self._defs_by_file = by_file
        return by_file

    def _definitions_in_file(self, rel: str) -> List[Tuple[int, int, Any]]:
        return list(self._ensure_defs_index().get(rel, []))
