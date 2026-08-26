"""Tests for the loader (parse_file / parse_files) and registry."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from openjiuwen_search_base.codegraph import parse_file, parse_files
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.languages import LanguageRegistry, get_default_registry, register_builtins


class TestParseFile:
    @staticmethod
    def test_unknown_extension_raises():
        with tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False) as f:
            f.write("hello")
            path = Path(f.name)
        try:
            with pytest.raises(ValueError, match="Cannot determine language"):
                asyncio.run(parse_file(path))
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
