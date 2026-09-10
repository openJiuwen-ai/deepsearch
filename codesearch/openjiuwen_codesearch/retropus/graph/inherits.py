"""Build and query INHERITS edges between class-like AST nodes.

Edges point subtype → supertype. Coverage spans all ContextBench languages:

* Python — ``class Sub(Base):``
* Java / JavaScript / TypeScript — ``class``/``interface`` ``extends``/``implements``
* C++ — ``class``/``struct`` base-class clauses
* Go — struct / interface embedding
* Rust — trait supertraits (``trait Sub: Super``)
* C — no inheritance (structs indexed as types but emit no bases)

Resolution is precision-first: ambiguous base names are left unresolved
rather than linked to the wrong type.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from openjiuwen_codesearch.retropus.graph.graph_types import (
    ASTNode,
    FileNode,
    KnowledgeGraphEdge,
    KnowledgeGraphEdgeType,
    KnowledgeGraphNode,
)
from openjiuwen_codesearch.retropus.graph.imports import (  # noqa: F401
    parse_python_import_map,
)
from openjiuwen_codesearch.retropus.path_utils import is_test_path

logger = logging.getLogger(__name__)

_DEFINITION_KEYWORDS = (
    "function",
    "method",
    "class",
    "constructor",
    "interface",
    "module",
    "struct",
    "trait",
    "type",
)
_DEFINITION_SUFFIXES = ("definition", "declaration", "specifier", "item", "spec")


def _is_definition_ast_type(ast_type: str) -> bool:
    """True for tree-sitter types that look like named definitions (fn/class/…)."""
    t = ast_type.lower()
    if not any(keyword in t for keyword in _DEFINITION_KEYWORDS):
        return False
    if t.endswith(_DEFINITION_SUFFIXES):
        return True
    return t in {"function", "method", "class", "module", "type_spec"}


# Bases that are almost never the gold edit target in ContextBench / SWE-bench.
_SKIP_BASES = frozenset(
    {
        # Python
        "object",
        "type",
        "ABC",
        "ABCMeta",
        "Protocol",
        "Generic",
        "TypedDict",
        "NamedTuple",
        "Enum",
        "IntEnum",
        "Flag",
        "IntFlag",
        "Exception",
        "BaseException",
        "dict",
        "list",
        "tuple",
        "set",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "frozenset",
        "memoryview",
        "Mixin",
        # Java / JS / TS
        "Object",
        "Record",
        "Throwable",
        "Error",
        "RuntimeException",
        # Rust auto / marker / std traits (rarely gold edit targets)
        "Send",
        "Sync",
        "Copy",
        "Clone",
        "Debug",
        "Default",
        "Sized",
        "Unpin",
        "Display",
        "ToString",
        "Iterator",
        "IntoIterator",
        "From",
        "Into",
        "AsRef",
        "AsMut",
        "Drop",
        "Eq",
        "PartialEq",
        "Ord",
        "PartialOrd",
        "Hash",
        "Any",
        # Go builtins
        "error",
        "any",
        "comparable",
    }
)

# Python class headers: class Name(...): / class Name:
# Negative lookahead avoids C++ ``class Name : public Base``.
_PY_CLASS_RE = re.compile(
    r"^\s*class\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:\(([^)]*)\))?\s*:"
    r"(?!\s*(?:public|protected|private|virtual)\b)",
    re.M,
)
# Java / JS / TS class keyword (header parsed separately for generics / extends).
_OO_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_]\w*)\b", re.M)
_OO_INTERFACE_RE = re.compile(r"\binterface\s+([A-Za-z_]\w*)\b", re.M)
# C++ class/struct with optional base-class clause.
_CPP_CLASS_RE = re.compile(
    r"\b(class|struct)\s+([A-Za-z_]\w*)\b(?:\s*(?:final|alignas\s*\([^)]*\)))*",
    re.M,
)
# Go type declaration / type_spec (type_spec text has no leading ``type``).
_GO_TYPE_DECL_RE = re.compile(
    r"\btype\s+([A-Za-z_]\w*)\s+(struct|interface)\b",
    re.M,
)
_GO_TYPE_SPEC_RE = re.compile(
    r"^([A-Za-z_]\w*)\s+(struct|interface)\s*\{",
    re.M,
)
# Rust trait / struct.
_RUST_TRAIT_RE = re.compile(r"\btrait\s+([A-Za-z_]\w*)\b", re.M)
_RUST_STRUCT_RE = re.compile(r"\bstruct\s+([A-Za-z_]\w*)\b", re.M)
# C / C++ access / virtual noise in base lists.
_CPP_BASE_NOISE_RE = re.compile(
    r"\b(?:public|protected|private|virtual)\b"
)
_CODE_EXTS = (
    ".py",
    ".java",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
)


def is_class_ast_type(ast_type: str) -> bool:
    """True for class-like type definitions across ContextBench languages."""
    t = (ast_type or "").lower()
    if "function" in t or "method" in t:
        return False
    # Prefer Go ``type_spec`` over the wrapping ``type_declaration`` so grouped
    # ``type ( ... )`` blocks do not create duplicate / ambiguous candidates.
    if t == "type_declaration":
        return False
    if t == "type_spec":
        return True
    return any(k in t for k in ("class", "interface", "struct", "trait"))


def _strip_angle_generics(text: str) -> str:
    """Remove ``<...>`` generics (handles nesting)."""
    out: List[str] = []
    depth = 0
    for ch in text:
        if ch == "<":
            depth += 1
            continue
        if ch == ">":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def _header_before_body(text: str) -> str:
    """Return text up to the first top-level ``{`` (or full text if none)."""
    depth_angle = 0
    for i, ch in enumerate(text):
        if ch == "<":
            depth_angle += 1
        elif ch == ">":
            depth_angle = max(0, depth_angle - 1)
        elif ch == "{" and depth_angle == 0:
            return text[:i]
    return text


def _matching_brace_body(text: str, open_idx: int) -> Optional[str]:
    """Return the substring inside ``{...}`` starting at ``open_idx``."""
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "{":
        return None
    depth = 0
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    return None


def _simple_name(expr: str) -> str:
    """Last segment of a dotted or ``::``-qualified type expression."""
    return re.split(r"::|\.", expr)[-1]


def extract_class_name_and_bases(text: str) -> Tuple[Optional[str], List[str]]:
    """Parse type name + base/supertype expressions from a class-like AST node."""
    if not text:
        return None, []
    for extractor in (
        _extract_python,
        _extract_rust_trait,
        _extract_go_type,
        _extract_oo_interface,
        _extract_oo_class,
        _extract_cpp_class_struct,
        _extract_rust_struct,
    ):
        result = extractor(text)
        if result is not None:
            return result
    return None, []


def _extract_python(text: str) -> Optional[Tuple[str, List[str]]]:
    """Parse ``class Name(Base, ...):`` → ``(name, bases)``."""
    match = _PY_CLASS_RE.search(text)
    if not match:
        return None
    name = match.group(1)
    raw = (match.group(2) or "").strip()
    if not raw:
        return name, []
    bases: List[str] = []
    for part in _split_base_args(raw):
        cleaned = _normalize_base_expr(part)
        if cleaned:
            bases.append(cleaned)
    return name, bases


def _extract_oo_class(text: str) -> Optional[Tuple[str, List[str]]]:
    """Java / JS / TS ``class Name[...] extends X implements Y, Z``."""
    match = _OO_CLASS_RE.search(text)
    if not match:
        return None
    # Python classes are handled earlier; a trailing ``:`` before ``{`` is Python.
    # If this looks like Python but failed the Python regex, still skip.
    name = match.group(1)
    header = _strip_angle_generics(_header_before_body(text[match.start():]))
    if re.search(r"\)\s*:", header) or re.search(
        rf"\bclass\s+{re.escape(name)}\s*:", header
    ):
        return None
    # C++ ``class Name : public Base`` — leave for the C++ extractor.
    if (
        "extends" not in header
        and "implements" not in header
        and re.search(rf"\bclass\s+{re.escape(name)}\b[^:{{]*:", header)
    ):
        return None
    bases: List[str] = []
    extends = re.search(r"\bextends\s+([\w.$/]+)", header)
    if extends:
        cleaned = _normalize_base_expr(extends.group(1))
        if cleaned:
            bases.append(cleaned)
    implements = re.search(r"\bimplements\s+(.+)$", header)
    if implements:
        for part in _split_base_args(implements.group(1)):
            cleaned = _normalize_base_expr(part)
            if cleaned:
                bases.append(cleaned)
    return name, bases


def _extract_oo_interface(text: str) -> Optional[Tuple[str, List[str]]]:
    """Java / TS ``interface Name extends A, B``."""
    match = _OO_INTERFACE_RE.search(text)
    if not match:
        return None
    name = match.group(1)
    header = _strip_angle_generics(_header_before_body(text[match.start():]))
    bases: List[str] = []
    extends = re.search(r"\bextends\s+(.+)$", header)
    if extends:
        for part in _split_base_args(extends.group(1)):
            cleaned = _normalize_base_expr(part)
            if cleaned:
                bases.append(cleaned)
    return name, bases


def _extract_cpp_class_struct(text: str) -> Optional[Tuple[str, List[str]]]:
    """C / C++ ``class``/``struct``; bases only when a ``:`` clause is present."""
    match = _CPP_CLASS_RE.search(text)
    if not match:
        return None
    keyword = match.group(1)
    name = match.group(2)
    bases: List[str] = []
    # Base clause: ``class Name : public Base, private Other``
    colon = re.search(
        rf"\b{keyword}\s+{re.escape(name)}\b[^:{{;]*:\s*([^{{;]+)",
        _strip_angle_generics(text[match.start():]),
        re.M,
    )
    if colon:
        raw = _CPP_BASE_NOISE_RE.sub(" ", colon.group(1))
        for part in _split_base_args(raw):
            cleaned = _normalize_base_expr(part)
            if cleaned:
                bases.append(cleaned)
    return name, bases


def _extract_go_type(text: str) -> Optional[Tuple[str, List[str]]]:
    """Go struct / interface embedding from ``type Name struct|interface`` or type_spec."""
    stripped = text.lstrip()
    match = _GO_TYPE_DECL_RE.search(stripped)
    if match is None:
        # type_spec nodes are ``Name struct { ... }`` (no leading ``type``).
        match = _GO_TYPE_SPEC_RE.match(stripped)
    if not match:
        return None
    name = match.group(1)
    kind = match.group(2)
    # Body starts at the first ``{`` after the type name (type_spec regex may
    # already consume that brace).
    brace_at = stripped.find("{", match.start())
    body = _matching_brace_body(stripped, brace_at) if brace_at != -1 else None
    if body is None:
        return name, []
    bases: List[str] = []
    if kind == "struct":
        bases.extend(_go_struct_embedded_types(body))
    else:
        bases.extend(_go_interface_embedded_types(body))
    return name, bases


def _go_struct_embedded_types(body: str) -> List[str]:
    """Anonymous fields are embeddings; ``Name Type`` fields are not."""
    bases: List[str] = []
    for raw_line in body.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        # Tags / trailing comments already stripped; drop struct tags.
        if "`" in line:
            line = line[: line.index("`")].strip()
        if not line:
            continue
        # Embedded: optional *, optional pkg., type name only.
        embed = re.match(
            r"^\*?((?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)\s*$",
            line,
        )
        if embed:
            cleaned = _normalize_base_expr(embed.group(1))
            if cleaned:
                bases.append(cleaned)
    return bases


def _go_interface_embedded_types(body: str) -> List[str]:
    """Interface embeddings are type names; methods contain ``(``."""
    bases: List[str] = []
    for raw_line in body.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line or "(" in line:
            continue
        embed = re.match(
            r"^((?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)\s*$",
            line,
        )
        if embed:
            cleaned = _normalize_base_expr(embed.group(1))
            if cleaned:
                bases.append(cleaned)
    return bases


def _extract_rust_trait(text: str) -> Optional[Tuple[str, List[str]]]:
    """Parse ``trait Name: Super + Other`` → ``(name, supertraits)``."""
    match = _RUST_TRAIT_RE.search(text)
    if not match:
        return None
    name = match.group(1)
    header = _strip_angle_generics(_header_before_body(text[match.start():]))
    bases: List[str] = []
    bounds = re.search(rf"\btrait\s+{re.escape(name)}\s*:\s*(.+)$", header)
    if bounds:
        for part in re.split(r"\+", bounds.group(1)):
            cleaned = _normalize_base_expr(part.strip())
            if cleaned:
                bases.append(cleaned)
    return name, bases


def _extract_rust_struct(text: str) -> Optional[Tuple[str, List[str]]]:
    """Rust structs have no inheritance; index the name only."""
    match = _RUST_STRUCT_RE.search(text)
    if not match:
        return None
    return match.group(1), []


def _split_base_args(raw: str) -> List[str]:
    """Split a base-class argument list, ignoring commas inside brackets."""
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


def _normalize_base_expr(expr: str) -> Optional[str]:
    """Clean a base-type expression; return ``None`` for skippable / invalid bases."""
    expr = (expr or "").strip()
    if not expr or expr == "...":
        return None
    # Drop keyword args / unpacking / metaclass=...
    if "=" in expr or expr.startswith("*"):
        # Go pointer embeddings are handled before normalize; leftover ``*T`` → T.
        if expr.startswith("*") and re.match(r"^\*?[A-Za-z_][\w.:]*$", expr):
            expr = expr.lstrip("*").strip()
        else:
            return None
    # Drop generics: Foo[T] / Foo<T> → Foo
    if "[" in expr:
        expr = expr.split("[", 1)[0].strip()
    if "<" in expr:
        expr = _strip_angle_generics(expr).strip()
    expr = expr.strip()
    # Allow qualified names with ``.`` or ``::``.
    if not expr or not re.match(r"^[A-Za-z_][\w.:]*$", expr):
        return None
    simple = _simple_name(expr)
    if simple in _SKIP_BASES or expr in _SKIP_BASES:
        return None
    return expr


ClassCandidate = Tuple[KnowledgeGraphNode, KnowledgeGraphNode]  # (class_ast, file)


def build_class_index(
    class_nodes: Sequence[ClassCandidate],
) -> Dict[str, List[ClassCandidate]]:
    """Map simple class name → [(class_ast, file_node), ...]."""
    index: Dict[str, List[ClassCandidate]] = {}
    for class_ast, file_node in class_nodes:
        parsed, _ = extract_class_name_and_bases(class_ast.node.text)
        name = parsed or ""
        if not name:
            continue
        index.setdefault(name, []).append((class_ast, file_node))
    return index


def resolve_base_candidate(
    base_expr: str,
    from_file: str,
    index: Dict[str, List[ClassCandidate]],
) -> Optional[ClassCandidate]:
    """Precision-first resolution of a base expression to one class candidate."""
    simple = _simple_name(base_expr)
    cands = list(index.get(simple, ()))
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]

    same_file = [c for c in cands if c[1].node.relative_path == from_file]
    if len(same_file) == 1:
        return same_file[0]
    if len(same_file) > 1:
        return None

    parent = str(Path(from_file).parent)
    same_dir = [
        c for c in cands if str(Path(c[1].node.relative_path).parent) == parent
    ]
    if len(same_dir) == 1:
        return same_dir[0]

    # Qualified path hint: pkg.Model / ns::Base / com.foo.Bar → matching source file.
    if "." in base_expr or "::" in base_expr:
        mod = base_expr.replace("::", ".").rsplit(".", 1)[0].replace(".", "/")
        path_hits = []
        seen_ids: Set[int] = set()
        for c in cands:
            rel = c[1].node.relative_path.replace("\\", "/")
            hit = False
            for ext in _CODE_EXTS:
                if (
                    rel in {f"{mod}{ext}", f"{mod}/__init__.py"}
                    or rel.endswith(f"/{mod}{ext}")
                    or rel.endswith(f"/{mod}/__init__.py")
                ):
                    hit = True
                    break
            if hit and c[0].node_id not in seen_ids:
                seen_ids.add(c[0].node_id)
                path_hits.append(c)
        if len(path_hits) == 1:
            return path_hits[0]

    prod = [c for c in cands if not is_test_path(c[1].node.relative_path)]
    if len(prod) == 1:
        return prod[0]
    return None


def collect_class_candidates(
    ast_file_pairs: Sequence[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]],
) -> List[ClassCandidate]:
    """Filter ``(file, ast)`` pairs down to class-like definition AST nodes."""
    out: List[ClassCandidate] = []
    for file_node, ast_node in ast_file_pairs:
        if not isinstance(ast_node.node, ASTNode) or not isinstance(file_node.node, FileNode):
            continue
        if not _is_definition_ast_type(ast_node.node.type):
            continue
        if not is_class_ast_type(ast_node.node.type):
            continue
        out.append((ast_node, file_node))
    return out


def build_inherits_edges(
    ast_file_pairs: Sequence[Tuple[KnowledgeGraphNode, KnowledgeGraphNode]],
) -> List[KnowledgeGraphEdge]:
    """Create subtype→supertype INHERITS edges for resolvable bases."""
    classes = collect_class_candidates(ast_file_pairs)
    index = build_class_index(classes)
    edges: List[KnowledgeGraphEdge] = []
    seen: Set[Tuple[int, int]] = set()

    for class_ast, file_node in classes:
        _name, bases = extract_class_name_and_bases(class_ast.node.text)
        if not bases:
            continue
        from_file = file_node.node.relative_path
        for base_expr in bases:
            resolved = resolve_base_candidate(base_expr, from_file, index)
            if resolved is None:
                continue
            super_ast, _super_file = resolved
            if super_ast.node_id == class_ast.node_id:
                continue
            key = (class_ast.node_id, super_ast.node_id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                KnowledgeGraphEdge(
                    class_ast, super_ast, KnowledgeGraphEdgeType.inherits
                )
            )
    return edges


def inheritance_neighbors(
    class_ast_id: int,
    inherits_out: Dict[int, List[KnowledgeGraphNode]],
    inherits_in: Dict[int, List[KnowledgeGraphNode]],
) -> List[KnowledgeGraphNode]:
    """Parents and children of a class node (1-hop, either direction)."""
    seen: Set[int] = set()
    out: List[KnowledgeGraphNode] = []
    for node in list(inherits_out.get(class_ast_id, ())) + list(
        inherits_in.get(class_ast_id, ())
    ):
        if node.node_id in seen:
            continue
        seen.add(node.node_id)
        out.append(node)
    return out


# Fraction of a seed class BM25 score assigned to its 1-hop inheritance neighbors.
INHERITS_SCORE_FACTOR = 0.88
# Only expand inheritance from the highest-scoring seed defs (keeps fanout tight).
INHERITS_SEED_TOP_DEFS = 12


def boost_ranked_files_with_inherits(
    kg: Any,
    ranked_files: List[Dict[str, Any]],
    *,
    top_k: int,
    max_defs_per_file: Optional[int] = 20,
    score_factor: float = INHERITS_SCORE_FACTOR,
    max_seed_defs: int = INHERITS_SEED_TOP_DEFS,
) -> List[Dict[str, Any]]:
    """Inject/boost superclass and subclass defs next to high-scoring class hits.

    Neighbors receive ``seed_score * score_factor`` (or keep their own score if
    higher). New files introduced by inheritance are eligible for the final
    top-k cut. No-op when the KnowledgeGraph has no INHERITS edges.
    """
    if not ranked_files:
        return ranked_files
    get_edges = getattr(kg, "get_inherits_edges", None)
    if get_edges is None or not get_edges():
        return ranked_files

    # Flatten seeds: (score, class_ast, file_node)
    seeds: List[Tuple[float, KnowledgeGraphNode, KnowledgeGraphNode]] = []
    per_file: Dict[int, Dict[str, Any]] = {}
    for entry in ranked_files:
        file_node = entry["file_node"]
        per_file[file_node.node_id] = {
            "file_node": file_node,
            "score": float(entry["score"]),
            "defs": list(entry["defs"]),
        }
        for def_node, score in entry["defs"]:
            if is_class_ast_type(getattr(def_node.node, "type", "")):
                seeds.append((float(score), def_node, file_node))

    seeds.sort(key=lambda t: -t[0])
    seen_def_ids: Set[int] = set()
    for entry in per_file.values():
        for def_node, _ in entry["defs"]:
            seen_def_ids.add(def_node.node_id)

    injected = 0
    for seed_score, seed_ast, _seed_file in seeds[:max_seed_defs]:
        try:
            neighbors = kg.get_inheritance_neighbors(seed_ast)
        except Exception:
            logger.debug(
                "inheritance neighbors lookup failed for seed %s",
                getattr(seed_ast, "node_id", seed_ast),
                exc_info=True,
            )
            continue
        for neighbor in neighbors:
            file_node = kg.get_file_for_ast(neighbor)
            if file_node is None:
                continue
            boosted = seed_score * score_factor
            entry = per_file.get(file_node.node_id)
            if entry is None:
                entry = {"file_node": file_node, "score": boosted, "defs": []}
                per_file[file_node.node_id] = entry
            else:
                entry["score"] = max(float(entry["score"]), boosted)

            if neighbor.node_id in seen_def_ids:
                # Raise score on an existing def row if the inherit boost is higher.
                new_defs = []
                for d, s in entry["defs"]:
                    if d.node_id == neighbor.node_id:
                        new_defs.append((d, max(float(s), boosted)))
                    else:
                        new_defs.append((d, s))
                entry["defs"] = new_defs
                continue

            if max_defs_per_file is not None and len(entry["defs"]) >= max_defs_per_file:
                # Prefer replacing a lower-scoring def when the file is full.
                if entry["defs"]:
                    worst_i = min(
                        range(len(entry["defs"])),
                        key=lambda i: float(entry["defs"][i][1]),
                    )
                    if float(entry["defs"][worst_i][1]) >= boosted:
                        continue
                    entry["defs"].pop(worst_i)
                else:
                    continue
            entry["defs"].append((neighbor, boosted))
            seen_def_ids.add(neighbor.node_id)
            injected += 1

    if injected == 0 and len(per_file) == len(ranked_files):
        # May still have raised scores on existing defs — re-sort.
        pass

    ranked = sorted(per_file.values(), key=lambda e: float(e["score"]), reverse=True)
    # Keep defs inside each file sorted by score.
    for entry in ranked:
        entry["defs"].sort(key=lambda t: float(t[1]), reverse=True)
        if max_defs_per_file is not None:
            entry["defs"] = entry["defs"][:max_defs_per_file]
    return ranked[:top_k]
