"""Language hooks for C and C++ resolution."""

import re
from functools import cached_property

from .. import LanguageHooks


class CHooks(LanguageHooks):
    """Resolution hooks for C."""

    @cached_property
    def builtins(self) -> frozenset[str]:
        return frozenset(
            {
                # stdio.h
                "printf",
                "fprintf",
                "sprintf",
                "snprintf",
                "scanf",
                "fscanf",
                "sscanf",
                "fopen",
                "fclose",
                "fread",
                "fwrite",
                "fgets",
                "fputs",
                "puts",
                "getchar",
                "putchar",
                "fflush",
                "fseek",
                "ftell",
                "rewind",
                "feof",
                "ferror",
                # stdlib.h
                "malloc",
                "calloc",
                "realloc",
                "free",
                "exit",
                "abort",
                "atexit",
                "atoi",
                "atof",
                "atol",
                "strtol",
                "strtod",
                "strtoul",
                "abs",
                "labs",
                "div",
                "ldiv",
                "rand",
                "srand",
                "qsort",
                "bsearch",
                "getenv",
                "system",
                # string.h
                "memcpy",
                "memmove",
                "memset",
                "memcmp",
                "strlen",
                "strcpy",
                "strncpy",
                "strcat",
                "strncat",
                "strcmp",
                "strncmp",
                "strchr",
                "strrchr",
                "strstr",
                "strtok",
                # math.h
                "sin",
                "cos",
                "tan",
                "asin",
                "acos",
                "atan",
                "atan2",
                "sqrt",
                "pow",
                "exp",
                "log",
                "log10",
                "ceil",
                "floor",
                "fabs",
                "fmod",
                # assert.h
                "assert",
                # ctype.h
                "isalpha",
                "isdigit",
                "isalnum",
                "isspace",
                "toupper",
                "tolower",
                # stdarg.h
                "va_start",
                "va_end",
                "va_arg",
                "va_copy",
                # signal.h
                "signal",
                "raise",
                # setjmp.h
                "setjmp",
                "longjmp",
                # Common types
                "size_t",
                "ptrdiff_t",
                "NULL",
                "EOF",
                "int8_t",
                "int16_t",
                "int32_t",
                "int64_t",
                "uint8_t",
                "uint16_t",
                "uint32_t",
                "uint64_t",
                "FILE",
                "errno",
            }
        )

    @cached_property
    def null_type_names(self) -> frozenset[str]:
        return frozenset({"void", "NULL"})

    def is_constructor_call(self, callee: str) -> bool:
        return False

    _TYPE_STRIP_RE = re.compile(r"\b(?:const|volatile|restrict|struct|union|enum|unsigned|signed)\s+")
    _POINTER_RE = re.compile(r"[*&\[\]]+$")

    def extract_type_names(self, annotation: str) -> list[str]:
        """Extract type names from a C type annotation, stripping qualifiers and pointers."""
        if not annotation:
            return []
        ann = self._TYPE_STRIP_RE.sub("", annotation).strip()
        ann = self._POINTER_RE.sub("", ann).strip()
        ann = ann.replace("*", "").replace("&", "").strip()
        if not ann or ann in ("int", "char", "float", "double", "void", "long", "short", "unsigned", "signed"):
            return []
        parts = ann.split()
        if parts:
            return [parts[-1]]
        return []

    @cached_property
    def implicit_this(self) -> bool:
        return False

    def detect_modules(
        self,
        folder_rel: str,
        file_names: frozenset[str],
        root: str,
    ) -> list:
        return []

    _CONTAINER_TYPES: frozenset[str] = frozenset(
        {
            "std::vector",
            "std::array",
            "std::list",
            "std::deque",
            "std::set",
            "std::unordered_set",
            "std::multiset",
            "std::unordered_multiset",
            "std::stack",
            "std::queue",
            "std::priority_queue",
            # Short forms
            "vector",
            "array",
            "list",
            "deque",
            "set",
            "unordered_set",
            "stack",
            "queue",
            "priority_queue",
        }
    )
    _MAP_TYPES: frozenset[str] = frozenset(
        {
            "std::map",
            "std::unordered_map",
            "std::multimap",
            "std::unordered_multimap",
            "map",
            "unordered_map",
            "multimap",
            "unordered_multimap",
        }
    )
    _POINTER_WRAPPERS: frozenset[str] = frozenset(
        {
            "std::shared_ptr",
            "std::unique_ptr",
            "std::weak_ptr",
            "shared_ptr",
            "unique_ptr",
            "weak_ptr",
            "std::optional",
            "optional",
        }
    )

    def unwrap_receiver_type(self, annotation: str, subscript_depth: int) -> str | None:
        """Unwrap C/C++ container and pointer types to get the element type."""
        if not annotation:
            return None
        ann = annotation.strip()
        # Strip const/volatile qualifiers
        ann = re.sub(r"\b(?:const|volatile|mutable)\s+", "", ann).strip()
        ann = re.sub(r"\s+(?:const|volatile)$", "", ann).strip()

        for _ in range(subscript_depth):
            inner = self._peel_template(ann)
            if inner is None:
                # Try raw array: int arr[N] style won't appear as annotations typically
                # but T* with subscript is valid
                if ann.endswith("*"):
                    ann = ann[:-1].strip()
                    continue
                return None
            ann = inner

        # After subscript peeling, strip pointer wrappers and raw pointers
        ann = self._strip_pointer_wrappers(ann)
        # Strip any remaining * or & suffixes
        ann = ann.rstrip("*& ").strip()
        # Strip const/volatile again after unwrap
        ann = re.sub(r"\b(?:const|volatile|mutable)\s+", "", ann).strip()

        if not ann or ann in (
            "int",
            "char",
            "float",
            "double",
            "void",
            "long",
            "short",
            "unsigned",
            "signed",
            "bool",
            "auto",
        ):
            return None
        # Return the final simple type name (last part after ::)
        return ann.split("::")[-1]

    def _peel_template(self, ann: str) -> str | None:
        """Peel one layer of container template: vector<T> -> T, map<K,V> -> V."""
        # Find the outermost template type name
        bracket_start = ann.find("<")
        if bracket_start == -1:
            return None
        outer_type = ann[:bracket_start].strip()

        # Extract the content between < and the matching >
        inner = self._extract_template_inner(ann, bracket_start)
        if inner is None:
            return None

        if outer_type in self._MAP_TYPES:
            # For maps, return the value type (second template arg)
            parts = self._split_template_args(inner)
            if len(parts) >= 2:
                return parts[1].strip()
            return None
        if outer_type in self._CONTAINER_TYPES:
            parts = self._split_template_args(inner)
            return parts[0].strip() if parts else None
        if outer_type in self._POINTER_WRAPPERS:
            return inner.strip()
        # Unknown template type -- try returning inner anyway
        parts = self._split_template_args(inner)
        return parts[0].strip() if parts else None

    def _strip_pointer_wrappers(self, ann: str) -> str:
        """Recursively strip smart pointer wrappers from an annotation."""
        while True:
            bracket_start = ann.find("<")
            if bracket_start == -1:
                break
            outer_type = ann[:bracket_start].strip()
            if outer_type not in self._POINTER_WRAPPERS:
                break
            inner = self._extract_template_inner(ann, bracket_start)
            if inner is None:
                break
            ann = inner.strip()
        return ann

    @staticmethod
    def _extract_template_inner(ann: str, start: int) -> str | None:
        """Extract content between < at *start* and the matching >."""
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
    def _split_template_args(inner: str) -> list[str]:
        """Split template arguments at top-level commas (not nested in <>)."""
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
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current))
        return parts


class CppHooks(CHooks):
    """Resolution hooks for C++, extending CHooks."""

    @cached_property
    def builtins(self) -> frozenset[str]:
        c_builtins = super(CHooks, self).builtins  # noqa: UP008
        return c_builtins | frozenset(
            {
                # STL containers
                "std::vector",
                "std::map",
                "std::unordered_map",
                "std::set",
                "std::unordered_set",
                "std::list",
                "std::deque",
                "std::array",
                "std::stack",
                "std::queue",
                "std::priority_queue",
                "std::pair",
                "std::tuple",
                # Smart pointers
                "std::unique_ptr",
                "std::shared_ptr",
                "std::weak_ptr",
                "std::make_unique",
                "std::make_shared",
                # Strings and streams
                "std::string",
                "std::wstring",
                "std::string_view",
                "std::cout",
                "std::cerr",
                "std::cin",
                "std::endl",
                "std::ostringstream",
                "std::istringstream",
                "std::stringstream",
                # Algorithms / utilities
                "std::move",
                "std::forward",
                "std::swap",
                "std::sort",
                "std::find",
                "std::transform",
                "std::accumulate",
                "std::begin",
                "std::end",
                "std::next",
                "std::prev",
                "std::min",
                "std::max",
                "std::clamp",
                # Functional
                "std::function",
                "std::bind",
                "std::ref",
                # Memory
                "std::allocator",
                "new",
                "delete",
                # Threading
                "std::thread",
                "std::mutex",
                "std::lock_guard",
                "std::unique_lock",
                "std::condition_variable",
                "std::atomic",
                # Common types
                "std::size_t",
                "std::nullptr_t",
                "std::optional",
                "std::variant",
                "std::any",
                "std::span",
                # Short forms (frequently used without std:: via using)
                "vector",
                "map",
                "unordered_map",
                "set",
                "string",
                "unique_ptr",
                "shared_ptr",
                "cout",
                "cerr",
                "cin",
                "endl",
            }
        )

    @cached_property
    def null_type_names(self) -> frozenset[str]:
        return frozenset({"void", "NULL", "nullptr", "std::nullopt"})

    def is_constructor_call(self, callee: str) -> bool:
        """Uppercase-starting names are likely constructors in C++."""
        if not callee:
            return False
        return callee[0].isupper() and not callee.startswith("std::")

    _TEMPLATE_RE = re.compile(r"<[^>]*>")

    def extract_type_names(self, annotation: str) -> list[str]:
        """Extract type names from C++ annotations, handling templates and references."""
        if not annotation:
            return []
        names: list[str] = []
        # Strip const/volatile/mutable
        ann = re.sub(r"\b(?:const|volatile|mutable|typename|class)\s+", "", annotation)
        ann = ann.replace("&&", "").replace("&", "").replace("*", "").strip()

        # Extract template arguments recursively
        template_match = re.findall(r"(\w[\w:]*)\s*<", ann)
        for t in template_match:
            clean = t.replace("::", ".").split(".")[-1]
            if clean and clean[0].isupper():
                names.append(clean)

        # Extract inner template types
        inner = re.findall(r"<([^<>]+)>", ann)
        for group in inner:
            for part in group.split(","):
                part = part.strip()
                part = re.sub(r"\b(?:const|volatile|typename|class)\s+", "", part)
                part = part.replace("*", "").replace("&", "").strip()
                if part and part[0].isupper() and not part.startswith("std::"):
                    names.append(part.replace("::", ".").split(".")[-1])

        # Main type (strip templates first)
        bare = self._TEMPLATE_RE.sub("", ann).strip()
        bare = bare.replace("::", ".").split(".")[-1].strip()
        if bare and bare[0].isupper():
            names.append(bare)

        return [n for n in names if n and n not in ("T", "U", "V", "Args")]

    @cached_property
    def implicit_this(self) -> bool:
        return True

    def detect_modules(
        self,
        folder_rel: str,
        file_names: frozenset[str],
        root: str,
    ) -> list:
        return []
