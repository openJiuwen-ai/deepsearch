"""Java-specific resolution hooks."""

import re
from functools import cached_property
from pathlib import Path

from ...custom_types import ModuleInfo
from .. import LanguageHooks

_JAVA_GENERIC_RE = re.compile(r"(\w+)<(.+)>$")

_JAVA_CONTAINERS = frozenset(
    {
        "List",
        "Set",
        "Collection",
        "Map",
        "Optional",
        "Stream",
        "Iterable",
        "Iterator",
        "Supplier",
        "Consumer",
        "Function",
        "Predicate",
        "CompletableFuture",
        "Future",
        "Queue",
        "Deque",
        "SortedSet",
        "SortedMap",
        "NavigableSet",
        "NavigableMap",
        "BlockingQueue",
        "ConcurrentMap",
    }
)

_JAVA_PRIMITIVES = frozenset(
    {
        "int",
        "long",
        "short",
        "byte",
        "float",
        "double",
        "boolean",
        "char",
        "void",
    }
)

JAVA_BUILTINS: frozenset[str] = frozenset(
    {
        "Object",
        "String",
        "System",
        "Math",
        "Integer",
        "Long",
        "Short",
        "Byte",
        "Float",
        "Double",
        "Boolean",
        "Character",
        "Void",
        "Number",
        "Class",
        "Comparable",
        "Iterable",
        "Runnable",
        "Thread",
        "Throwable",
        "Exception",
        "RuntimeException",
        "Error",
        "NullPointerException",
        "IllegalArgumentException",
        "IllegalStateException",
        "IndexOutOfBoundsException",
        "UnsupportedOperationException",
        "ClassCastException",
        "ArithmeticException",
        "StackOverflowError",
        "OutOfMemoryError",
        "StringBuilder",
        "StringBuffer",
        "Enum",
        "Override",
        "Deprecated",
        "SuppressWarnings",
        "FunctionalInterface",
        "SafeVarargs",
    }
)


def _split_top_level_java(s: str) -> list[str]:
    """Split a string by commas at bracket depth 0 (angle + square brackets)."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in ("<", "[", "("):
            depth += 1
            current.append(ch)
        elif ch in (">", "]", ")"):
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _extract_java_type_names(annotation: str) -> list[str]:
    """Extract concrete type names from a Java type annotation string."""
    if not annotation:
        return []

    results: list[str] = []
    ann = annotation.strip()

    if ann in _JAVA_PRIMITIVES:
        return []

    if ann.endswith("[]"):
        inner = ann[:-2].strip()
        if inner:
            results.extend(_extract_java_type_names(inner))
        return results

    if ann.startswith("? extends "):
        return _extract_java_type_names(ann[10:].strip())
    if ann.startswith("? super "):
        return _extract_java_type_names(ann[8:].strip())
    if ann == "?":
        return []

    m = _JAVA_GENERIC_RE.match(ann)
    if m:
        outer, inner = m.group(1), m.group(2)
        if outer in _JAVA_CONTAINERS:
            for sub in _split_top_level_java(inner):
                results.extend(_extract_java_type_names(sub))
        else:
            results.append(outer)
        return results

    if "." in ann:
        results.append(ann.rsplit(".", maxsplit=1)[-1])
        results.append(ann)
    else:
        results.append(ann)

    return results


class JavaHooks(LanguageHooks):
    """Java-specific resolution hooks."""

    @cached_property
    def builtins(self) -> frozenset[str]:
        return JAVA_BUILTINS

    @cached_property
    def null_type_names(self) -> frozenset[str]:
        return frozenset({"null", "void"})

    @cached_property
    def supports_decorators(self) -> bool:
        return True

    @cached_property
    def supports_metaclass(self) -> bool:
        return False

    def is_protocol_base(self, name: str) -> bool:
        return False

    def is_constructor_call(self, callee: str) -> bool:
        return True

    def extract_type_names(self, annotation: str) -> list[str]:
        return _extract_java_type_names(annotation)

    @cached_property
    def package_init_files(self) -> frozenset[str]:
        return frozenset({"package-info.java"})

    @cached_property
    def implicit_this(self) -> bool:
        return True

    @cached_property
    def implicit_package_loading(self) -> bool:
        return False

    def detect_modules(
        self,
        folder_rel: str,
        file_names: frozenset[str],
        root: str,
    ) -> list[ModuleInfo]:
        java_files = {f for f in file_names if f.endswith(".java")}
        if not java_files:
            return []
        name = folder_rel.replace("/", ".") if folder_rel else "."
        path = str(Path(root) / folder_rel) if folder_rel else root
        return [ModuleInfo(name=name, language="java", path=path)]

    def unwrap_receiver_type(self, annotation: str, subscript_depth: int) -> str | None:
        """Unwrap Java array types by stripping [] per subscript depth."""
        if not annotation:
            return None
        ann = annotation.strip()
        for _ in range(subscript_depth):
            if ann.endswith("[]"):
                ann = ann[:-2].strip()
            else:
                return None
        if not ann or ann in _JAVA_PRIMITIVES:
            return None
        # Strip generics for the final type name
        m = _JAVA_GENERIC_RE.match(ann)
        if m:
            return m.group(1)
        return ann
