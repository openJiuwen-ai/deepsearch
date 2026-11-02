# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import textwrap

from openjiuwen_codesearch.indexing.chunkers.python import PythonAstChunker

SOURCE = textwrap.dedent(
    '''
    import os

    def top(a, b):
        return helper(a) + b

    class Widget:
        """doc"""

        def method(self):
            return os.path.join("x", str(self))

    def helper(x):
        return x * 2
    '''
).strip()


def test_named_defs_extracted_with_kinds():
    chunks = PythonAstChunker().chunk_source(SOURCE)
    by_name = {c.name: c for c in chunks}
    assert set(by_name) == {"top", "Widget", "method", "helper"}
    assert by_name["top"].kind == "function_definition"
    assert by_name["Widget"].kind == "class_definition"


def test_nested_defs_are_separate_overlapping_chunks():
    chunks = PythonAstChunker().chunk_source(SOURCE)
    by_name = {c.name: c for c in chunks}
    widget, method = by_name["Widget"], by_name["method"]
    assert widget.start_line <= method.start_line <= method.end_line <= widget.end_line


def test_chunk_text_matches_line_span():
    chunks = PythonAstChunker().chunk_source(SOURCE)
    top = next(c for c in chunks if c.name == "top")
    assert top.text.startswith("def top(a, b):")
    assert top.end_line - top.start_line + 1 == len(top.text.split("\n"))


def test_calls_collected():
    chunks = PythonAstChunker().chunk_source(SOURCE)
    by_name = {c.name: c for c in chunks}
    assert "helper" in by_name["top"].calls
    assert "os.path.join" in by_name["method"].calls


def test_no_defs_falls_back_to_whole_file():
    source = "x = 1\ny = x + 2\n"
    chunks = PythonAstChunker().chunk_source(source)
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].name == "" and chunks[0].kind == ""


def test_syntax_error_falls_back_to_whole_file():
    chunks = PythonAstChunker().chunk_source("def broken(:\n    pass")
    assert len(chunks) == 1


def test_empty_source_gives_no_chunks():
    assert PythonAstChunker().chunk_source("   \n") == []
