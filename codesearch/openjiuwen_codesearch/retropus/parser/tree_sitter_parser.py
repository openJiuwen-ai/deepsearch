# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Parse source files into tree-sitter syntax trees."""

from __future__ import annotations

from pathlib import Path

from tree_sitter._binding import Tree
from tree_sitter_language_pack import get_parser

from openjiuwen_codesearch.retropus.parser.file_types import FileType

# FileType → tree-sitter-language-pack language key.
_LANG_BY_TYPE: dict[FileType, str] = {
    FileType.BASH: "bash",
    FileType.C: "c",
    FileType.CSHARP: "csharp",
    FileType.CSS: "css",
    FileType.CPP: "cpp",
    FileType.DOCKERFILE: "dockerfile",
    FileType.GO: "go",
    FileType.JAVA: "java",
    FileType.JAVASCRIPT: "javascript",
    FileType.KOTLIN: "kotlin",
    FileType.PHP: "php",
    FileType.PYTHON: "python",
    FileType.SQL: "sql",
    FileType.RUST: "rust",
    FileType.RUBY: "ruby",
    FileType.TYPESCRIPT: "typescript",
    FileType.HTML: "html",
    FileType.YAML: "yaml",
    FileType.XML: "xml",
    FileType.PROPERTIES: "properties",
}

# Back-compat alias used by older call sites / tests.
FILE_TYPE_TO_LANG = _LANG_BY_TYPE


def language_for(path: Path) -> str | None:
    """Return the tree-sitter language key for ``path``, or ``None``."""
    return _LANG_BY_TYPE.get(FileType.from_path(path))


def supports_file(file: Path) -> bool:
    """Whether ``file`` has a registered tree-sitter grammar."""
    return language_for(file) is not None


def parse(file: Path) -> Tree:
    """Parse ``file`` bytes with the matching grammar; raises if unsupported."""
    lang = language_for(file)
    if lang is None:
        raise ValueError(f"unsupported file type for tree-sitter: {file}")
    parser = get_parser(lang)
    return parser.parse(file.read_bytes())
