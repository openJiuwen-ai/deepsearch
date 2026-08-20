"""Static catalogs and body builders for MCP node/edge type resources."""

from dataclasses import dataclass
from functools import cache

from ..parser.constants import EdgeType, NodeType


@dataclass(frozen=True, slots=True)
class FieldDoc:
    """One field on an exported node or edge dict."""

    name: str
    type_name: str
    description: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class NodeTypeDoc:
    """Documentation for one graph node type."""

    category: str
    description: str
    fields: tuple[FieldDoc, ...] = ()
    internal: bool = False


@dataclass(frozen=True, slots=True)
class EdgeTypeDoc:
    """Documentation for one graph edge relation."""

    description: str
    confidence: str


# Always present on exported node dicts (``_node_to_dict`` / synthetic folders).
COMMON_NODE_FIELDS: tuple[FieldDoc, ...] = (
    FieldDoc("id", "string", "Stable unique node identifier"),
    FieldDoc("type", "string", "Python model class name (e.g. FunctionNode, ClassNode)"),
    FieldDoc("name", "string", "Display name"),
    FieldDoc("node_type", "string", "Node kind discriminator (matches this resource key)"),
    FieldDoc("path", "string", "Absolute or project-relative source path"),
    FieldDoc(
        "span",
        "list[int]",
        "[line_start, line_end, col_start, col_end] (1-indexed lines, 0-indexed cols)",
    ),
    FieldDoc("tags", "list[string]", "Filter tags such as cat:*, type:*, lang:*, dir:*"),
    FieldDoc("docstring", "string", "Docstring when present", optional=True),
    FieldDoc("signature", "string", "One-line signature when the node provides one", optional=True),
)

COMMON_EDGE_FIELDS: tuple[FieldDoc, ...] = (
    FieldDoc("source", "string", "Source node id"),
    FieldDoc("target", "string", "Target node id"),
    FieldDoc("relation", "string", "Edge kind discriminator (matches this resource key)"),
    FieldDoc(
        "confidence",
        "number",
        "Resolution confidence in [0, 1]; omitted when exactly 1.0",
        optional=True,
    ),
    FieldDoc(
        "resolved_by",
        "string",
        "Resolver pass / strategy that produced the edge",
        optional=True,
    ),
)

_PARAM_TYPE = "list[{name: string, type_annotation: string|null, default: string|null}]"

NODE_TYPE_DOCS: dict[str, NodeTypeDoc] = {
    NodeType.FOLDER.value: NodeTypeDoc(
        "Structural",
        "A directory in the project tree",
        fields=(),
    ),
    NodeType.FILE.value: NodeTypeDoc(
        "Structural",
        "A parsed source file",
        fields=(FieldDoc("language", "string", "Detected language id (e.g. python, go)"),),
    ),
    NodeType.CLASS.value: NodeTypeDoc(
        "Core",
        "A class definition",
        fields=(
            FieldDoc("bases", "list[string]", "Base class names", optional=True),
            FieldDoc("metaclass", "string", "Metaclass name", optional=True),
            FieldDoc("decorators", "list[string]", "Decorator expressions", optional=True),
        ),
    ),
    NodeType.INTERFACE.value: NodeTypeDoc(
        "Core",
        "An interface or Python Protocol",
        fields=(FieldDoc("bases", "list[string]", "Extended interface / protocol names", optional=True),),
    ),
    NodeType.DUCK_TYPE.value: NodeTypeDoc(
        "Core",
        "A structurally-inferred type defined by its required method set",
        fields=(FieldDoc("methods", "list[string]", "Sorted required method names"),),
    ),
    NodeType.FUNCTION.value: NodeTypeDoc(
        "Core",
        "A function, method, or nested function",
        fields=(
            FieldDoc("owner", "string", "Enclosing class or function name", optional=True),
            FieldDoc(
                "func_type",
                '"function"|"method"|"nested"|"method-guessed"|"lambda"',
                "Kind of function/method",
            ),
            FieldDoc("parameters", _PARAM_TYPE, "Parameters (omitted when empty)", optional=True),
            FieldDoc("return_type", "string", "Return type annotation", optional=True),
            FieldDoc("decorators", "list[string]", "Decorator expressions", optional=True),
            FieldDoc("is_async", "boolean", "Whether the function is async"),
            FieldDoc("cyclomatic_complexity", "number", "Estimated cyclomatic complexity"),
            FieldDoc(
                "duck_type_refs",
                "list[string]",
                "Referenced duck-type ids",
                optional=True,
            ),
        ),
    ),
    NodeType.PROPERTY.value: NodeTypeDoc(
        "Core",
        "A variable, attribute, or property with optional type info",
        fields=(
            FieldDoc("owner", "string", "Owning class name when not module-level", optional=True),
            FieldDoc("type_annotation", "string", "Declared type", optional=True),
            FieldDoc("default_value", "string", "Default / initializer text", optional=True),
        ),
    ),
    NodeType.CODE_BLOCK.value: NodeTypeDoc(
        "Core",
        "Root-level executable code (e.g. if-guard, bare loop)",
        fields=(),
    ),
    NodeType.IMPORT.value: NodeTypeDoc(
        "Resolution",
        "An import statement (used by the resolver, not emitted in the export)",
        fields=(
            FieldDoc("module", "string", "Imported module path"),
            FieldDoc("names", "list[string]", "Imported symbol names", optional=True),
            FieldDoc("alias", "string", "Import alias", optional=True),
            FieldDoc("is_wildcard", "boolean", "Whether this is a wildcard import"),
            FieldDoc("is_reexport", "boolean", "Whether this is a re-export"),
        ),
        internal=True,
    ),
    NodeType.CALL.value: NodeTypeDoc(
        "Resolution",
        "A function/method call site (not emitted in the export)",
        fields=(
            FieldDoc("callee", "string", "Callee name"),
            FieldDoc("receiver", "string", "Receiver expression / name", optional=True),
            FieldDoc("full_expression", "string", "Full call expression text"),
            FieldDoc("context", "string", "Enclosing context hint", optional=True),
            FieldDoc("arguments", "list[string]", "Argument expression texts", optional=True),
            FieldDoc("assign_target", "string", "Assignment target if any", optional=True),
        ),
        internal=True,
    ),
    NodeType.LOCAL_VAR.value: NodeTypeDoc(
        "Resolution",
        "A typed local variable inside a function body (not emitted; used for receiver type inference)",
        fields=(
            FieldDoc("type_annotation", "string", "Declared type", optional=True),
            FieldDoc("default_value", "string", "Initializer text", optional=True),
        ),
        internal=True,
    ),
    NodeType.ENUM.value: NodeTypeDoc(
        "Language-specific",
        "An enumeration type",
        fields=(
            FieldDoc("members", "list[string]", "Enum member names", optional=True),
            FieldDoc("bases", "list[string]", "Base types", optional=True),
        ),
    ),
    NodeType.STRUCT.value: NodeTypeDoc(
        "Language-specific",
        "A struct (C/C++, Go, Rust, etc.)",
        fields=(
            FieldDoc(
                "fields",
                "list[object]",
                "Nested field records (property-like dicts)",
                optional=True,
            ),
            FieldDoc("bases", "list[string]", "Base / embedded types", optional=True),
        ),
    ),
    NodeType.UNION.value: NodeTypeDoc(
        "Language-specific",
        "A union type (C/C++)",
        fields=(FieldDoc("variants", "list[string]", "Variant type names", optional=True),),
    ),
    NodeType.MACRO.value: NodeTypeDoc(
        "Language-specific",
        "A preprocessor macro (C/C++)",
        fields=(
            FieldDoc("parameters", "list[string]", "Macro parameter names", optional=True),
            FieldDoc("expansion", "string", "Macro expansion text"),
        ),
    ),
    NodeType.MODULE.value: NodeTypeDoc(
        "Language-specific",
        "A named module, namespace, or documentation section",
        fields=(
            FieldDoc("language", "string", "Language when synthesised from folder markers", optional=True),
            FieldDoc("exports", "list[string]", "Exported names when known", optional=True),
        ),
    ),
    NodeType.TYPE_ALIAS.value: NodeTypeDoc(
        "Language-specific",
        "A type alias (type X = Y)",
        fields=(FieldDoc("aliased_type", "string", "Aliased type expression"),),
    ),
    NodeType.ANNOTATION.value: NodeTypeDoc(
        "Language-specific",
        "A decorator/annotation targeting another symbol",
        fields=(FieldDoc("target", "string", "Annotated symbol name", optional=True),),
    ),
}

EDGE_TYPE_DOCS: dict[str, EdgeTypeDoc] = {
    EdgeType.CONTAINS.value: EdgeTypeDoc(
        "Parent–child structural containment (folder→file, file→class, class→method, etc.)",
        "1.0",
    ),
    EdgeType.IMPORTS.value: EdgeTypeDoc("One file imports a symbol from another", "1.0"),
    EdgeType.INHERITS.value: EdgeTypeDoc("A class extends another class", "1.0"),
    EdgeType.IMPLEMENTS.value: EdgeTypeDoc(
        "A class structurally implements a duck type or protocol",
        "1.0",
    ),
    EdgeType.OVERRIDES.value: EdgeTypeDoc(
        "A method redefines a same-name, same-arity method on a nearest inherited/implemented ancestor",
        "1.0",
    ),
    EdgeType.DECORATED_BY.value: EdgeTypeDoc(
        "A function or class is decorated by another symbol",
        "1.0",
    ),
    EdgeType.METACLASS.value: EdgeTypeDoc(
        "A class uses another class as its metaclass",
        "1.0",
    ),
    EdgeType.CALLS.value: EdgeTypeDoc(
        "A function/method calls another (tiered: import-exact 1.0, local-scope 0.9, "
        "sibling-method 0.85, method-receiver 0.7, indirect-receiver 0.6, name-match 0.5)",
        "0.5–1.0",
    ),
    EdgeType.INSTANTIATES.value: EdgeTypeDoc(
        "A call expression constructs an instance of a class",
        "0.9",
    ),
    EdgeType.TYPE_OF.value: EdgeTypeDoc(
        "A type annotation references a class/interface (property types, return types, parameter types)",
        "0.8",
    ),
    EdgeType.EXPECTS.value: EdgeTypeDoc(
        "A duck type expects a specific method signature",
        "1.0",
    ),
    EdgeType.IS_SUBSET_OF.value: EdgeTypeDoc(
        "One duck type's method set is a subset of another's",
        "1.0",
    ),
}

_NODE_TYPE_KEYS = tuple(nt.value for nt in NodeType)
_EDGE_TYPE_KEYS = tuple(et.value for et in EdgeType)


def _format_fields(fields: tuple[FieldDoc, ...], *, heading: str) -> list[str]:
    lines = ["", heading, ""]
    for field in fields:
        opt = " (optional)" if field.optional else ""
        lines.append(f"- {field.name}: {field.type_name}{opt} — {field.description}")
    return lines


@cache
def types_index_body() -> str:
    """Return markdown listing all node and edge type resource URIs.

    :returns: Index body for ``jiuwen-code-parser://types``.
    """
    lines = [
        "# Jiuwen Code Parser — type resources",
        "",
        "Read a URI below for documentation of one node or edge type used in the code graph.",
        "Each type page lists exported JSON fields and their types.",
        "",
        "## Common node fields",
        "",
    ]
    for field in COMMON_NODE_FIELDS:
        opt = " (optional)" if field.optional else ""
        lines.append(f"- {field.name}: {field.type_name}{opt} — {field.description}")
    lines.extend(["", "## Common edge fields", ""])
    for field in COMMON_EDGE_FIELDS:
        opt = " (optional)" if field.optional else ""
        lines.append(f"- {field.name}: {field.type_name}{opt} — {field.description}")
    lines.extend(["", "## Node types", ""])
    for key in _NODE_TYPE_KEYS:
        lines.append(f"- jiuwen-code-parser://types/nodes/{key}")
    lines.extend(["", "## Edge types", ""])
    for key in _EDGE_TYPE_KEYS:
        lines.append(f"- jiuwen-code-parser://types/edges/{key}")
    lines.append("")
    return "\n".join(lines)


@cache
def node_type_resource_body(node_type: str) -> str:
    """Return plain-text documentation for one node type.

    :param node_type: Node type key (e.g. ``function``).
    :returns: Body for ``jiuwen-code-parser://types/nodes/{node_type}``.
    """
    doc = NODE_TYPE_DOCS.get(node_type)
    if doc is None:
        allowed = ", ".join(_NODE_TYPE_KEYS)
        return f"Unknown node type {node_type!r}. Allowed: {allowed}."

    lines = [
        f"node_type: {node_type}",
        f"category: {doc.category}",
        f"description: {doc.description}",
    ]
    if doc.internal:
        lines.append("internal: true (not emitted in graph export)")
    lines.extend(_format_fields(COMMON_NODE_FIELDS, heading="common fields:"))
    if doc.fields:
        lines.extend(_format_fields(doc.fields, heading="type fields:"))
    else:
        lines.extend(["", "type fields:", "", "(none beyond common fields)"])
    lines.append("")
    return "\n".join(lines)


@cache
def edge_type_resource_body(edge_type: str) -> str:
    """Return plain-text documentation for one edge relation.

    :param edge_type: Edge relation key (e.g. ``CALLS``).
    :returns: Body for ``jiuwen-code-parser://types/edges/{edge_type}``.
    """
    doc = EDGE_TYPE_DOCS.get(edge_type)
    if doc is None:
        allowed = ", ".join(_EDGE_TYPE_KEYS)
        return f"Unknown edge type {edge_type!r}. Allowed: {allowed}."

    lines = [
        f"edge_type: {edge_type}",
        f"description: {doc.description}",
        f"confidence: {doc.confidence}",
    ]
    lines.extend(_format_fields(COMMON_EDGE_FIELDS, heading="fields:"))
    lines.append("")
    return "\n".join(lines)
