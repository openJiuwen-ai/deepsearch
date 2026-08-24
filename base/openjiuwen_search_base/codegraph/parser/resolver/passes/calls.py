"""CALLS edge resolution pass with tiered confidence."""

from collections.abc import Callable

from ...constants import FILTER_BUILTIN_NAMES
from ...languages import LanguageHooks
from ...models.core import (
    BaseNode,
    CallNode,
    ClassNode,
    CodeBlockNode,
    FunctionNode,
    InterfaceNode,
    LocalVarNode,
    PropertyNode,
)
from ...models.extensions.data_types import EnumNode, StructNode
from ...models.structural import FileNode
from ..indexes import ClassMethodIndex, ImportIndex, SymbolIndex
from ..types import EdgeType, ResolvedEdge
from ._utils import _strip_scope, contain_name, match_name

_TYPE_RECEIVER_NODES = (ClassNode, InterfaceNode, StructNode, EnumNode)


def resolve_calls(
    file_nodes: list[FileNode],
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    class_method_index: ClassMethodIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    hooks_map: dict[str, LanguageHooks],
) -> list[ResolvedEdge]:
    """Resolve call sites to their target definitions.

    Resolution proceeds through a tiered strategy, highest confidence first:

    - **Tier 1** ``import_exact`` (1.0) -- callee resolved via import index.
    - **Tier 2** ``local_scope`` (0.9) -- callee is a top-level function/class
      defined in the same file.
    - **Tier 2.5** ``sibling_method`` (0.85) -- unqualified call to another
      method in the same class (Java: ``ReSeed(x)`` without ``this.``).
    - **Tier 3** ``method_receiver`` (0.7) -- receiver-qualified call
      (e.g. ``self.foo()``, ``obj.bar()``), type inferred from annotations
      or call-chain analysis.
    - **Tier 4** ``name_match`` (0.5) -- global symbol lookup, only when
      exactly one candidate exists to avoid ambiguity.

    First match wins; tiers are tried in order and the loop moves to the
    next CallNode as soon as one succeeds.
    """
    edges: list[ResolvedEdge] = []
    _default = LanguageHooks()

    for fnode in file_nodes:
        fp = fnode.path
        hooks = hooks_map.get(fnode.language, _default)

        # Build a map of top-level names -> IDs for Tier 2 (local scope).
        # Owned methods (child.owner is not None) are excluded because they
        # are class members, not top-level definitions.
        local_scope: dict[str, str] = {}
        for child in fnode.children:
            if isinstance(child, (FunctionNode, ClassNode)):
                if isinstance(child, FunctionNode) and child.owner is not None:
                    continue
                local_scope[child.name] = node_id_fn(fp, child)

        wrapper_aliases = _build_wrapper_aliases(fnode, hooks, import_index)
        call_assign_map = _build_call_assign_map(fnode)

        for child in fnode.children:
            if not isinstance(child, CallNode):
                continue

            callee = child.callee
            if not callee:
                continue
            if callee in hooks.builtins and not symbol_index.lookup(callee) and FILTER_BUILTIN_NAMES:
                continue

            # If the callee was assigned from a wrapper (e.g. partial), resolve
            # through to the wrapped callable instead.
            effective_callee = callee
            if callee in wrapper_aliases.get(child.context, {}):
                effective_callee = wrapper_aliases[child.context][callee]

            # Determine which node the call originates from (the "source" of
            # the CALLS edge). Falls back to the FileNode for module-level calls.
            source_id = _find_enclosing(fnode, child, node_id_fn)
            if source_id is None:
                source_id = node_id_fn(fp, fnode)

            # Skip direct constructor calls -- these are handled by the types
            # pass as INSTANTIATES edges. Emitting a CALLS edge to the ClassNode
            # would be redundant. Wrapper-aliased calls (e.g. partial(Widget))
            # are kept because the types pass doesn't follow wrapper chains.
            is_ctor = effective_callee == callee and hooks.is_constructor_call(effective_callee)

            # Tier 1 (confidence 1.0): callee matches an imported name, and
            # the original symbol exists in the symbol index.
            imp = import_index.resolve_name(fp, effective_callee)
            if imp is not None:
                _module, original_name, _imp_id = imp
                candidates = symbol_index.lookup(original_name)
                if candidates:
                    if is_ctor and isinstance(candidates[0][1], (ClassNode, InterfaceNode)):
                        continue
                    edges.append(
                        ResolvedEdge(
                            source_id=source_id,
                            target_id=candidates[0][0],
                            relation=EdgeType.CALLS,
                            confidence=1.0,
                            resolved_by="import_exact",
                        )
                    )
                    continue

            # Tier 2 (confidence 0.9): callee is a top-level function or class
            # defined in the same file.
            if effective_callee in local_scope:
                target_node = symbol_index.get_by_id(local_scope[effective_callee])
                if is_ctor and isinstance(target_node, (ClassNode, InterfaceNode)):
                    continue
                edges.append(
                    ResolvedEdge(
                        source_id=source_id,
                        target_id=local_scope[effective_callee],
                        relation=EdgeType.CALLS,
                        confidence=0.9,
                        resolved_by="local_scope",
                    )
                )
                continue

            # Tier 2.5 (confidence 0.85): unqualified call to a sibling method
            # in the same class body. Only for languages with implicit this/self
            # (Java, C#, Kotlin). Python requires ``self.method()`` and JS/TS
            # requires ``this.method()``, so bare calls are outer-scope lookups.
            if hooks.implicit_this and child.receiver is None and child.context:
                sibling_id = _resolve_sibling_call(
                    fnode,
                    child,
                    class_method_index,
                    symbol_index,
                    node_id_fn,
                )
                if sibling_id is not None:
                    edges.append(
                        ResolvedEdge(
                            source_id=source_id,
                            target_id=sibling_id,
                            relation=EdgeType.CALLS,
                            confidence=0.85,
                            resolved_by="sibling_method",
                        )
                    )
                    continue

            # Tier 3 (confidence 0.7): receiver-qualified call (e.g.
            # ``self.foo()``, ``obj.bar()``). The receiver's type is inferred
            # from parameter/property annotations or call-chain analysis.
            if child.receiver:
                target_id = _resolve_method_call(
                    fnode,
                    child,
                    class_method_index,
                    symbol_index,
                    import_index,
                    node_id_fn,
                    wrapper_aliases,
                    call_assign_map,
                )
                if target_id is not None:
                    edges.append(
                        ResolvedEdge(
                            source_id=source_id,
                            target_id=target_id,
                            relation=EdgeType.CALLS,
                            confidence=0.7,
                            resolved_by="method_receiver",
                        )
                    )
                    continue

            # Tier 4 (confidence 0.5): fall back to global symbol index.
            # Only accepted when exactly one candidate exists to avoid
            # ambiguous matches.
            candidates = symbol_index.lookup(effective_callee)
            if len(candidates) == 1:
                if is_ctor and isinstance(candidates[0][1], (ClassNode, InterfaceNode)):
                    continue
                edges.append(
                    ResolvedEdge(
                        source_id=source_id,
                        target_id=candidates[0][0],
                        relation=EdgeType.CALLS,
                        confidence=0.5,
                        resolved_by="name_match",
                    )
                )

    return edges


def _resolve_wrapper_qualified_name(
    callee: str,
    receiver: str | None,
    file_path: str,
    import_index: ImportIndex,
) -> str | None:
    """Resolve a call's callee to its fully-qualified module name via imports.

    Handles both ``partial(X)`` (from ``from functools import partial``) and
    ``functools.partial(X)`` (from ``import functools``).
    """
    if receiver:
        imp = import_index.resolve_name(file_path, receiver)
        if imp is not None:
            module, _original, _id = imp
            return f"{module}.{callee}"
        return None
    imp = import_index.resolve_name(file_path, callee)
    if imp is not None:
        module, original, _id = imp
        return f"{module}.{original}"
    return None


def _build_wrapper_aliases(
    fnode: FileNode,
    hooks: LanguageHooks,
    import_index: ImportIndex,
) -> dict[str | None, dict[str, str]]:
    """Build per-context maps of wrapper aliases.

    For ``make = partial(SparseEmbedding)``, records
    ``{context: {"make": "SparseEmbedding"}}``.

    Only treats a call as a wrapper if it resolves to a known qualified name
    (e.g. ``functools.partial``) via the import index.
    """
    wrappers = hooks.callable_wrappers
    if not wrappers:
        return {}
    aliases: dict[str | None, dict[str, str]] = {}
    fp = fnode.path
    for child in fnode.children:
        if not isinstance(child, CallNode):
            continue
        if not child.assign_target or not child.arguments:
            continue
        qualified = _resolve_wrapper_qualified_name(
            child.callee,
            child.receiver,
            fp,
            import_index,
        )
        if qualified not in wrappers:
            continue
        ctx = child.context
        if ctx not in aliases:
            aliases[ctx] = {}
        aliases[ctx][child.assign_target] = child.arguments[0]
    return aliases


def _build_call_assign_map(
    fnode: FileNode,
) -> dict[str | None, dict[str, str]]:
    """Build per-context maps of call assignment targets.

    For ``obj = make()``, records ``{context: {"obj": "make"}}``.
    """
    result: dict[str | None, dict[str, str]] = {}
    for child in fnode.children:
        if not isinstance(child, CallNode):
            continue
        if not child.assign_target or not child.callee:
            continue
        ctx = child.context
        if ctx not in result:
            result[ctx] = {}
        result[ctx][child.assign_target] = child.callee
    return result


def _find_enclosing(
    fnode: FileNode,
    call_node: CallNode,
    node_id_fn: Callable[[str, BaseNode], str],
) -> str | None:
    """Determine the source node for a CALLS edge (the "caller").

    Uses the ``CallNode.context`` field set by the parser to locate the
    enclosing scope. The search order matters:

    1. **Top-level function** -- ``context`` matches a FunctionNode name
       directly under the file (e.g. Python ``def helper(): ...``).
    2. **Class method** -- ``context`` matches a FunctionNode name inside
       a ClassNode (e.g. ``MyClass.process`` for a call inside ``process``).
    3. **Constructor (Java)** -- Java's parser reports the *class name* as
       the context for code inside constructors (not ``ClassName.<init>``).
       When ``context`` equals the class name, we search for the first
       ``<init>`` FunctionNode. Overloaded constructors may have names like
       ``Foo.<init>(int, String)``, so we use prefix matching.
       Falls back to the ClassNode itself if no ``<init>`` member is found.
    4. **Code block** -- module-level calls (no context, or unmatched context)
       whose span falls inside a ``CodeBlockNode`` are attributed to that block.

    Returns ``None`` when no enclosing scope is found, in which case the
    caller falls back to the FileNode as the edge source.
    """
    context_name = call_node.context
    fp = fnode.path

    if context_name:
        for child in fnode.children:
            # Case 1: top-level function matches context directly
            if isinstance(child, FunctionNode):
                # For out-of-class methods (C++ pattern), the node name is
                # qualified (e.g. "PlyParser.parseFaces") but context is short
                # ("parseFaces"). Build a qualified form using the owner.
                if child.owner:
                    qualified_context = f"{child.owner}.{context_name}"
                else:
                    qualified_context = None
                if match_name(child.name, context_name, qualified_context):
                    return node_id_fn(fp, child)

            if isinstance(child, ClassNode):
                # Case 2: context matches a method inside this class.
                qualified_context = f"{child.name}.{context_name}"
                for member in child.children:
                    if isinstance(member, FunctionNode) and match_name(member.name, context_name, qualified_context):
                        return node_id_fn(fp, member)

                # Case 3: context is the class name itself -> call is inside a
                # constructor. Prefer the <init> FunctionNode over the ClassNode
                # so the edge source is the constructor, not the class.
                if context_name == child.name:
                    init_prefix = f"{child.name}.<init>"
                    for member in child.children:
                        if isinstance(member, FunctionNode) and member.name.startswith(init_prefix):
                            return node_id_fn(fp, member)
                    return node_id_fn(fp, child)

    # Case 4: module-level call inside a CodeBlockNode
    line = call_node.span.line_start
    best: CodeBlockNode | None = None
    best_size = float("inf")
    for child in fnode.children:
        if not isinstance(child, CodeBlockNode):
            continue
        s = child.span
        if s.line_start <= line <= s.line_end:
            size = s.line_end - s.line_start
            if size < best_size:
                best, best_size = child, size
    if best is not None:
        return node_id_fn(fp, best)

    return None


def _infer_receiver_as_type(
    file_path: str,
    receiver: str,
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
) -> str | None:
    """Treat *receiver* as a type name when it resolves to a class-like symbol.

    Handles static/class method calls such as ``Widget.create()`` where the
    receiver is an imported or locally defined class, not an instance variable.
    """
    imp = import_index.resolve_name(file_path, receiver)
    if imp is not None:
        _module, original_name, _imp_id = imp
        candidates = symbol_index.lookup(original_name)
        for _nid, node in candidates:
            if isinstance(node, _TYPE_RECEIVER_NODES):
                return node.name

    candidates = symbol_index.lookup(receiver)
    for _nid, node in candidates:
        if isinstance(node, _TYPE_RECEIVER_NODES):
            return node.name

    return None


def _resolve_sibling_call(
    fnode: FileNode,
    call_node: CallNode,
    class_method_index: ClassMethodIndex,
    symbol_index: SymbolIndex,
    node_id_fn: Callable[[str, BaseNode], str],
) -> str | None:
    """Resolve an unqualified call to a sibling method in the same class.

    In Java (and similar languages), methods can call siblings without an
    explicit receiver::

        class Canvas {
            Canvas(long seed) { ReSeed(seed); }  // no ``this.``
            void ReSeed(long s) { ... }
        }

    This function finds the enclosing class for the call and checks whether
    the callee matches another method in that same class.

    The context can match in two ways:

    - **Class name** -- the call is inside a constructor or initializer
      (Java reports the class name as context for constructors).
    - **Method name** -- the call is inside a regular method; we verify that
      a method with that name exists in the class.
    """
    context = call_node.context
    callee = call_node.callee
    fp = fnode.path

    # Strategy 1: look for ClassNode in this file with methods defined inline
    for child in fnode.children:
        if not isinstance(child, ClassNode):
            continue

        # Determine whether this call's context belongs to this class.
        # Direct class-name match means a constructor/initializer context.
        if context == child.name:
            in_class = True
        else:
            qualified_context = f"{child.name}.{context}"
            in_class = any(
                isinstance(m, FunctionNode) and match_name(m.name, context, qualified_context) for m in child.children
            )
        if not in_class:
            continue

        # Search for the callee among sibling methods of this class.
        qualified_callee = f"{child.name}.{callee}"
        for member in child.children:
            if isinstance(member, FunctionNode) and match_name(member.name, callee, qualified_callee):
                return node_id_fn(fp, member)

    # Strategy 2: out-of-class methods (C++ pattern) -- the call's context is a
    # short method name, and the enclosing method has owner=ClassName. Look for
    # sibling methods among top-level FunctionNodes with the same owner.
    for child in fnode.children:
        if not isinstance(child, FunctionNode) or child.owner is None:
            continue
        # Check if this method matches the context
        if not match_name(child.name, context, f"{child.owner}.{context}"):
            continue
        # Found the enclosing method -- now search for the callee among siblings
        owner = child.owner
        qualified_callee = f"{owner}.{callee}"
        for sibling in fnode.children:
            if not isinstance(sibling, FunctionNode) or sibling.owner != owner:
                continue
            if match_name(sibling.name, callee, qualified_callee):
                return node_id_fn(fp, sibling)
        break

    return None


def _resolve_method_call(
    fnode: FileNode,
    call_node: CallNode,
    class_method_index: ClassMethodIndex,
    symbol_index: SymbolIndex,
    import_index: ImportIndex,
    node_id_fn: Callable[[str, BaseNode], str],
    wrapper_aliases: dict[str | None, dict[str, str]],
    call_assign_map: dict[str | None, dict[str, str]],
) -> str | None:
    """Resolve a receiver-qualified method call (e.g. ``self.foo()``, ``obj.bar()``).

    Strategy:

    1. Infer the receiver's type from annotations (parameter types, property
       types) or from call-chain analysis (``obj = make()`` where ``make``
       wraps a known class).
    2. Look up the inferred type in the ``ClassMethodIndex`` to verify the
       callee is a known method of that class.
    3. Locate the actual ``FunctionNode`` in the target class to get its ID.
    """
    receiver = call_node.receiver
    callee = call_node.callee
    if not receiver or not callee:
        return None

    # Step 1: infer receiver type from annotations, then from call-chain,
    # then treat the receiver itself as an imported/local class name
    # (static / class method calls like ``Widget.create()``).
    receiver_type = _infer_receiver_type(fnode, receiver, call_node.context)
    if receiver_type is None:
        receiver_type = _infer_type_from_call_chain(receiver, call_node.context, wrapper_aliases, call_assign_map)
    if receiver_type is None:
        receiver_type = _infer_receiver_as_type(fnode.path, receiver, symbol_index, import_index)
    if receiver_type is None:
        return None

    # Step 2: verify the callee is a known method on the inferred type
    methods = class_method_index.get_methods(receiver_type)
    qualified_callee = f"{receiver_type}.{callee}"
    if not contain_name(callee, qualified_callee, methods):
        return None

    # Step 3: find the FunctionNode in the target class for the edge target
    class_ids = class_method_index.get_class_ids(receiver_type)
    if not class_ids:
        return None

    target_class_id = class_ids[0]
    target_class = symbol_index.get_by_id(target_class_id)
    if target_class is None:
        return None

    for member in target_class.children:
        if isinstance(member, FunctionNode) and match_name(member.name, callee, qualified_callee):
            fp = target_class_id.split("::")[0]
            return node_id_fn(fp, member)

    # Fallback: synthesized nodes (e.g. method-guessed) are registered in
    # the symbol index but not attached to the ClassNode's children.
    for candidate_name in (qualified_callee, callee):
        candidates = symbol_index.lookup(candidate_name)
        if candidates:
            return candidates[0][0]

    return None


def _infer_receiver_type(
    fnode: FileNode,
    receiver_name: str,
    context: str | None,
) -> str | None:
    """Infer the type of a receiver variable from static annotations.

    Searches three locations, returning the first match:

    1. **Module-level property** -- ``x: Foo = ...`` at file scope.
    2. **Enclosing function** -- parameter annotation (``def f(x: Foo)``)
       or local annotated variable inside the function body.
    3. **Class method parameter** -- parameter annotation on the method
       that matches the call's context.
    """
    for child in fnode.children:
        if (
            isinstance(child, (PropertyNode, LocalVarNode))
            and _strip_scope(child.name) == receiver_name
            and child.type_annotation
        ):
            return _extract_simple_type(child.type_annotation)

        if isinstance(child, FunctionNode) and context and match_name(child.name, context):
            for param in child.parameters:
                if param.name == receiver_name and param.type_annotation:
                    return _extract_simple_type(param.type_annotation)
            for member in child.children:
                if (
                    isinstance(member, (PropertyNode, LocalVarNode))
                    and _strip_scope(member.name) == receiver_name
                    and member.type_annotation
                ):
                    return _extract_simple_type(member.type_annotation)

        if isinstance(child, ClassNode):
            qualified_context = f"{child.name}.{context}" if context else None
            for member in child.children:
                if isinstance(member, FunctionNode) and context and match_name(member.name, context, qualified_context):
                    for param in member.parameters:
                        if param.name == receiver_name and param.type_annotation:
                            return _extract_simple_type(param.type_annotation)

    return None


def _infer_type_from_call_chain(
    receiver_name: str,
    context: str | None,
    wrapper_aliases: dict[str | None, dict[str, str]],
    call_assign_map: dict[str | None, dict[str, str]],
) -> str | None:
    """Infer a receiver's type by following call assignments and wrapper aliases.

    Handles patterns like::

        make = partial(SparseEmbedding)  # wrapper alias: make -> SparseEmbedding
        obj = make()                     # call assign: obj -> make
        obj.embed("hello")              # receiver_name = "obj"

    We trace: obj was assigned from calling ``make``, which is a wrapper alias
    for ``SparseEmbedding``, so ``obj`` is of type ``SparseEmbedding``.
    """
    ctx_assigns = call_assign_map.get(context, {})
    ctx_wrappers = wrapper_aliases.get(context, {})

    callee_for_receiver = ctx_assigns.get(receiver_name)
    if callee_for_receiver is None:
        return None

    if callee_for_receiver in ctx_wrappers:
        return ctx_wrappers[callee_for_receiver]

    if callee_for_receiver and callee_for_receiver[0].isupper():
        return callee_for_receiver

    return None


def _extract_simple_type(annotation: str) -> str:
    """Extract a simple type name from an annotation (strip generics, Optional, etc.)."""
    ann = annotation.strip()
    if "[" in ann:
        ann = ann.split("[", maxsplit=1)[0]
    if "|" in ann:
        parts = [p.strip() for p in ann.split("|")]
        for p in parts:
            if p != "None":
                return p
    return ann
