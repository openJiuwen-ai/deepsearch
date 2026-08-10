# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""On-disk dump/load for Retropus knowledge graph + BM25 index.

Layout under ``{index_dir}/{collection}/``::

    manifest.json   # schema version, repo path, config fingerprint
    kg.pkl          # nodes / edges / imports_labels (+ build knobs)
    documents.json  # BM25 corpus row → (kind, file_node_id, node_id)
    bm25/           # bm25s native index (absent when corpus is empty)

Cache is keyed by ``collection`` name. A hit requires matching
``schema_version``, resolved ``repo_dir``, and index-build fingerprint
(``max_ast_depth`` / chunk sizes / tokenizer). Use ``reset=True`` on
index to rebuild.
"""

import json
import pickle
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig
from openjiuwen_codesearch.retropus.graph.graph_types import KnowledgeGraphNode
from openjiuwen_codesearch.retropus.graph.knowledge_graph import KnowledgeGraph
from openjiuwen_codesearch.utils.log_utils import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
KG_NAME = "kg.pkl"
DOCUMENTS_NAME = "documents.json"
BM25_DIRNAME = "bm25"

_SAFE_COLLECTION_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_collection_name(collection: str) -> str:
    """Map a collection name to a single path segment."""
    cleaned = _SAFE_COLLECTION_RE.sub("_", (collection or "").strip())
    return cleaned or "default"


def collection_index_dir(index_dir: str | Path, collection: str) -> Path:
    """Directory that holds one collection's Retropus dump."""
    return Path(index_dir).expanduser().resolve() / safe_collection_name(collection)


def config_fingerprint(config: RetropusSearchAgentConfig) -> dict[str, Any]:
    """Fields that affect KnowledgeGraph / BM25 contents (not agent-loop knobs)."""
    return {
        "retriever": config.retriever,
        "max_ast_depth": config.max_ast_depth,
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap,
        "code_aware_tokenizer": bool(config.code_aware_tokenizer),
    }


def _node_by_id(kg: KnowledgeGraph) -> dict[int, KnowledgeGraphNode]:
    return {n.node_id: n for n in kg.get_all_nodes()}


def dump_knowledge_graph(kg: KnowledgeGraph, path: Path) -> None:
    """Serialize KnowledgeGraph nodes/edges/labels (pickle; local trusted cache)."""
    payload = {
        "max_ast_depth": kg.max_ast_depth,
        "chunk_size": kg.chunk_size,
        "chunk_overlap": kg.chunk_overlap,
        "root_node_id": kg.root_node_id,
        "root_node": kg.root_node,
        "nodes": list(kg.get_all_nodes()),
        "edges": list(kg.get_all_edges()),
        "imports_labels": [
            [src, tgt, label]
            for (src, tgt), label in kg.get_imports_labels_map().items()
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)


def load_knowledge_graph(path: Path) -> KnowledgeGraph:
    """Rebuild a ``KnowledgeGraph`` from :func:`dump_knowledge_graph` output."""
    with path.open("rb") as fh:
        payload = pickle.load(fh)

    kg = KnowledgeGraph(
        max_ast_depth=int(payload["max_ast_depth"]),
        chunk_size=int(payload["chunk_size"]),
        chunk_overlap=int(payload["chunk_overlap"]),
        root_node_id=int(payload["root_node_id"]),
        root_node=payload.get("root_node"),
        knowledge_graph_nodes=payload["nodes"],
        knowledge_graph_edges=payload["edges"],
    )
    labels: dict[tuple[int, int], str] = {}
    for row in payload.get("imports_labels") or ():
        src, tgt, label = row
        labels[(int(src), int(tgt))] = str(label)
    kg.set_imports_labels_map(labels)
    return kg


def dump_bm25_retriever(retriever: Any, directory: Path) -> None:
    """Write BM25 index + document id map under ``directory``.

    ``retriever`` is a :class:`BM25Retriever`. Empty corpora skip the bm25s
    subdirectory (only ``documents.json`` with ``[]``).
    """
    directory.mkdir(parents=True, exist_ok=True)
    if hasattr(retriever, "get_documents"):
        docs = list(retriever.get_documents())
    else:
        docs = []
    doc_rows = [
        {
            "kind": doc.kind,
            "file_node_id": doc.file_node.node_id,
            "node_id": doc.node.node_id,
        }
        for doc in docs
    ]
    (directory / DOCUMENTS_NAME).write_text(
        json.dumps(doc_rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    bm25_dir = directory / BM25_DIRNAME
    if bm25_dir.exists():
        shutil.rmtree(bm25_dir)

    underlying = getattr(retriever, "retriever", None)
    if underlying is None or not docs:
        return

    bm25_dir.mkdir(parents=True, exist_ok=True)
    # Corpus text is recoverable from the KnowledgeGraph; keep the sparse index only.
    underlying.save(str(bm25_dir), corpus=None)


def load_bm25_retriever(
    directory: Path,
    kg: KnowledgeGraph,
    config: RetropusSearchAgentConfig,
) -> Any:
    """Restore a :class:`BM25Retriever` bound to ``kg`` from a dump."""
    from openjiuwen_codesearch.retropus.retrievers.bm25 import (  # noqa: PLC0415
        BM25Retriever,
    )

    docs_path = directory / DOCUMENTS_NAME
    if not docs_path.is_file():
        raise FileNotFoundError(f"missing BM25 document map: {docs_path}")

    doc_rows = json.loads(docs_path.read_text(encoding="utf-8"))
    by_id = _node_by_id(kg)
    documents = []
    for row in doc_rows:
        file_node = by_id.get(int(row["file_node_id"]))
        node = by_id.get(int(row["node_id"]))
        if file_node is None or node is None:
            raise ValueError(
                f"BM25 document refers to missing KnowledgeGraph node "
                f"(file={row.get('file_node_id')}, node={row.get('node_id')})"
            )
        documents.append(
            BM25Retriever.make_document(str(row["kind"]), file_node, node)
        )

    out = BM25Retriever(
        kg,
        code_aware_tokenizer=config.code_aware_tokenizer,
        tokenize_workers=config.tokenize_workers,
    )
    out.set_documents(documents)

    bm25_dir = directory / BM25_DIRNAME
    if not documents:
        out.retriever = None
        return out
    if not bm25_dir.is_dir():
        raise FileNotFoundError(f"missing bm25s index directory: {bm25_dir}")

    import bm25s  # noqa: PLC0415

    out.retriever = bm25s.BM25.load(
        str(bm25_dir), load_corpus=False, show_progress=False
    )
    return out


def write_manifest(
    directory: Path,
    *,
    repo_dir: Path,
    collection: str,
    fingerprint: Mapping[str, Any],
) -> None:
    """Write cache metadata next to the dumped index."""
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "repo_dir": str(repo_dir.resolve()),
        "collection": collection,
        "fingerprint": dict(fingerprint),
    }
    (directory / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_manifest(directory: Path) -> Optional[dict[str, Any]]:
    """Return parsed manifest or ``None`` if missing/invalid JSON."""
    path = directory / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def cache_is_compatible(
    manifest: Mapping[str, Any],
    *,
    repo_dir: Path,
    fingerprint: Mapping[str, Any],
) -> bool:
    """True when ``manifest`` matches schema, repo, and build fingerprint."""
    if int(manifest.get("schema_version", -1)) != SCHEMA_VERSION:
        return False
    try:
        cached_repo = Path(str(manifest["repo_dir"])).resolve()
    except (KeyError, TypeError, ValueError, OSError):
        return False
    if cached_repo != repo_dir.resolve():
        return False
    cached_fp = manifest.get("fingerprint")
    if not isinstance(cached_fp, dict):
        return False
    return dict(cached_fp) == dict(fingerprint)


def dump_retropus_index(
    directory: Path,
    *,
    kg: KnowledgeGraph,
    retriever: Any,
    repo_dir: Path,
    collection: str,
    config: RetropusSearchAgentConfig,
) -> Path:
    """Atomically replace ``directory`` with a fresh dump of kg + BM25."""
    directory = Path(directory)
    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{directory.name}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        dump_knowledge_graph(kg, staging / KG_NAME)
        dump_bm25_retriever(retriever, staging)
        write_manifest(
            staging,
            repo_dir=repo_dir,
            collection=collection,
            fingerprint=config_fingerprint(config),
        )
        if directory.exists():
            shutil.rmtree(directory)
        staging.rename(directory)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    logger.info("Retropus index dumped to %s", directory)
    return directory


def load_retropus_index(
    directory: Path,
    *,
    config: RetropusSearchAgentConfig,
    repo_dir: Optional[Path] = None,
) -> Optional[Tuple[KnowledgeGraph, Any, Path]]:
    """Load kg + BM25 from ``directory`` if present and compatible.

    When ``repo_dir`` is set, it must match the manifest. When omitted,
    the manifest's ``repo_dir`` is used (CLI search-by-collection).

    Returns:
      ``(kg, retriever, repo_dir)`` or ``None`` if the cache is missing
      or incompatible.
    """
    directory = Path(directory)
    manifest = read_manifest(directory)
    if manifest is None:
        return None

    try:
        cached_repo = Path(str(manifest["repo_dir"])).resolve()
    except (KeyError, TypeError, ValueError, OSError):
        return None

    if repo_dir is not None and cached_repo != Path(repo_dir).resolve():
        logger.info(
            "Retropus cache at %s is for a different repo (%s != %s)",
            directory,
            cached_repo,
            Path(repo_dir).resolve(),
        )
        return None

    if not cache_is_compatible(
        manifest,
        repo_dir=cached_repo,
        fingerprint=config_fingerprint(config),
    ):
        logger.info(
            "Retropus cache at %s incompatible with current config/schema",
            directory,
        )
        return None

    kg_path = directory / KG_NAME
    if not kg_path.is_file():
        return None

    try:
        kg = load_knowledge_graph(kg_path)
        retriever = load_bm25_retriever(directory, kg, config)
    except Exception as exc:  # noqa: BLE001 — corrupt cache → rebuild
        logger.warning("Failed to load Retropus cache at %s: %s", directory, exc)
        return None

    logger.info("Retropus index loaded from %s (repo=%s)", directory, cached_repo)
    return kg, retriever, cached_repo


def clear_retropus_index(directory: Path) -> None:
    """Remove a dumped index directory if it exists."""
    directory = Path(directory)
    if directory.exists():
        shutil.rmtree(directory)
        logger.info("Cleared Retropus index at %s", directory)
