# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CLI --engine / --index-dir wiring (no network / Milvus)."""

import argparse
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen_codesearch.cli import _run
from openjiuwen_codesearch.domain.result import CodeSearchResult, Termination


def _args(**overrides):
    base = dict(
        command="search",
        milvus_host="",
        milvus_port="",
        engine="retropus",
        index_dir="",
        query="q",
        query_file="",
        collection="local_repo",
        revision="local",
        top_k=5,
        repo="",
        reset=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cli_sets_retropus_engine_and_index_dir(tmp_path: Path):
    report = MagicMock(files_total=1, files_new=1, files_reused=0, chunks_inserted=1)
    result = CodeSearchResult(
        hits=[], termination=Termination.SUBMITTED, turns=1
    )

    fake = MagicMock()
    fake.index_repository = AsyncMock(return_value=report)
    fake.search = AsyncMock(return_value=result)

    with patch("openjiuwen_codesearch.cli.CodeSearchRetriever", return_value=fake) as ctor:
        with patch("openjiuwen_codesearch.cli.CodeSearchConfig.from_env") as from_env:
            cfg = from_env.return_value
            cfg.agent.engine = "graph"
            cfg.retropus.index_dir = "./output/retropus"
            cfg.milvus.host = "localhost"
            cfg.milvus.port = "19530"

            code = asyncio.run(
                _run(
                    _args(
                        command="index",
                        repo=str(tmp_path),
                        engine="retropus",
                        index_dir=str(tmp_path / "idx"),
                        max_files=None,
                        no_trigram=False,
                    )
                )
            )

    assert code == 0
    assert cfg.agent.engine == "retropus"
    assert cfg.retropus.index_dir == str(tmp_path / "idx")
    ctor.assert_called_once()
    fake.index_repository.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "expected"),
    [("", 0), ("retropus index not ready", 1)],
)
def test_search_exit_code_reflects_result_error(error: str, expected: int):
    """搜索结果带 error（如 INDEX_NOT_READY）时退出码必须非零，否则脚本/CI 误判成功。"""
    termination = Termination.INDEX_NOT_READY if error else Termination.SUBMITTED
    result = CodeSearchResult(hits=[], termination=termination, error=error)

    fake = MagicMock()
    fake.search = AsyncMock(return_value=result)

    with patch("openjiuwen_codesearch.cli.CodeSearchRetriever", return_value=fake):
        with patch("openjiuwen_codesearch.cli.CodeSearchConfig.from_env") as from_env:
            from_env.return_value = MagicMock()
            code = asyncio.run(_run(_args()))

    assert code == expected
