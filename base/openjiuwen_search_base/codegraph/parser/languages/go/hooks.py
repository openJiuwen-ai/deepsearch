"""Go-specific resolution hooks."""

import re
from functools import cached_property
from pathlib import Path

from ...custom_types import ModuleInfo
from .. import LanguageHooks

GO_BUILTINS: frozenset[str] = frozenset(
    {
        # Built-in functions
        "append",
        "cap",
        "close",
        "complex",
        "copy",
        "delete",
        "imag",
        "len",
        "make",
        "new",
        "panic",
        "print",
        "println",
        "real",
        "recover",
        "min",
        "max",
        "clear",
        # Built-in types
        "bool",
        "byte",
        "complex64",
        "complex128",
        "error",
        "float32",
        "float64",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "rune",
        "string",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
        "any",
        "comparable",
        "true",
        "false",
        "nil",
        "iota",
        # Common stdlib package short names used as receivers/callees
        "fmt",
        "context",
        "errors",
        "io",
        "os",
        "sync",
        "time",
        "strings",
        "bytes",
        "strconv",
        "json",
        "http",
    }
)

_MAP_RE = re.compile(r"^map\s*\[")


class GoHooks(LanguageHooks):
    """Go-specific resolution hooks."""

    @cached_property
    def builtins(self) -> frozenset[str]:
        return GO_BUILTINS

    @cached_property
    def null_type_names(self) -> frozenset[str]:
        return frozenset()

    @cached_property
    def supports_decorators(self) -> bool:
        return False

    @cached_property
    def implicit_this(self) -> bool:
        return False

    def is_constructor_call(self, callee: str) -> bool:
        """PascalCase callees are often constructor-like factory functions."""
        if not callee:
            return False
        short = callee.rsplit(".", 1)[-1]
        return short[:1].isupper()

    def extract_type_names(self, annotation: str) -> list[str]:
        """Extract type names from a Go type annotation."""
        if not annotation:
            return []
        names: list[str] = []
        self._collect(annotation.strip(), names)
        return names

    def _collect(self, ann: str, out: list[str]) -> None:
        ann = ann.strip()
        if not ann:
            return
        while ann.startswith("*"):
            ann = ann[1:].strip()
        if ann.startswith("[]"):
            self._collect(ann[2:].strip(), out)
            return
        if ann.startswith("[") and "]" in ann:
            # [N]T or [...]T
            rest = ann[ann.find("]") + 1 :].strip()
            self._collect(rest, out)
            return
        if _MAP_RE.match(ann):
            inner = self._bracket_inner(ann, ann.find("["))
            if inner is not None:
                # map[K]V — collect both
                self._collect(inner, out)
                after = ann[ann.find("]") + 1 :].strip()
                self._collect(after, out)
            return
        if ann.startswith("chan ") or ann.startswith("<-chan") or ann.startswith("chan<-"):
            rest = re.sub(r"^(?:<-)?chan(?:<-)?\s*", "", ann).strip()
            self._collect(rest, out)
            return
        # Strip type params: Foo[T] or pkg.Foo[T]
        bare = ann.split("[", 1)[0].strip()
        if not bare or bare in GO_BUILTINS:
            return
        short = bare.rsplit(".", 1)[-1]
        if short and short[0].isupper():
            out.append(short)
            if "." in bare:
                out.append(bare)

    @staticmethod
    def _bracket_inner(ann: str, start: int) -> str | None:
        depth = 0
        for i in range(start, len(ann)):
            if ann[i] == "[":
                depth += 1
            elif ann[i] == "]":
                depth -= 1
                if depth == 0:
                    return ann[start + 1 : i]
        return None

    def unwrap_receiver_type(self, annotation: str, subscript_depth: int) -> str | None:
        """Peel slice/map/pointer wrappers for subscripted receivers."""
        if not annotation or subscript_depth <= 0:
            return None
        ann = annotation.strip()
        for _ in range(subscript_depth):
            peeled = self._peel_once(ann)
            if peeled is None:
                return None
            ann = peeled
        while ann.startswith("*"):
            ann = ann[1:].strip()
        short = ann.split("[", 1)[0].rsplit(".", 1)[-1].strip()
        if short and short[0].isupper() and short not in GO_BUILTINS:
            return short
        return short if short and short[0].isupper() else None

    def _peel_once(self, ann: str) -> str | None:
        ann = ann.strip()
        if ann.startswith("*"):
            return ann[1:].strip()
        if ann.startswith("[]"):
            return ann[2:].strip()
        if ann.startswith("[") and "]" in ann and not _MAP_RE.match(ann):
            return ann[ann.find("]") + 1 :].strip()
        if _MAP_RE.match(ann):
            # map[K]V → V
            end = ann.find("]")
            if end >= 0:
                return ann[end + 1 :].strip()
        return None

    @cached_property
    def package_init_files(self) -> frozenset[str]:
        return frozenset()

    @cached_property
    def implicit_package_loading(self) -> bool:
        return False

    def detect_modules(
        self,
        folder_rel: str,
        file_names: frozenset[str],
        root: str,
    ) -> list[ModuleInfo]:
        if not any(n.endswith(".go") for n in file_names):
            return []
        name = folder_rel.replace("/", ".") if folder_rel else Path(root).name
        path = str(Path(root) / folder_rel) if folder_rel else root
        return [ModuleInfo(name=name, language="go", path=path)]
