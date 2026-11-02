"""JavaScript language parser and hooks."""

from ..typescript.hooks import TsHooks as JsHooks
from .parse import JavaScriptParser

__all__ = ["JavaScriptParser", "JsHooks"]
