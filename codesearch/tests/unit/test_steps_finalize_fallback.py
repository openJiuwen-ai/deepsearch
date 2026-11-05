# -*- coding: UTF-8 -*-
"""降级终止时最终命中按检索相关性挑选，而不是按写入顺序。"""

import pytest

from openjiuwen_codesearch.domain.models import Snippet
from openjiuwen_codesearch.domain.result import Termination
from openjiuwen_codesearch.framework.openjiuwen.runtime_context import CodeSearchRunContext
from openjiuwen_codesearch.framework.openjiuwen.steps import finalize


def _snippet(sid: int, path: str) -> Snippet:
    return Snippet(
        id=sid,
        file_path=path,
        start_line=1,
        end_line=1,
        text=f"File: {path} (L1-L1)\n\nline{sid}",
        kind="function_definition",
        original_name="",
    )


def _context_with_working_memory(top_k: int) -> CodeSearchRunContext:
    ctx = CodeSearchRunContext(
        config=None,
        query="q",
        revision="local",
        top_k=top_k,
        retriever=None,  # type: ignore[arg-type]
        main_llm=None,  # type: ignore[arg-type]
        filter_llm=None,  # type: ignore[arg-type]
    )
    ranking = [
        (_snippet(1, "a.py"), []),        # 最早写入，但从未被检索命中
        (_snippet(2, "b.py"), [2]),       # 命中一次，名次 2
        (_snippet(3, "c.py"), [0, 0]),    # 两次命中且都是首位 → 最相关
    ]
    for snippet, ranks in ranking:
        ctx.working_memory.mark_processed(snippet)
        ctx.working_memory.add_ranges(snippet, [(1, 1)])
        for rank in ranks:
            ctx.working_memory.record_hit(snippet, rank)
    return ctx


@pytest.mark.parametrize(
    "termination",
    [Termination.MAX_TURNS, Termination.STAGNATED, Termination.LLM_ERROR],
)
def test_fallback_returns_most_relevant_snippets(termination):
    ctx = _context_with_working_memory(top_k=1)

    result = finalize(ctx, termination)

    assert result.termination == termination
    assert [hit.id for hit in result.hits] == [3]
