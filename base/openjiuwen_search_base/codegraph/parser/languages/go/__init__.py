"""Go language parser and hooks."""

from .hooks import GoHooks
from .parse import GoParser

__all__ = ["GoParser", "GoHooks"]
