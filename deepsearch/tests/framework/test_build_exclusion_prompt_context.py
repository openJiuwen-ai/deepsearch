"""test build_exclusion_prompt_context：禁引写作 prompt 上下文。

验证：有 exclude_url/titles 时注入禁引指令（silent + URLs + titles），
无约束时返回空；且**不**输出 blocked authors 段（exclude_authors 已移除，
避免 writer 误伤同作者合法论文）。
"""
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    ResearchIntent,
    build_exclusion_prompt_context,
)


def test_no_exclusion_returns_empty():
    ctx = build_exclusion_prompt_context(None)
    assert ctx["has_exclusion"] is False
    assert ctx["exclusion_instruction"] == ""


def test_exclusion_with_urls_and_titles():
    intent = ResearchIntent(
        exclude_url=["https://example.com/forbidden"],
        exclude_titles=["Forbidden Review Title"],
    )
    ctx = build_exclusion_prompt_context(intent)
    assert ctx["has_exclusion"] is True
    instr = ctx["exclusion_instruction"]
    assert "https://example.com/forbidden" in instr
    assert "Forbidden Review Title" in instr
    # silent constraint：不重述规则
    assert "silent" in instr.lower() or "do not restate" in instr.lower()


def test_exclusion_no_authors_section():
    """不输出 blocked authors 段（exclude_authors 已移除）。"""
    intent = ResearchIntent(
        exclude_url=["https://example.com/forbidden"],
        exclude_titles=["Forbidden Review Title"],
    )
    ctx = build_exclusion_prompt_context(intent)
    instr = ctx["exclusion_instruction"]
    assert "blocked authors" not in instr.lower()
    assert "forbidden source's authors" not in instr.lower()
