"""Indexes built from parsed file trees for symbol resolution."""

from collections.abc import Callable
from pathlib import Path as _Path

from ..models.core import (
    BaseNode,
    ClassNode,
    FunctionNode,
    ImportNode,
    InterfaceNode,
)
from ..models.extensions.data_types import EnumNode, StructNode
from ..models.structural import FileNode

_INDEXABLE_TYPES = (ClassNode, InterfaceNode, FunctionNode, EnumNode, StructNode)
_TYPE_WITH_METHODS = (ClassNode, InterfaceNode, StructNode, EnumNode)


class SymbolIndex:
    """Maps symbol names to ``(node_id, node)`` tuples for classes, functions, interfaces, enums, and structs."""

    def __init__(self, file_nodes: list[FileNode], node_id_fn: Callable[[str, BaseNode], str]):
        self._by_name: dict[str, list[tuple[str, BaseNode]]] = {}
        self._by_id: dict[str, BaseNode] = {}

        for fnode in file_nodes:
            fp = fnode.path
            for child in fnode.children:
                if not isinstance(child, _INDEXABLE_TYPES):
                    continue
                # Only index top-level functions (not methods)
                if isinstance(child, FunctionNode) and child.owner is not None:
                    continue
                nid = node_id_fn(fp, child)
                self._by_id[nid] = child
                self._by_name.setdefault(child.name, []).append((nid, child))
                # Qualified name for classes/interfaces with an owner-like context
                if fp:
                    p = _Path(fp)
                    module = str(p.with_suffix("")).replace("/", ".")
                    qname = f"{module}.{child.name}"
                    self._by_name.setdefault(qname, []).append((nid, child))

    def lookup(self, name: str) -> list[tuple[str, BaseNode]]:
        """Find all nodes matching *name* (short or qualified)."""
        return list(self._by_name.get(name, []))

    def get_by_id(self, node_id: str) -> BaseNode | None:
        """Direct ID lookup."""
        return self._by_id.get(node_id)

    def register(self, node_id: str, node: BaseNode) -> None:
        """Add a node to the index after initial construction."""
        self._by_id[node_id] = node
        self._by_name.setdefault(node.name, []).append((node_id, node))


class ImportIndex:
    """Per-file import resolution: maps local names to source modules and imported names."""

    def __init__(self, file_nodes: list[FileNode], node_id_fn: Callable[[str, BaseNode], str]):
        self._file_imports: dict[str, dict[str, tuple[str, str, str]]] = {}

        for fnode in file_nodes:
            fp = fnode.path
            mapping: dict[str, tuple[str, str, str]] = {}
            for child in fnode.children:
                if not isinstance(child, ImportNode):
                    continue
                imp_id = node_id_fn(fp, child)
                if child.is_wildcard:
                    continue
                if child.alias:
                    # ``import module as alias`` or ``from module import name as alias``
                    original = child.names[0] if child.names else child.module
                    mapping[child.alias] = (child.module, original, imp_id)
                elif child.names:
                    for imported_name in child.names:
                        mapping[imported_name] = (child.module, imported_name, imp_id)
                else:
                    # ``import module`` – the local name is the module itself
                    short = child.module.rsplit(".", maxsplit=1)[-1]
                    mapping[short] = (child.module, short, imp_id)
            self._file_imports[fp] = mapping

    def resolve_name(self, file_path: str, local_name: str) -> tuple[str, str, str] | None:
        """Given a file and a local name, return ``(source_module, original_name, import_node_id)``."""
        return self._file_imports.get(file_path, {}).get(local_name)

    def get_file_imports(self, file_path: str) -> dict[str, tuple[str, str, str]]:
        """All imports for a file."""
        return dict(self._file_imports.get(file_path, {}))


class ClassMethodIndex:
    """Maps class names to their method names for OOP resolution."""

    def __init__(self, file_nodes: list[FileNode], node_id_fn: Callable[[str, BaseNode], str]):
        self._methods: dict[str, set[str]] = {}
        self._class_ids: dict[str, list[str]] = {}

        for fnode in file_nodes:
            fp = fnode.path
            for child in fnode.children:
                if isinstance(child, _TYPE_WITH_METHODS):
                    cid = node_id_fn(fp, child)
                    self._class_ids.setdefault(child.name, []).append(cid)
                    methods: set[str] = set()
                    for member in child.children:
                        if isinstance(member, FunctionNode):
                            methods.add(member.name)
                    self._methods.setdefault(child.name, set()).update(methods)
                elif isinstance(child, FunctionNode) and child.owner is not None:
                    # Out-of-class method definitions (C++ / Rust impl pattern)
                    self._methods.setdefault(child.owner, set()).add(child.name)

    def get_methods(self, class_name: str) -> set[str]:
        """Return method names for a class."""
        return set(self._methods.get(class_name, set()))

    def get_class_ids(self, class_name: str) -> list[str]:
        """Return node IDs for classes with this name."""
        return list(self._class_ids.get(class_name, []))

    def add_method(self, class_name: str, method_name: str) -> None:
        """Register an additional method on an already-indexed class.

        Used by the inherited-method guessing pass to make synthesized
        methods visible to downstream passes (e.g. duck type IMPLEMENTS).
        """
        self._methods.setdefault(class_name, set()).add(method_name)

    def all_class_names(self) -> list[str]:
        """Return all indexed class names."""
        return list(self._methods.keys())
