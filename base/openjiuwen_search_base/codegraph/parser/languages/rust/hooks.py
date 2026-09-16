"""Rust-specific resolution hooks."""

import re
from functools import cached_property
from pathlib import Path

from ...custom_types import ModuleInfo
from .. import LanguageHooks

_GENERIC_RE = re.compile(r"^([\w:]+)\s*<")
_LIFETIME_RE = re.compile(r"'\w+\s*")
_REF_RE = re.compile(r"^&\s*(?:mut\s+)?")

_RUST_CONTAINERS = frozenset(
    {
        "Vec",
        "vec",
        "Option",
        "Result",
        "Box",
        "Rc",
        "Arc",
        "Cell",
        "RefCell",
        "Mutex",
        "RwLock",
        "HashMap",
        "BTreeMap",
        "HashSet",
        "BTreeSet",
        "VecDeque",
        "LinkedList",
        "Cow",
        "Pin",
        "Slice",
    }
)

RUST_BUILTINS: frozenset[str] = frozenset(
    {
        # Types
        "Vec",
        "String",
        "str",
        "Option",
        "Result",
        "Box",
        "Rc",
        "Arc",
        "Cell",
        "RefCell",
        "Mutex",
        "RwLock",
        "HashMap",
        "BTreeMap",
        "HashSet",
        "BTreeSet",
        "VecDeque",
        "LinkedList",
        "Cow",
        "Pin",
        "Path",
        "PathBuf",
        "OsString",
        "CString",
        "Duration",
        "Instant",
        # Variants / constructors
        "Some",
        "None",
        "Ok",
        "Err",
        # Macros / functions
        "println",
        "print",
        "eprintln",
        "eprint",
        "format",
        "panic",
        "todo",
        "unimplemented",
        "unreachable",
        "assert",
        "assert_eq",
        "assert_ne",
        "debug_assert",
        "vec",
        "drop",
        "clone",
        "default",
        # Primitives often seen as type names
        "i8",
        "i16",
        "i32",
        "i64",
        "i128",
        "isize",
        "u8",
        "u16",
        "u32",
        "u64",
        "u128",
        "usize",
        "f32",
        "f64",
        "bool",
        "char",
    }
)


class RustHooks(LanguageHooks):
    """Rust-specific resolution hooks."""

    @cached_property
    def builtins(self) -> frozenset[str]:
        return RUST_BUILTINS

    @cached_property
    def null_type_names(self) -> frozenset[str]:
        return frozenset({"()"})

    @cached_property
    def supports_decorators(self) -> bool:
        return True

    @cached_property
    def implicit_this(self) -> bool:
        return False

    def is_constructor_call(self, callee: str) -> bool:
        """PascalCase callees and ``Type::new``-style associated functions."""
        if not callee:
            return False
        short = callee.rsplit("::", 1)[-1]
        if short == "new":
            return True
        return short[0].isupper()

    def extract_type_names(self, annotation: str) -> list[str]:
        """Extract type names from a Rust type annotation."""
        if not annotation:
            return []
        ann = annotation.strip()
        if ann in self.null_type_names:
            return []
        names: list[str] = []
        self._collect_types(ann, names)
        return names

    def _collect_types(self, ann: str, out: list[str]) -> None:
        ann = ann.strip()
        if not ann or ann == "()":
            return
        ann = _LIFETIME_RE.sub("", ann).strip()
        while True:
            stripped = _REF_RE.sub("", ann).strip()
            if stripped == ann:
                break
            ann = stripped
        if ann.startswith("[") and "]" in ann:
            inner = ann[1 : ann.rfind("]")]
            # [T; N] or [T]
            part = inner.split(";")[0].strip()
            self._collect_types(part, out)
            return

        match = _GENERIC_RE.match(ann)
        if match:
            outer = match.group(1)
            short = outer.rsplit("::", 1)[-1]
            if short not in _RUST_CONTAINERS and short[0].isupper():
                out.append(short)
                if "::" in outer:
                    out.append(outer)
            inner = self._template_inner(ann)
            if inner:
                for part in self._split_args(inner):
                    self._collect_types(part, out)
            return

        # Path or simple type
        bare = ann.split("<", 1)[0].strip()
        bare = bare.rstrip("*& ").strip()
        if not bare or bare in self.null_type_names:
            return
        short = bare.rsplit("::", 1)[-1]
        if short and short[0].isupper() and short not in ("Self",):
            out.append(short)
            if "::" in bare:
                out.append(bare)
        elif short and short not in RUST_BUILTINS and short.isidentifier():
            # lowercase path segments like module types rarely; skip primitives
            if short[0].isupper():
                out.append(short)

    @staticmethod
    def _template_inner(ann: str) -> str | None:
        start = ann.find("<")
        if start < 0:
            return None
        depth = 0
        for i in range(start, len(ann)):
            if ann[i] == "<":
                depth += 1
            elif ann[i] == ">":
                depth -= 1
                if depth == 0:
                    return ann[start + 1 : i]
        return None

    @staticmethod
    def _split_args(inner: str) -> list[str]:
        parts: list[str] = []
        depth = 0
        current: list[str] = []
        for ch in inner:
            if ch == "<":
                depth += 1
                current.append(ch)
            elif ch == ">":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current).strip())
        return [p for p in parts if p]

    def unwrap_receiver_type(self, annotation: str, subscript_depth: int) -> str | None:
        """Peel Vec/slice/Box/Option wrappers for subscripted receivers."""
        if not annotation:
            return None
        ann = annotation.strip()
        ann = _LIFETIME_RE.sub("", ann).strip()
        ann = _REF_RE.sub("", ann).strip()

        for _ in range(max(subscript_depth, 1) if subscript_depth else 0):
            peeled = self._peel_once(ann)
            if peeled is None:
                return None
            ann = peeled

        if subscript_depth == 0:
            # Still strip smart pointers when depth is 0? plan says peel by depth.
            return None

        ann = _REF_RE.sub("", ann).strip()
        short = ann.rsplit("::", 1)[-1].split("<", 1)[0].strip()
        if not short or short[0].islower() or short in RUST_BUILTINS - {"String"}:
            if short and short[0].isupper() and short not in _RUST_CONTAINERS:
                return short
            return short if short and short[0].isupper() else None
        return short

    def _peel_once(self, ann: str) -> str | None:
        if ann.startswith("[") and "]" in ann:
            return ann[1 : ann.rfind("]")].split(";")[0].strip()
        match = _GENERIC_RE.match(ann)
        if not match:
            return None
        outer = match.group(1).rsplit("::", 1)[-1]
        inner = self._template_inner(ann)
        if inner is None:
            return None
        parts = self._split_args(inner)
        if outer in ("HashMap", "BTreeMap"):
            return parts[1].strip() if len(parts) >= 2 else None
        if outer in _RUST_CONTAINERS or outer in ("Result",):
            return parts[0].strip() if parts else None
        return parts[0].strip() if parts else None

    @cached_property
    def package_init_files(self) -> frozenset[str]:
        return frozenset({"mod.rs", "lib.rs", "main.rs"})

    @cached_property
    def implicit_package_loading(self) -> bool:
        return False

    def detect_modules(
        self,
        folder_rel: str,
        file_names: frozenset[str],
        root: str,
    ) -> list[ModuleInfo]:
        if not ({"mod.rs", "lib.rs", "main.rs"} & file_names):
            return []
        name = folder_rel.replace("/", "::") if folder_rel else "crate"
        path = str(Path(root) / folder_rel) if folder_rel else root
        return [ModuleInfo(name=name, language="rust", path=path)]
