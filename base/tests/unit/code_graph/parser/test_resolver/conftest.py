"""Common fixtures and helpers for resolver tests."""

from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.custom_types import Parameter, SourceSpan
from openjiuwen_search_base.codegraph.parser.models.core import (
    BaseNode,
    CallNode,
    ClassNode,
    FunctionNode,
    ImportNode,
    InterfaceNode,
    PropertyNode,
)
from openjiuwen_search_base.codegraph.parser.models.extensions.data_types import EnumNode
from openjiuwen_search_base.codegraph.parser.models.structural import FileNode


def make_hooks_map(language: str = "python") -> dict:
    """Create a hooks_map for resolver tests."""
    from openjiuwen_search_base.codegraph.parser.languages import get_default_registry, register_builtins

    register_builtins()
    registry = get_default_registry()
    return {language: registry.get_hooks(language)}


_SPAN = SourceSpan(1, 1, 0, 0)
_LINE = 0


def _next_span() -> SourceSpan:
    global _LINE
    _LINE += 1
    return SourceSpan(_LINE, _LINE, 0, 0)


def make_span(line: int) -> SourceSpan:
    """Create a SourceSpan at a specific line."""
    return SourceSpan(line, line, 0, 0)


def make_node_id(file_path: str, node: BaseNode) -> str:
    """Deterministic node ID for tests, mirrors ``node_id`` from parser.ids."""
    from openjiuwen_search_base.codegraph.parser.ids import node_id

    return node_id(file_path, node)


def make_file_node(
    path: str = "test.py",
    language: str = "python",
    children: tuple[BaseNode, ...] = (),
) -> FileNode:
    """Create a minimal ``FileNode`` for testing."""
    return FileNode(
        node_type=NodeType.FILE,
        name=path.rsplit("/", maxsplit=1)[-1],
        span=SourceSpan(1, 100, 0, 0),
        path=path,
        language=language,
        children=children,
    )


def make_class_node(
    name: str,
    line: int = 1,
    bases: tuple[str, ...] = (),
    metaclass: str | None = None,
    decorators: tuple[str, ...] = (),
    children: tuple[BaseNode, ...] = (),
) -> ClassNode:
    """Create a minimal ``ClassNode``."""
    return ClassNode(
        node_type=NodeType.CLASS,
        name=name,
        span=make_span(line),
        bases=bases,
        metaclass=metaclass,
        decorators=decorators,
        children=children,
    )


def make_interface_node(
    name: str,
    line: int = 1,
    bases: tuple[str, ...] = (),
    children: tuple[BaseNode, ...] = (),
) -> InterfaceNode:
    """Create a minimal ``InterfaceNode``."""
    return InterfaceNode(
        node_type=NodeType.INTERFACE,
        name=name,
        span=make_span(line),
        bases=bases,
        children=children,
    )


def make_function_node(
    name: str,
    line: int = 1,
    line_end: int | None = None,
    owner: str | None = None,
    func_type: str = "function",
    parameters: tuple[Parameter, ...] = (),
    return_type: str | None = None,
    decorators: tuple[str, ...] = (),
    children: tuple[BaseNode, ...] = (),
) -> FunctionNode:
    """Create a minimal ``FunctionNode``."""
    span = SourceSpan(line, line_end if line_end is not None else line, 0, 0)
    return FunctionNode(
        node_type=NodeType.FUNCTION,
        name=name,
        span=span,
        children=children,
        owner=owner,
        func_type=func_type,
        parameters=parameters,
        return_type=return_type,
        decorators=decorators,
    )


def make_import_node(
    module: str,
    names: tuple[str, ...] = (),
    alias: str | None = None,
    is_wildcard: bool = False,
    is_reexport: bool = False,
    line: int = 1,
) -> ImportNode:
    """Create a minimal ``ImportNode``."""
    import_name = alias or (names[0] if names else module)
    return ImportNode(
        node_type=NodeType.IMPORT,
        name=import_name,
        span=make_span(line),
        module=module,
        names=names,
        alias=alias,
        is_wildcard=is_wildcard,
        is_reexport=is_reexport,
    )


def make_call_node(
    callee: str,
    receiver: str | None = None,
    context: str | None = None,
    full_expression: str = "",
    line: int = 1,
    arguments: tuple[str, ...] = (),
    assign_target: str | None = None,
) -> CallNode:
    """Create a minimal ``CallNode``."""
    return CallNode(
        node_type=NodeType.CALL,
        name=callee,
        span=make_span(line),
        callee=callee,
        receiver=receiver,
        full_expression=full_expression or callee,
        context=context,
        arguments=arguments,
        assign_target=assign_target,
    )


def make_property_node(
    name: str,
    line: int = 1,
    owner: str | None = None,
    type_annotation: str | None = None,
    default_value: str | None = None,
) -> PropertyNode:
    """Create a minimal ``PropertyNode``."""
    return PropertyNode(
        node_type=NodeType.PROPERTY,
        name=name,
        span=make_span(line),
        owner=owner,
        type_annotation=type_annotation,
        default_value=default_value,
    )


def make_enum_node(
    name: str,
    line: int = 1,
    members: tuple[str, ...] = (),
) -> EnumNode:
    """Create a minimal ``EnumNode``."""
    return EnumNode(
        node_type=NodeType.ENUM,
        name=name,
        span=make_span(line),
        members=members,
    )
