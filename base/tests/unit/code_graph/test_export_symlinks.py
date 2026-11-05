"""Symlink handling for ``index_directory`` (codegraph CLI and MCP tool)."""

import asyncio
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph.export import index_directory


def _make_symlink(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):  # pragma: no cover - Windows without privilege
        pytest.skip("symlinks are not supported in this environment")


def _relative_files(root: Path) -> list[str]:
    paths = asyncio.run(index_directory(root, quiet=True))[3]
    return sorted(path.relative_to(root).as_posix() for path in paths)


def test_directory_symlink_escaping_root_is_not_indexed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 42\n", encoding="utf-8")
    _make_symlink(outside, root / "escape")

    assert _relative_files(root) == ["main.py"]


def test_directory_symlink_cycle_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "a").mkdir(parents=True)
    (root / "a" / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _make_symlink(root, root / "a" / "loop")

    assert _relative_files(root) == ["a/main.py"]


def test_file_symlink_escaping_root_is_not_indexed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 42\n", encoding="utf-8")
    _make_symlink(outside / "secret.py", root / "secret_link.py")

    assert _relative_files(root) == ["main.py"]
