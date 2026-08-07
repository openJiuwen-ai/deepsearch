# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""On-disk Retropus KG / BM25 dump-load (no tree-sitter required for KG tests)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen_codesearch.config.agent import RetropusSearchAgentConfig
from openjiuwen_codesearch.config.config import CodeSearchConfig
from openjiuwen_codesearch.config.llm import LLMConfig, LLMSuite
from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    FileNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
    TextNode,
)
from openjiuwen_codesearch.retropus.graph.knowledge_graph import KnowledgeGraph
from openjiuwen_codesearch.retropus.persist import (
    SCHEMA_VERSION,
    cache_is_compatible,
    collection_index_dir,
    config_fingerprint,
    dump_knowledge_graph,
    dump_retropus_index,
    load_knowledge_graph,
    load_retropus_index,
    read_manifest,
    safe_collection_name,
)


def _mini_kg() -> KnowledgeGraph:
    root = KnowledgeGraphNode(0, FileNode(basename=".", relative_path="."))
    file_n = KnowledgeGraphNode(1, FileNode(basename="a.py", relative_path="a.py"))
    ast_n = KnowledgeGraphNode(
        2,
        ASTNode(type="function_definition", start_line=1, end_line=2, text="def f():\n  pass\n"),
    )
    text_n = KnowledgeGraphNode(
        3, TextNode(text="hello world", start_line=1, end_line=1)
    )
    nodes = [root, file_n, ast_n, text_n]
    edges = [
        KnowledgeGraphEdge(root, file_n, KnowledgeGraphEdgeType.has_file),
        KnowledgeGraphEdge(file_n, ast_n, KnowledgeGraphEdgeType.has_ast),
        KnowledgeGraphEdge(file_n, text_n, KnowledgeGraphEdgeType.has_text),
    ]
    kg = KnowledgeGraph(
        max_ast_depth=6,
        chunk_size=1000,
        chunk_overlap=200,
        root_node_id=0,
        root_node=root,
        knowledge_graph_nodes=nodes,
        knowledge_graph_edges=edges,
    )
    kg.set_imports_labels_map({(1, 1): "self"})
    return kg


def test_safe_collection_name():
    assert safe_collection_name("my/repo") == "my_repo"
    assert safe_collection_name("") == "default"


def test_dump_load_knowledge_graph(tmp_path: Path):
    kg = _mini_kg()
    path = tmp_path / "kg.pkl"
    dump_knowledge_graph(kg, path)
    loaded = load_knowledge_graph(path)

    assert len(loaded.get_all_nodes()) == 4
    assert len(loaded.get_all_edges()) == 3
    assert loaded.max_ast_depth == 6
    assert loaded.chunk_size == 1000
    assert loaded.chunk_overlap == 200
    assert loaded.get_imports_label(1, 1) == "self"
    assert loaded.get_file_nodes()[0].node.relative_path in (".", "a.py")


def test_cache_fingerprint_mismatch(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = RetropusSearchAgentConfig()
    fp = config_fingerprint(cfg)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "repo_dir": str(repo.resolve()),
        "fingerprint": fp,
    }
    assert cache_is_compatible(manifest, repo_dir=repo, fingerprint=fp)
    other = dict(fp)
    other["chunk_size"] = 999
    assert not cache_is_compatible(manifest, repo_dir=repo, fingerprint=other)
    assert not cache_is_compatible(
        {**manifest, "schema_version": SCHEMA_VERSION + 1},
        repo_dir=repo,
        fingerprint=fp,
    )


def test_dump_load_retropus_index_roundtrip(tmp_path: Path):
    pytest.importorskip("bm25s", reason="requires retropus extra (bm25s)")

    from openjiuwen_codesearch.retropus.retrievers.bm25 import BM25Retriever

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n  return 1\n", encoding="utf-8")

    kg = _mini_kg()
    cfg = RetropusSearchAgentConfig(tokenize_workers=1)
    retriever = BM25Retriever(kg, tokenize_workers=1)
    retriever.build_index()

    cache = tmp_path / "cache" / "col"
    dump_retropus_index(
        cache,
        kg=kg,
        retriever=retriever,
        repo_dir=repo,
        collection="col",
        config=cfg,
    )
    assert (cache / "manifest.json").is_file()
    manifest = read_manifest(cache)
    assert manifest is not None
    assert manifest["schema_version"] == SCHEMA_VERSION

    loaded = load_retropus_index(cache, config=cfg, repo_dir=repo)
    assert loaded is not None
    kg2, ret2, repo2 = loaded
    assert repo2 == repo.resolve()
    assert len(kg2.get_ast_nodes()) == len(kg.get_ast_nodes())
    assert len(ret2.get_documents()) == len(retriever.get_documents())

    # Wrong repo → miss
    assert load_retropus_index(cache, config=cfg, repo_dir=tmp_path / "other") is None

    # Config fingerprint miss
    bad_cfg = RetropusSearchAgentConfig(tokenize_workers=1, chunk_size=123)
    assert load_retropus_index(cache, config=bad_cfg, repo_dir=repo) is None


def test_retriever_reuses_disk_cache_across_instances(tmp_path: Path):
    pytest.importorskip("bm25s", reason="requires retropus extra (bm25s)")
    pytest.importorskip("tree_sitter", reason="requires retropus extra (tree-sitter)")

    import asyncio

    from openjiuwen_codesearch.api.retriever import CodeSearchRetriever

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def alpha():\n    return 42\n", encoding="utf-8")

    index_root = tmp_path / "retropus_idx"
    cfg = CodeSearchConfig(llm=LLMSuite(main=LLMConfig(model_name="fake")))
    cfg.agent.engine = "retropus"
    cfg.retropus.index_dir = str(index_root)
    cfg.retropus.tokenize_workers = 1

    r1 = CodeSearchRetriever(config=cfg, collection_name="c1")
    report = asyncio.run(r1.index_repository(str(repo)))
    assert report.files_total >= 1
    assert report.files_new >= 1
    cache_path = collection_index_dir(index_root, "c1")
    assert (cache_path / "kg.pkl").is_file()
    asyncio.run(r1.close())

    r2 = CodeSearchRetriever(config=cfg, collection_name="c1")
    report2 = asyncio.run(r2.index_repository(str(repo)))
    assert report2.files_reused >= 1
    assert report2.files_new == 0
    assert report2.chunks_inserted == 0
    assert r2.has_retropus_index()
    asyncio.run(r2.close())
