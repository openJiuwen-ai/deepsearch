"""Language plugin system: base class, hooks, and registry."""

import threading
from abc import ABC, abstractmethod
from functools import cached_property
from pathlib import Path

from ..constants import detect_language
from ..custom_types import ModuleInfo
from ..models.structural import FileNode


class BaseLanguageParser(ABC):
    """Abstract base for every language-specific parser.

    Subclasses implement :meth:`parse` which receives already-read source
    bytes and returns a fully-populated :class:`FileNode`.
    """

    @abstractmethod
    async def parse(self, path: Path, source: bytes) -> FileNode:
        """Parse *source* bytes into a :class:`FileNode` tree."""
        ...


class LanguageHooks:
    """Per-language hooks for the resolution pipeline.

    Subclass and override methods for language-specific behavior.
    The defaults are safe no-ops suitable for languages with no
    resolution-specific logic (HTML, CSS, Markdown, etc.).
    """

    @cached_property
    def builtins(self) -> frozenset[str]:
        return frozenset()

    @cached_property
    def null_type_names(self) -> frozenset[str]:
        return frozenset()

    @cached_property
    def supports_decorators(self) -> bool:
        return False

    @cached_property
    def supports_metaclass(self) -> bool:
        return False

    def is_protocol_base(self, name: str) -> bool:
        return False

    def is_constructor_call(self, callee: str) -> bool:
        return False

    def extract_type_names(self, annotation: str) -> list[str]:
        """Extract concrete type names from a type annotation string."""
        if not annotation:
            return []
        return [annotation.strip()]

    @cached_property
    def callable_wrappers(self) -> frozenset[str]:
        """Fully-qualified names of wrapper functions (e.g. ``functools.partial``).

        The resolver verifies that calls actually refer to these via imports
        before treating the first positional argument as the underlying callable.
        """
        return frozenset()

    @cached_property
    def package_init_files(self) -> frozenset[str]:
        return frozenset()

    @cached_property
    def implicit_this(self) -> bool:
        """Whether unqualified method calls implicitly refer to ``this``/``self``.

        When ``True``, a bare call like ``foo()`` inside a class method is
        treated as a potential sibling method call (Java, C#, Kotlin).
        When ``False`` (Python, JS/TS), ``self.``/``this.`` is required and
        bare calls are resolved through outer scopes instead.
        """
        return False

    @cached_property
    def implicit_package_loading(self) -> bool:
        return False

    def detect_modules(
        self,
        folder_rel: str,
        file_names: frozenset[str],
        root: str,
    ) -> list[ModuleInfo]:
        """Return modules found in a folder. Default: none."""
        return []

    def unwrap_receiver_type(self, annotation: str, subscript_depth: int) -> str | None:
        """Unwrap container/pointer type annotations to infer the element type."""
        return None


class LanguageRegistry:
    """Maps language names to parser classes.  Thread-safe, lazily instantiates.

    Usage::

        registry = LanguageRegistry()
        registry.register("python", PythonParser)
        parser = registry.get("python")
    """

    def __init__(self) -> None:
        self._classes: dict[str, type[BaseLanguageParser]] = {}
        self._instances: dict[str, BaseLanguageParser] = {}
        self._hooks_classes: dict[str, type[LanguageHooks]] = {}
        self._hooks_instances: dict[str, LanguageHooks] = {}
        self._registered_languages: tuple[str, ...] = ()
        self._lock = threading.Lock()

    def register(
        self,
        language: str,
        parser_cls: type[BaseLanguageParser],
        hooks_cls: type[LanguageHooks] | None = None,
    ) -> None:
        """Register a parser class for *language*."""
        with self._lock:
            self._classes[language] = parser_cls
            self._instances.pop(language, None)
            if hooks_cls is not None:
                self._hooks_classes[language] = hooks_cls
                self._hooks_instances.pop(language, None)
            self._registered_languages = tuple(self._classes)

    def get(self, language: str) -> BaseLanguageParser | None:
        """Return a (cached) parser instance for *language*, or ``None``."""
        if language in self._instances:
            return self._instances[language]
        with self._lock:
            if language in self._instances:
                return self._instances[language]
            cls = self._classes.get(language)
            if cls is None:
                return None
            instance = cls()
            self._instances[language] = instance
            return instance

    def get_hooks(self, language: str) -> LanguageHooks:
        """Return a (cached) hooks instance for *language*."""
        if language in self._hooks_instances:
            return self._hooks_instances[language]
        with self._lock:
            if language in self._hooks_instances:
                return self._hooks_instances[language]
            cls = self._hooks_classes.get(language, LanguageHooks)
            instance = cls()
            self._hooks_instances[language] = instance
            return instance

    def supports(self, filename: str) -> bool:
        """Return whether *filename* (e.g. ``"foo.py"``) has a registered parser."""
        lang = detect_language(filename)
        return lang is not None and lang in self._classes

    def language_for_file(self, filename: str) -> str | None:
        """Resolve a filename to its language name, or ``None``."""
        return detect_language(filename)

    @property
    def registered_languages(self) -> tuple[str, ...]:
        """Return all registered language names (cached, reset on register)."""
        return self._registered_languages


# -- Singleton registry & built-in registration ------------------------------

_DEFAULT_REGISTRY = LanguageRegistry()


def get_default_registry() -> LanguageRegistry:
    """Return the process-wide default :class:`LanguageRegistry`."""
    return _DEFAULT_REGISTRY


def register_builtins() -> None:
    """Register all built-in language parsers with the default registry."""
    from .c.cpp_parse import CppParser
    from .c.hooks import CHooks, CppHooks
    from .c.parse import CBaseParser
    from .css.parse import CssParser
    from .go.hooks import GoHooks
    from .go.parse import GoParser
    from .html.parse import HtmlParser
    from .java.hooks import JavaHooks
    from .java.parse import JavaParser
    from .javascript.hooks import JsHooks
    from .javascript.parse import JavaScriptParser
    from .makefile.parse import MakefileParser
    from .markdown.parse import MarkdownParser
    from .python.hooks import PythonHooks
    from .python.parse import PythonParser
    from .rst.parse import RstParser
    from .rust.hooks import RustHooks
    from .rust.parse import RustParser
    from .txt.parse import TxtParser
    from .typescript.hooks import TsHooks
    from .typescript.parse import TsxParser, TypeScriptParser

    _DEFAULT_REGISTRY.register("python", PythonParser, PythonHooks)
    _DEFAULT_REGISTRY.register("markdown", MarkdownParser)
    _DEFAULT_REGISTRY.register("typescript", TypeScriptParser, TsHooks)
    _DEFAULT_REGISTRY.register("tsx", TsxParser, TsHooks)
    _DEFAULT_REGISTRY.register("javascript", JavaScriptParser, JsHooks)
    _DEFAULT_REGISTRY.register("java", JavaParser, JavaHooks)
    _DEFAULT_REGISTRY.register("c", CBaseParser, CHooks)
    _DEFAULT_REGISTRY.register("cpp", CppParser, CppHooks)
    _DEFAULT_REGISTRY.register("rust", RustParser, RustHooks)
    _DEFAULT_REGISTRY.register("go", GoParser, GoHooks)
    _DEFAULT_REGISTRY.register("html", HtmlParser)
    _DEFAULT_REGISTRY.register("css", CssParser)
    _DEFAULT_REGISTRY.register("makefile", MakefileParser)
    _DEFAULT_REGISTRY.register("rst", RstParser)
    _DEFAULT_REGISTRY.register("txt", TxtParser)
