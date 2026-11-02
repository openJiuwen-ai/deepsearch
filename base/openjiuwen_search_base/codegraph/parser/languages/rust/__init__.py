"""Rust language parser and hooks."""

from .hooks import RustHooks
from .parse import RustParser

__all__ = ["RustParser", "RustHooks"]
