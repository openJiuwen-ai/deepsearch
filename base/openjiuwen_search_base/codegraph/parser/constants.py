"""Shared enums and constants for the parser package."""

import re
from enum import Enum


class NodeType(str, Enum):
    """Discriminator for all node kinds produced by parsing."""

    # Structural
    FOLDER = "folder"
    FILE = "file"

    # Core
    CLASS = "class"
    DUCK_TYPE = "duck_type"
    INTERFACE = "interface"
    FUNCTION = "function"
    PROPERTY = "property"
    CODE_BLOCK = "code_block"

    # Resolution
    IMPORT = "import"
    CALL = "call"
    LOCAL_VAR = "local_var"

    # Language-specific
    ENUM = "enum"
    STRUCT = "struct"
    UNION = "union"
    MACRO = "macro"
    MODULE = "module"
    TYPE_ALIAS = "type_alias"
    ANNOTATION = "annotation"


class EdgeType(str, Enum):
    """All relation types in the code graph."""

    CONTAINS = "CONTAINS"
    EXPECTS = "EXPECTS"
    IS_SUBSET_OF = "IS_SUBSET_OF"
    IMPORTS = "IMPORTS"
    INHERITS = "INHERITS"
    IMPLEMENTS = "IMPLEMENTS"
    OVERRIDES = "OVERRIDES"
    DECORATED_BY = "DECORATED_BY"
    METACLASS = "METACLASS"
    CALLS = "CALLS"
    INSTANTIATES = "INSTANTIATES"
    TYPE_OF = "TYPE_OF"


FILENAME_PATTERN: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.pyi?$", re.IGNORECASE), "python"),
    (re.compile(r"\.(?:md|markdown)$", re.IGNORECASE), "markdown"),
    (re.compile(r"\.(?:js|jsx|mjs|cjs)$", re.IGNORECASE), "javascript"),
    (re.compile(r"\.ts$", re.IGNORECASE), "typescript"),
    (re.compile(r"\.tsx$", re.IGNORECASE), "tsx"),
    (re.compile(r"\.(?:html?|xhtml)$", re.IGNORECASE), "html"),
    (re.compile(r"\.css$", re.IGNORECASE), "css"),
    (re.compile(r"\.rst$", re.IGNORECASE), "rst"),
    (re.compile(r"(?:^[Mm]akefile(?:\..+)?$|^GNUmakefile$|\.(?:mk|mak|make)$)", re.IGNORECASE), "makefile"),
    (re.compile(r"\.java$", re.IGNORECASE), "java"),
    (re.compile(r"\.c$", re.IGNORECASE), "c"),
    (re.compile(r"\.(?:cpp|cc|cxx|c\+\+|hpp|hh|hxx|h)$", re.IGNORECASE), "cpp"),
    (re.compile(r"\.rs$", re.IGNORECASE), "rust"),
    (re.compile(r"\.go$", re.IGNORECASE), "go"),
    (re.compile(r"\.txt$", re.IGNORECASE), "txt"),
]


def detect_language(filename: str) -> str | None:
    """Match a bare filename (no parent path) against known patterns."""
    for pattern, lang in FILENAME_PATTERN:
        if pattern.search(filename):
            return lang
    return None


MAX_AST_DEPTH: int = 256  # Safety limit when recursively walking tree-sitter ASTs.
INTERNAL_NODES: set[NodeType] = {NodeType.CALL, NodeType.IMPORT, NodeType.LOCAL_VAR}  # Exclude these in export.
FILTER_BUILTIN_NAMES: bool = True
