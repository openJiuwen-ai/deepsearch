"""Tests for the loader (parse_file / parse_files) and registry."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph import parse_file, parse_files
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.languages import LanguageRegistry, get_default_registry, register_builtins
from openjiuwen_search_base.codegraph.parser.models import FileNode


def _parse_bytes(source: bytes, suffix: str = ".py") -> FileNode:
    """Write raw bytes to a temp file and parse it."""
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="wb", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _function_names(file_node: FileNode) -> list[str]:
    return [c.name for c in file_node.children if c.node_type == NodeType.FUNCTION]


class TestParseFile:
    @staticmethod
    def test_unknown_extension_raises():
        with tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False) as f:
            f.write("hello")
            path = Path(f.name)
        try:
            with pytest.raises(ValueError, match="Cannot determine language"):
                asyncio.run(parse_file(path))
            with pytest.raises(ValueError, match="Cannot determine language"):
                asyncio.run(parse_file(path, errors="strict"))
        finally:
            path.unlink()

    @staticmethod
    def test_unknown_extension_ignore_returns_none():
        with tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False) as f:
            f.write("hello")
            path = Path(f.name)
        try:
            assert asyncio.run(parse_file(path, errors="ignore")) is None
        finally:
            path.unlink()

    @staticmethod
    def test_unknown_extension_as_txt():
        with tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False) as f:
            f.write("hello custom\n")
            path = Path(f.name)
        try:
            r = asyncio.run(parse_file(path, errors="as_txt"))
            assert r is not None
            assert r.language == "txt"
            assert r.source == "hello custom\n"
            assert r.children == ()
        finally:
            path.unlink()

    @staticmethod
    def test_invalid_errors_mode_raises():
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("x = 1\n")
            path = Path(f.name)
        try:
            with pytest.raises(ValueError, match="Invalid errors mode"):
                asyncio.run(parse_file(path, errors="skip"))  # type: ignore[arg-type]
        finally:
            path.unlink()

    @staticmethod
    def test_explicit_language_override():
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("# Heading\ntext\n")
            path = Path(f.name)
        try:
            r = asyncio.run(parse_file(path, language="markdown"))
            assert r.language == "markdown"
            assert len(r.children) == 1
        finally:
            path.unlink()

    @staticmethod
    def test_unregistered_language_raises():
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("x")
            path = Path(f.name)
        try:
            with pytest.raises(ValueError, match="No parser registered"):
                asyncio.run(parse_file(path, language="brainfuck"))
        finally:
            path.unlink()

    @staticmethod
    def test_latin1_python_file_is_parsed():
        source = "# café\ndef greet():\n    return 'Café'\n".encode(encoding="latin-1")
        r = _parse_bytes(source)
        assert r.language == "python"
        assert _function_names(r) == ["greet"]
        greet = next(c for c in r.children if c.name == "greet")
        assert "Café" in (greet.source or "")

    @staticmethod
    def test_utf16_python_file_is_parsed():
        source = "def greet():\n    return 'hello'\n".encode(encoding="utf-16")
        r = _parse_bytes(source)
        assert _function_names(r) == ["greet"]

    @staticmethod
    def test_gbk_python_file_is_parsed():
        source = "# 这是一段中文注释，用于测试编码检测。\ndef greet():\n    return '你好世界'\n".encode(encoding="gbk")
        r = _parse_bytes(source)
        assert _function_names(r) == ["greet"]
        greet = next(c for c in r.children if c.name == "greet")
        assert "你好世界" in (greet.source or "")

    @staticmethod
    def test_utf8_non_ascii_python_file_is_parsed():
        source = "def greet():\n    return 'café'\n".encode(encoding="utf-8")
        r = _parse_bytes(source)
        assert _function_names(r) == ["greet"]
        greet = next(c for c in r.children if c.name == "greet")
        assert "café" in (greet.source or "")

    @staticmethod
    def test_undetectable_encoding_does_not_raise():
        r = _parse_bytes(bytes(range(256)) * 4)
        assert r.node_type == NodeType.FILE
        assert r.language == "python"


class TestParseFiles:
    @staticmethod
    def test_multiple_files():
        paths = []
        for i in range(3):
            f = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
            f.write(f"x_{i} = {i}\n")
            f.close()
            paths.append(Path(f.name))
        try:
            results = asyncio.run(parse_files(paths))
            assert len(results) == 3
            for r in results:
                assert r.node_type == NodeType.FILE
        finally:
            for p in paths:
                p.unlink()

    @staticmethod
    def test_preserves_order():
        paths = []
        for name in ["aaa", "zzz", "mmm"]:
            f = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, prefix=name)
            f.write(f"{name} = 1\n")
            f.close()
            paths.append(Path(f.name))
        try:
            results = asyncio.run(parse_files(paths))
            for path, result in zip(paths, results):
                assert result.path == str(path)
        finally:
            for p in paths:
                p.unlink()

    @staticmethod
    def test_ignore_skips_unknown_extensions():
        known = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
        known.write("x = 1\n")
        known.close()
        unknown = tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False)
        unknown.write("nope\n")
        unknown.close()
        known_b = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
        known_b.write("y = 2\n")
        known_b.close()
        paths = [Path(known.name), Path(unknown.name), Path(known_b.name)]
        try:
            results = asyncio.run(parse_files(paths, errors="ignore"))
            assert len(results) == 2
            assert [r.path for r in results] == [str(paths[0]), str(paths[2])]
        finally:
            for p in paths:
                p.unlink()

    @staticmethod
    def test_as_txt_parses_unknown_extensions():
        known = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
        known.write("x = 1\n")
        known.close()
        unknown = tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False)
        unknown.write("plain\n")
        unknown.close()
        paths = [Path(known.name), Path(unknown.name)]
        try:
            results = asyncio.run(parse_files(paths, errors="as_txt"))
            assert len(results) == 2
            assert results[0].language == "python"
            assert results[1].language == "txt"
            assert results[1].source == "plain\n"
        finally:
            for p in paths:
                p.unlink()


class TestLanguageRegistry:
    @staticmethod
    def test_supports_python():
        register_builtins()
        registry = get_default_registry()
        assert registry.supports("foo.py")
        assert registry.supports("README.md")

    @staticmethod
    def test_not_supports_unknown():
        register_builtins()
        registry = get_default_registry()
        assert not registry.supports("file.xyz")

    @staticmethod
    def test_language_for_file():
        register_builtins()
        registry = get_default_registry()
        assert registry.language_for_file("foo.py") == "python"
        assert registry.language_for_file("README.md") == "markdown"
        assert registry.language_for_file("Makefile") == "makefile"
        assert registry.language_for_file("foo.txt") == "txt"
        assert registry.language_for_file("unknown.xyz") is None

    @staticmethod
    def test_get_unregistered_returns_none():
        registry = LanguageRegistry()
        assert registry.get("nonexistent") is None

    @staticmethod
    def test_get_caches_instance():
        register_builtins()
        registry = get_default_registry()
        p1 = registry.get("python")
        p2 = registry.get("python")
        assert p1 is p2
