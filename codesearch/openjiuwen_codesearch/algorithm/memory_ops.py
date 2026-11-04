# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""最终结果构造。

每个不相交保存区间独立成条；最终排序为 (file_path, start_line)，不是模型提交顺序。
"""

from openjiuwen_codesearch.domain.memory import SnippetMemory
from openjiuwen_codesearch.domain.result import FinalHit


def construct_final_hits(snippet_ids: list[int], memory: SnippetMemory) -> list[FinalHit]:
    final_results: list[FinalHit] = []
    for sid in snippet_ids:
        if sid not in memory.cache or sid not in memory.saved:
            continue
        snippet = memory.cache[sid]
        header = snippet.header_lines
        body = snippet.body_lines
        for st, en in sorted(memory.saved[sid]):
            trimmed_body = []
            for line_no in range(st, en + 1):
                idx = line_no - snippet.start_line
                if 0 <= idx < len(body):
                    trimmed_body.append(body[idx])
            final_results.append(
                FinalHit(
                    id=snippet.id,
                    file_path=snippet.file_path,
                    start_line=st,
                    end_line=en,
                    text="\n".join(header + trimmed_body),
                    kind=snippet.kind,
                    original_name=snippet.original_name,
                )
            )
    final_results.sort(key=lambda h: (h.file_path, h.start_line))
    return final_results
