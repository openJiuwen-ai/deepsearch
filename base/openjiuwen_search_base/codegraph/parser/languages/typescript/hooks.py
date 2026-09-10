"""TypeScript/JavaScript-specific resolution hooks."""

import re
from functools import cached_property
from pathlib import Path

from ...custom_types import ModuleInfo
from .. import LanguageHooks

_TS_GENERIC_RE = re.compile(r"(\w+)<(.+)>$")

_TS_CONTAINERS = frozenset(
    {
        "Array",
        "Promise",
        "Partial",
        "Readonly",
        "Required",
        "Record",
        "Pick",
        "Omit",
        "Exclude",
        "Extract",
        "ReturnType",
        "InstanceType",
        "Awaited",
    }
)

JS_TS_BUILTINS: frozenset[str] = frozenset(
    {
        "console",
        "Math",
        "JSON",
        "Object",
        "Array",
        "String",
        "Number",
        "Boolean",
        "Date",
        "RegExp",
        "Error",
        "TypeError",
        "RangeError",
        "Promise",
        "Map",
        "Set",
        "WeakMap",
        "WeakSet",
        "Symbol",
        "Proxy",
        "Reflect",
        "parseInt",
        "parseFloat",
        "isNaN",
        "isFinite",
        "encodeURIComponent",
        "decodeURIComponent",
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "clearInterval",
        "fetch",
        "alert",
        "confirm",
        "require",
        "module",
        "exports",
        "process",
        "Buffer",
        "global",
        "window",
        "document",
    }
)


def _split_top_level_ts(s: str) -> list[str]:
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


class TsHooks(LanguageHooks):
    """TypeScript/JavaScript-specific resolution hooks."""

    @cached_property
    def builtins(self) -> frozenset[str]:
        return JS_TS_BUILTINS

    @cached_property
    def null_type_names(self) -> frozenset[str]:
        return frozenset({"null", "undefined"})

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
        return _extract_ts_type_names(annotation)

    @cached_property
    def package_init_files(self) -> frozenset[str]:
        return frozenset(
            {
                "index.js",
                "index.jsx",
                "index.mjs",
                "index.cjs",
                "index.ts",
                "index.tsx",
            }
        )

    def detect_modules(
        self,
        folder_rel: str,
        file_names: frozenset[str],
        root: str,
    ) -> list[ModuleInfo]:
        if not self.package_init_files & file_names:
            return []
        name = Path(folder_rel).as_posix() if folder_rel else "."
        path = str(Path(root) / folder_rel) if folder_rel else root
        return [ModuleInfo(name=name, language="typescript", path=path)]


def _extract_ts_type_names(annotation: str) -> list[str]:
    """Extract concrete type names from a TS/JS type annotation."""
    if not annotation:
        return []

    results: list[str] = []

    if "|" in annotation:
        for part in annotation.split("|"):
            part = part.strip()
            if part and part not in ("null", "undefined"):
                results.extend(_extract_ts_type_names(part))
        return results

    ann = annotation.strip()

    m = _TS_GENERIC_RE.match(ann)
    if m:
        outer, inner = m.group(1), m.group(2)
        if outer in _TS_CONTAINERS:
            for sub in _split_top_level_ts(inner):
                results.extend(_extract_ts_type_names(sub))
        else:
            results.append(outer)
        return results

    if ann.endswith("[]"):
        inner = ann[:-2].strip()
        if inner:
            results.extend(_extract_ts_type_names(inner))
        return results

    if "." in ann:
        results.append(ann.rsplit(".", maxsplit=1)[-1])
        results.append(ann)
    else:
        results.append(ann)

    return results
