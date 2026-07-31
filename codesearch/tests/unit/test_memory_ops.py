# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from openjiuwen_codesearch.algorithm.memory_ops import construct_final_hits
from openjiuwen_codesearch.domain.memory import SnippetMemory

from tests.conftest import make_snippet


def _memory_with(*entries):
    memory = SnippetMemory()
    for snippet, ranges in entries:
        memory.add_ranges(snippet, ranges)
    return memory


def test_each_disjoint_span_becomes_separate_hit():
    s = make_snippet(1, "a.py", 10, [f"l{i}" for i in range(10, 20)])
    memory = _memory_with((s, [(10, 11), (15, 16)]))
    hits = construct_final_hits([1], memory)
    assert [(h.start_line, h.end_line) for h in hits] == [(10, 11), (15, 16)]


def test_final_sort_is_path_then_line_not_submission_order():
    # 行为契约（Phase 0 固化）：最终排序按 (file_path, start_line)
    sa = make_snippet(1, "b.py", 5, ["b5"])
    sb = make_snippet(2, "a.py", 50, ["a50"])
    memory = _memory_with((sa, [(5, 5)]), (sb, [(50, 50)]))
    hits = construct_final_hits([1, 2], memory)
    assert [h.file_path for h in hits] == ["a.py", "b.py"]


def test_text_keeps_header_and_trims_body():
    s = make_snippet(1, "a.py", 10, ["line10", "line11", "line12"])
    memory = _memory_with((s, [(11, 11)]))
    hit = construct_final_hits([1], memory)[0]
    assert hit.text.startswith("File: a.py")
    assert "line11" in hit.text
    assert "line10" not in hit.text


def test_out_of_bounds_lines_clipped():
    s = make_snippet(1, "a.py", 10, ["line10", "line11"])
    memory = _memory_with((s, [(9, 30)]))
    hit = construct_final_hits([1], memory)[0]
    assert "line10" in hit.text and "line11" in hit.text


def test_unknown_and_unsaved_ids_skipped():
    s = make_snippet(1, "a.py", 1, ["x"])
    memory = SnippetMemory()
    memory.mark_processed(s)  # cached 但未 saved
    assert construct_final_hits([1, 42], memory) == []
