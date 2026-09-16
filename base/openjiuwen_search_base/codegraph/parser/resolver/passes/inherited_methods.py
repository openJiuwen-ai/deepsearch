"""Inherited method guessing pass.

Synthesizes ``method-guessed`` FunctionNodes for methods called on classes
that inherit from unresolvable (builtin/external) bases.  This bridges the
gap where ``ClassMethodIndex`` only knows about directly-declared methods,
causing calls to inherited methods (e.g. ``MyList.append``) and duck-type
IMPLEMENTS checks to fail.
"""

from collections import defaultdict
from collections.abc import Callable

from ...constants import NodeType
from ...custom_types import SourceSpan
from ...models.core import BaseNode, CallNode, ClassNode, FunctionNode, PropertyNode
from ...models.structural import FileNode
from ..indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from ..types import EdgeType
from ._utils import contain_name, match_name


def resolve_inherited_methods(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    class_method_index: ClassMethodIndex,
    node_id_fn: Callable[[str, BaseNode], str],
) -> tuple[list[dict], list[dict]]:
    """Synthesize guessed methods for classes with unresolvable bases.

    Returns ``(synth_node_dicts, synth_edge_dicts)`` in the same format as
    duck-type synthesis, ready to be merged into the graph export.
    """
    # Step 1: find classes with at least one unresolvable base
    ext_classes = _find_externally_based_classes(
        file_nodes,
        symbol_index,
        import_index,
        node_id_fn,
    )
    if not ext_classes:
        return [], []

    ext_class_names = {name for name, _, _ in ext_classes}

    # Step 2: collect demanded methods from CallNodes
    demanded = _collect_demanded_methods(file_nodes, ext_class_names)
    if not demanded:
        return [], []

    # Step 3: depth-order classes (deepest external inheritance first)
    ordered = _depth_order(ext_classes, symbol_index)

    # Step 4+5: synthesize nodes and update the index
    synth_nodes: list[dict] = []
    synth_edges: list[dict] = []

    for class_name, class_id, file_path in ordered:
        existing = class_method_index.get_methods(class_name)
        for method_name in sorted(demanded.get(class_name, set())):
            qualified = f"{class_name}.{method_name}"
            if contain_name(method_name, qualified, existing):
                continue

            fn = FunctionNode(
                node_type=NodeType.FUNCTION,
                name=qualified,
                span=SourceSpan(0, 0, 0, 0),
                source="",
                owner=class_name,
                func_type="method-guessed",
            )
            fn_id = node_id_fn(file_path, fn)

            synth_nodes.append(
                {
                    "id": fn_id,
                    "type": "FunctionNode",
                    "name": qualified,
                    "node_type": NodeType.FUNCTION.value,
                    "path": file_path,
                    "span": [0, 0, 0, 0],
                    "func_type": "method-guessed",
                    "owner": class_name,
                    "tags": ["cat:core", "type:function", "guessed"],
                }
            )
            synth_edges.append(
                {
                    "source": class_id,
                    "target": fn_id,
                    "relation": EdgeType.CONTAINS.value,
                }
            )

            class_method_index.add_method(class_name, qualified)
            symbol_index.register(fn_id, fn)

    return synth_nodes, synth_edges


# ---------------------------------------------------------------------------
# Step 1: identify externally-based classes
# ---------------------------------------------------------------------------


def _find_externally_based_classes(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    node_id_fn: Callable[[str, BaseNode], str],
) -> list[tuple[str, str, str]]:
    """Return ``(class_name, class_id, file_path)`` for classes with unresolvable bases."""
    result: list[tuple[str, str, str]] = []
    for fnode in file_nodes:
        fp = fnode.path
        for child in fnode.children:
            if not isinstance(child, ClassNode):
                continue
            if not child.bases:
                continue
            if _has_unresolvable_base(child, fp, symbol_index, import_index):
                result.append((child.name, node_id_fn(fp, child), fp))
    return result


def _has_unresolvable_base(
    cls: ClassNode,
    file_path: str,
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
) -> bool:
    """True if at least one base of *cls* cannot be resolved in the codebase."""
    for base_name in cls.bases:
        imp = import_index.resolve_name(file_path, base_name)
        if imp is not None:
            _module, original, _id = imp
            if symbol_index.lookup(original):
                continue
        if symbol_index.lookup(base_name):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Step 2: collect methods demanded by call sites
# ---------------------------------------------------------------------------


def _collect_demanded_methods(
    file_nodes: list[FileNode],
    ext_class_names: set[str],
) -> dict[str, set[str]]:
    """Scan CallNodes to find methods called on instances of externally-based classes.

    Uses two strategies to map a receiver to a class name:

    1. **Annotation-based** -- the receiver has a type annotation matching
       an externally-based class (via parameter or property annotations).
    2. **Constructor-based** -- the receiver was assigned from a constructor
       call (``obj = MyList()``) in a preceding CallNode with
       ``assign_target``.
    """
    demanded: dict[str, set[str]] = defaultdict(set)

    for fnode in file_nodes:
        constructor_assigns = _build_constructor_assigns(fnode, ext_class_names)

        for child in fnode.children:
            if not isinstance(child, CallNode):
                continue
            if not child.receiver or not child.callee:
                continue

            receiver_type = _infer_receiver_type_simple(
                fnode,
                child.receiver,
                child.context,
                ext_class_names,
            )
            if receiver_type is None:
                ctx_assigns = constructor_assigns.get(child.context, {})
                receiver_type = ctx_assigns.get(child.receiver)

            if receiver_type is not None and receiver_type in ext_class_names:
                demanded[receiver_type].add(child.callee)

    return demanded


def _infer_receiver_type_simple(
    fnode: FileNode,
    receiver_name: str,
    context: str | None,
    target_names: set[str],
) -> str | None:
    """Lightweight type inference restricted to types in *target_names*."""
    for child in fnode.children:
        if isinstance(child, PropertyNode) and child.name == receiver_name and child.type_annotation:
            t = _extract_simple(child.type_annotation)
            if t in target_names:
                return t

        if isinstance(child, FunctionNode) and context and match_name(child.name, context):
            for param in child.parameters:
                if param.name == receiver_name and param.type_annotation:
                    t = _extract_simple(param.type_annotation)
                    if t in target_names:
                        return t

        if isinstance(child, ClassNode):
            qualified_context = f"{child.name}.{context}" if context else None
            for member in child.children:
                if isinstance(member, FunctionNode) and context and match_name(member.name, context, qualified_context):
                    for param in member.parameters:
                        if param.name == receiver_name and param.type_annotation:
                            t = _extract_simple(param.type_annotation)
                            if t in target_names:
                                return t

    return None


def _extract_simple(annotation: str) -> str:
    """Strip generics and union types to get a bare type name."""
    ann = annotation.strip()
    if "[" in ann:
        ann = ann.split("[", maxsplit=1)[0]
    if "|" in ann:
        for p in ann.split("|"):
            p = p.strip()
            if p != "None":
                return p
    return ann


def _build_constructor_assigns(
    fnode: FileNode,
    ext_class_names: set[str],
) -> dict[str | None, dict[str, str]]:
    """Build ``{context: {var_name: class_name}}`` from constructor-call assignments.

    Detects patterns like ``obj = MyList()`` where ``MyList`` is externally-based.
    """
    result: dict[str | None, dict[str, str]] = {}
    for child in fnode.children:
        if not isinstance(child, CallNode):
            continue
        if not child.assign_target or not child.callee:
            continue
        if child.callee in ext_class_names:
            ctx = child.context
            if ctx not in result:
                result[ctx] = {}
            result[ctx][child.assign_target] = child.callee
    return result


# ---------------------------------------------------------------------------
# Step 3: depth ordering
# ---------------------------------------------------------------------------


def _depth_order(
    ext_classes: list[tuple[str, str, str]],
    symbol_index: SymbolIndex,
) -> list[tuple[str, str, str]]:
    """Sort externally-based classes so children are processed before parents.

    Depth is determined by the number of in-project ancestry hops: a class
    whose bases are all external gets depth 0, while a class inheriting from
    a depth-0 class gets depth 1, etc.  Lower depth is processed first.
    """
    name_set = {name for name, _, _ in ext_classes}
    depths: dict[str, int] = {}

    def _depth(name: str, visited: set[str]) -> int:
        if name in depths:
            return depths[name]
        if name in visited:
            return 0
        visited.add(name)
        candidates = symbol_index.lookup(name)
        if not candidates:
            depths[name] = 0
            return 0
        node = candidates[0][1]
        if not isinstance(node, ClassNode) or not node.bases:
            depths[name] = 0
            return 0
        max_base_depth = 0
        for base in node.bases:
            if base in name_set:
                max_base_depth = max(max_base_depth, _depth(base, visited) + 1)
        depths[name] = max_base_depth
        return max_base_depth

    for name, _, _ in ext_classes:
        _depth(name, set())

    return sorted(ext_classes, key=lambda t: depths.get(t[0], 0))
