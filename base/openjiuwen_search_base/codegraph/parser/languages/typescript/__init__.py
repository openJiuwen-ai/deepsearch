"""TypeScript / TSX language parser and hooks."""

from .hooks import TsHooks
from .parse import TsxParser, TypeScriptParser, parse_sync

__all__ = ["TypeScriptParser", "TsxParser", "TsHooks", "parse_sync"]
