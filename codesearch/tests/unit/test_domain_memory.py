# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from openjiuwen_codesearch.domain.memory import (
    EMPTY_MEMORY_TEXT,
    SnippetMemory,
    merge_intervals,
)

from tests.conftest import make_snippet


class TestMergeIntervals:
    @staticmethod
    def test_empty():
        assert merge_intervals([]) == []

    @staticmethod
    def test_disjoint_kept():
        assert merge_intervals([(1, 3), (10, 12)]) == [(1, 3), (10, 12)]

    @staticmethod
    def test_overlapping_merged():
        assert merge_intervals([(1, 5), (3, 8)]) == [(1, 8)]

    @staticmethod
    def test_adjacent_merged():
        # 间隔恰好 1 行也合并（current[0] <= last[1] + 1，旧实现行为）
        assert merge_intervals([(1, 3), (4, 6)]) == [(1, 6)]

    @staticmethod
    def test_unsorted_input():
        assert merge_intervals([(10, 12), (1, 3), (2, 5)]) == [(1, 5), (10, 12)]

    @staticmethod
    def test_contained_absorbed():
        assert merge_intervals([(1, 10), (3, 5)]) == [(1, 10)]


class TestSnippetMemory:
    @staticmethod
    def test_empty_render():
        assert SnippetMemory().render() == EMPTY_MEMORY_TEXT

    @staticmethod
    def test_add_and_render_format():
        memory = SnippetMemory()
        s = make_snippet(7, "pkg/mod.py", 10, ["def f():", "    x = 1", "    return x"],
                         name="f")
        assert memory.add_ranges(s, [(11, 12)]) is True

        rendered = memory.render()
        assert "--- CURRENT SAVED SNIPPETS ---" in rendered
        assert "\nFile: pkg/mod.py\n" in rendered
        assert "ID: 7" in rendered
        assert "Name: f" in rendered
        # 行号映射：body[idx] 对应 start_line + idx
        assert "11:     x = 1" in rendered
        assert "12:     return x" in rendered
        assert "10: def f():" not in rendered  # 未保存的行不出现

    @staticmethod
    def test_add_ranges_merges_incrementally():
        memory = SnippetMemory()
        s = make_snippet(1, "a.py", 1, [f"line{i}" for i in range(1, 21)])
        memory.add_ranges(s, [(1, 3)])
        memory.add_ranges(s, [(4, 6)])  # 相邻 → 合并
        assert memory.saved[1] == [(1, 6)]

    @staticmethod
    def test_empty_ranges_cached_but_not_saved():
        memory = SnippetMemory()
        s = make_snippet(1, "a.py", 1, ["x"])
        assert memory.add_ranges(s, []) is False
        assert 1 in memory.cache
        assert 1 not in memory.saved

    @staticmethod
    def test_delete():
        memory = SnippetMemory()
        s1 = make_snippet(1, "a.py", 1, ["x"])
        s2 = make_snippet(2, "a.py", 5, ["y"])
        memory.add_ranges(s1, [(1, 1)])
        memory.add_ranges(s2, [(5, 5)])
        assert memory.delete([1, 99]) == 1
        assert memory.saved_ids() == [2]

    @staticmethod
    def test_processed_dedup():
        memory = SnippetMemory()
        s = make_snippet(1, "a.py", 1, ["x"])
        assert not memory.is_processed(1)
        memory.mark_processed(s)
        assert memory.is_processed(1)

    @staticmethod
    def test_render_orders_items_by_start_line():
        memory = SnippetMemory()
        late = make_snippet(2, "a.py", 50, ["later line"])
        early = make_snippet(1, "a.py", 5, ["early line"])
        memory.add_ranges(late, [(50, 50)])
        memory.add_ranges(early, [(5, 5)])
        rendered = memory.render()
        assert rendered.index("early line") < rendered.index("later line")


class TestRelevanceRanking:
    """降级路径的兜底排序：按检索相关性而非写入顺序。"""

    @staticmethod
    def _mem_with_hits():
        memory = SnippetMemory()
        # 写入顺序：a(第1) b(第2) c(第3)；相关性顺序应为 c > b > a
        a = make_snippet(1, "a.py", 1, ["x"])
        b = make_snippet(2, "b.py", 1, ["y"])
        c = make_snippet(3, "c.py", 1, ["z"])
        a.score, b.score, c.score = 1.0, 5.0, 9.0
        memory.record_hit(a, rank=9)              # 命中 1 次，名次靠后
        memory.record_hit(b, rank=3)              # 命中 2 次
        memory.record_hit(b, rank=5)
        memory.record_hit(c, rank=0)              # 命中 2 次且拿过第一
        memory.record_hit(c, rank=1)
        for s in (a, b, c):
            memory.add_ranges(s, [(1, 1)])
        return memory

    def test_ranked_by_hit_count_then_best_rank(self):
        memory = self._mem_with_hits()
        assert memory.saved_ids() == [1, 2, 3]           # 写入顺序不变
        assert memory.ranked_saved_ids() == [3, 2, 1]    # 相关性顺序

    @staticmethod
    def test_repeated_hits_outrank_single_hit():
        memory = SnippetMemory()
        once = make_snippet(1, "a.py", 1, ["x"])
        twice = make_snippet(2, "b.py", 1, ["y"])
        memory.record_hit(once, rank=0)      # 名次更好但只命中一次
        memory.record_hit(twice, rank=8)
        memory.record_hit(twice, rank=8)     # 被两次不同检索命中
        memory.add_ranges(once, [(1, 1)])
        memory.add_ranges(twice, [(1, 1)])
        assert memory.ranked_saved_ids()[0] == 2

    @staticmethod
    def test_snippets_without_relevance_kept_at_end():
        memory = SnippetMemory()
        ranked = make_snippet(1, "a.py", 1, ["x"])
        orphan = make_snippet(2, "b.py", 1, ["y"])   # 无 record_hit
        memory.record_hit(ranked, rank=0)
        memory.add_ranges(ranked, [(1, 1)])
        memory.add_ranges(orphan, [(1, 1)])
        assert memory.ranked_saved_ids() == [1, 2]   # 不丢弃，排末尾

    @staticmethod
    def test_ranking_ignores_unsaved_snippets():
        memory = SnippetMemory()
        s = make_snippet(1, "a.py", 1, ["x"])
        memory.record_hit(s, rank=0)
        memory.mark_processed(s)          # 命中但过滤未保留任何行
        assert memory.ranked_saved_ids() == []
