"""Multi-language import graph over knowledge-graph file nodes.

``IMPORTS`` edges are FileNode → FileNode (importer → imported module), built
into the KG at graph-construction time. An inverted adjacency is derived from
those edges for "who imports me?".

Coverage spans all ContextBench languages:

* Python — ``import`` / ``from … import``
* Java — ``import com.example.Foo;``
* JavaScript / TypeScript — ``import`` / ``export … from`` / ``require()``
* Go — ``import "…pkg"`` (resolved to a representative package ``.go`` file)
* Rust — ``use`` / ``mod`` (crate-local + relative)
* C / C++ — ``#include "…"`` / ``#include <…>`` (in-repo only)

Resolution is limited to paths that exist among KG file nodes (in-repo only).
Ambiguous targets are left unresolved rather than linked incorrectly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from openjiuwen_codesearch.retropus.graph.graph_types import (
    FileNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
)

# ---------------------------------------------------------------------------
# Language routing
# ---------------------------------------------------------------------------

# ContextBench languages + common companion extensions (headers, TSX, …).
_IMPORT_LANG_BY_EXT: Dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".hh": "cpp",
}

_SOURCE_EXTENSIONS = frozenset(_IMPORT_LANG_BY_EXT)


def language_for_path(path: str) -> Optional[str]:
    """Return the ContextBench import-language key for ``path``, if supported."""
    suffix = Path(path).suffix.lower()
    return _IMPORT_LANG_BY_EXT.get(suffix)


def is_import_source_file(path: str) -> bool:
    """True if ``path`` has an extension covered by the import resolvers."""
    return language_for_path(path) is not None


# ---------------------------------------------------------------------------
# Shared path helpers
# ---------------------------------------------------------------------------

def _norm_rel(path: str) -> str:
    """Normalize a repo-relative path (``a/../b`` → ``b``), no filesystem I/O."""
    parts: List[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _unique_suffix_hit(suffix: str, known_files: Set[str]) -> Optional[str]:
    """Return the unique known file ending with ``/<suffix>`` or equal to it."""
    suffix = suffix.replace("\\", "/").lstrip("/")
    if not suffix:
        return None
    hits = [
        f
        for f in known_files
        if f == suffix or f.endswith("/" + suffix)
    ]
    # Dedup while preserving order
    seen: Set[str] = set()
    uniq: List[str] = []
    for f in hits:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    if len(uniq) == 1:
        return uniq[0]
    return None


def _first_existing(candidates: Sequence[str], known_files: Set[str]) -> Optional[str]:
    """Return the first normalized candidate that exists in ``known_files``."""
    for cand in candidates:
        norm = _norm_rel(cand)
        if norm in known_files:
            return norm
    return None


# ---------------------------------------------------------------------------
# Python (existing behaviour)
# ---------------------------------------------------------------------------

# ``from pkg.mod import Name as Alias`` / ``from .mod import Name`` / ``from . import x``
_PY_FROM_IMPORT_RE = re.compile(
    r"^\s*from\s+(\.+[A-Za-z_][\w.]*|\.+|[A-Za-z_][\w.]*)\s+import\s+([^\n#]+)",
    re.M,
)
# ``import pkg.mod as alias`` (module alias for ``alias.Class`` / qualified uses)
_PY_IMPORT_AS_RE = re.compile(
    r"^\s*import\s+([A-Za-z_][\w.]*)\s+as\s+([A-Za-z_]\w*)\s*(?:#.*)?$",
    re.M,
)
# ``import pkg.mod, other`` (no ``as``); skip lines already handled by ``as`` form.
_PY_IMPORT_PLAIN_RE = re.compile(
    r"^\s*import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*)\s*(?:#.*)?$",
    re.M,
)


def _split_comma_args(raw: str) -> List[str]:
    """Split a comma-separated list, ignoring commas inside brackets."""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in raw:
        if ch in "([{<":
            depth += 1
            buf.append(ch)
        elif ch in ")]}>":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def _resolve_python_from_module(module_expr: str, from_file: str) -> Optional[str]:
    """Map a ``from``-import module expr to a slash path (no extension)."""
    expr = (module_expr or "").strip()
    if not expr:
        return None
    rel = from_file.replace("\\", "/")
    if expr.startswith("."):
        dots = 0
        while dots < len(expr) and expr[dots] == ".":
            dots += 1
        rest = expr[dots:].lstrip(".")
        package = Path(rel).parent
        for _ in range(max(0, dots - 1)):
            package = package.parent
            if str(package) in ("", "."):
                break
        if rest:
            return str(package / rest.replace(".", "/")).replace("\\", "/")
        return str(package).replace("\\", "/")
    return expr.replace(".", "/")


def _split_python_import_names(raw: str) -> List[Tuple[str, str]]:
    """Parse ``Name, Other as Alias`` into ``[(local, original), ...]``."""
    out: List[Tuple[str, str]] = []
    for part in _split_comma_args(raw):
        part = part.strip()
        if not part or part.startswith("(") or part == "*":
            continue
        part = part.strip("()")
        if " as " in part:
            original, _, alias = part.partition(" as ")
            original = original.strip()
            alias = alias.strip()
            if original and alias and re.match(r"^[A-Za-z_]\w*$", alias):
                out.append((alias, original))
            continue
        if re.match(r"^[A-Za-z_]\w*$", part):
            out.append((part, part))
    return out


def parse_python_import_map(file_text: str, from_file: str) -> Dict[str, str]:
    """Map local imported names → module slash-paths for Python files.

    Supports:
    * ``from .base import Base`` / ``from pkg.mod import Base as B``
    * ``from . import base`` (module binding for ``base.Class``)
    * ``import pkg.mod as mod`` (module alias for ``mod.Class``)
    """
    mapping: Dict[str, str] = {}
    if not file_text:
        return mapping

    for match in _PY_FROM_IMPORT_RE.finditer(file_text):
        mod_expr = match.group(1)
        names_raw = match.group(2)
        mod_path = _resolve_python_from_module(mod_expr, from_file)
        if not mod_path:
            continue
        bare_relative = bool(re.fullmatch(r"\.+", mod_expr))
        for local, original in _split_python_import_names(names_raw):
            if bare_relative:
                mapping[local] = f"{mod_path}/{original}".replace("\\", "/")
            else:
                mapping[local] = mod_path

    for match in _PY_IMPORT_AS_RE.finditer(file_text):
        mod_expr = match.group(1)
        alias = match.group(2)
        mapping[alias] = mod_expr.replace(".", "/")

    return mapping


def resolve_module_to_existing_file(
    mod_path: str,
    known_files: Set[str],
) -> Optional[str]:
    """Map a slash module path (no extension) to an existing KG file path."""
    mod = (mod_path or "").replace("\\", "/").replace(".", "/").strip("/")
    if not mod:
        return None
    candidates = (
        f"{mod}.py",
        f"{mod}/__init__.py",
    )
    for cand in candidates:
        if cand in known_files:
            return cand
    # Suffix match: ``pkg/mod`` → ``src/pkg/mod.py`` when unique among known files.
    py_suffix = f"/{mod}.py"
    init_suffix = f"/{mod}/__init__.py"
    py_exact = f"{mod}.py"
    init_exact = f"{mod}/__init__.py"
    suffix_hits = []
    for known in known_files:
        if (
            known.endswith(py_suffix)
            or known.endswith(init_suffix)
            or known == py_exact
            or known == init_exact
        ):
            suffix_hits.append(known)
    seen: Set[str] = set()
    uniq: List[str] = []
    for f in suffix_hits:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    if len(uniq) == 1:
        return uniq[0]
    return None


def iter_python_import_module_paths(
    file_text: str, from_file: str
) -> List[Tuple[str, str]]:
    """Return ``[(local_name, module_slash_path), ...]`` for Python imports.

    Extends :func:`parse_python_import_map` with plain ``import a, b.c`` lines
    (no ``as``). ``local_name`` is the binding used in the importer when known,
    otherwise the last segment of the module path.
    """
    mapping = parse_python_import_map(file_text, from_file)
    out: List[Tuple[str, str]] = list(mapping.items())
    seen_mods = {mod for _, mod in out}

    for match in _PY_IMPORT_PLAIN_RE.finditer(file_text or ""):
        # Skip if this line is an ``import X as Y`` (also matches a looser pattern).
        line = match.group(0)
        if re.search(r"\bas\b", line):
            continue
        for part in match.group(1).split(","):
            mod_expr = part.strip()
            if not mod_expr or not re.match(r"^[A-Za-z_][\w.]*$", mod_expr):
                continue
            mod_path = mod_expr.replace(".", "/")
            if mod_path in seen_mods:
                continue
            seen_mods.add(mod_path)
            local = mod_expr.rsplit(".", 1)[-1]
            out.append((local, mod_path))

    return out


def _resolve_python_targets(
    file_text: str,
    from_file: str,
    known_files: Set[str],
) -> List[Tuple[str, str]]:
    """Resolve Python imports in ``from_file`` to ``[(target_rel, local_name), ...]``."""
    rel = from_file.replace("\\", "/")
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []
    for local, mod_path in iter_python_import_module_paths(file_text, rel):
        candidates = []
        if local:
            candidates.append(f"{mod_path}/{local}".replace("\\", "/"))
        candidates.append(mod_path)
        target = None
        for cand in candidates:
            target = resolve_module_to_existing_file(cand, known_files)
            if target is not None:
                break
        if target is None or target == rel or target in seen:
            continue
        seen.add(target)
        out.append((target, local))
    return out


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

_JAVA_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:static\s+)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;",
    re.M,
)


def _resolve_java_fqcn(fqcn: str, known_files: Set[str]) -> Optional[str]:
    """Map ``com.example.Foo`` (or static ``…Foo.MEMBER``) to a ``.java`` file."""
    if not fqcn or fqcn.endswith(".*"):
        return None
    parts = fqcn.split(".")
    # Try longest type path first so static imports resolve to the class file.
    for end in range(len(parts), 0, -1):
        class_path = "/".join(parts[:end]) + ".java"
        hit = _unique_suffix_hit(class_path, known_files)
        if hit is not None:
            return hit
        if class_path in known_files:
            return class_path
    return None


def _resolve_java_targets(
    file_text: str,
    from_file: str,
    known_files: Set[str],
) -> List[Tuple[str, str]]:
    """Resolve Java ``import`` lines to ``[(target_rel, simple_name), ...]``."""
    rel = from_file.replace("\\", "/")
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []
    for match in _JAVA_IMPORT_RE.finditer(file_text or ""):
        fqcn = match.group(1)
        target = _resolve_java_fqcn(fqcn, known_files)
        if target is None or target == rel or target in seen:
            continue
        seen.add(target)
        local = fqcn.rsplit(".", 1)[-1]
        out.append((target, local))
    return out


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------

_JS_FROM_RE = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?(?:[^'"\n;]+?\s+from\s+)?['"]([^'"]+)['"]""",
    re.M,
)
_JS_SIDE_EFFECT_RE = re.compile(
    r"""^\s*import\s+['"]([^'"]+)['"]\s*;?""",
    re.M,
)
_JS_REQUIRE_RE = re.compile(
    r"""(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)""",
)

_JS_EXTENSIONS = (
    "",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
)
_JS_INDEX_NAMES = (
    "index.ts",
    "index.tsx",
    "index.mts",
    "index.js",
    "index.jsx",
    "index.mjs",
    "index.cjs",
)


def _iter_js_specs(file_text: str) -> List[Tuple[str, str]]:
    """Return ``[(local_hint, module_specifier), ...]`` (deduped by specifier)."""
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []
    for regex in (_JS_FROM_RE, _JS_SIDE_EFFECT_RE, _JS_REQUIRE_RE):
        for match in regex.finditer(file_text or ""):
            spec = match.group(1).strip()
            if not spec or spec in seen:
                continue
            seen.add(spec)
            local = Path(spec).stem or spec.rsplit("/", 1)[-1]
            out.append((local, spec))
    return out


def _resolve_js_specifier(
    from_file: str,
    spec: str,
    known_files: Set[str],
) -> Optional[str]:
    """Resolve relative (and absolute-in-repo) JS/TS module specifiers only."""
    if not spec or (not spec.startswith((".", "/"))):
        return None
    if spec.startswith("/"):
        base_target = spec.lstrip("/")
    else:
        parent = str(Path(from_file).parent).replace("\\", "/")
        if parent in (".", ""):
            base_target = spec
        else:
            base_target = f"{parent}/{spec}"
    base_target = _norm_rel(base_target)

    candidates: List[str] = []
    for ext in _JS_EXTENSIONS:
        candidates.append(base_target + ext)
    for index_name in _JS_INDEX_NAMES:
        candidates.append(f"{base_target}/{index_name}")
    return _first_existing(candidates, known_files)


def _resolve_js_targets(
    file_text: str,
    from_file: str,
    known_files: Set[str],
) -> List[Tuple[str, str]]:
    """Resolve relative JS/TS imports/requires to ``[(target_rel, local_hint), ...]``."""
    rel = from_file.replace("\\", "/")
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []
    for local, spec in _iter_js_specs(file_text):
        target = _resolve_js_specifier(rel, spec, known_files)
        if target is None or target == rel or target in seen:
            continue
        seen.add(target)
        out.append((target, local))
    return out


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

_GO_IMPORT_BLOCK_RE = re.compile(
    r"^\s*import\s*\((.*?)\)",
    re.M | re.S,
)
_GO_IMPORT_SINGLE_RE = re.compile(
    r"""^\s*import\s+(?:(?:\.|_|\w+)\s+)?["']([^"']+)["']""",
    re.M,
)
_GO_IMPORT_LINE_RE = re.compile(
    r"""^\s*(?:(?:\.|_|\w+)\s+)?["']([^"']+)["']\s*$""",
    re.M,
)


def _iter_go_import_paths(file_text: str) -> List[str]:
    """Collect Go import paths from single-line and parenthesized ``import`` forms."""
    paths: List[str] = []
    seen: Set[str] = set()
    text = file_text or ""
    for match in _GO_IMPORT_BLOCK_RE.finditer(text):
        for line_match in _GO_IMPORT_LINE_RE.finditer(match.group(1)):
            path = line_match.group(1).strip()
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
    for match in _GO_IMPORT_SINGLE_RE.finditer(text):
        path = match.group(1).strip()
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _pick_go_package_file(files: Sequence[str], package_dir: str) -> Optional[str]:
    """Choose a representative non-test ``.go`` file for a package directory."""
    non_test = [f for f in files if not f.endswith("_test.go")]
    pool = non_test or list(files)
    if not pool:
        return None
    pkg_name = Path(package_dir).name if package_dir not in (".", "") else ""
    if pkg_name:
        preferred = f"{package_dir}/{pkg_name}.go" if package_dir else f"{pkg_name}.go"
        if preferred in pool:
            return preferred
    return sorted(pool)[0]


def _resolve_go_import_path(
    import_path: str,
    known_files: Set[str],
) -> Optional[str]:
    """Map a Go import path to one in-repo package file (suffix match)."""
    if not import_path or import_path.startswith("C"):  # cgo
        return None
    go_files = [f for f in known_files if f.endswith(".go")]
    if not go_files:
        return None

    by_dir: Dict[str, List[str]] = {}
    for f in go_files:
        d = str(Path(f).parent).replace("\\", "/")
        if d == ".":
            d = ""
        by_dir.setdefault(d, []).append(f)

    if import_path in by_dir:
        return _pick_go_package_file(by_dir[import_path], import_path)

    # Prefer longest unique directory suffix of the import path.
    candidates: List[str] = []
    for d in by_dir:
        if not d:
            continue
        if import_path == d or import_path.endswith("/" + d):
            candidates.append(d)
    if not candidates:
        # Last-segment fallback when unique among package dirs.
        last = import_path.rsplit("/", 1)[-1]
        loose = [d for d in by_dir if d == last or d.endswith("/" + last)]
        if len(loose) == 1:
            candidates = loose
    if not candidates:
        return None
    best_len = max(len(c) for c in candidates)
    best = [c for c in candidates if len(c) == best_len]
    if len(best) != 1:
        return None
    package_files = by_dir.get(best[0])
    if package_files is None:
        return None
    return _pick_go_package_file(package_files, best[0])


def _resolve_go_targets(
    file_text: str,
    from_file: str,
    known_files: Set[str],
) -> List[Tuple[str, str]]:
    """Resolve Go imports to one representative in-repo ``.go`` file each."""
    rel = from_file.replace("\\", "/")
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []
    for import_path in _iter_go_import_paths(file_text):
        target = _resolve_go_import_path(import_path, known_files)
        if target is None or target == rel or target in seen:
            continue
        seen.add(target)
        local = import_path.rsplit("/", 1)[-1]
        out.append((target, local))
    return out


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

_RUST_USE_RE = re.compile(
    r"^\s*(?:pub\s+)?use\s+([^;]+);",
    re.M,
)
_RUST_MOD_RE = re.compile(
    r"^\s*(?:pub\s+)?mod\s+([A-Za-z_][\w]*)\s*;",
    re.M,
)


def _rust_crate_root_dir(from_file: str, known_files: Set[str]) -> str:
    """Directory containing ``lib.rs`` / ``main.rs`` for this file, else ``src``/``.""."""
    parts = Path(from_file.replace("\\", "/")).parts
    for i in range(len(parts) - 1, -1, -1):
        parent = "/".join(parts[:i])
        for name in ("lib.rs", "main.rs"):
            cand = f"{parent}/{name}" if parent else name
            if cand in known_files:
                return parent
    if any(f == "src/lib.rs" or f.startswith("src/") for f in known_files):
        return "src"
    return str(Path(from_file).parent).replace("\\", "/")


def _rust_module_dir(from_file: str) -> str:
    """Directory in which ``mod child;`` looks for child modules."""
    p = Path(from_file.replace("\\", "/"))
    if p.name in {"mod.rs", "lib.rs", "main.rs"}:
        return str(p.parent).replace("\\", "/")
    # ``foo.rs`` → children live under ``foo/``
    parent = str(p.parent).replace("\\", "/")
    stem_dir = f"{parent}/{p.stem}" if parent not in (".", "") else p.stem
    return stem_dir


def _rust_parent_module_dir(from_file: str) -> str:
    """Directory for ``super::`` resolution."""
    p = Path(from_file.replace("\\", "/"))
    if p.name in {"lib.rs", "main.rs"}:
        return str(p.parent).replace("\\", "/")
    if p.name == "mod.rs":
        return str(p.parent.parent).replace("\\", "/")
    return str(p.parent).replace("\\", "/")


def _rust_file_candidates(module_path: str) -> List[str]:
    """Candidate files for a slash module path (no extension)."""
    mod = _norm_rel(module_path)
    if not mod:
        return []
    return [f"{mod}.rs", f"{mod}/mod.rs"]


def _resolve_rust_module_path(
    module_slash: str,
    known_files: Set[str],
) -> Optional[str]:
    """Map a slash module path to ``mod.rs`` / ``*.rs`` if present in ``known_files``."""
    return _first_existing(_rust_file_candidates(module_slash), known_files)


def _split_rust_use_paths(use_body: str) -> List[List[str]]:
    """Expand a ``use`` body into path segment lists (braces flattened simply)."""
    body = use_body.strip()
    if not body:
        return []
    # Drop leading visibility noise already stripped; strip ``crate::`` etc later.
    # Handle ``foo::{a, b::c}`` / ``foo::bar as Baz`` lightly.
    brace = re.match(r"^(.+?)::\{(.+)\}\s*$", body, re.S)
    if brace:
        prefix = [p for p in brace.group(1).split("::") if p]
        inner = brace.group(2)
        paths: List[List[str]] = []
        for part in _split_top_level(inner):
            part = re.sub(r"\s+as\s+\w+\s*$", "", part.strip())
            if not part:
                continue
            if part == "self":
                paths.append(prefix)
            else:
                paths.append(prefix + [p for p in part.split("::") if p])
        return paths

    body = re.sub(r"\s+as\s+\w+\s*$", "", body).strip()
    segs = [p for p in body.split("::") if p]
    return [segs] if segs else []


def _split_top_level(raw: str) -> List[str]:
    """Split a comma-separated list, ignoring commas inside ``{...}`` groups."""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in raw:
        if ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def _resolve_rust_segments(
    segments: List[str],
    from_file: str,
    known_files: Set[str],
) -> Optional[str]:
    """Resolve a ``use``/path segment list (``crate``/``super``/``self``/crate-local)."""
    if not segments:
        return None
    head = segments[0]
    rest = segments[1:]

    if head == "crate":
        root = _rust_crate_root_dir(from_file, known_files)
        base_segs = rest
        prefix = root
    elif head == "super":
        prefix = _rust_parent_module_dir(from_file)
        base_segs = rest
        # Nested super::super::
        while base_segs and base_segs[0] == "super":
            prefix = (
                str(Path(prefix).parent).replace("\\", "/")
                if prefix not in (".", "")
                else ""
            )
            base_segs = base_segs[1:]
    elif head == "self":
        # ``self`` in a use path refers to the current module file's namespace.
        p = Path(from_file.replace("\\", "/"))
        if p.name in {"mod.rs", "lib.rs", "main.rs"}:
            prefix = str(p.parent).replace("\\", "/")
        else:
            prefix = str(p.parent).replace("\\", "/")
            # Items in the same file — no new edge; only submodule paths.
        base_segs = rest
        if not base_segs:
            return None
        # Prefer child modules under the module dir.
        prefix = _rust_module_dir(from_file) if p.name not in {
            "mod.rs",
            "lib.rs",
            "main.rs",
        } else prefix
    else:
        # External crate or absolute path from crate root — try crate-local first.
        root = _rust_crate_root_dir(from_file, known_files)
        # External crates usually don't match; require a unique hit under the crate.
        prefix = root
        base_segs = segments

    # Walk longest module prefix → file.
    for n in range(len(base_segs), 0, -1):
        mod_parts = base_segs[:n]
        rel = "/".join(mod_parts)
        module_slash = f"{prefix}/{rel}" if prefix not in (".", "") else rel
        hit = _resolve_rust_module_path(module_slash, known_files)
        if hit is not None:
            return hit
    return None


def _resolve_rust_targets(
    file_text: str,
    from_file: str,
    known_files: Set[str],
) -> List[Tuple[str, str]]:
    """Resolve Rust ``mod`` / ``use`` statements to in-crate source files."""
    rel = from_file.replace("\\", "/")
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []

    for match in _RUST_MOD_RE.finditer(file_text or ""):
        name = match.group(1)
        mod_dir = _rust_module_dir(rel)
        module_slash = f"{mod_dir}/{name}" if mod_dir not in (".", "") else name
        target = _resolve_rust_module_path(module_slash, known_files)
        if target is None or target == rel or target in seen:
            continue
        seen.add(target)
        out.append((target, name))

    for match in _RUST_USE_RE.finditer(file_text or ""):
        for segments in _split_rust_use_paths(match.group(1)):
            target = _resolve_rust_segments(segments, rel, known_files)
            if target is None or target == rel or target in seen:
                continue
            seen.add(target)
            local = segments[-1] if segments else Path(target).stem
            out.append((target, local))

    return out


# ---------------------------------------------------------------------------
# C / C++
# ---------------------------------------------------------------------------

_INCLUDE_RE = re.compile(
    r"""^\s*#\s*include\s*([<"])([^>"]+)[>"]""",
    re.M,
)


def _resolve_include(
    from_file: str,
    kind: str,
    header: str,
    known_files: Set[str],
) -> Optional[str]:
    """Resolve a C/C++ ``#include`` to an in-repo header when uniquely matchable."""
    header = header.strip().replace("\\", "/")
    if not header:
        return None

    candidates: List[str] = []
    if kind == '"':
        parent = str(Path(from_file).parent).replace("\\", "/")
        if parent in (".", ""):
            candidates.append(header)
        else:
            candidates.append(_norm_rel(f"{parent}/{header}"))
        # Also try as repo-root relative.
        candidates.append(_norm_rel(header))

    hit = _first_existing(candidates, known_files)
    if hit is not None:
        return hit
    # Basename / suffix match (quoted + angle), precision-first.
    return _unique_suffix_hit(header, known_files) or _unique_suffix_hit(
        Path(header).name, known_files
    )


def _resolve_c_targets(
    file_text: str,
    from_file: str,
    known_files: Set[str],
) -> List[Tuple[str, str]]:
    """Resolve C/C++ ``#include`` directives to ``[(target_rel, header_basename), ...]``."""
    rel = from_file.replace("\\", "/")
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []
    for match in _INCLUDE_RE.finditer(file_text or ""):
        kind, header = match.group(1), match.group(2)
        target = _resolve_include(rel, kind, header, known_files)
        if target is None or target == rel or target in seen:
            continue
        seen.add(target)
        out.append((target, Path(header).name))
    return out


# ---------------------------------------------------------------------------
# Public resolve / index API
# ---------------------------------------------------------------------------

_RESOLVERS = {
    "python": _resolve_python_targets,
    "java": _resolve_java_targets,
    "javascript": _resolve_js_targets,
    "typescript": _resolve_js_targets,
    "go": _resolve_go_targets,
    "rust": _resolve_rust_targets,
    "c": _resolve_c_targets,
    "cpp": _resolve_c_targets,
}


def resolve_import_targets(
    file_text: str,
    from_file: str,
    known_files: Set[str],
) -> List[Tuple[str, str]]:
    """Resolve imports in ``from_file`` to existing KG paths.

    Returns ``[(target_rel, local_name), ...]`` (deduped by target).

    Language is inferred from ``from_file``'s extension. For Python
    ``from pkg import name``, prefers ``pkg/name.py`` when that file exists
    (submodule import) over ``pkg/__init__.py``.
    """
    lang = language_for_path(from_file)
    if lang is None:
        return []
    resolver = _RESOLVERS[lang]
    return resolver(file_text, from_file.replace("\\", "/"), known_files)


@dataclass
class ImportIndex:
    """Bidirectional import graph among KG file paths."""

    outgoing: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    incoming: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    known_files: Set[str] = field(default_factory=set)

    def imports_of(self, path: str) -> List[Tuple[str, str]]:
        """Files imported by ``path`` as ``[(target_rel, local_name), ...]``."""
        return list(self.outgoing.get(path.replace("\\", "/"), ()))

    def importers_of(self, path: str) -> List[Tuple[str, str]]:
        """Files that import ``path`` as ``[(importer_rel, local_name), ...]``."""
        return list(self.incoming.get(path.replace("\\", "/"), ()))

    def neighbors(
        self,
        path: str,
        *,
        direction: str = "both",
        depth: int = 1,
        max_nodes: int = 80,
    ) -> List[Tuple[str, str, str]]:
        """BFS import neighbors.

        Returns ``[(other_path, via_name, edge_dir)]`` where ``edge_dir`` is
        ``"imports"`` (outgoing) or ``"imported_by"`` (incoming).
        """
        start = path.replace("\\", "/")
        direction = (direction or "both").strip().lower()
        if direction not in {"both", "out", "in", "imports", "importers"}:
            direction = "both"
        if direction == "imports":
            direction = "out"
        if direction == "importers":
            direction = "in"

        depth = max(1, min(int(depth or 1), 3))
        max_nodes = max(1, int(max_nodes or 80))

        seen: Set[str] = {start}
        results: List[Tuple[str, str, str]] = []
        frontier: List[str] = [start]

        for _ in range(depth):
            nxt: List[str] = []
            for cur in frontier:
                if direction in {"both", "out"}:
                    for tgt, name in self.imports_of(cur):
                        if tgt in seen:
                            continue
                        seen.add(tgt)
                        results.append((tgt, name, "imports"))
                        nxt.append(tgt)
                        if len(results) >= max_nodes:
                            return results
                if direction in {"both", "in"}:
                    for src, name in self.importers_of(cur):
                        if src in seen:
                            continue
                        seen.add(src)
                        results.append((src, name, "imported_by"))
                        nxt.append(src)
                        if len(results) >= max_nodes:
                            return results
            frontier = nxt
            if not frontier:
                break
        return results


def build_imports_edges(
    file_nodes: Sequence[KnowledgeGraphNode],
    repo_root: Path,
) -> Tuple[List[KnowledgeGraphEdge], Dict[Tuple[int, int], str]]:
    """Create File→File ``IMPORTS`` edges for resolvable in-repo imports.

    Covers all ContextBench languages (see module docstring). Returns
    ``(edges, labels)`` where ``labels[(src_id, tgt_id)]`` is the local
    binding / imported name used in the importer (best-effort).
    """
    root = Path(repo_root)
    rel_to_node: Dict[str, KnowledgeGraphNode] = {}
    for kg_node in file_nodes:
        node = kg_node.node
        if not isinstance(node, FileNode):
            continue
        rel = node.relative_path.replace("\\", "/")
        if rel and rel != ".":
            rel_to_node[rel] = kg_node

    known = set(rel_to_node.keys())
    edges: List[KnowledgeGraphEdge] = []
    labels: Dict[Tuple[int, int], str] = {}
    seen_pairs: Set[Tuple[int, int]] = set()

    for rel, src_node in sorted(rel_to_node.items()):
        if not is_import_source_file(rel):
            continue
        abs_path = root / rel
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for tgt_rel, local in resolve_import_targets(text, rel, known):
            tgt_node = rel_to_node.get(tgt_rel)
            if tgt_node is None:
                continue
            pair = (src_node.node_id, tgt_node.node_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append(
                KnowledgeGraphEdge(
                    src_node, tgt_node, KnowledgeGraphEdgeType.imports
                )
            )
            if local:
                labels[pair] = local

    return edges, labels


def build_import_index(
    repo_root: Path,
    file_rels: Sequence[str],
) -> ImportIndex:
    """Parse importable source files under ``repo_root`` in ``file_rels``.

    Fallback when a live KG with ``IMPORTS`` edges is unavailable (tests).
    Only edges whose endpoints both exist in ``file_rels`` are kept.
    """
    root = Path(repo_root)
    known = {f.replace("\\", "/") for f in file_rels if f}
    sources = sorted(f for f in known if is_import_source_file(f))
    index = ImportIndex(known_files=set(known))

    for rel in sources:
        abs_path = root / rel
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        targets = resolve_import_targets(text, rel, known)
        if not targets:
            continue
        index.outgoing[rel] = targets
        for tgt, name in targets:
            index.incoming.setdefault(tgt, []).append((rel, name))

    return index


def import_index_from_kg(kg: object) -> ImportIndex:
    """Project KG ``IMPORTS`` edges into an :class:`ImportIndex` for tools."""
    get_edges = getattr(kg, "get_imports_edges", None)
    get_files = getattr(kg, "get_file_nodes", None)
    get_label = getattr(kg, "get_imports_label", None)
    index = ImportIndex()
    if get_files is not None:
        for n in get_files():
            node = getattr(n, "node", None)
            rel = getattr(node, "relative_path", None) if node is not None else None
            if isinstance(rel, str) and rel and rel != ".":
                index.known_files.add(rel.replace("\\", "/"))
    if get_edges is None:
        return index
    for edge in get_edges():
        src = edge.source.node
        tgt = edge.target.node
        if not isinstance(src, FileNode) or not isinstance(tgt, FileNode):
            continue
        srel = src.relative_path.replace("\\", "/")
        trel = tgt.relative_path.replace("\\", "/")
        name = ""
        if get_label is not None:
            name = get_label(edge.source.node_id, edge.target.node_id) or ""
        if not name:
            name = Path(trel).stem
        index.outgoing.setdefault(srel, []).append((trel, name))
        index.incoming.setdefault(trel, []).append((srel, name))
        index.known_files.add(srel)
        index.known_files.add(trel)
    return index


def collect_kg_file_paths(file_nodes: Iterable[object]) -> List[str]:
    """Extract repo-relative paths from KG file nodes."""
    out: List[str] = []
    for n in file_nodes:
        node = getattr(n, "node", None)
        rel = getattr(node, "relative_path", None) if node is not None else None
        if isinstance(rel, str) and rel:
            out.append(rel.replace("\\", "/"))
    return out
