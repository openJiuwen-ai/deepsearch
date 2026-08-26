# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Map filesystem paths to tree-sitter language identifiers."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class FileType(StrEnum):
    """Languages / config formats we can feed to tree-sitter."""

    BASH = "bash"
    C = "c"
    CSHARP = "csharp"
    CPP = "cpp"
    CSS = "css"
    DOCKERFILE = "dockerfile"
    GO = "go"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    KOTLIN = "kotlin"
    PHP = "php"
    PYTHON = "python"
    SQL = "sql"
    RUST = "rust"
    RUBY = "ruby"
    TYPESCRIPT = "typescript"
    HTML = "html"
    YAML = "yaml"
    XML = "xml"
    PROPERTIES = "properties"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_path(cls, path: Path) -> FileType:
        """Infer type from basename / suffix; ``UNKNOWN`` when unsupported."""
        return resolve_file_type(path)


# Extension → language. Lookups are O(1); special filenames handled separately.
_SUFFIX_TO_TYPE: dict[str, FileType] = {
    ".sh": FileType.BASH,
    ".bash": FileType.BASH,
    ".c": FileType.C,
    ".cs": FileType.CSHARP,
    ".css": FileType.CSS,
    ".cpp": FileType.CPP,
    ".cc": FileType.CPP,
    ".cxx": FileType.CPP,
    ".go": FileType.GO,
    ".java": FileType.JAVA,
    ".js": FileType.JAVASCRIPT,
    ".kt": FileType.KOTLIN,
    ".php": FileType.PHP,
    ".py": FileType.PYTHON,
    ".sql": FileType.SQL,
    ".rs": FileType.RUST,
    ".rb": FileType.RUBY,
    ".ts": FileType.TYPESCRIPT,
    ".html": FileType.HTML,
    ".yaml": FileType.YAML,
    ".yml": FileType.YAML,
    ".xml": FileType.XML,
    ".properties": FileType.PROPERTIES,
}


def resolve_file_type(path: Path) -> FileType:
    """Infer ``FileType`` from basename / suffix; ``UNKNOWN`` when unsupported."""
    if path.name.lower() == "dockerfile":
        return FileType.DOCKERFILE
    return _SUFFIX_TO_TYPE.get(path.suffix.lower(), FileType.UNKNOWN)
