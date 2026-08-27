# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Snippet 记忆：过滤后区间的存取、合并与提示词渲染。

渲染文本格式属行为契约（由单元测试锁定），修改会影响模型看到的记忆内容。
"""

from pydantic import BaseModel, Field

from openjiuwen_codesearch.domain.models import Snippet

EMPTY_MEMORY_TEXT = "--- CURRENT SAVED SNIPPETS ---\nNo snippets saved yet.\n"
MEMORY_HEADER = "--- CURRENT SAVED SNIPPETS (Maintained by Filter Agent) ---\n"


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并重叠或相邻（间隔 1 行）的闭区间。与旧实现逐行为一致。"""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = [intervals[0]]
    for current in intervals[1:]:
        last = merged[-1]
        if current[0] <= last[1] + 1:
            merged[-1] = (last[0], max(last[1], current[1]))
        else:
            merged.append(current)
    return merged


class SnippetRelevance(BaseModel):
    """片段的检索相关性证据，用于降级路径的排序。

    智能体主动提交时用它自己挑选的片段；但轮次耗尽/停滞等降级路径没有智能体
    的判断，此前按"写入顺序"取前 N 个——顺序与相关性无关，导致降级结果多而不准。
    这里累积客观信号：被多少次检索命中、达到过的最好名次、最高 BM25 分。
    """

    hit_count: int = 0
    best_rank: int = 10**6   # 越小越好（0 = 某次检索的首位）
    best_score: float = 0.0  # BM25 分，越大越好


class SnippetMemory(BaseModel):
    """检索缓存 + 已保存行区间 + 已处理去重集 + 相关性证据。"""

    cache: dict[int, Snippet] = Field(default_factory=dict)
    saved: dict[int, list[tuple[int, int]]] = Field(default_factory=dict)
    processed_ids: set[int] = Field(default_factory=set)
    relevance: dict[int, SnippetRelevance] = Field(default_factory=dict)

    def record_hit(self, snippet: Snippet, rank: int) -> None:
        """记录一次检索命中（rank 为该次检索结果中的 0-based 名次）。

        对同一片段可多次调用：被不同查询反复命中是较强的相关性信号。
        """
        rel = self.relevance.setdefault(snippet.id, SnippetRelevance())
        rel.hit_count += 1
        rel.best_rank = min(rel.best_rank, rank)
        if snippet.score is not None:
            rel.best_score = max(rel.best_score, float(snippet.score))

    def mark_processed(self, snippet: Snippet) -> None:
        self.processed_ids.add(snippet.id)
        self.cache[snippet.id] = snippet

    def is_processed(self, snippet_id: int) -> bool:
        return snippet_id in self.processed_ids

    def add_ranges(self, snippet: Snippet, ranges: list[tuple[int, int]]) -> bool:
        """记录 snippet 及其相关行区间；返回是否有新增内容。"""
        self.cache[snippet.id] = snippet
        if not ranges:
            return False
        existing = self.saved.get(snippet.id, [])
        self.saved[snippet.id] = merge_intervals(existing + list(ranges))
        return True

    def delete(self, snippet_ids: list[int]) -> int:
        deleted = 0
        for sid in snippet_ids:
            if sid in self.saved:
                del self.saved[sid]
                self.relevance.pop(sid, None)  # 一并清理，避免长会话残留
                deleted += 1
        return deleted

    def saved_ids(self) -> list[int]:
        """按写入顺序返回（保留原语义，供需要稳定顺序的场景使用）。"""
        return list(self.saved.keys())

    def ranked_saved_ids(self) -> list[int]:
        """按检索相关性降序返回已保存片段 ID——降级路径的兜底选择依据。

        排序键（依次）：命中次数多 → 最好名次靠前 → BM25 分高 → 写入顺序。
        检索命中与 `expand_context`（智能体显式点名，按最优名次计）都会留下
        相关性记录；无记录的片段排在末尾但不丢弃。
        """
        order = {sid: i for i, sid in enumerate(self.saved)}

        def key(sid: int):
            rel = self.relevance.get(sid)
            if rel is None:
                return (0, 10**6, 0.0, order[sid])
            return (-rel.hit_count, rel.best_rank, -rel.best_score, order[sid])

        return sorted(self.saved.keys(), key=key)

    def render(self, title: str = "CURRENT SAVED SNIPPETS") -> str:
        """渲染为注入提示词的记忆文本。"""
        if not self.saved:
            return f"--- {title} ---\nNo snippets saved yet.\n"

        files_map: dict[str, list[tuple[int, list[tuple[int, int]], Snippet]]] = {}
        for sid, ranges in self.saved.items():
            if not ranges:
                continue
            snippet = self.cache[sid]
            files_map.setdefault(snippet.file_path, []).append((sid, ranges, snippet))

        memory_str = f"--- {title} ---\n"
        for fp, items in files_map.items():
            memory_str += f"\nFile: {fp}\n"
            items.sort(key=lambda x: x[2].start_line)
            for sid, ranges, snippet in items:
                body = snippet.body_lines
                trimmed_body = []
                for st, en in ranges:
                    for line_no in range(st, en + 1):
                        idx = line_no - snippet.start_line
                        if 0 <= idx < len(body):
                            trimmed_body.append(f"{line_no}: {body[idx]}")
                name_str = f"Name: {snippet.original_name}\n" if snippet.original_name else ""
                memory_str += f"---\nID: {sid}\n{name_str}Code:\n" + "\n".join(trimmed_body) + "\n"
        return memory_str + "---\n"
