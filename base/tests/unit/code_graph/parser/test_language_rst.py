"""Tests for reStructuredText language parser."""

import pytest

from openjiuwen_search_base.codegraph.parser.constants import detect_language
from openjiuwen_search_base.codegraph.parser.languages import register_builtins
from openjiuwen_search_base.codegraph.parser.loader import parse_file
from openjiuwen_search_base.codegraph.parser.models.core import ImportNode, PropertyNode
from openjiuwen_search_base.codegraph.parser.models.extensions import ModuleNode


@pytest.fixture(autouse=True)
def _register():
    register_builtins()


def _write_rst(tmp_path, content: str, name: str = "doc.rst"):
    f = tmp_path / name
    f.write_text(content)
    return f


class TestDetectLanguage:
    def test_rst_extension(self):
        assert detect_language("index.rst") == "rst"

    def test_rst_case_insensitive(self):
        assert detect_language("README.RST") == "rst"


@pytest.mark.asyncio(loop_scope="function")
class TestSections:
    async def test_single_section(self, tmp_path):
        f = _write_rst(tmp_path, "Title\n=====\n\nSome body text.\n")
        result = await parse_file(f)
        assert result.language == "rst"
        assert len(result.children) == 1
        sec = result.children[0]
        assert isinstance(sec, ModuleNode)
        assert sec.name == "Title"
        assert sec.source is not None
        assert "Some body text." in sec.source

    async def test_nested_sections(self, tmp_path):
        f = _write_rst(tmp_path, ("Top\n===\n\nSub A\n-----\n\nContent A.\n\nSub B\n-----\n\nContent B.\n"))
        result = await parse_file(f)
        top = result.children[0]
        assert isinstance(top, ModuleNode)
        assert top.name == "Top"
        assert len(top.children) == 2
        assert top.children[0].name == "Sub A"
        assert top.children[1].name == "Sub B"

    async def test_three_levels(self, tmp_path):
        f = _write_rst(tmp_path, ("H1\n==\n\nH2\n--\n\nH3\n^^\n\nDeep.\n"))
        result = await parse_file(f)
        h1 = result.children[0]
        assert h1.name == "H1"
        h2 = h1.children[0]
        assert isinstance(h2, ModuleNode)
        assert h2.name == "H2"
        h3 = h2.children[0]
        assert isinstance(h3, ModuleNode)
        assert h3.name == "H3"
        assert h3.source is not None
        assert "Deep." in h3.source


@pytest.mark.asyncio(loop_scope="function")
class TestDirectives:
    async def test_code_block(self, tmp_path):
        f = _write_rst(tmp_path, ("Demo\n====\n\n.. code-block:: python\n\n   print('hello')\n"))
        result = await parse_file(f)
        demo = result.children[0]
        props = [c for c in demo.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].name == ".. code-block::"
        assert "print('hello')" in (props[0].source or "")

    async def test_note_directive(self, tmp_path):
        f = _write_rst(tmp_path, ("Info\n====\n\n.. note::\n\n   Important info here.\n"))
        result = await parse_file(f)
        info = result.children[0]
        props = [c for c in info.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].name == ".. note::"

    async def test_image_directive(self, tmp_path):
        f = _write_rst(tmp_path, ("Pics\n====\n\n.. image:: logo.png\n"))
        result = await parse_file(f)
        pics = result.children[0]
        props = [c for c in pics.children if isinstance(c, PropertyNode)]
        assert len(props) == 1
        assert props[0].name == ".. image::"


@pytest.mark.asyncio(loop_scope="function")
class TestIncludes:
    async def test_include_directive(self, tmp_path):
        f = _write_rst(tmp_path, ("Doc\n===\n\n.. include:: footer.rst\n"))
        result = await parse_file(f)
        doc = result.children[0]
        imports = [c for c in doc.children if isinstance(c, ImportNode)]
        assert len(imports) == 1
        assert imports[0].module == "footer.rst"
        assert imports[0].name == "include footer.rst"

    async def test_literalinclude(self, tmp_path):
        f = _write_rst(tmp_path, ("Examples\n========\n\n.. literalinclude:: example.py\n"))
        result = await parse_file(f)
        sec = result.children[0]
        imports = [c for c in sec.children if isinstance(c, ImportNode)]
        assert len(imports) == 1
        assert imports[0].module == "example.py"


@pytest.mark.asyncio(loop_scope="function")
class TestToctree:
    async def test_toctree_entries(self, tmp_path):
        f = _write_rst(
            tmp_path,
            ("Index\n=====\n\n.. toctree::\n   :maxdepth: 2\n\n   intro\n   tutorial/index\n   api/reference\n"),
        )
        result = await parse_file(f)
        sec = result.children[0]
        imports = [c for c in sec.children if isinstance(c, ImportNode)]
        assert len(imports) == 1
        toc = imports[0]
        assert toc.name == "toctree"
        assert toc.names == ("intro", "tutorial/index", "api/reference")

    async def test_toctree_with_labels(self, tmp_path):
        f = _write_rst(
            tmp_path, ("Index\n=====\n\n.. toctree::\n\n   Getting Started <quickstart>\n   API <api/index>\n")
        )
        result = await parse_file(f)
        sec = result.children[0]
        toc = [c for c in sec.children if isinstance(c, ImportNode)][0]
        assert toc.names == ("quickstart", "api/index")


@pytest.mark.asyncio(loop_scope="function")
class TestEdgeCases:
    async def test_empty_file(self, tmp_path):
        f = _write_rst(tmp_path, "")
        result = await parse_file(f)
        assert result.language == "rst"
        assert result.children == ()

    async def test_no_sections(self, tmp_path):
        f = _write_rst(tmp_path, "Just a plain paragraph.\n")
        result = await parse_file(f)
        assert result.children == ()

    async def test_sibling_sections_same_level(self, tmp_path):
        f = _write_rst(tmp_path, ("A\n=\n\nBody A.\n\nB\n=\n\nBody B.\n"))
        result = await parse_file(f)
        assert len(result.children) == 2
        assert result.children[0].name == "A"
        assert result.children[1].name == "B"

    async def test_directive_between_sections(self, tmp_path):
        f = _write_rst(tmp_path, ("Top\n===\n\n.. warning::\n\n   Be careful.\n\nSub\n---\n\nDetails.\n"))
        result = await parse_file(f)
        top = result.children[0]
        assert top.name == "Top"
        warnings = [c for c in top.children if isinstance(c, PropertyNode)]
        assert len(warnings) == 1
        assert warnings[0].name == ".. warning::"
        subs = [c for c in top.children if isinstance(c, ModuleNode)]
        assert len(subs) == 1
        assert subs[0].name == "Sub"
