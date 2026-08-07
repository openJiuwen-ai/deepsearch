"""BM25-based knowledge-graph text retrieval using ``bm25s`` (copied from Prometheus).

The knowledge graph is tokenized and indexed **once** (lazily, on the first
search) into a single persistent ``bm25s.BM25`` instance kept on
``self.retriever``. Subsequent queries reuse that index and simply score/filter
against it. Call :meth:`reindex` if the underlying knowledge graph changes.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import bm25s
from tqdm import tqdm

from openjiuwen_codesearch.retropus.graph.graph_types import KnowledgeGraphNode
from openjiuwen_codesearch.retropus.retrievers.base import MAX_RESULT, AbstractBaseRetriever
from openjiuwen_codesearch.utils.log_utils import get_logger

logger = get_logger(__name__)

# Match this Mac Mini's logical core count (``os.cpu_count()`` → 10 here).
DEFAULT_TOKENIZE_WORKERS = os.cpu_count() or 10
# Below this, process-pool spawn overhead usually exceeds any speedup.
_PARALLEL_TOKENIZE_MIN_DOCS = 256

# Identifiers / numbers for code-aware tokenization (keeps single-char idents).
_CODE_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
# Split PascalCase / camelCase / SCREAMING_SNACKS pieces after `_` splitting.
_CAMEL_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
# Same pattern bm25s uses by default (sklearn CountVectorizer-style).
_BM25S_DEFAULT_TOKEN_RE = re.compile(r"(?u)\b\w\w+\b")

EMPTY_DATA_MESSAGE = "Your query returned empty result, please try a different query!"


def format_knowledge_graph_data(data: Sequence[Mapping[str, Any]]) -> str:
    """Format retriever result dicts into a readable string for the LLM.

    Copied from Prometheus's ``format_knowledge_graph_data`` (the Neo4j-era name is
    kept so the copied retrievers work unchanged).

    Emits a static header first, then results with keys in sorted order so the
    observation prefix stays byte-stable across similar queries.
    """
    if not data:
        return EMPTY_DATA_MESSAGE

    output = (
        "Retriever results (inspect with read_file, then add_context for tight spans):\n\n"
    )
    for index, row_result in enumerate(data):
        output += f"Result {index + 1}:\n"
        for key in sorted(row_result.keys()):
            output += f"{key}: {str(row_result[key])}\n"
        output += "\n\n"
    return output.strip()


def _split_identifier(ident: str) -> List[str]:
    """Split ``snake_case`` / ``camelCase`` / ``PascalCase`` into fragments."""
    parts: List[str] = []
    for piece in ident.split("_"):
        if not piece:
            continue
        camel_parts = _CAMEL_SPLIT_RE.findall(piece)
        parts.extend(camel_parts if camel_parts else [piece])
    return parts


def tokenize_code_text(text: str) -> List[str]:
    """Code-aware lexical tokens: full identifiers plus snake/camel subtokens.

    Emits the lowercased identifier and, when it has multiple parts, each
    snake_case / camelCase fragment. Unlike ``bm25s``'s default
    ``(?u)\\b\\w\\w+\\b`` splitter, this keeps 1-char identifiers (``x``, ``i``).
    """
    tokens: List[str] = []
    for match in _CODE_IDENT_RE.finditer(text):
        ident = match.group(0)
        lower = ident.lower()
        tokens.append(lower)
        parts = _split_identifier(ident)
        if len(parts) > 1:
            for part in parts:
                fragment = part.lower()
                if fragment and fragment != lower:
                    tokens.append(fragment)
    return tokens


def tokenize_bm25s_default_text(text: str) -> List[str]:
    """Lowercased tokens matching ``bm25s.tokenize``'s default regex (no stopwords)."""
    return [token.lower() for token in _BM25S_DEFAULT_TOKEN_RE.findall(text)]


def tokenize_corpus_parallel(
    texts: Sequence[str],
    tokenize_fn: Callable[[str], List[str]],
    *,
    workers: int = DEFAULT_TOKENIZE_WORKERS,
    show_progress: bool = False,
    desc: str = "Tokenize",
) -> List[List[str]]:
    """Tokenize ``texts`` with a process pool (order-preserving).

    Falls back to sequential for tiny corpora or ``workers <= 1`` so spawn
    overhead does not dominate.
    """
    n_docs = len(texts)
    if n_docs == 0:
        return []

    workers = max(1, int(workers))
    use_pool = workers > 1 and n_docs >= _PARALLEL_TOKENIZE_MIN_DOCS
    if not use_pool:
        iterator: Sequence[str] = texts
        if show_progress:
            iterator = tqdm(texts, desc=desc, unit="doc", leave=False)
        return [tokenize_fn(text) for text in iterator]

    chunksize = max(32, n_docs // (workers * 16))
    logger.info(
        "BM25: parallel tokenize workers=%d chunksize=%d docs=%d",
        workers,
        chunksize,
        n_docs,
    )
    with ProcessPoolExecutor(max_workers=workers) as pool:
        mapped = pool.map(tokenize_fn, texts, chunksize=chunksize)
        if show_progress:
            mapped = tqdm(mapped, total=n_docs, desc=desc, unit="doc", leave=False)
        return list(mapped)


def tokenize_code_corpus(
    texts: Sequence[str],
    *,
    workers: int = DEFAULT_TOKENIZE_WORKERS,
    show_progress: bool = False,
) -> List[List[str]]:
    """Tokenize many documents with :func:`tokenize_code_text`."""
    return tokenize_corpus_parallel(
        texts,
        tokenize_code_text,
        workers=workers,
        show_progress=show_progress,
        desc="Code tokenize",
    )

# Keywords / suffixes used to recognise class/function definition AST nodes across
# languages (e.g. Python ``function_definition`` / ``class_definition``, Java
# ``method_declaration``, Rust ``function_item``).
_DEFINITION_KEYWORDS = (
    "function",
    "method",
    "class",
    "constructor",
    "interface",
    "module",
    "struct",
    "trait",
    "type",
)
_DEFINITION_SUFFIXES = ("definition", "declaration", "specifier", "item", "spec")


def is_definition_ast_type(ast_type: str) -> bool:
    """Best-effort check that a tree-sitter node type denotes a class/function def."""
    t = ast_type.lower()
    if not any(keyword in t for keyword in _DEFINITION_KEYWORDS):
        return False
    if t.endswith(_DEFINITION_SUFFIXES):
        return True
    return t in {"function", "method", "class", "module", "type_spec"}


# Name after a definition keyword: ``def foo`` / ``class Bar`` / ``func baz`` etc.
_DEF_NAME_RE = re.compile(
    r"\b(def|class|func|function|fn|interface|module|struct|trait|enum)\s+"
    r"([A-Za-z_$][\w$]*)"
)
# Fallback: an identifier immediately followed by ``(`` (e.g. Java/C method decls).
_CALL_NAME_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")


def definition_name(ast_node: KnowledgeGraphNode) -> str:
    """Extract just the class/function name from a definition AST node."""
    text = ast_node.node.text
    match = _DEF_NAME_RE.search(text)
    if match:
        return match.group(2)
    match = _CALL_NAME_RE.search(text)
    if match:
        return match.group(1)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line or ast_node.node.type


def definition_label(ast_node: KnowledgeGraphNode) -> str:
    """Render a definition as ``<keyword> <name>`` (e.g. ``class Foo`` / ``def bar``)."""
    text = ast_node.node.text
    match = _DEF_NAME_RE.search(text)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return definition_name(ast_node)


def _ast_contains(outer: KnowledgeGraphNode, inner: KnowledgeGraphNode) -> bool:
    """True if ``outer``'s line range strictly encloses ``inner``'s (same file)."""
    outer_node, inner_node = outer.node, inner.node
    if (
        outer_node.start_line <= inner_node.start_line
        and outer_node.end_line >= inner_node.end_line
    ):
        return (outer_node.start_line, outer_node.end_line) != (
            inner_node.start_line,
            inner_node.end_line,
        )
    return False


def _build_definition_forest(defs: List[Tuple[KnowledgeGraphNode, float]]) -> List[Dict[str, Any]]:
    """Nest definitions by containment (methods under their class), keeping input order."""
    entries = [{"node": node, "score": score, "children": []} for node, score in defs]

    def parent_index(child_idx: int) -> Optional[int]:
        """Index of the tightest enclosing definition, or ``None`` for a root."""
        child = entries[child_idx]["node"]
        best_idx: Optional[int] = None
        best_span = None
        for other_idx, other in enumerate(entries):
            if other_idx == child_idx:
                continue
            if _ast_contains(other["node"], child):
                span = other["node"].node.end_line - other["node"].node.start_line
                if best_span is None or span < best_span:
                    best_span = span
                    best_idx = other_idx
        return best_idx

    roots: List[Dict[str, Any]] = []
    for idx, entry in enumerate(entries):
        p_idx = parent_index(idx)
        if p_idx is None:
            roots.append(entry)
        else:
            entries[p_idx]["children"].append(entry)
    return roots


def _render_forest(entries: List[Dict[str, Any]], depth: int, lines: List[str]) -> None:
    """Append indented definition labels for ``entries`` (and nested children) to ``lines``."""
    seen: set[str] = set()
    for entry in entries:
        name = definition_name(entry["node"])
        if name in seen:
            continue
        seen.add(name)
        lines.append(f"{'    ' * depth}- {definition_label(entry['node'])}")
        if entry["children"]:
            _render_forest(entry["children"], depth + 1, lines)


def render_scored_file_tree(ranked_files: List[Dict[str, Any]]) -> str:
    """Render :meth:`BM25Retriever.score_files_and_defs` output as a compact tree.

    Static instruction header first, ranked paths last — keeps any shared prefix
    of the observation stable for KV / prompt caching.
    """
    lines: List[str] = [
        "Query-scoped file tree (most relevant files and their definitions). "
        "Use search_code / read_file / add_context on candidates below:"
    ]
    for entry in ranked_files:
        file_node = entry["file_node"]
        lines.append(file_node.node.relative_path)
        forest = _build_definition_forest(entry["defs"])
        _render_forest(forest, depth=1, lines=lines)
    return "\n".join(lines)


@dataclass
class _Document:
    """Metadata for a single indexed node, aligned by position with the corpus."""

    kind: str  # "ast" or "text"
    file_node: KnowledgeGraphNode
    node: KnowledgeGraphNode


class BM25Retriever(AbstractBaseRetriever):
    """Rank AST/Text nodes with BM25 via :mod:`bm25s`, indexing the KG only once."""

    def __init__(
        self,
        kg,
        k1: float = 1.5,
        b: float = 0.75,
        method: str = "lucene",
        code_aware_tokenizer: bool = False,
        tokenize_workers: int = DEFAULT_TOKENIZE_WORKERS,
    ):
        """Configure BM25 hyperparameters and optional code-aware tokenization."""
        super().__init__(kg)
        self.k1 = k1
        self.b = b
        self.method = method
        self.code_aware_tokenizer = code_aware_tokenizer
        self.tokenize_workers = max(1, int(tokenize_workers))

        # Persistent index state, built lazily and reused across queries.
        self.retriever: Optional[bm25s.BM25] = None
        self._documents: List[_Document] = []

    def get_documents(self) -> List[_Document]:
        """Corpus rows currently bound to the BM25 index (may be empty)."""
        return list(self._documents)

    def set_documents(self, documents: Sequence[_Document]) -> None:
        """Replace corpus rows (used when restoring an index from disk)."""
        self._documents = list(documents)

    @staticmethod
    def make_document(
        kind: str, file_node: KnowledgeGraphNode, node: KnowledgeGraphNode
    ) -> _Document:
        """Build a corpus row for persist/restore without exposing ``_Document``."""
        return _Document(kind, file_node, node)

    # ------------------------------------------------------------------ #
    #                         Index construction                         #
    # ------------------------------------------------------------------ #

    def _tokenize_fn(self) -> Callable[[str], List[str]]:
        """Return the tokenizer used for both index build and query time."""
        return tokenize_code_text if self.code_aware_tokenizer else tokenize_bm25s_default_text

    def _tokenize(
        self, texts: str | Sequence[str], *, show_progress: bool = False
    ) -> List[List[str]]:
        """Tokenize with bm25s-compatible defaults, or the code-aware splitter."""
        tokenize_fn = self._tokenize_fn()
        if isinstance(texts, str):
            return [tokenize_fn(texts)]
        return tokenize_corpus_parallel(
            texts,
            tokenize_fn,
            workers=self.tokenize_workers,
            show_progress=show_progress,
            desc="Code tokenize" if self.code_aware_tokenizer else "BM25 tokenize",
        )

    def _collect_documents(self) -> List[_Document]:
        """Gather every searchable node (AST + text) from the knowledge graph."""
        t0 = time.perf_counter()
        documents: List[_Document] = []

        # Uses KG's cached AST→file map (built once after parse).
        for file_node, ast_node in self.iter_ast_candidates(list(self.kg.get_file_nodes())):
            documents.append(_Document("ast", file_node, ast_node))
        n_ast = len(documents)
        logger.info(
            "BM25: collected %d AST docs (%.1fs)", n_ast, time.perf_counter() - t0
        )

        t1 = time.perf_counter()
        for text_node in self.kg.get_text_nodes():
            file_node = self.find_file_node_of_a_text_node(text_node)
            documents.append(_Document("text", file_node, text_node))
        logger.info(
            "BM25: collected %d text docs (%.1fs); total=%d",
            len(documents) - n_ast,
            time.perf_counter() - t1,
            len(documents),
        )
        return documents

    def build_index(self, force: bool = False) -> None:
        """Tokenize and index the knowledge graph once (no-op if already built)."""
        if self.retriever is not None and not force:
            return

        t0 = time.perf_counter()
        tokenizer_name = "code-aware" if self.code_aware_tokenizer else "bm25s-default"
        logger.info("BM25: collecting searchable nodes from knowledge graph")
        self._documents = self._collect_documents()
        if not self._documents:
            self.retriever = None
            logger.info("BM25: no documents to index (%.1fs)", time.perf_counter() - t0)
            return

        n_docs = len(self._documents)
        logger.info(
            "BM25: tokenizing %d documents with %s (workers=%d)",
            n_docs,
            tokenizer_name,
            self.tokenize_workers,
        )
        # Progress bars for the corpus build only; query tokenization stays quiet.
        show_pbar = logger.isEnabledFor(logging.INFO)
        corpus_tokens = self._tokenize(
            [doc.node.node.text for doc in self._documents],
            show_progress=show_pbar,
        )
        logger.info("BM25: building index over %d documents", n_docs)
        retriever = bm25s.BM25(k1=self.k1, b=self.b, method=self.method)
        retriever.index(corpus_tokens, show_progress=show_pbar, leave_progress=False)
        self.retriever = retriever
        logger.info(
            "BM25: index ready (%d docs, tokenizer=%s, workers=%d, %.1fs)",
            n_docs,
            tokenizer_name,
            self.tokenize_workers,
            time.perf_counter() - t0,
        )

    def reindex(self) -> None:
        """Force a rebuild of the index (e.g. after the knowledge graph changes)."""
        self.build_index(force=True)

    # ------------------------------------------------------------------ #
    #                            Querying                                 #
    # ------------------------------------------------------------------ #

    def _rank_all(self, query: str) -> List[Tuple[int, float]]:
        """Score the full indexed corpus, returning ``(doc_index, score)`` best first."""
        self.build_index()
        if self.retriever is None or not query.strip():
            return []

        query_tokens = self._tokenize(query)
        k = len(self._documents)
        doc_ids, scores = self.retriever.retrieve(query_tokens, k=k, show_progress=False)

        ranked: List[Tuple[int, float]] = []
        for doc_id, score in zip(doc_ids[0], scores[0]):
            score_f = float(score)
            if score_f > 0:
                ranked.append((int(doc_id), score_f))
        return ranked

    def search_ast_nodes(
        self, query: str, target_file_nodes: List[KnowledgeGraphNode]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """BM25-rank AST nodes under ``target_file_nodes``; return formatted text + hits."""
        target_ids = {n.node_id for n in target_file_nodes}

        results: List[Dict[str, Any]] = []
        for idx, _score in self._rank_all(query):
            doc = self._documents[idx]
            if doc.kind != "ast" or doc.file_node.node_id not in target_ids:
                continue
            results.append(self._format_ast_result(doc.file_node, doc.node))
            if len(results) >= MAX_RESULT:
                break

        return format_knowledge_graph_data(results), results

    def search_text_nodes(
        self, query: str, basename: Optional[str] = None
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """BM25-rank text chunks, optionally restricted to a file basename."""
        results: List[Dict[str, Any]] = []
        for idx, _score in self._rank_all(query):
            doc = self._documents[idx]
            if doc.kind != "text":
                continue
            if basename is not None and doc.file_node.node.basename != basename:
                continue
            results.append(self._format_text_result(doc.node, doc.file_node))
            if len(results) >= MAX_RESULT:
                break

        return format_knowledge_graph_data(results), results

    # ------------------------------------------------------------------ #
    #                     Scored definition overview                     #
    # ------------------------------------------------------------------ #

    def score_files_and_defs(
        self,
        query: str,
        top_k: int = 32,
        max_defs_per_file: Optional[int] = 20,
        min_score_ratio: float = 0.1,
    ) -> List[Dict[str, Any]]:
        """Rank files by their best-scoring class/function AST node for ``query``.

        Files whose score is below ``min_score_ratio * best_score`` are dropped
        before the inherits boost / top-k cut. ``min_score_ratio <= 0`` disables
        the relative filter.
        """
        per_file: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()
        for idx, score in self._rank_all(query):
            doc = self._documents[idx]
            if doc.kind != "ast" or not is_definition_ast_type(doc.node.node.type):
                continue
            entry = per_file.get(doc.file_node.node_id)
            if entry is None:
                entry = {"file_node": doc.file_node, "score": score, "defs": []}
                per_file[doc.file_node.node_id] = entry
            else:
                entry["score"] = max(entry["score"], score)
            if max_defs_per_file is None or len(entry["defs"]) < max_defs_per_file:
                entry["defs"].append((doc.node, score))

        ranked_files = sorted(per_file.values(), key=lambda e: e["score"], reverse=True)
        if ranked_files and min_score_ratio > 0:
            best = float(ranked_files[0]["score"])
            if best > 0:
                floor = best * min_score_ratio
                ranked_files = [e for e in ranked_files if float(e["score"]) >= floor]
        from openjiuwen_codesearch.retropus.graph.inherits import boost_ranked_files_with_inherits

        # Pass the full scored pool so a high-scoring subclass can surface its
        # superclass file even when that file was weakly ranked on its own.
        return boost_ranked_files_with_inherits(
            self.kg,
            ranked_files,
            top_k=top_k,
            max_defs_per_file=max_defs_per_file,
        )
