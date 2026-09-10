# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Parse source files into tree-sitter syntax trees."""

from __future__ import annotations

from pathlib import Path

from tree_sitter._binding import Tree
from tree_sitter_language_pack import get_parser

from openjiuwen_codesearch.retropus.parser.file_types import FileType


def language_for(path: Path) -> str | None:
    """Return the tree-sitter language key for ``path``, or ``None``.

    ``FileType`` member values already match ``tree_sitter_language_pack``
    language ids, so no secondary lookup table is required.
    """
    kind = FileType.from_path(path)
    if kind is FileType.UNKNOWN:
        return None
    return str(kind)


def supports_file(file: Path) -> bool:
    """Whether ``file`` has a registered tree-sitter grammar."""
    return language_for(file) is not None


def parse(file: Path) -> Tree:
    """Parse ``file`` bytes with the matching grammar; raises if unsupported."""
    lang = language_for(file)
    if lang is None:
        raise ValueError(f"unsupported file type for tree-sitter: {file}")
    return get_parser(lang).parse(file.read_bytes())
