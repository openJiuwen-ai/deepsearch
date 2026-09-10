"""Python-specific resolution hooks."""

import builtins as _builtins_mod
import re
from functools import cached_property
from pathlib import Path

from ...custom_types import ModuleInfo
from .. import LanguageHooks

_GENERIC_RE = re.compile(r"(\w+)\[(.+)]$")

_PY_CONTAINERS = frozenset(
    {
        "Optional",
        "list",
        "List",
        "dict",
        "Dict",
        "set",
        "Set",
        "tuple",
        "Tuple",
        "Sequence",
        "Iterable",
        "Iterator",
        "Generator",
        "Callable",
        "Type",
        "ClassVar",
        "Final",
    }
)

PYTHON_BUILTINS: frozenset[str] = frozenset(dir(_builtins_mod))


def _split_top_level(s: str) -> list[str]:
    """Split a string by commas at bracket depth 0."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in ("[", "("):
            depth += 1
            current.append(ch)
        elif ch in ("]", ")"):
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


class PythonHooks(LanguageHooks):
    """Python-specific resolution hooks."""

    @cached_property
    def builtins(self) -> frozenset[str]:
        return PYTHON_BUILTINS

    @cached_property
    def null_type_names(self) -> frozenset[str]:
        return frozenset({"None"})

    @cached_property
    def supports_decorators(self) -> bool:
        return True

    @cached_property
    def supports_metaclass(self) -> bool:
        return True

    def is_protocol_base(self, name: str) -> bool:
        return "Protocol" in name

    def is_constructor_call(self, callee: str) -> bool:
        return callee[0].isupper() if callee else False

    def extract_type_names(self, annotation: str) -> list[str]:
        return _extract_py_type_names(annotation)

    @cached_property
    def callable_wrappers(self) -> frozenset[str]:
        return frozenset({"functools.partial"})

    @cached_property
    def package_init_files(self) -> frozenset[str]:
        return frozenset({"__init__.py"})

    @cached_property
    def implicit_package_loading(self) -> bool:
        return True

    def detect_modules(
        self,
        folder_rel: str,
        file_names: frozenset[str],
        root: str,
    ) -> list[ModuleInfo]:
        if "__init__.py" not in file_names:
            return []
        name = folder_rel.replace("/", ".") if folder_rel else "."
        path = str(Path(root) / folder_rel) if folder_rel else root
        return [ModuleInfo(name=name, language="python", path=path)]

    _SUBSCRIPT_CONTAINERS: frozenset[str] = frozenset(
        {
            "list",
            "List",
            "Sequence",
            "MutableSequence",
            "set",
            "Set",
            "frozenset",
            "FrozenSet",
            "MutableSet",
            "Deque",
            "deque",
        }
    )
    _MAP_CONTAINERS: frozenset[str] = frozenset(
        {
            "dict",
            "Dict",
            "defaultdict",
            "OrderedDict",
            "MutableMapping",
            "Mapping",
            "ChainMap",
            "Counter",
        }
    )
    _TUPLE_CONTAINERS: frozenset[str] = frozenset({"tuple", "Tuple"})

    def unwrap_receiver_type(self, annotation: str, subscript_depth: int) -> str | None:
        """Unwrap Python generic container annotations to get the element type."""
        if not annotation:
            return None
        ann = annotation.strip()
        for _ in range(subscript_depth):
            m = _GENERIC_RE.match(ann)
            if m is None:
                return None
            outer, inner = m.group(1), m.group(2)
            if outer in self._MAP_CONTAINERS:
                parts = _split_top_level(inner)
                if len(parts) >= 2:
                    ann = parts[1].strip()
                else:
                    return None
            elif outer in self._SUBSCRIPT_CONTAINERS:
                parts = _split_top_level(inner)
                ann = parts[0].strip() if parts else ""
            elif outer in self._TUPLE_CONTAINERS:
                parts = _split_top_level(inner)
                # tuple[T, ...] -> T
                non_ellipsis = [p for p in parts if p != "..."]
                ann = non_ellipsis[0].strip() if non_ellipsis else ""
            else:
                return None
        if not ann or ann == "None":
            return None
        # Strip any remaining generics to get the simple type name
        final_m = _GENERIC_RE.match(ann)
        if final_m:
            return final_m.group(1)
        return ann


def _extract_py_type_names(annotation: str) -> list[str]:
    """Extract concrete type names from a Python type annotation."""
    if not annotation:
        return []

    results: list[str] = []

    if "|" in annotation:
        for part in annotation.split("|"):
            part = part.strip()
            if part and part != "None":
                results.extend(_extract_py_type_names(part))
        return results

    ann = annotation.strip()

    m = _GENERIC_RE.match(ann)
    if m:
        outer, inner = m.group(1), m.group(2)
        if outer in _PY_CONTAINERS:
            for sub in _split_top_level(inner):
                results.extend(_extract_py_type_names(sub))
        else:
            results.append(outer)
        return results

    if "." in ann:
        results.append(ann.rsplit(".", maxsplit=1)[-1])
        results.append(ann)
    else:
        results.append(ann)

    return results
