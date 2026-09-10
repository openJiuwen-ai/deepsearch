"""Python language parser and hooks."""

from .hooks import PythonHooks
from .parse import PythonParser

__all__ = ["PythonParser", "PythonHooks"]
