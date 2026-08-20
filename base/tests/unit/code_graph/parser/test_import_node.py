"""Tests for ImportNode extraction in Python and TypeScript parsers."""

import asyncio
import tempfile
from pathlib import Path

from openjiuwen_search_base.codegraph import parse_file
from openjiuwen_search_base.codegraph.parser.constants import NodeType
from openjiuwen_search_base.codegraph.parser.models import FileNode, ImportNode


def _parse_py(source: str) -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _parse_ts(source: str, ext: str = ".ts") -> FileNode:
    with tempfile.NamedTemporaryFile(suffix=ext, mode="w", delete=False) as f:
        f.write(source)
        path = Path(f.name)
    try:
        return asyncio.run(parse_file(path))
    finally:
        path.unlink()


def _imports(file_node: FileNode) -> list[ImportNode]:
    return [c for c in file_node.children if c.node_type == NodeType.IMPORT]


# ---------------------------------------------------------------------------
# Python imports
# ---------------------------------------------------------------------------


class TestPythonImports:
    def test_import_simple(self):
        r = _parse_py("import os\n")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].module == "os"
        assert imps[0].names == ("os",)

    def test_import_aliased(self):
        r = _parse_py("import numpy as np\n")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].module == "numpy"
        assert imps[0].alias == "np"

    def test_from_import(self):
        r = _parse_py("from os.path import join, exists\n")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].module == "os.path"
        assert "join" in imps[0].names
        assert "exists" in imps[0].names

    def test_from_import_relative(self):
        r = _parse_py("from . import sibling\n")
        imps = _imports(r)
        assert len(imps) == 1
        assert "." in imps[0].module
        assert "sibling" in imps[0].names

    def test_wildcard_import(self):
        r = _parse_py("from typing import *\n")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].module == "typing"
        assert imps[0].is_wildcard is True
        assert imps[0].names == ()

    def test_multiple_imports(self):
        src = "import os\nimport sys\nfrom pathlib import Path\n"
        r = _parse_py(src)
        imps = _imports(r)
        assert len(imps) == 3


# ---------------------------------------------------------------------------
# TypeScript imports
# ---------------------------------------------------------------------------


class TestTypeScriptImports:
    def test_named_import(self):
        r = _parse_ts("import { Foo, Bar } from './foo';")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].module == "./foo"
        assert "Foo" in imps[0].names
        assert "Bar" in imps[0].names

    def test_default_import(self):
        r = _parse_ts("import React from 'react';")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].module == "react"
        assert "React" in imps[0].names

    def test_namespace_import(self):
        r = _parse_ts("import * as utils from '../utils';")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].module == "../utils"
        assert imps[0].is_wildcard is True
        assert "utils" in imps[0].names

    def test_reexport(self):
        r = _parse_ts("export { Bar } from './bar';")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].is_reexport is True
        assert imps[0].module == "./bar"
        assert "Bar" in imps[0].names

    def test_type_only_import(self):
        r = _parse_ts("import type { X } from './types';")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].module == "./types"

    def test_side_effect_import(self):
        r = _parse_ts("import './polyfill';")
        imps = _imports(r)
        assert len(imps) == 1
        assert imps[0].module == "./polyfill"
