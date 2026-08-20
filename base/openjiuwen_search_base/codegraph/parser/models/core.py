"""Core node models: the base class and primary code-structure nodes."""

from dataclasses import dataclass, field
from typing import Literal

from ..constants import NodeType
from ..custom_types import Parameter, SourceSpan


@dataclass(frozen=True, slots=True)
class BaseNode:
    """Common identity shared by every node in the parse tree.

    Concrete subclasses add language-specific fields but always inherit
    *node_type*, *name*, *span*, and the optional *docstring* / *source* /
    *children* triple.
    """

    node_type: NodeType
    name: str
    span: SourceSpan
    docstring: str | None = None
    source: str | None = None
    children: tuple["BaseNode", ...] = ()


# -- Core nodes --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassNode(BaseNode):
    """A class definition."""

    bases: tuple[str, ...] = ()
    metaclass: str | None = None
    decorators: tuple[str, ...] = ()

    @property
    def signature(self) -> str:
        """Produce e.g. ``class Foo(Base, metaclass=Meta)``."""
        parts = [f"class {self.name}"]
        args: list[str] = []
        args.extend(self.bases)
        if self.metaclass:
            args.append(f"metaclass={self.metaclass}")
        if args:
            parts.append(f"({', '.join(args)})")
        return "".join(parts)


@dataclass(frozen=True, slots=True)
class InterfaceNode(BaseNode):
    """An interface or Python ``Protocol``."""

    bases: tuple[str, ...] = ()

    @property
    def signature(self) -> str:
        """Produce e.g. ``interface Bar(Protocol)``."""
        parts = [f"interface {self.name}"]
        if self.bases:
            parts.append(f"({', '.join(self.bases)})")
        return "".join(parts)


@dataclass(frozen=True, slots=True)
class DuckTypeNode(BaseNode):
    """A structurally-inferred type defined by its required method set.

    Identity is the *methods* frozenset; *name* is auto-generated from the
    sorted method names (e.g. ``"DuckType{embed, search}"``).
    """

    methods: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class FunctionNode(BaseNode):
    """A function or method definition.

    *func_type* discriminates between free functions, methods, and closures.
    *owner* names the enclosing class (for methods) or enclosing function
    (for nested/closure functions); ``None`` for top-level functions.
    """

    owner: str | None = None
    func_type: Literal["function", "method", "nested", "method-guessed", "lambda"] = "function"
    parameters: tuple[Parameter, ...] = ()
    return_type: str | None = None
    decorators: tuple[str, ...] = ()
    is_async: bool = False
    cyclomatic_complexity: int = 1
    duck_type_refs: tuple[str, ...] = ()

    @property
    def signature(self) -> str:
        """Produce e.g. ``async def MyClass.method(self, x: int) -> str [method]``."""
        parts: list[str] = []
        if self.is_async:
            parts.append("async ")
        parts.append("def ")
        parts.append(self.name)
        parts.append("(")
        param_strs: list[str] = []
        for p in self.parameters:
            s = p.name
            if p.type_annotation:
                s += f": {p.type_annotation}"
            if p.default:
                s += f" = {p.default}"
            param_strs.append(s)
        parts.append(", ".join(param_strs))
        parts.append(")")
        if self.return_type:
            parts.append(f" -> {self.return_type}")
        if self.func_type != "function":
            parts.append(f" [{self.func_type}]")
        return "".join(parts)


@dataclass(frozen=True, slots=True)
class PropertyNode(BaseNode):
    """A variable / attribute / property with optional type info.

    When *owner* is ``None`` this is a module-level variable; otherwise it
    names the class this property belongs to.
    """

    owner: str | None = None
    type_annotation: str | None = None
    default_value: str | None = None

    @property
    def signature(self) -> str:
        """Produce e.g. ``Cfg.debug: bool = False``."""
        parts: list[str] = []
        if self.owner:
            parts.append(f"{self.owner}.")
        parts.append(self.name)
        if self.type_annotation:
            parts.append(f": {self.type_annotation}")
        if self.default_value:
            parts.append(f" = {self.default_value}")
        return "".join(parts)


@dataclass(frozen=True, slots=True)
class CodeBlockNode(BaseNode):
    """A block of root-level executable code (e.g. if-guard, bare loop)."""

    @property
    def signature(self) -> str:
        """Return the first source line as a concise content summary."""
        return (self.source or self.name).splitlines()[0]


@dataclass(frozen=True, slots=True)
class LocalVarNode(BaseNode):
    """A local variable declaration inside a function body.

    Used by the resolver for type inference on receiver expressions but
    collapsed (not emitted) in the exported graph, similar to ImportNode.
    """

    type_annotation: str | None = None
    default_value: str | None = None


@dataclass(frozen=True, slots=True)
class ImportNode(BaseNode):
    """An import statement."""

    module: str = ""
    names: tuple[str, ...] = ()
    alias: str | None = None
    is_wildcard: bool = False
    is_reexport: bool = False


@dataclass(frozen=True, slots=True)
class CallNode(BaseNode):
    """A function/method call site."""

    callee: str = ""
    receiver: str | None = None
    full_expression: str = ""
    context: str | None = None
    arguments: tuple[str, ...] = ()
    assign_target: str | None = None
