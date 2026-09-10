# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared fakes for Retropus unit tests (no tree-sitter / bm25s)."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import RetrievalTools
from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig

# Explicit offs so tests are not coupled to product defaults (e.g. inherits_expand).
_DEFAULT_FEAT_OFF = dict(
    feat_ban_tests=False,
    feat_second_file_probe=False,
    feat_same_file_expand=False,
    feat_anti_early_finish=False,
    feat_inherits_expand=False,
    feat_expand_imports=False,
    feat_delete_snippets=False,
)


class FakeKG:
    """Minimal KG stand-in for RetrievalTools / expand / finish tests."""

    def __init__(
        self,
        rels: Optional[list[str]] = None,
        *,
        inherits_edges: Optional[list[Any]] = None,
        file_by_ast: Optional[dict[int, Any]] = None,
    ) -> None:
        self._rels = rels or []
        self._inherits_edges = inherits_edges or []
        self._file_by_ast = file_by_ast or {}

    def get_file_nodes(self):
        out = []
        for rel in self._rels:
            node = SimpleNamespace(relative_path=rel)
            out.append(SimpleNamespace(node=node))
        return out

    def get_inherits_edges(self):
        return self._inherits_edges

    def get_inheritance_neighbors(self, node):
        out = []
        for e in self._inherits_edges:
            if e.source.node_id == node.node_id:
                out.append(e.target)
            elif e.target.node_id == node.node_id:
                out.append(e.source)
        return out

    def get_file_for_ast(self, node):
        return self._file_by_ast.get(node.node_id)


class FakeRetriever:
    """Optional ranked-file stub for pad / search tests."""

    def __init__(self, ranked: Optional[list[Any]] = None) -> None:
        self._ranked = ranked or []

    def score_files_and_defs(self, query, top_k=10, max_defs_per_file=20):
        return self._ranked[:top_k]


def make_retropus_tools(
    repo: Path,
    *,
    kg: Any = None,
    retriever: Any = None,
    rels: Optional[list[str]] = None,
    **cfg: Any,
) -> RetrievalTools:
    """Build ``RetrievalTools`` with feature flags off unless overridden in ``cfg``."""
    opts = dict(_DEFAULT_FEAT_OFF)
    opts.update(cfg)
    return RetrievalTools(
        kg if kg is not None else FakeKG(rels),
        retriever if retriever is not None else FakeRetriever(),
        repo,
        RetropusSearchAgentConfig(**opts),
    )
