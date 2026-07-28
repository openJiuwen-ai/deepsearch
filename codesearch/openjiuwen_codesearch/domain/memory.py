# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Snippet 记忆：过滤后区间的存取、合并与提示词渲染。

行为与 jiuwenCoder `agent.py` 的 `merge_intervals` / `_format_memory` 保持一致
（渲染文本逐字对齐，属 parity 契约，改动需登记行为变更）。
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


class SnippetMemory(BaseModel):
    """检索缓存 + 已保存行区间 + 已处理去重集。"""

    cache: dict[int, Snippet] = Field(default_factory=dict)
    saved: dict[int, list[tuple[int, int]]] = Field(default_factory=dict)
    processed_ids: set[int] = Field(default_factory=set)

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
                deleted += 1
        return deleted

    def saved_ids(self) -> list[int]:
        return list(self.saved.keys())

    def render(self) -> str:
        """渲染为注入提示词的记忆文本（与旧 `_format_memory` 输出一致）。"""
        if not self.saved:
            return EMPTY_MEMORY_TEXT

        files_map: dict[str, list[tuple[int, list[tuple[int, int]], Snippet]]] = {}
        for sid, ranges in self.saved.items():
            if not ranges:
                continue
            snippet = self.cache[sid]
            files_map.setdefault(snippet.file_path, []).append((sid, ranges, snippet))

        memory_str = MEMORY_HEADER
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
