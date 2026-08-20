# JiuwenCodeParser

Async Python library that parses source files into typed node trees, exports them as a graph (JSONL, `.jcp` or LadybugDB), and provides an interactive browser-based viewer.

## Features

- **Tree-sitter parsers** — Python, Java, C/C++, Rust, Go, JavaScript, TypeScript/TSX, HTML, CSS, Makefile, and reStructuredText/Sphinx — extracting classes, functions (methods/nested/lambdas), properties, local variables, enums, structs, unions, macros, interfaces, duck types, type aliases, imports, calls, and root-level code blocks
- **Markdown parser** — rule-based heading hierarchy
- **RST parser** — Sphinx documentation with section hierarchy, directives, toctree, and include/literalinclude
- **Chunker** — builds top-level embedding chunks from `list[FileNode]` (nested symbols collapse into their nearest file-distance-1 ancestor), runs the same resolution pipeline as graph export, and remaps edges onto `ChunkEdge`s with original node endpoints preserved ([visual demo](./resources/chunker_demo))
- **Graph export** — JSONL + compressed `.jcp` format with 12 semantic edge types (see tables below); intermediate resolution nodes (`import`, `call`, `local_var`) are collapsed and not emitted as graph vertices
- **LadybugDB export** — optional LadybugDB backend with per-type node/edge tables, configurable batch sizes, and built-in query helpers
- **Viewer** — React + TypeScript SPA with force-directed graph and tree views

![Screenshot](./resources/assets/img/screenshot-v2.png)

## Quick Start

```bash
# Parse and export a project
make export

# Open the viewer
make viewer-dev
# Drop .jiuwen_graph/graph.jcp into the browser
```

Ladybug export is optional:

```bash
# Install the optional Ladybug bindings
uv sync --group ladybug

# Export only a Ladybug database
uv run python -m openjiuwen_search_base.codegraph.export . --backend ladybug

# Export both the browser bundle and a Ladybug database
uv run python -m openjiuwen_search_base.codegraph.export . --backend both

# Or use the Makefile shortcut
make export-ladybug

# Tune batch sizes for large projects
uv run python -m openjiuwen_search_base.codegraph.export . --backend ladybug \
    --node-batch-size 2000 --edge-batch-size 10000

# Browse the exported database with Ladybug Explorer
./ladybug.sh
```

## MCP Server

Optional [FastMCP](https://gofastmcp.com/) server for indexing a project and searching the code graph with the same viewer query syntax.

```bash
uv sync --extra mcp
uv run python -m openjiuwen_search_base.codegraph.mcp
```

**Tools**

| Tool | Description |
|---|---|
| `index(path)` | Parse a directory, write `<path>/.jiuwen_graph`, keep the graph in session memory |
| `search_nodes(query, limit=50)` | Viewer-syntax search; returns matches + tag stats |
| `search_edges(query, limit=50)` | Viewer-syntax search; returns matches + endpoint tag stats |
| `search_regex(pattern, target="nodes", limit=50)` | Regex search over node or edge fields (`target`: `nodes`\|`edges`) |

**Resources**

| URI | Description |
|---|---|
| `jiuwen-code-parser://types` | Index of all node/edge type doc URIs |
| `jiuwen-code-parser://types/nodes/<node_type>` | Docs for one node type (e.g. `…/nodes/function`) |
| `jiuwen-code-parser://types/edges/<edge_type>` | Docs for one edge relation (e.g. `…/edges/CALLS`) |

Cursor / MCP client (stdio) example:

```json
{
  "mcpServers": {
    "jiuwen-code-graph": {
      "command": "uv",
      "args": [
        "run",
        "--extra",
        "mcp",
        "python",
        "-m",
        "openjiuwen_search_base.codegraph.mcp"
      ]
    }
  }
}
```

The MCP package is isolated: core parse/search/export do not require FastMCP. Install `openjiuwen_search_base.codegraph[mcp]` (or sync with `--extra mcp`) only when running the server.

## Programmatic Usage

```python
import asyncio
from pathlib import Path
from openjiuwen_search_base.codegraph import parse_files, chunks_from_file_nodes


async def main():
    paths = list(Path("src").rglob("*.py"))
    file_nodes = await parse_files(paths)
    chunks, edges = chunks_from_file_nodes(file_nodes, run_resolver=True)
    for chunk in chunks:
        print(chunk.signature, chunk.node_type.value)
    for edge in edges[:5]:
        print(edge.relation, edge.original_lhs, "->", edge.original_rhs)


asyncio.run(main())
```

See also the [chunker demo](./resources/chunker_demo) (`show.py`).

Ladybug read/query helpers are available separately:

```python
from openjiuwen_search_base.codegraph.lbug_query import get_node, neighbors, search_nodes

db_path = ".jiuwen_graph/graph.lbug"
matches = search_nodes(db_path, node_type="function", limit=10)
node = get_node(db_path, matches[0]["id"])
links = neighbors(db_path, node["id"], relation="CALLS")
```

<details>
<summary><strong>Setup</strong></summary>

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
make sync                # install Python dependencies
make viewer-install      # install viewer dependencies
make test                # run unit tests
make cov                 # tests with coverage report
make check               # format + docstring + type check
make export              # export this project's graph (JSONL + .jcp)
make export-ladybug      # export to LadybugDB (.lbug)
uv sync --group ladybug  # install optional Ladybug bindings
uv sync --extra mcp      # install optional FastMCP server dependency
make viewer-dev          # start viewer dev server
make viewer-build        # production build → viewer/dist/
```

</details>

## Viewer Search Syntax

The graph view search bar supports free-text matching and structured predicates.

**Free text** — matches against node name and signature (case-insensitive):
```
BaseNode
parse_file
```

**Predicates** — filter on any node or edge field using `{field:pattern}`. Patterns support `*` as a glob wildcard and are case-insensitive:
```
{type:class}                 # all class nodes
{owner:*Node}                # nodes whose owner ends with "Node"
{language:python}            # python-language nodes
{type:function} parse        # functions matching "parse"
```

**Edge predicates** — `relation`, `confidence`, and `resolved_by` match against edges; both endpoints of matching edges are highlighted:
```
{relation:INHERITS}          # all inheritance edges
{relation:CALLS}             # all call edges
```

**Multiple predicates** — combine freely; all must match:
```
{type:property} {owner:File*}
```

Non-matching nodes and edges are dimmed. Use the **Show Neighbours** slider (bottom-right) to expand highlighting to nodes within *k* hops — when a search is active it expands from all matched nodes, otherwise from the selected node.

### Programmatic search

The same syntax is available in Python over exported graph dicts (separate from the Ladybug exact-match helper in `openjiuwen_search_base.codegraph.lbug_query`):

```python
from openjiuwen_search_base.codegraph import search_nodes, search_edges
from openjiuwen_search_base.codegraph.parser.graph_export import export_graph_from_file_nodes

nodes, edges = export_graph_from_file_nodes(file_nodes, root=str(root))

fns = search_nodes(nodes, "{type:function} parse", limit=20)
print(fns.total, fns.tag_counts)
for n in fns.matches:
    print(n["id"], n["name"])

calls = search_edges(edges, "{relation:CALLS}", limit=50, nodes=nodes)
print(calls.total, calls.tag_combo_counts)
for e in calls.matches:
    print(e["source"], "->", e["target"], e["relation"])
```

- `search_nodes` / `search_edges` return a `SearchResult` with `matches`, `total`, `tag_counts`, and `tag_combo_counts`.
- Callers always get the pre-limit hit count via `total`. `limit` only truncates `matches` (`-1` or `None` = all); tag stats also cover the full hit set.
- Node sort: free-text relevance (exact name → name contains → signature-only), then `node_type` / `name` / `path` / `id`.
- Edge sort: `confidence` descending (missing = 1.0), then `relation` / `source` / `target`.
- `tag_combo_counts` counts each node's (or edge endpoint's) **full** tag set (`a|b|c`), keeps the top 10 preferring longer sets then higher count — not all pairwise combinations.
- `search_nodes` — free text on `name` / `signature`; `{type:…}` matches `type` or `node_type`; edge-field predicates are ignored.
- `search_edges` — free text on `relation` / `resolved_by` / `source` / `target`; only edge-field predicates apply; node-only fields are ignored.
- `search_regex(pattern, target="nodes"|"edges")` — regex search over node fields `name`/`signature`/`id`/`path`/`type`/`node_type` or edge fields `relation`/`resolved_by`/`source`/`target`; invalid patterns raise `ValueError`.

## Node Types

| Category | Node type | Description |
|---|---|---|
| Structural | `folder` | A directory in the project tree |
| Structural | `file` | A parsed source file |
| Core | `class` | A class definition |
| Core | `interface` | An interface or Python `Protocol` |
| Core | `duck_type` | A structurally-inferred type defined by its required method set |
| Core | `function` | A function, method, or nested function |
| Core | `property` | A variable, attribute, or property with optional type info |
| Core | `code_block` | Root-level executable code (e.g. if-guard, bare loop) |
| Resolution | `import` | An import statement *(internal — used by the resolver, not emitted in the export)* |
| Resolution | `call` | A function/method call site *(internal — not emitted)* |
| Resolution | `local_var` | A typed local variable inside a function body *(internal — not emitted; used for receiver type inference)* |
| Language-specific | `enum` | An enumeration type |
| Language-specific | `struct` | A struct (C/C++, Go, Rust, etc.) |
| Language-specific | `union` | A union type (C/C++) |
| Language-specific | `macro` | A preprocessor macro (C/C++) |
| Language-specific | `module` | A named module, namespace, or documentation section |
| Language-specific | `type_alias` | A type alias (`type X = Y`) |
| Language-specific | `annotation` | A decorator/annotation targeting another symbol |

## Edge Types

| Edge type | Description | Confidence |
|---|---|---|
| `CONTAINS` | Parent–child structural containment (folder→file, file→class, class→method, etc.) | 1.0 |
| `IMPORTS` | One file imports a symbol from another | 1.0 |
| `INHERITS` | A class extends another class | 1.0 |
| `IMPLEMENTS` | A class structurally implements a duck type or protocol | 1.0 |
| `OVERRIDES` | A method redefines a same-name, same-arity method on a nearest inherited/implemented ancestor | 1.0 |
| `DECORATED_BY` | A function or class is decorated by another symbol | 1.0 |
| `METACLASS` | A class uses another class as its metaclass | 1.0 |
| `CALLS` | A function/method calls another (tiered: import-exact 1.0, local-scope 0.9, sibling-method 0.85, method-receiver 0.7, indirect-receiver 0.6, name-match 0.5) | 0.5–1.0 |
| `INSTANTIATES` | A call expression constructs an instance of a class | 0.9 |
| `TYPE_OF` | A type annotation references a class/interface (property types, return types, parameter types) | 0.8 |
| `EXPECTS` | A duck type expects a specific method signature | 1.0 |
| `IS_SUBSET_OF` | One duck type's method set is a subset of another's | 1.0 |

## Architecture

```
openjiuwen_search_base.codegraph/
  parser/
    constants.py        # NodeType enum, FILENAME_PATTERN, detect_language()
    custom_types.py     # SourceSpan, SignatureProvider protocol
    models/             # BaseNode, ClassNode, FunctionNode, PropertyNode, LocalVarNode, ...
    languages/          # BaseLanguageParser ABC, LanguageHooks, per-language parsers
    resolver/           # Multi-pass resolution pipeline (imports, calls, indirect calls, types, etc.)
    loader/             # parse_file(), parse_files() async API
    chunker.py          # chunks_from_file_nodes() — top-level chunks + ChunkEdges
    ids.py              # shared node_id() / folder_id() for export and chunker
    graph_export.py     # graph build + backend orchestration
  backends/
    jsonl_jcp.py        # nodes.jsonl + edges.jsonl + graph.jcp writer
    ladybug.py          # .lbug writer + query helpers (per-type tables)
  lbug_query.py         # public Ladybug query API (get_node, neighbors, search_nodes, …)
  search.py             # viewer-syntax search_nodes / search_edges over export dicts
  export.py             # CLI: python -m openjiuwen_search_base.codegraph.export
  mcp/                  # optional FastMCP server (index + search tools, type resources)

viewer/                 # React + Vite + Tailwind SPA
ladybug.sh              # launch Ladybug Explorer against .jiuwen_graph/graph.lbug
```

## License

[MIT](./LICENSE)
