# Language Hooks

`openjiuwen_search_base.codegraph` uses a plugin system to support multiple programming languages. Each language provides two components:

1. **Parser** -- extracts structural nodes (classes, functions, imports, etc.) from source files using tree-sitter or custom rules.
2. **Hooks** -- supplies language-specific behavior to the resolution pipeline (builtin names, type annotation parsing, module conventions, etc.).

Both are registered together in the `LanguageRegistry` and discovered automatically at startup.

## Architecture

```
languages/
  __init__.py          LanguageHooks base, BaseLanguageParser ABC, LanguageRegistry
  _common.py           Shared tree-sitter helpers (text(), span(), complexity(), etc.)
  python/
    __init__.py         Re-exports PythonParser, PythonHooks
    parse.py            PythonParser (tree-sitter based)
    hooks.py            PythonHooks (builtins, Protocol detection, generics, etc.)
  typescript/
    __init__.py         Re-exports TypeScriptParser, TsxParser, TsHooks
    parse.py            TypeScriptParser, TsxParser (tree-sitter based)
    hooks.py            TsHooks (TS/JS builtins, angle-bracket generics, etc.)
  javascript/
    __init__.py         Re-exports JavaScriptParser, JsHooks
    parse.py            JavaScriptParser (thin wrapper reusing TS extractors)
    hooks.py            JsHooks (alias of TsHooks)
  html/
    __init__.py
    parse.py
    hooks.py            Uses default LanguageHooks (no resolution logic)
  css/
    __init__.py
    parse.py
    hooks.py            Uses default LanguageHooks
  markdown/
    __init__.py
    parse.py
    hooks.py            Uses default LanguageHooks
  makefile/
    __init__.py
    parse.py
    hooks.py            Uses default LanguageHooks
  rst/
    __init__.py
    parse.py            RstParser (tree-sitter based, manual section hierarchy)
    hooks.py            Uses default LanguageHooks
  txt/
    __init__.py
    parse.py            TxtParser (no tree-sitter; FileNode + full source)
                        Fallback via parse_file(..., errors="as_txt")
  java/
    __init__.py         Re-exports JavaParser, JavaHooks
    parse.py            JavaParser (tree-sitter based, Javadoc extraction)
    hooks.py            JavaHooks (JDK builtins, Java generics/wildcards/arrays, package detection)
  c/
    __init__.py         Re-exports CBaseParser, CppParser, CHooks, CppHooks
    parse.py            CBaseParser (tree-sitter C: structs, unions, macros, functions)
    cpp_parse.py        CppParser (extends C: classes, namespaces, out-of-class methods, lambdas)
    hooks.py            CHooks / CppHooks (STL unwrap, implicit_this, no Makefile-based modules yet)
  rust/
    __init__.py         Re-exports RustParser, RustHooks
    parse.py            RustParser (structs, enums, traits, impl, use, mods, calls, locals)
    hooks.py            RustHooks (std builtins, generics/lifetimes, mod.rs module detection)
  go/
    __init__.py         Re-exports GoParser, GoHooks
    parse.py            GoParser (package, import, struct/interface, methods, calls, locals)
    hooks.py            GoHooks (builtins, slice/map unwrap, directory packages)
```
The resolver pipeline builds a `hooks_map: dict[str, LanguageHooks]` from the registry and passes it to each resolution pass. Passes look up hooks per file: `hooks = hooks_map.get(fnode.language, default_hooks)`.

## `LanguageHooks` Reference

The base class lives in `openjiuwen_search_base/codegraph/parser/languages/__init__.py`. All properties use `functools.cached_property` for zero-overhead repeated access. Subclasses override only what they need; the defaults are safe no-ops.

| Hook | Return type | Default | Purpose |
|---|---|---|---|
| `builtins` | `frozenset[str]` | `frozenset()` | Names to skip during resolution (unless redefined in the project). Checked against `FILTER_BUILTIN_NAMES`. |
| `null_type_names` | `frozenset[str]` | `frozenset()` | Null/void type names to exclude from type annotations (e.g. `None`, `null`, `undefined`). |
| `supports_decorators` | `bool` | `False` | Whether the decorator resolution pass should process files of this language. |
| `supports_metaclass` | `bool` | `False` | Whether metaclass edges should be emitted (Python-only concept). |
| `is_protocol_base(name)` | `bool` | `False` | Whether a base class name implies an `IMPLEMENTS` rather than `INHERITS` edge (e.g. Python's `Protocol`). |
| `is_constructor_call(callee)` | `bool` | `False` | Whether a call expression looks like a constructor invocation (Python: uppercase first letter; TS/JS: always `True`, relies on `ClassNode` check). Used by the calls resolver to skip direct constructor calls that resolve to a `ClassNode`, since those are handled as `INSTANTIATES` edges by the types pass. |
| `extract_type_names(annotation)` | `list[str]` | `[annotation]` | Parse a type annotation string into concrete type names, unwrapping generics and unions. |
| `callable_wrappers` | `frozenset[str]` | `frozenset()` | Fully-qualified names of functions that wrap a callable (e.g. `functools.partial`). The resolver verifies calls actually refer to these via the import index before treating the first positional argument as the underlying callable. |
| `implicit_this` | `bool` | `False` | Whether bare method calls inside a class implicitly refer to `this`/`self`. When `True` (Java, C/C++), unqualified calls resolve as sibling method calls. When `False` (Python, JS/TS), `self.`/`this.` is required and bare calls resolve through outer scopes. |
| `package_init_files` | `frozenset[str]` | `frozenset()` | Filenames that mark a directory as a package entry point (e.g. `__init__.py`, `index.ts`). Used by the duck-types resolver for import-chain scoping. |
| `implicit_package_loading` | `bool` | `False` | Whether importing a submodule implicitly loads parent `__init__` files (Python's package semantics). |
| `detect_modules(folder_rel, file_names, root)` | `list[ModuleInfo]` | `[]` | Detect modules/packages in a folder. Each language implements its own strategy (marker files, every-file-is-a-module, etc.). Returns `ModuleInfo(name, language, path)` instances. |
| `unwrap_receiver_type(annotation, subscript_depth)` | `str \| None` | `None` | Peel container/pointer wrappers from a type annotation for subscripted/dereferenced receivers (e.g. `std::vector<MyCamera>` + depth 1 → `MyCamera`). Used by the indirect-calls pass. |

## Adding a New Language

### Step 1: Create the package

Create a new directory under `openjiuwen_search_base/codegraph/parser/languages/`:

```
languages/
  rust/
    __init__.py
    parse.py
    hooks.py
```

### Step 2: Implement the parser

In `parse.py`, subclass `BaseLanguageParser` and implement the async `parse` method. For tree-sitter languages:

```python
import asyncio
from pathlib import Path

import tree_sitter_rust  # pip install tree-sitter-rust
from tree_sitter import Language, Parser

from ...models.structural import FileNode
from .. import BaseLanguageParser

_LANG = Language(tree_sitter_rust.language())


class RustParser(BaseLanguageParser):
    def __init__(self) -> None:
        self._parser = Parser(_LANG)

    async def parse(self, path: Path, source: bytes) -> FileNode:
        return await asyncio.to_thread(self._parse_sync, path, source)

    def _parse_sync(self, path: Path, source: bytes) -> FileNode:
        tree = self._parser.parse(source)
        # Walk tree.root_node and build FileNode with children
        ...
```

Use helpers from `_common.py` (`text()`, `span()`, `complexity()`, etc.) for tree-sitter node extraction.

### Step 3: Implement hooks (if needed)

If the language has resolution-specific behavior, subclass `LanguageHooks` in `hooks.py`:

```python
from functools import cached_property

from .. import LanguageHooks


from ...custom_types import ModuleInfo


class RustHooks(LanguageHooks):
    @cached_property
    def builtins(self) -> frozenset[str]:
        return frozenset(
            {
                "println",
                "eprintln",
                "format",
                "vec",
                "todo",
                "unimplemented",
                "panic",
                "assert",
                "assert_eq",
                "assert_ne",
                "dbg",
                "String",
                "Vec",
                "Box",
                "Option",
                "Result",
                "Some",
                "None",
                "Ok",
                "Err",
            }
        )

    @cached_property
    def package_init_files(self) -> frozenset[str]:
        return frozenset({"mod.rs", "lib.rs"})

    def detect_modules(self, folder_rel, file_names, root):
        if not ({"mod.rs", "lib.rs"} & file_names):
            return []
        from pathlib import Path

        name = folder_rel.replace("/", "::") if folder_rel else "crate"
        path = str(Path(root) / folder_rel) if folder_rel else root
        return [ModuleInfo(name=name, language="rust", path=path)]
```

If the language has no resolution-specific behavior (no builtins to skip, no decorators, no special type annotations), you can skip `hooks.py` entirely or leave it empty -- the base `LanguageHooks` defaults will be used.

### Step 4: Wire up `__init__.py`

```python
"""Rust language parser and hooks."""

from .hooks import RustHooks
from .parse import RustParser

__all__ = ["RustParser", "RustHooks"]
```

### Step 5: Register the filename pattern

Add a regex pattern to `FILENAME_PATTERN` in `openjiuwen_search_base/codegraph/parser/constants.py`:

```python
FILENAME_PATTERN: list[tuple[re.Pattern[str], str]] = [
    ...(re.compile(r"\.rs$", re.IGNORECASE), "rust"),
]
```

### Step 6: Register parser and hooks

Add registration calls to `register_builtins()` in `openjiuwen_search_base/codegraph/parser/languages/__init__.py`:

```python
def register_builtins() -> None:
    ...
    from .rust.hooks import RustHooks
    from .rust.parse import RustParser

    _DEFAULT_REGISTRY.register("rust", RustParser, RustHooks)
```

If the language uses no custom hooks, omit the third argument:

```python
    _DEFAULT_REGISTRY.register("rust", RustParser)
```

### Step 7: Add the tree-sitter dependency

```bash
uv add tree-sitter-rust
```

### Step 8: Add tests

At minimum, add a test file at `tests/parser/test_language_rust.py` that parses a sample source file and verifies the expected node tree. If custom hooks were implemented, add hook-level tests in `tests/parser/test_hooks.py`.

## Builtin Filtering

The resolver skips names found in `hooks.builtins` to avoid creating edges to standard library symbols. This is controlled by two mechanisms:

- **Redefinition guard**: if a builtin name (e.g. `Error`) is also defined as a `ClassNode` or `FunctionNode` in the parsed codebase, it is *not* treated as a builtin and resolution proceeds normally.
- **`FILTER_BUILTIN_NAMES` flag**: set in `constants.py` (default `True`). When `False`, no builtin filtering occurs and all names are resolved.

Python builtins are derived automatically from `dir(builtins)` at import time, covering all ~160 names without manual maintenance. JS/TS and Java builtins are hand-curated since there is no equivalent runtime introspection.

## Sharing Hooks Between Languages

Languages with identical resolution behavior can share hooks. JavaScript reuses TypeScript's hooks:

```python
# javascript/hooks.py
from ..typescript.hooks import TsHooks as JsHooks
```

Languages with no resolution logic (HTML, CSS, Markdown, plain text) need no custom hooks class at all -- the base `LanguageHooks` is used automatically when no `hooks_cls` is passed to `register()`.

## Java Resolver Notes

Java introduced several resolver-level concerns not present in Python or JS/TS. These are documented here as a reference for future languages with similar characteristics.

### Sibling method calls (no explicit receiver)

In Java, methods within the same class can call each other without an explicit receiver (no `this.` required):

```java
public class Canvas {
    public Canvas(long seed) {
        ReSeed(seed);  // calls sibling method, no receiver
    }
    public void ReSeed(long seed) { ... }
}
```

The calls resolver handles this via a **Tier 2.5: `sibling_method`** pass, gated behind the `implicit_this` hook (default `False`). Languages that set `implicit_this = True` (Java, C/C++) enable this resolution tier. When active, the resolver checks whether the callee matches another method in the same class. This pass lives in `calls.py::_resolve_sibling_call`.

Python and JS/TS are unaffected because `implicit_this` is `False` -- they require explicit `self.method()` / `this.method()` for sibling calls, and bare calls resolve through outer scopes instead.

### Context name matching (short vs qualified)

Java's parser reports call contexts as **short names** (e.g. `PaintAll`), but `FunctionNode.name` is **qualified** (e.g. `Canvas.PaintAll`). The resolver's `_find_enclosing` function handles this by checking both the short name and the qualified form `ClassName.context` when searching for the enclosing method. Without this, calls inside regular methods would be incorrectly attributed to the `FileNode` instead of the enclosing method.

For constructors specifically, Java reports the **class name** itself as the context (e.g. `"Canvas"` for code inside `public Canvas(...) {}`). `_find_enclosing` recognizes this and searches for the `<init>` constructor function node as the actual call source, using prefix matching to handle overloaded constructors.

### Method and constructor overload disambiguation

Java supports method overloading (multiple methods/constructors with the same name but different parameter types). The parser disambiguates overloads by appending a type signature suffix only when needed:

| Scenario | Generated name |
|---|---|
| Single constructor | `Foo.<init>` |
| Overloaded constructors | `Foo.<init>()`, `Foo.<init>(int)`, `Foo.<init>(int, String)` |
| Single method | `Foo.bar` |
| Overloaded methods | `Foo.bar()`, `Foo.bar(int)`, `Foo.bar(String, int)` |

The suffix is only added when the class body contains multiple declarations with the same name, keeping names clean for the common non-overloaded case. The resolver's `_find_enclosing` uses prefix matching (`startswith`) for constructor names to correctly handle both suffixed and unsuffixed forms.

### Modifier decorators

Java modifiers (`static`, `abstract`, `final`, `synchronized`, `native`) are promoted to synthetic `@`-prefixed decorators on `FunctionNode`, paralleling Python's `@staticmethod` / `@abstractmethod` convention. This makes modifiers visible in the `decorators` tuple for the resolver and viewer. For example, `public static void bar()` produces `decorators=("@static",)`, while `@Override public void baz()` produces `decorators=("@Override",)`. Both real annotations and synthetic modifiers coexist in the same tuple.

The `resolve_overrides` pass treats `@Override` / `@override` as confirmation when emitting an `OVERRIDES` edge (`resolved_by="override_annotation"`). The annotation is **not required** — name + arity matching alone is enough (`resolved_by="override_match"`), which covers Python and other languages without an override keyword.

### Java generics and type extraction

`JavaHooks.extract_type_names` handles Java-specific type syntax:

- **Generics**: `Map<String, Foo>` extracts `Foo` (skipping known containers in `_JAVA_CONTAINERS`)
- **Wildcards**: `? extends Comparable`, `? super Number` recurse into the bound
- **Arrays**: `int[][]` strips brackets and recurses
- **Qualified names**: `com.example.Foo` yields both `Foo` and `com.example.Foo`
- **Primitives**: `int`, `void`, etc. are filtered out (not resolvable types)

## Method Overrides (`OVERRIDES`)

Class-level `INHERITS` / `IMPLEMENTS` edges do not capture method redefinition. The `resolve_overrides` pass (immediately after `resolve_inheritance`, before decorators / inherited-method guessing) emits method → method `OVERRIDES` edges.

### How it works

1. Build a class → bases map from resolved `INHERITS` and `IMPLEMENTS` edges.
2. For each real class/interface method (including C++ out-of-class methods via `FunctionNode.owner`), BFS ancestors.
3. Emit **one** edge to the **nearest** ancestor method that matches:
   - **Name** — unqualified basename (`Circle.area` ↔ `Shape.area`), via the same scope/overload-tolerant helpers as other passes.
   - **Arity** — `len(parameters)` equal on both sides (includes `self`/`cls` when present). Same name with a different parameter count is treated as overload/shadow, not override.
4. Skip targets with `func_type="method-guessed"` (those are synthesized later and are not real definitions).

No new `LanguageHooks` entry is required; language differences are already encoded in method naming, parameters, and optional `@Override` / `@override` decorators (Java annotations; C++ promotes the `override` specifier to `@override` the same way as other modifiers).

### Limitations

- Parameter **types** / names are not compared — arity only.
- Only the nearest matching ancestor gets an edge (not every ancestor in the chain).
- Property / field shadowing is out of scope.

## Inherited Method Guessing

When a class inherits from a builtin or external base (e.g. `class MyList(list):`), the base class has no `ClassNode` in the parsed codebase. Methods like `append` and `extend` that exist on the base are invisible to the `ClassMethodIndex`, which only indexes directly-declared methods. This causes two problems:

1. **CALLS resolution** (Tier 3) fails for calls like `obj.append()` where `obj: MyList`.
2. **Duck type IMPLEMENTS** matching fails because `MyList` appears to have no methods matching the duck type's method set.

### How it works

The `resolve_inherited_methods` pass runs after `resolve_calls` and before `resolve_duck_types` (and after `resolve_overrides`, so override targets are always real AST methods). It:

1. **Identifies externally-based classes** -- classes where at least one base name cannot be resolved in the `SymbolIndex` or `ImportIndex`.
2. **Collects demanded methods** -- scans `CallNode`s for method calls on receivers typed to those classes (via annotations or constructor assignments like `obj = MyList()`).
3. **Depth-orders classes** -- processes children before parents so guessed methods propagate correctly through inheritance chains.
4. **Synthesizes `FunctionNode`s** with `func_type="method-guessed"` for each demanded method not already declared on the class.
5. **Updates `ClassMethodIndex`** so downstream passes (particularly duck type IMPLEMENTS) see the guessed methods.

### Output

Guessed methods appear as synthesized nodes in the graph export with a `"guessed"` tag and `func_type="method-guessed"`. They have `span=(0, 0, 0, 0)` and empty `source` since there is no actual source code for them -- they represent methods that *could* come from the builtin base or could be incorrect code.

### Limitations

- Only methods that are actually **called** in the codebase are guessed. Methods that exist on the builtin base but are never referenced remain invisible.
- The pass cannot distinguish between a method that truly comes from the base class and a typo/incorrect call. Both produce `method-guessed` nodes.

## Local Variables (`LocalVarNode`)

Typed locals are extracted as `LocalVarNode` children of the enclosing `FunctionNode`. Like `ImportNode` and `CallNode`, they live in `INTERNAL_NODES` and are **collapsed in export** (not emitted as graph vertices). The resolver uses them only for receiver type inference.

### Scoping and naming

| Language | Scope model | Name format |
|---|---|---|
| Python | Function-scoped | Plain name (`cam`) |
| Java, C/C++, TypeScript, Rust, Go | Block-scoped | `varname@L<line>@D<depth>` (e.g. `cam@L15@D0`) |

`@L` is the declaration start line; `@D` is nesting depth (`0` = function body, `1` = inside one nested block, …).

### `match_name` / `contain_name`

Resolver helpers in `resolver/passes/_utils.py` strip `@L…@D…` suffixes before comparing names, so a call with `receiver="cam"` matches a `LocalVarNode` named `cam@L15@D0`. Overload suffixes like `Foo.bar(int)` are still tolerated. `match_name` is `@functools.cache`'d and cleared at the end of each export run.

When multiple locals share the same base name (shadowing), the first match in `FunctionNode.children` (declaration order) wins — acceptable for a 0.6-confidence edge.

## Indirect Calls (Subscript / Pointer Receivers)

Calls like `cam[i].renderFrame()`, `objects[f][objIndex]->setMaterial(m)`, or `self.items[0].update()` cannot be resolved by the plain receiver-type tier because the receiver string is not a simple identifier.

The `resolve_indirect_calls` pass (after the second `resolve_calls` pass):

1. Strips subscripts / `*` dereference from the receiver (`cam[i]` → base `cam`, depth 1).
2. Looks up the base's type from parameters, `LocalVarNode`s, or class members (including C++ out-of-class methods and header/source splits).
3. Calls `hooks.unwrap_receiver_type(annotation, depth)` to peel containers/pointers.
4. Emits a `CALLS` edge with confidence **0.6** and `resolved_by="indirect_receiver"`.

### `unwrap_receiver_type` per language

| Language | What is unwrapped |
|---|---|
| C/C++ | `std::vector`/`map`/…, `shared_ptr`/`unique_ptr`, raw `T*` |
| Java | Array types only (`T[]`, `T[][]`) |
| Python | Annotated generics (`list[T]`, `dict[K,V]`, `tuple[T, …]`, …) |
| Rust | `Vec`/`Box`/`Option`/`Result`/`HashMap`/…, slices `[T]` |
| Go | slices `[]T`, maps `map[K]V`, pointers `*T` |
| Default | Returns `None` (no unwrap) |

`self.` / `this->` prefixes are kept in the receiver string so member lookups still work.

## C/C++ Resolver Notes

### Out-of-class method definitions

C++ often defines methods outside the class body (`void MyCamera::renderFrame() { … }`). The parser emits a top-level `FunctionNode` with `owner="MyCamera"` and qualified `name="MyCamera.renderFrame"`. `ClassMethodIndex` indexes these; `_find_enclosing` and `_resolve_sibling_call` match short call contexts against the qualified form; `_find_target_method` in the indirect-calls pass also searches file-level owned methods (not only `ClassNode.children`).

### Call nodes are file-level

Like Java/Python, C/C++ extracts `CallNode`s as siblings under `FileNode` (not nested under the function). `CallNode.context` holds the enclosing function/method short name.

### Module detection

`CHooks` / `CppHooks.detect_modules` currently returns `[]`. True C/C++ modules depend on build systems (Makefile, CMakeLists, etc.); heuristic `.h`/`.cpp` pairing was deliberately avoided as misleading.

### Constructors

`is_constructor_call` and the types pass emit `INSTANTIATES` for constructor invocations; direct constructor calls that resolve to a `ClassNode` do **not** also get a redundant `CALLS` edge.

### Specifier decorators (`override`, `virtual`, …)

C++ specifiers (`virtual`, `override`, `final`, `explicit`, `constexpr`, `consteval`) are promoted to synthetic `@`-prefixed decorators on `FunctionNode` (e.g. `void foo() override` → `decorators=("@override",)`). The overrides pass uses `@override` the same way as Java's `@Override` — as optional confirmation on an otherwise name+arity `OVERRIDES` edge.

## Rust Resolver Notes

Rust is registered as language `"rust"` (`.rs`) with [`RustParser`](../../../../openjiuwen_search_base/codegraph/parser/languages/rust/parse.py) + [`RustHooks`](../../../../openjiuwen_search_base/codegraph/parser/languages/rust/hooks.py).

### Construct mapping

| Rust | Node |
|---|---|
| `use` | `ImportNode` (groups / `as` / `*` supported) |
| `struct` / `enum` | `StructNode` / `EnumNode` (optional `bases` for trait impls) |
| `trait` | `InterfaceNode` (supertraits → `bases`; stubs → `function_signature_item`) |
| `impl Type` / `impl Trait for Type` | `FunctionNode` with `owner=Type` (C++ out-of-class pattern); trait name merged into type `bases` → `IMPLEMENTS` |
| `mod` | `ModuleNode` |
| `type` / `const` / `static` | `TypeAliasNode` / `PropertyNode` |
| calls | File-level `CallNode` from `call_expression` (methods via `field_expression`) |
| typed `let` | `LocalVarNode` with `@L@D` names |

`ClassMethodIndex` and `resolve_inheritance` / `resolve_overrides` index **StructNode and EnumNode** as well as classes/interfaces so inherent and trait methods resolve.

### Hooks

- `implicit_this = False` (explicit `self.` / `Self::`)
- `supports_decorators = True` (outer `#[…]` → function `decorators`)
- `package_init_files` = `mod.rs` / `lib.rs` / `main.rs`; `detect_modules` uses `::` paths
- `extract_type_names` strips lifetimes/refs and unwraps common generics; `unwrap_receiver_type` peels `Vec`/`Box`/… for indirect calls

### Out of scope (v1)

Macro expansion (`macro_invocation` / `macro_definition` left unexpanded), full crate graph beyond folder module markers.

## Go Resolver Notes

Go is registered as language `"go"` (`.go`) with [`GoParser`](../../../../openjiuwen_search_base/codegraph/parser/languages/go/parse.py) + [`GoHooks`](../../../../openjiuwen_search_base/codegraph/parser/languages/go/hooks.py).

### Construct mapping

| Go | Node |
|---|---|
| `package` | `ModuleNode` (flat sibling; items stay file-level for the resolver) |
| `import` | `ImportNode` per `import_spec` (alias / `.` wildcard / `_` blank) |
| free `func` | `FunctionNode` |
| method | `method_declaration` → `FunctionNode` with `owner` from receiver type (`*T` peeled) |
| `type T struct` | `StructNode`; embedded fields (no name) → `bases` |
| `type T interface` | `InterfaceNode`; `method_elem` stubs; `type_elem` embeds → `bases` |
| `type T = U` / defined type | `TypeAliasNode` |
| `const` / package `var` | `PropertyNode` |
| calls | File-level `CallNode`; method/package selector via `selector_expression` |
| typed `var` in body | `LocalVarNode` with `@L@D` |

Interface satisfaction without embedding is **not** inferred in v1 (no Go `implements` keyword). Embedding an interface in a struct sets `bases` → `IMPLEMENTS`.

### Hooks

- `implicit_this = False`; `supports_decorators = False`
- `detect_modules`: any folder containing `.go` files

## Plain Text (`txt`)

Plain text is registered as language `"txt"` (`.txt`) with [`TxtParser`](../../../../openjiuwen_search_base/codegraph/parser/languages/txt/parse.py). There is no tree-sitter grammar and no custom hooks.

The parser emits a single `FileNode` whose `source` is the full decoded text and whose `children` are empty. Unknown extensions are not auto-detected as `txt`; callers opt in with `parse_file` / `parse_files` `errors="as_txt"` (see the loader). `errors="ignore"` skips those files instead; `errors="strict"` (default) still raises.
