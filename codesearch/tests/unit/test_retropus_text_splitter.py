# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the recursive character text splitter."""

from openjiuwen_codesearch.retropus.graph.text_splitter import split_text


def test_prefers_paragraph_breaks():
    text = "aaa\n\nbbb\n\nccc"
    chunks = split_text(text, chunk_size=5, chunk_overlap=0)
    assert chunks == ["aaa", "bbb", "ccc"]


def test_respects_chunk_size_and_overlap():
    text = "one two three four five six"
    chunks = split_text(text, chunk_size=10, chunk_overlap=3)
    assert all(len(c) <= 10 for c in chunks)
    assert len(chunks) >= 2
    # Overlap should keep trailing tokens from the previous chunk.
    assert "three" in chunks[0] or "two" in chunks[0]
    assert any("four" in c or "five" in c for c in chunks[1:])


def test_empty_and_short_text():
    assert split_text("", chunk_size=10, chunk_overlap=2) == []
    assert split_text("short", chunk_size=100, chunk_overlap=10) == ["short"]
