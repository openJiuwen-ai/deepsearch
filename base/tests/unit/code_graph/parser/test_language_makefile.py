"""Tests for Makefile language parser."""

import pytest

from openjiuwen_search_base.codegraph.parser.languages import register_builtins
from openjiuwen_search_base.codegraph.parser.loader import parse_file
from openjiuwen_search_base.codegraph.parser.models.core import CodeBlockNode, FunctionNode, ImportNode, PropertyNode


@pytest.fixture(autouse=True)
def _register():
    register_builtins()


def _write_makefile(tmp_path, content: str, name: str = "Makefile"):
    f = tmp_path / name
    f.write_text(content)
    return f


@pytest.mark.asyncio(loop_scope="function")
class TestTargetRules:
    async def test_simple_target(self, tmp_path):
        f = _write_makefile(tmp_path, "test:\n\t@echo hello\n")
        result = await parse_file(f)
        targets = [c for c in result.children if isinstance(c, FunctionNode) and "target" in c.decorators]
        assert len(targets) == 1
        assert targets[0].name == "test"
        assert targets[0].func_type == "function"
        assert "target" in targets[0].decorators
        assert "@echo hello" in (targets[0].source or "")

    async def test_target_with_prerequisites(self, tmp_path):
        f = _write_makefile(tmp_path, "build: compile link\n\t@echo done\n")
        result = await parse_file(f)
        targets = [c for c in result.children if isinstance(c, FunctionNode) and "target" in c.decorators]
        assert len(targets) == 1
        assert targets[0].name == "build"
        param_names = [p.name for p in targets[0].parameters]
        assert "compile" in param_names
        assert "link" in param_names

    async def test_target_no_recipe(self, tmp_path):
        f = _write_makefile(tmp_path, "all: build test\n")
        result = await parse_file(f)
        targets = [c for c in result.children if isinstance(c, FunctionNode) and "target" in c.decorators]
        assert len(targets) == 1
        assert targets[0].name == "all"
        assert len(targets[0].parameters) == 2

    async def test_multiple_targets(self, tmp_path):
        f = _write_makefile(tmp_path, "test:\n\t@echo test\n\nlint:\n\t@echo lint\n")
        result = await parse_file(f)
        targets = [c for c in result.children if isinstance(c, FunctionNode) and "target" in c.decorators]
        assert len(targets) == 2
        names = {t.name for t in targets}
        assert names == {"test", "lint"}


@pytest.mark.asyncio(loop_scope="function")
class TestPhonyDirective:
    async def test_phony_is_code_block(self, tmp_path):
        f = _write_makefile(tmp_path, ".PHONY: test lint\n")
        result = await parse_file(f)
        code_blocks = [c for c in result.children if isinstance(c, CodeBlockNode)]
        assert len(code_blocks) == 1
        assert ".PHONY" in code_blocks[0].name


@pytest.mark.asyncio(loop_scope="function")
class TestVariables:
    async def test_simple_assignment(self, tmp_path):
        f = _write_makefile(tmp_path, "CC := gcc\n")
        result = await parse_file(f)
        props = [c for c in result.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].name == "CC"
        assert props[0].default_value == "gcc"
        assert props[0].type_annotation == ":="

    async def test_conditional_assignment(self, tmp_path):
        f = _write_makefile(tmp_path, "PYTHON ?= python3\n")
        result = await parse_file(f)
        props = [c for c in result.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].name == "PYTHON"
        assert props[0].default_value == "python3"
        assert props[0].type_annotation == "?="

    async def test_append_assignment(self, tmp_path):
        f = _write_makefile(tmp_path, "CFLAGS += -Wall\n")
        result = await parse_file(f)
        props = [c for c in result.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].type_annotation == "+="

    async def test_empty_value(self, tmp_path):
        f = _write_makefile(tmp_path, "FOO ?=\n")
        result = await parse_file(f)
        props = [c for c in result.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].name == "FOO"


@pytest.mark.asyncio(loop_scope="function")
class TestDefineDirective:
    async def test_define_is_function(self, tmp_path):
        f = _write_makefile(tmp_path, "define greet\necho hello $(1)\nendef\n")
        result = await parse_file(f)
        defines = [c for c in result.children if isinstance(c, FunctionNode) and "define" in c.decorators]
        assert len(defines) == 1
        assert defines[0].name == "greet"
        assert defines[0].func_type == "nested"
        assert "define" in defines[0].decorators
        assert defines[0].source is not None


@pytest.mark.asyncio(loop_scope="function")
class TestConditionals:
    async def test_conditional_is_code_block(self, tmp_path):
        f = _write_makefile(tmp_path, "ifeq ($(DEBUG),1)\nCFLAGS += -g\nendif\n")
        result = await parse_file(f)
        blocks = [c for c in result.children if isinstance(c, CodeBlockNode)]
        assert len(blocks) == 1
        assert "ifeq" in (blocks[0].source or "")


@pytest.mark.asyncio(loop_scope="function")
class TestExportDirective:
    async def test_export_variable(self, tmp_path):
        f = _write_makefile(tmp_path, "export PATH := /usr/local/bin\n")
        result = await parse_file(f)
        props = [c for c in result.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].name == "PATH"


@pytest.mark.asyncio(loop_scope="function")
class TestIncludeDirective:
    async def test_include_is_import(self, tmp_path):
        f = _write_makefile(tmp_path, "include config.mk\n")
        result = await parse_file(f)
        imports = [c for c in result.children if isinstance(c, ImportNode)]
        assert len(imports) == 1
        assert imports[0].module == "config.mk"


@pytest.mark.asyncio(loop_scope="function")
class TestCommentAsDocstring:
    async def test_comment_before_target(self, tmp_path):
        f = _write_makefile(tmp_path, "# Run the tests\ntest:\n\t@echo test\n")
        result = await parse_file(f)
        targets = [c for c in result.children if isinstance(c, FunctionNode)]
        assert len(targets) == 1
        assert targets[0].docstring == "Run the tests"

    async def test_comment_before_variable(self, tmp_path):
        f = _write_makefile(tmp_path, "# Compiler\nCC := gcc\n")
        result = await parse_file(f)
        props = [c for c in result.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].docstring == "Compiler"


@pytest.mark.asyncio(loop_scope="function")
class TestErrorNodeResilience:
    async def test_error_nodes_skipped(self, tmp_path):
        content = "test:\nifeq ($(X),1)\n\t@echo yes\nendif\n\nclean:\n\trm -rf build\n"
        f = _write_makefile(tmp_path, content)
        result = await parse_file(f)
        assert result.language == "makefile"


@pytest.mark.asyncio(loop_scope="function")
class TestFileDetection:
    async def test_makefile_name(self, tmp_path):
        f = _write_makefile(tmp_path, "all:\n\t@echo hi\n", name="Makefile")
        result = await parse_file(f)
        assert result.language == "makefile"

    async def test_mk_extension(self, tmp_path):
        f = _write_makefile(tmp_path, "all:\n\t@echo hi\n", name="rules.mk")
        result = await parse_file(f)
        assert result.language == "makefile"


@pytest.mark.asyncio(loop_scope="function")
class TestComplexMakefile:
    async def test_mixed_content(self, tmp_path):
        content = """\
.PHONY: test lint

CC := gcc
CFLAGS ?= -Wall

# Build the project
build: main.o utils.o
\t$(CC) $(CFLAGS) -o app main.o utils.o

clean:
\trm -rf *.o app

define compile-rule
$(CC) $(CFLAGS) -c $(1)
endef

ifeq ($(DEBUG),1)
CFLAGS += -g
endif
"""
        f = _write_makefile(tmp_path, content)
        result = await parse_file(f)

        phony = [c for c in result.children if isinstance(c, CodeBlockNode) and ".PHONY" in c.name]
        assert len(phony) == 1

        props = [c for c in result.children if isinstance(c, PropertyNode)]
        assert len(props) >= 2
        prop_names = {p.name for p in props}
        assert "CC" in prop_names
        assert "CFLAGS" in prop_names

        targets = [c for c in result.children if isinstance(c, FunctionNode) and "target" in c.decorators]
        assert len(targets) >= 2
        target_names = {t.name for t in targets}
        assert "build" in target_names
        assert "clean" in target_names

        build = next(t for t in targets if t.name == "build")
        assert build.docstring == "Build the project"
        param_names = [p.name for p in build.parameters]
        assert "main.o" in param_names
        assert "utils.o" in param_names

        defines = [c for c in result.children if isinstance(c, FunctionNode) and "define" in c.decorators]
        assert len(defines) == 1
        assert defines[0].name == "compile-rule"

        conditionals = [c for c in result.children if isinstance(c, CodeBlockNode) and c.name == "conditional"]
        assert len(conditionals) == 1
