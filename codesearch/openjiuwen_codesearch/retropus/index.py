"""Build a Retropus knowledge-graph index from a checked-out repository."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Union

from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig
from openjiuwen_codesearch.retropus.graph.knowledge_graph import KnowledgeGraph
from openjiuwen_codesearch.retropus.retrievers import BM25Retriever
from openjiuwen_codesearch.retropus.retrievers.base import AbstractBaseRetriever
from openjiuwen_codesearch.utils.log_utils import get_logger

logger = get_logger(__name__)


def build_index(
    repo_dir: Union[str, Path], config: RetropusSearchAgentConfig
) -> KnowledgeGraph:
    """Parse ``repo_dir`` into an in-memory tree-sitter knowledge graph.

    Args:
      repo_dir: Path to a checked-out repository (at the target commit).
      config: Retropus configuration (AST depth, chunk sizes).

    Returns:
      A fully-built :class:`KnowledgeGraph`.
    """
    repo_path = Path(repo_dir)
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repository directory does not exist: {repo_path}")

    logger.info("Building knowledge graph for %s", repo_path)
    t0 = time.perf_counter()
    kg = KnowledgeGraph(
        max_ast_depth=config.max_ast_depth,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        root_node_id=0,
    )
    asyncio.run(kg.build_graph(repo_path))
    logger.info(
        "Knowledge graph built in %.1fs (files=%d ast=%d text=%d)",
        time.perf_counter() - t0,
        len(kg.get_file_nodes()),
        len(kg.get_ast_nodes()),
        len(kg.get_text_nodes()),
    )
    return kg


def build_retriever(
    kg: KnowledgeGraph, config: RetropusSearchAgentConfig
) -> AbstractBaseRetriever:
    """Instantiate the BM25 retriever over ``kg`` and build its index eagerly."""
    if config.retriever != "bm25":
        raise ValueError(
            f"Unsupported Retropus retriever {config.retriever!r}; only 'bm25' is available."
        )
    logger.info("Building bm25 retriever index")
    t0 = time.perf_counter()
    retriever: AbstractBaseRetriever = BM25Retriever(
        kg,
        code_aware_tokenizer=config.code_aware_tokenizer,
        tokenize_workers=config.tokenize_workers,
    )

    # Build the (lazy) index now so the first tool call isn't slow / surprising.
    retriever.build_index()  # type: ignore[attr-defined]
    logger.info("Retriever index ready in %.1fs", time.perf_counter() - t0)
    return retriever
