# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from openjiuwen_codesearch.domain.memory import (
    EMPTY_MEMORY_TEXT,
    SnippetMemory,
    merge_intervals,
)

from tests.conftest import make_snippet


class TestMergeIntervals:
    def test_empty(self):
        assert merge_intervals([]) == []

    def test_disjoint_kept(self):
        assert merge_intervals([(1, 3), (10, 12)]) == [(1, 3), (10, 12)]

    def test_overlapping_merged(self):
        assert merge_intervals([(1, 5), (3, 8)]) == [(1, 8)]

    def test_adjacent_merged(self):
        # 间隔恰好 1 行也合并（current[0] <= last[1] + 1，旧实现行为）
        assert merge_intervals([(1, 3), (4, 6)]) == [(1, 6)]

    def test_unsorted_input(self):
        assert merge_intervals([(10, 12), (1, 3), (2, 5)]) == [(1, 5), (10, 12)]

    def test_contained_absorbed(self):
        assert merge_intervals([(1, 10), (3, 5)]) == [(1, 10)]


class TestSnippetMemory:
    def test_empty_render(self):
        assert SnippetMemory().render() == EMPTY_MEMORY_TEXT

    def test_add_and_render_format(self):
        memory = SnippetMemory()
        s = make_snippet(7, "pkg/mod.py", 10, ["def f():", "    x = 1", "    return x"],
                         name="f")
        assert memory.add_ranges(s, [(11, 12)]) is True

        rendered = memory.render()
        assert "--- CURRENT SAVED SNIPPETS (Maintained by Filter Agent) ---" in rendered
        assert "\nFile: pkg/mod.py\n" in rendered
        assert "ID: 7" in rendered
        assert "Name: f" in rendered
        # 行号映射：body[idx] 对应 start_line + idx
        assert "11:     x = 1" in rendered
        assert "12:     return x" in rendered
        assert "10: def f():" not in rendered  # 未保存的行不出现

    def test_add_ranges_merges_incrementally(self):
        memory = SnippetMemory()
        s = make_snippet(1, "a.py", 1, [f"line{i}" for i in range(1, 21)])
        memory.add_ranges(s, [(1, 3)])
        memory.add_ranges(s, [(4, 6)])  # 相邻 → 合并
        assert memory.saved[1] == [(1, 6)]

    def test_empty_ranges_cached_but_not_saved(self):
        memory = SnippetMemory()
        s = make_snippet(1, "a.py", 1, ["x"])
        assert memory.add_ranges(s, []) is False
        assert 1 in memory.cache
        assert 1 not in memory.saved

    def test_delete(self):
        memory = SnippetMemory()
        s1 = make_snippet(1, "a.py", 1, ["x"])
        s2 = make_snippet(2, "a.py", 5, ["y"])
        memory.add_ranges(s1, [(1, 1)])
        memory.add_ranges(s2, [(5, 5)])
        assert memory.delete([1, 99]) == 1
        assert memory.saved_ids() == [2]

    def test_processed_dedup(self):
        memory = SnippetMemory()
        s = make_snippet(1, "a.py", 1, ["x"])
        assert not memory.is_processed(1)
        memory.mark_processed(s)
        assert memory.is_processed(1)

    def test_render_orders_items_by_start_line(self):
        memory = SnippetMemory()
        late = make_snippet(2, "a.py", 50, ["later line"])
        early = make_snippet(1, "a.py", 5, ["early line"])
        memory.add_ranges(late, [(50, 50)])
        memory.add_ranges(early, [(5, 5)])
        rendered = memory.render()
        assert rendered.index("early line") < rendered.index("later line")
