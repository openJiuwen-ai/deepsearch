"""Java language parser and hooks."""

from .hooks import JavaHooks
from .parse import JavaParser

__all__ = ["JavaParser", "JavaHooks"]
