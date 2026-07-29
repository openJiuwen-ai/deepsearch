# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CLI 入口：索引 / 检索单个本地仓库。

    python main.py index --repo /path/to/repo --collection my_repo
    python main.py search --query "issue text..." --collection my_repo --top-k 10
"""

import argparse
import asyncio
import logging
import sys

from openjiuwen_codesearch import CodeSearchConfig, CodeSearchRetriever


async def _run(args: argparse.Namespace) -> int:
    config = CodeSearchConfig.from_env()
    if args.milvus_host:
        config.milvus.host = args.milvus_host
    if args.milvus_port:
        config.milvus.port = args.milvus_port
    if args.command == "index":
        if args.max_files is not None:
            config.index.max_num_files_per_repo = args.max_files
        if args.no_trigram:
            config.index.enable_trigram = False
    retriever = CodeSearchRetriever(config=config, collection_name=args.collection)

    if args.command == "index":
        report = await retriever.index_repository(
            args.repo, revision=args.revision, reset=args.reset
        )
        print(
            f"Indexed {report.files_total} files "
            f"({report.files_new} new, {report.files_reused} reused), "
            f"{report.chunks_inserted} chunks inserted."
        )
        return 0

    query = args.query
    if not query and args.query_file:
        with open(args.query_file, "r", encoding="utf-8") as f:
            query = f.read()
    if not query:
        print("Provide --query or --query-file", file=sys.stderr)
        return 2

    result = await retriever.search(query, revision=args.revision, top_k=args.top_k)
    print(f"Termination: {result.termination.value} | turns={result.turns} "
          f"| cost=${result.total_cost:.4f}")
    for i, hit in enumerate(result.hits, 1):
        print(f"{i:2d}. {hit.file_path} (L{hit.start_line}-L{hit.end_line})")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="codesearch")
    parser.add_argument("--milvus-host", default="", help="Milvus host (default localhost)")
    parser.add_argument("--milvus-port", default="", help="Milvus port (default 19530)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Index a local repository into Milvus")
    p_index.add_argument("--repo", required=True)
    p_index.add_argument("--collection", default="local_repo")
    p_index.add_argument("--revision", default="local")
    p_index.add_argument("--reset", action="store_true")
    p_index.add_argument("--max-files", type=int, default=None,
                         help="Cap the number of files indexed (space control for pilots)")
    p_index.add_argument("--no-trigram", action="store_true",
                         help="Skip trigram field (~7x text size); disables substring search")

    p_search = sub.add_parser("search", help="Run the agentic retriever")
    p_search.add_argument("--query", default="")
    p_search.add_argument("--query-file", default="")
    p_search.add_argument("--collection", default="local_repo")
    p_search.add_argument("--revision", default="local")
    p_search.add_argument("--top-k", type=int, default=20)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
