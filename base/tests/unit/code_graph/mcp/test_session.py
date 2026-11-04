"""Tests for MCP GraphSession indexing and search."""

from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph.mcp.session import GraphSession


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a tiny Python project for indexing."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(
        "class Greeter:\n"
        "    def hello(self, name: str) -> str:\n"
        "        return f'hi {name}'\n"
        "\n"
        "def greet(name: str) -> str:\n"
        "    return Greeter().hello(name)\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_index_and_search(sample_project: Path) -> None:
    session = GraphSession()
    assert not session.is_indexed

    summary = await session.index(sample_project)
    assert session.is_indexed
    assert summary["file_count"] >= 1
    assert summary["node_count"] > 0
    assert summary["edge_count"] > 0
    assert Path(summary["output_dir"]).is_dir()
    assert (Path(summary["output_dir"]) / "nodes.jsonl").is_file()

    fns = session.search_nodes("{type:function} greet", limit=10)
    assert any(n["name"] == "greet" for n in fns.matches)
    assert fns.total >= 1
    assert "type:function" in fns.tag_counts

    methods = session.search_nodes("hello")
    assert any(n["name"].endswith("hello") for n in methods.matches)

    classes = session.search_nodes("Greeter")
    assert any(n["name"] == "Greeter" for n in classes.matches)

    contains = session.search_edges("{relation:CONTAINS}", limit=50)
    assert contains.total > 0
    assert len(contains.matches) > 0
    assert contains.tag_counts


@pytest.mark.asyncio
async def test_search_before_index_raises() -> None:
    session = GraphSession()
    with pytest.raises(RuntimeError, match="No graph indexed"):
        session.search_nodes("x")
    with pytest.raises(RuntimeError, match="No graph indexed"):
        session.search_edges("{relation:CALLS}")


@pytest.mark.asyncio
async def test_index_missing_dir(tmp_path: Path) -> None:
    session = GraphSession()
    with pytest.raises(NotADirectoryError):
        await session.index(tmp_path / "does-not-exist")


@pytest.mark.asyncio
async def test_index_empty_dir(tmp_path: Path) -> None:
    session = GraphSession()
    with pytest.raises(FileNotFoundError, match="No supported files"):
        await session.index(tmp_path)
