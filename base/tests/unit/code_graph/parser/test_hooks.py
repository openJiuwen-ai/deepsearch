"""Tests for language hooks."""

from openjiuwen_search_base.codegraph.parser.languages import LanguageHooks, get_default_registry, register_builtins


def test_python_hooks():
    register_builtins()
    hooks = get_default_registry().get_hooks("python")
    assert "print" in hooks.builtins
    assert "len" in hooks.builtins
    assert "ValueError" in hooks.builtins
    assert "ArithmeticError" in hooks.builtins
    assert hooks.supports_decorators
    assert hooks.supports_metaclass
    assert hooks.is_protocol_base("Protocol")
    assert hooks.is_constructor_call("Foo")
    assert not hooks.is_constructor_call("foo")
    assert hooks.implicit_package_loading
    assert "__init__.py" in hooks.package_init_files


def test_python_builtins_from_runtime():
    """PYTHON_BUILTINS is derived from dir(builtins) and covers all stdlib names."""
    import builtins

    register_builtins()
    hooks = get_default_registry().get_hooks("python")
    expected = frozenset(dir(builtins))
    assert hooks.builtins == expected


def test_python_detect_modules():
    register_builtins()
    hooks = get_default_registry().get_hooks("python")
    mods = hooks.detect_modules("a/b/c", frozenset({"__init__.py", "foo.py"}), "/root")
    assert len(mods) == 1
    assert mods[0].name == "a.b.c"
    assert mods[0].language == "python"
    assert mods[0].path == "/root/a/b/c"

    assert hooks.detect_modules("a/b", frozenset({"foo.py"}), "/root") == []

    mods_root = hooks.detect_modules("", frozenset({"__init__.py"}), "/root")
    assert len(mods_root) == 1
    assert mods_root[0].name == "."
    assert mods_root[0].path == "/root"


def test_ts_hooks():
    register_builtins()
    hooks = get_default_registry().get_hooks("typescript")
    assert "console" in hooks.builtins
    assert hooks.supports_decorators
    assert not hooks.supports_metaclass
    assert not hooks.is_protocol_base("Protocol")
    assert hooks.is_constructor_call("foo")
    assert not hooks.implicit_package_loading
    assert "index.ts" in hooks.package_init_files


def test_ts_detect_modules():
    register_builtins()
    hooks = get_default_registry().get_hooks("typescript")
    mods = hooks.detect_modules("src/components", frozenset({"index.ts", "Button.tsx"}), "/proj")
    assert len(mods) == 1
    assert mods[0].name == "src/components"
    assert mods[0].language == "typescript"
    assert mods[0].path == "/proj/src/components"

    assert hooks.detect_modules("src/utils", frozenset({"helpers.ts"}), "/proj") == []


def test_default_hooks():
    hooks = LanguageHooks()
    assert hooks.builtins == frozenset()
    assert not hooks.supports_decorators
    assert not hooks.supports_metaclass
    assert hooks.extract_type_names("Foo") == ["Foo"]
    assert hooks.detect_modules("some/dir", frozenset({"file.txt"}), "/root") == []


def test_cached_property_returns_same_object():
    """cached_property should return the same object on repeated access."""
    hooks = LanguageHooks()
    assert hooks.builtins is hooks.builtins
    assert hooks.null_type_names is hooks.null_type_names
    assert hooks.package_init_files is hooks.package_init_files


def test_python_extract_type_names():
    register_builtins()
    hooks = get_default_registry().get_hooks("python")
    assert hooks.extract_type_names("list[Foo]") == ["Foo"]
    assert hooks.extract_type_names("Foo | None") == ["Foo"]
    assert hooks.extract_type_names("Optional[Bar]") == ["Bar"]


def test_ts_extract_type_names():
    register_builtins()
    hooks = get_default_registry().get_hooks("typescript")
    assert hooks.extract_type_names("Array<Foo>") == ["Foo"]
    assert hooks.extract_type_names("Foo | null") == ["Foo"]
    assert hooks.extract_type_names("Foo[]") == ["Foo"]


# ---------------------------------------------------------------------------
# Java hooks
# ---------------------------------------------------------------------------


def test_java_hooks():
    register_builtins()
    hooks = get_default_registry().get_hooks("java")
    assert "Object" in hooks.builtins
    assert "String" in hooks.builtins
    assert "Override" in hooks.builtins
    assert hooks.supports_decorators
    assert not hooks.supports_metaclass
    assert not hooks.is_protocol_base("Protocol")
    assert hooks.is_constructor_call("Foo")
    assert not hooks.implicit_package_loading
    assert "package-info.java" in hooks.package_init_files
    assert hooks.null_type_names == frozenset({"null", "void"})


def test_java_detect_modules():
    register_builtins()
    hooks = get_default_registry().get_hooks("java")
    mods = hooks.detect_modules("com/example/demo", frozenset({"Main.java", "Utils.java"}), "/proj")
    assert len(mods) == 1
    assert mods[0].name == "com.example.demo"
    assert mods[0].language == "java"
    assert mods[0].path == "/proj/com/example/demo"

    assert hooks.detect_modules("resources", frozenset({"config.xml"}), "/proj") == []

    mods_root = hooks.detect_modules("", frozenset({"Main.java"}), "/proj")
    assert len(mods_root) == 1
    assert mods_root[0].name == "."
    assert mods_root[0].path == "/proj"


def test_java_extract_type_names():
    register_builtins()
    hooks = get_default_registry().get_hooks("java")

    assert hooks.extract_type_names("List<Foo>") == ["Foo"]
    assert hooks.extract_type_names("Map<String, Foo>") == ["String", "Foo"]
    assert hooks.extract_type_names("? extends Foo") == ["Foo"]
    assert hooks.extract_type_names("? super Bar") == ["Bar"]
    assert hooks.extract_type_names("Foo[]") == ["Foo"]
    assert hooks.extract_type_names("com.example.Foo") == ["Foo", "com.example.Foo"]
    assert hooks.extract_type_names("int") == []
    assert hooks.extract_type_names("boolean") == []
    assert hooks.extract_type_names("void") == []
    assert hooks.extract_type_names("?") == []
    assert hooks.extract_type_names("MyClass") == ["MyClass"]
    assert hooks.extract_type_names("Optional<Bar>") == ["Bar"]
    assert hooks.extract_type_names("CompletableFuture<Result>") == ["Result"]
