# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""检索协议 + 进程内 fake 实现（单测/演示用）。"""

from typing import Protocol, runtime_checkable

from openjiuwen_codesearch.domain.models import Snippet


@runtime_checkable
class CodeRetriever(Protocol):
    async def search(
        self, query: str, revision: str, topk: int, use_trigram: bool
    ) -> list[Snippet]: ...

    async def get_repo_map(self, revision: str) -> str: ...

    async def fetch_overlapping(
        self, revision: str, file_path: str, start_line: int, end_line: int
    ) -> list[Snippet]: ...

    async def has_revision(self, revision: str) -> bool: ...


class InMemoryRetriever:
    """简单倒排 fake：按词元交集打分。用于单测与无 Milvus 的演示。"""

    def __init__(self, snippets: list[Snippet], revision: str = "local") -> None:
        self._snippets = list(snippets)
        self._revision = revision

    async def search(
        self, query: str, revision: str, topk: int, use_trigram: bool
    ) -> list[Snippet]:
        if revision != self._revision:
            return []
        if use_trigram:
            needle = query.lower()
            scored = [
                (1.0, s) for s in self._snippets if needle in s.text.lower()
            ]
        else:
            terms = {t for t in query.lower().split() if t}
            scored = []
            for s in self._snippets:
                text_terms = set(s.text.lower().split())
                overlap = len(terms & text_terms)
                if overlap:
                    scored.append((float(overlap), s))
            scored.sort(key=lambda pair: -pair[0])
        return [s.model_copy(update={"score": score}) for score, s in scored[:topk]]

    async def get_repo_map(self, revision: str) -> str:
        paths = sorted({s.file_path for s in self._snippets})
        if not paths:
            return "Repository Map unavailable (no files found)."
        return "\n".join(["Repository File Map:"] + [f"- {p}" for p in paths])

    async def fetch_overlapping(
        self, revision: str, file_path: str, start_line: int, end_line: int
    ) -> list[Snippet]:
        return [
            s
            for s in self._snippets
            if s.file_path == file_path
            and s.start_line <= end_line
            and s.end_line >= start_line
        ]

    async def has_revision(self, revision: str) -> bool:
        return revision == self._revision and bool(self._snippets)
