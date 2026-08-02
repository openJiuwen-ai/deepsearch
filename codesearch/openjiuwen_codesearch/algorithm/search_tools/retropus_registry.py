# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retropus tool runtime + registry — isolated from CodeSearch ``build_default_registry``.

Owns ``RetrievalTools`` (core search/read/span/finish tools) and builds per-run
``ToolSpec`` registries. Graph expand tools reuse executors from ``graph_tools``.
Never merge into the default CodeSearch registry.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple, TYPE_CHECKING

from openjiuwen_codesearch.algorithm.prompts.retropus import (
    READ_FILE_HEADER,
    SEARCH_CODE_HEADER,
)
from openjiuwen_codesearch.algorithm.search_tools.graph_tools import (
    EXPAND_FILE_DEFS_SCHEMA,
    EXPAND_IMPORTS_SCHEMA,
    EXPAND_INHERITANCE_SCHEMA,
    EXPAND_SPECS,
    GraphExpandTools,
    is_test_path,
    normalize_rel_path,
)
from openjiuwen_codesearch.algorithm.search_tools.registry import ToolOutcome, ToolSpec
from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig

if TYPE_CHECKING:
    from openjiuwen_codesearch.retropus.graph.knowledge_graph import KnowledgeGraph
    from openjiuwen_codesearch.retropus.retrievers.base import AbstractBaseRetriever

_ISSUE_ABOUT_TESTS_RE = re.compile(
    r"\b(unit\s*tests?|test\s*suite|pytest|unittest|failing\s+tests?|test\s+file)\b",
    re.IGNORECASE,
)


def _truncate(text: str, max_chars: int) -> str:
    """Trim ``text`` to ``max_chars``, appending a truncation marker when needed."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


class RetrievalTools(GraphExpandTools):
    """Stateful tool dispatcher for one Retropus instance run.

    Improvement flags (from ``RetropusSearchAgentConfig``) gate precision/recall
    guards: ban_tests, same_file_expand, second_file_probe, anti_early_finish.
    ``inherits_expand`` / ``expand_imports`` register suggest-only expand tools
    (they do not block ``finish``).
    """

    def __init__(
        self,
        kg: "KnowledgeGraph",
        retriever: "AbstractBaseRetriever",
        repo_dir: Path,
        config: Optional[RetropusSearchAgentConfig] = None,
        issue_text: str = "",
    ):
        """Wire KG, retriever, and per-run config; initialize span / expand state."""
        from openjiuwen_codesearch.retropus.graph.graph_types import (  # noqa: PLC0415
            FileNode,
        )

        self.kg = kg
        self.retriever = retriever
        self.repo_dir = Path(repo_dir)
        self.config = config or RetropusSearchAgentConfig()
        self.issue_text = issue_text or ""
        self._issue_about_tests = bool(_ISSUE_ABOUT_TESTS_RE.search(self.issue_text))
        self._file_nodes = list(kg.get_file_nodes())

        self._all_spans: List[Dict[str, Any]] = []
        self._seen_spans: set[Tuple[str, int, int]] = set()
        self._new_since_drain: List[Dict[str, Any]] = []
        self._line_count_cache: Dict[str, int] = {}
        self._rel_to_file_node: Dict[str, Any] = {
            n.node.relative_path: n
            for n in self._file_nodes
            if isinstance(n.node, FileNode)
        }
        # file_rel -> list of (start, end, kg_node) for definition AST nodes
        self._defs_by_file: Optional[Dict[str, List[Tuple[int, int, Any]]]] = None
        self._expanded_files: set[str] = set()
        self._second_file_probed = False
        self._inheritance_expanded = False
        self._import_index = None

    # ------------------------------------------------------------------ #
    #                          Tool schemas                              #
    # ------------------------------------------------------------------ #

    def tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas in a fixed order (stable prompt-cache prefix).

        Core tools first, optional tools next, ``finish`` always last. Order must
        not depend on runtime state — only on improvement flags for the run.
        """
        schemas: List[Dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "search_code",
                    "description": (
                        "Search the repository for class/function definitions relevant to a "
                        "query. Returns ranked definitions with their file path and exact "
                        "start/end line numbers. Use natural-language or identifier queries."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "What to look for."}
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_text",
                    "description": (
                        "Search documentation / markdown / text chunks (non-code files) for a "
                        "query. Returns matching text with file path and line range."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_repo_structure",
                    "description": (
                        "Show the repository structure. With a query, returns a compact tree of "
                        "the most relevant files and their classes/functions; without a query, "
                        "returns the top-level file tree."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Optional query to scope the tree.",
                            }
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": (
                        "Read exact numbered lines from a repo-relative file to verify or "
                        "narrow a span before adding it as context."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                        },
                        "required": ["path", "start_line", "end_line"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_context",
                    "description": (
                        "Record a final line span that must be read/edited to resolve the "
                        "issue. Prefer the smallest enclosing function/method that contains the "
                        "relevant code; avoid adding whole files or test files. Call once per "
                        "distinct span."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "description": "Repo-relative path."},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                            "reason": {
                                "type": "string",
                                "description": "Brief why-relevant note (optional).",
                            },
                        },
                        "required": ["file", "start_line", "end_line"],
                    },
                },
            },
        ]
        # Optional tools keep a fixed slot before finish so schema order is stable.
        if self.config.imp_same_file_expand:
            schemas.append(EXPAND_FILE_DEFS_SCHEMA)
        if self.config.imp_inherits_expand:
            schemas.append(EXPAND_INHERITANCE_SCHEMA)
        if self.config.imp_expand_imports:
            schemas.append(EXPAND_IMPORTS_SCHEMA)
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": (
                        "Stop once the added context fully covers the code needed to resolve "
                        "the issue with high precision."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        )
        return schemas

    # ------------------------------------------------------------------ #
    #                            Dispatch                                #
    # ------------------------------------------------------------------ #

    def dispatch(self, name: str, args: Dict[str, Any]) -> str:
        """Execute a tool call and return an observation string for the LLM."""
        limit = self.config.max_obs_chars
        try:
            if name == "search_code":
                return _truncate(self.search_code(str(args.get("query", ""))), limit)
            if name == "search_text":
                return _truncate(self.search_text(str(args.get("query", ""))), limit)
            if name == "get_repo_structure":
                return _truncate(self.get_repo_structure(args.get("query")), limit)
            if name == "read_file":
                return _truncate(
                    self.read_file(
                        str(args.get("path", "")),
                        int(args.get("start_line", 1)),
                        int(args.get("end_line", 1)),
                    ),
                    limit,
                )
            if name == "expand_file_defs":
                return _truncate(
                    self.expand_file_defs(
                        str(args.get("path", "")),
                        args.get("query"),
                    ),
                    limit,
                )
            if name == "expand_inheritance":
                return _truncate(
                    self.expand_inheritance(
                        args.get("path"),
                        args.get("query"),
                    ),
                    limit,
                )
            if name == "expand_imports":
                depth_raw = args.get("depth", 1)
                try:
                    depth = int(depth_raw) if depth_raw is not None else 1
                except (TypeError, ValueError):
                    depth = 1
                return _truncate(
                    self.expand_imports(
                        args.get("path"),
                        direction=str(args.get("direction") or "both"),
                        depth=depth,
                    ),
                    limit,
                )
            if name == "add_context":
                return self.add_context(
                    str(args.get("file", "")),
                    int(args.get("start_line", 0)),
                    int(args.get("end_line", 0)),
                    args.get("reason"),
                )
            if name == "finish":
                return self.finish()
            return f"Unknown tool: {name}"
        except Exception as exc:  # keep the loop alive on bad tool args
            return f"Tool '{name}' failed: {exc}"

    # ------------------------------------------------------------------ #
    #                         Tool implementations                       #
    # ------------------------------------------------------------------ #

    def search_code(self, query: str, top_files: int = 12, max_defs: int = 20) -> str:
        """Rank definitions for ``query`` and return a path:span / label listing."""
        from openjiuwen_codesearch.retropus.retrievers.bm25 import (  # noqa: PLC0415
            definition_label,
        )

        query = query.strip()
        if not query:
            return "Empty query."

        # Mark second-file probe progress when agent searches after having one file.
        if self.config.imp_second_file_probe and self.selected_files():
            self._second_file_probed = True

        ranked_files = self.retriever.score_files_and_defs(query, top_k=top_files)
        rows: List[Tuple[str, str, int, int, float]] = []
        for entry in ranked_files:
            rel = entry["file_node"].node.relative_path
            if self.config.imp_ban_tests and is_test_path(rel) and not self._issue_about_tests:
                continue
            for def_node, score in entry["defs"]:
                label = definition_label(def_node) if definition_label else def_node.node.type
                rows.append(
                    (rel, label, def_node.node.start_line, def_node.node.end_line, float(score))
                )

        rows.sort(key=lambda r: r[4], reverse=True)
        lines: List[str] = []
        seen: set[Tuple[str, int, int]] = set()
        for rel, label, start, end, score in rows:
            key = (rel, start, end)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"{rel}:{start}-{end}  {label}  (score {score:.2f})")
            if len(lines) >= max_defs:
                break

        if not lines:
            formatted, _ = self.retriever.search_ast_nodes(query, self._file_nodes)
            return formatted

        return SEARCH_CODE_HEADER + "\n".join(lines)

    def search_text(self, query: str) -> str:
        """Search markdown/text chunks for ``query`` and return the formatted hits."""
        query = query.strip()
        if not query:
            return "Empty query."
        formatted, _ = self.retriever.search_text_nodes(query)
        return formatted

    def get_repo_structure(self, query: Optional[str]) -> str:
        """Return a query-scoped definition tree, or a shallow file tree fallback."""
        from openjiuwen_codesearch.retropus.retrievers.bm25 import (  # noqa: PLC0415
            render_scored_file_tree,
        )

        query = (query or "").strip()
        if query and render_scored_file_tree is not None:
            ranked_files = self.retriever.score_files_and_defs(query, top_k=15)
            if self.config.imp_ban_tests and not self._issue_about_tests:
                ranked_files = [
                    e
                    for e in ranked_files
                    if not is_test_path(e["file_node"].node.relative_path)
                ]
            tree = render_scored_file_tree(ranked_files)
            if tree.strip():
                return tree
        tree = self.kg.get_file_tree(max_depth=4, max_lines=400)
        return (
            "Repository file tree (top levels). "
            "Use search_code / read_file / add_context on candidates below:\n"
            + tree
        )

    def _file_line_count(self, rel: str) -> Optional[int]:
        """Cached line count for a repo-relative path, or ``None`` if unreadable."""
        if rel in self._line_count_cache:
            return self._line_count_cache[rel]
        abs_path = self.repo_dir / rel
        if not abs_path.is_file():
            return None
        try:
            with abs_path.open("r", encoding="utf-8", errors="replace") as f:
                count = sum(1 for _ in f)
        except OSError:
            return None
        self._line_count_cache[rel] = count
        return count

    def read_file(self, path: str, start_line: int, end_line: int) -> str:
        """Return numbered source lines for ``path`` in ``[start_line, end_line]``."""
        rel = normalize_rel_path(path)
        abs_path = self.repo_dir / rel
        if not abs_path.is_file():
            return f"File not found: {rel}"

        try:
            with abs_path.open("r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except OSError as exc:
            return f"Could not read {rel}: {exc}"

        total = len(all_lines)
        start = max(1, start_line)
        end = min(total, max(start, end_line))
        if end - start + 1 > self.config.max_read_lines:
            end = start + self.config.max_read_lines - 1

        out = [
            READ_FILE_HEADER.format(path=rel, start=start, end=end, total=total).rstrip(
                "\n"
            )
        ]
        for i in range(start, end + 1):
            out.append(f"{i}\t{all_lines[i - 1].rstrip(chr(10))}")
        return "\n".join(out)

    def selected_files(self) -> List[str]:
        """Sorted unique file paths among recorded ``add_context`` spans."""
        return sorted({s["file"] for s in self._all_spans})

    def add_context(
        self, file: str, start_line: int, end_line: int, reason: Optional[str] = None
    ) -> str:
        """Record a retrieval span after path / test-ban / bounds checks."""
        rel = normalize_rel_path(file)
        if not rel:
            return "add_context ignored: empty file path."

        abs_path = self.repo_dir / rel
        if not abs_path.is_file():
            return f"add_context ignored: '{rel}' does not exist at this commit."

        if (
            self.config.imp_ban_tests
            and is_test_path(rel)
            and not self._issue_about_tests
        ):
            return (
                f"add_context rejected: '{rel}' looks like a test file. "
                "Only add production code unless the issue is specifically about tests."
            )

        start = max(1, int(start_line))
        end = max(start, int(end_line))
        total = self._file_line_count(rel)
        if total is not None:
            start = min(start, total)
            end = min(end, total)

        key = (rel, start, end)
        if key in self._seen_spans:
            return f"Span already recorded: {rel}:{start}-{end}."
        self._seen_spans.add(key)

        span = {"file": rel, "start": start, "end": end}
        if reason:
            span["reason"] = reason
        self._all_spans.append(span)
        self._new_since_drain.append(span)
        return f"Recorded context {rel}:{start}-{end} ({len(self._all_spans)} total)."

    def finish(self) -> str:
        """Validate finish against recall/precision guards; return observation."""
        n_spans = len(self._all_spans)
        n_files = len(self.selected_files())
        blocks: List[str] = []

        if self.config.imp_anti_early_finish:
            if n_spans < self.config.min_spans_before_finish:
                blocks.append(
                    f"Need at least {self.config.min_spans_before_finish} spans "
                    f"(have {n_spans}). Search for more edit sites "
                    "(use expand_file_defs on selected files, or search_code again)."
                )
            if n_files < self.config.min_files_before_finish:
                blocks.append(
                    f"Need at least {self.config.min_files_before_finish} file(s) "
                    f"(have {n_files})."
                )

        if self.config.imp_same_file_expand and self.selected_files():
            missing = [f for f in self.selected_files() if f not in self._expanded_files]
            if missing:
                blocks.append(
                    "Before finishing, call expand_file_defs on: "
                    + ", ".join(missing)
                    + " and add any additional relevant spans."
                )

        if (
            self.config.imp_second_file_probe
            and n_files == 1
            and not self._second_file_probed
        ):
            blocks.append(
                "Only one file is selected. Run search_code with a related-symbol query "
                "(imports, sibling modules, or the other side of the API) to check for a "
                "second file before finishing."
            )

        # inherits_expand is suggest-only: do not block finish. Surface a soft
        # reminder when finishing with unprobed INHERITS neighbors.
        suggestion = ""
        if self.config.imp_inherits_expand and self.selected_files():
            pending = self._pending_inheritance_targets()
            if pending and not self._inheritance_expanded:
                preview = ", ".join(pending[:5])
                more = f" (+{len(pending) - 5} more)" if len(pending) > 5 else ""
                suggestion = (
                    f"Note: selected class spans have INHERITS neighbors in other "
                    f"files ({preview}{more}). Optional: call expand_inheritance "
                    "next time if those look relevant."
                )

        if blocks:
            return "finish blocked:\n- " + "\n- ".join(blocks)
        if suggestion:
            return f"Finishing retrieval.\n{suggestion}"
        return "Finishing retrieval."

    def finish_allowed(self) -> bool:
        """True if finish() would accept (no blocking guards)."""
        return not self.finish().startswith("finish blocked")

    # ------------------------------------------------------------------ #
    #                      Trajectory accessors                          #
    # ------------------------------------------------------------------ #

    def drain_new_spans(self) -> List[Dict[str, Any]]:
        """Return and clear spans recorded since the previous drain."""
        spans = self._new_since_drain
        self._new_since_drain = []
        return spans

    def final_spans(self) -> List[Dict[str, Any]]:
        """Copy of all spans recorded for this run."""
        return list(self._all_spans)

    def has_spans(self) -> bool:
        """True if at least one ``add_context`` span has been recorded."""
        return bool(self._all_spans)


class _RetropusToolsLike(Protocol):
    """Minimal surface required to build a Retropus ``ToolSpec`` registry."""

    def tool_schemas(self) -> list[dict]:
        """OpenAI-style tool schema list for the current flag set."""
        ...

    def dispatch(self, name: str, args: dict) -> str:
        """Synchronously run tool ``name`` with ``args``; return observation text."""
        ...


def build_retropus_registry(tools: _RetropusToolsLike) -> dict[str, ToolSpec]:
    """Build a per-run registry whose schemas match ``tools.tool_schemas()``."""
    registry: dict[str, ToolSpec] = {}
    for schema in tools.tool_schemas():
        name = schema["function"]["name"]
        expand_spec = EXPAND_SPECS.get(name)
        if expand_spec is not None:
            # Keep flag-gated schema from tools; reuse graph_tools executor.
            registry[name] = ToolSpec(
                name=name,
                schema=schema,
                executor=expand_spec.executor,
            )
        else:
            registry[name] = ToolSpec(
                name=name,
                schema=schema,
                executor=_make_executor(tools, name),
            )
    return registry


def _make_executor(tools: _RetropusToolsLike, name: str):
    """Build an async ``ToolSpec`` executor that dispatches ``name`` on ``tools``."""

    async def execute(env: Any, args: dict) -> ToolOutcome:
        """Run the tool off-thread and update finish flags when ``name`` is ``finish``."""
        observation = await asyncio.to_thread(tools.dispatch, name, args or {})
        if name == "finish":
            if observation.startswith("finish blocked"):
                env.finish_blocked = True
                env.finish_requested = False
            else:
                env.finish_blocked = False
                env.finish_requested = True
        return ToolOutcome(message=observation)

    return execute
