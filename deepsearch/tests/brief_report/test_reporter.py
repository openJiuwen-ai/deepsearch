"""Brief 顶部摘要及拼装测试。"""
from unittest.mock import AsyncMock
import pytest
from openjiuwen_deepsearch.algorithm.brief_report.models import BriefSummaryRequest, BriefChapter, BriefCitationRecord, BriefAssemblyRequest
from openjiuwen_deepsearch.algorithm.brief_report.writer import _summary_prompt_input, generate_brief_summary, assemble_brief_report
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt

def _chapters(): return [BriefChapter(section_id="1",raw_markdown="## 第一章\n\n第一章首段。[citation:1]"),BriefChapter(section_id="2",raw_markdown="## 第二章\n\n第二章首段。[citation:1]")]
def _registry(): return [BriefCitationRecord(source_id="s",index=1,title="来源",url="https://e/a",original_content="x")]


def test_brief_summary_uses_main_content_contract_without_user_request_tag():
    """核心摘要应沿用专业版的 Main Content 输入形式，不新造用户请求标签。"""
    request = BriefSummaryRequest(
        llm=object(), title="报告", language="zh-CN", user_format="要点列表",
        chapters=_chapters(), section_evidence={}, citation_registry=_registry(),
    )

    rendered = apply_system_prompt(
        "brief_reporter",
        _summary_prompt_input(request, [{"section_id": "1", "markdown": "## 第一章\n\n结论。[citation:1]"}], []),
    )

    assert "<user_request>" not in rendered[0]["content"]
    assert "<chapters>" not in rendered[0]["content"]
    assert "Main Content:" in rendered[1]["content"]
    assert "## 第一章" in rendered[1]["content"]
@pytest.mark.asyncio
async def test_summary_includes_coverage_gaps_without_preemptive_trimming(monkeypatch):
    """摘要调用前应保留完整章节，并向模型暴露未覆盖步骤。"""
    captured = {}
    def prompt(_name, data):
        captured.update(data)
        return [{"role": "system", "content": "固定模板：" + __import__("json").dumps(data, ensure_ascii=False)}]
    invoke=AsyncMock(return_value={"content":"<executive_summary>- 核心。[citation:1]</executive_summary>"})
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.apply_system_prompt", prompt)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats",invoke)
    request = BriefSummaryRequest(
        llm=object(), title="x", language="zh-CN",
        chapters=[BriefChapter(section_id="1", raw_markdown="## 第一章\n\n短结论。[citation:1]\n\n" + "证据。" * 300)],
        section_evidence={"1": {"coverage": [{"step_id": "1-1", "status": "missing", "reason": "缺少数据"}]}},
        citation_registry=_registry(),
    )

    await generate_brief_summary(request)

    assert "证据。" * 300 in captured["messages"][0]["content"]
    assert "Known evidence limitations:" in captured["messages"][0]["content"]
    assert "缺少数据" in captured["messages"][0]["content"]


@pytest.mark.asyncio
async def test_summary_citations_are_limited_to_context_after_runtime_compaction(monkeypatch):
    """模型真实超限并触发压缩后，摘要不得引用已裁掉的来源。"""
    captured = {}
    def prompt(_name, data):
        captured.update(data)
        return [{"role": "system", "content": __import__("json").dumps(data, ensure_ascii=False)}]
    invoke = AsyncMock(side_effect=[
        RuntimeError("context_length_exceeded"),
        RuntimeError("context_length_exceeded"),
        {"content": "<executive_summary>- 核心。[citation:2]</executive_summary>"},
    ])
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.apply_system_prompt", prompt)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke)
    registry = _registry() + [BriefCitationRecord(source_id="s2", index=2, title="来源二", url="https://e/b", original_content="y")]
    request = BriefSummaryRequest(
        llm=object(), title="x", language="zh-CN",
        chapters=[BriefChapter(section_id="1", raw_markdown="## 第一章\n\n首段。[citation:1]\n\n" + "冗长。" * 400 + "[citation:2]")],
        section_evidence={}, citation_registry=registry,
    )

    summary = await generate_brief_summary(request)

    assert "allowed_citation_ids" not in captured
    assert summary == "- 核心。"


@pytest.mark.asyncio
async def test_summary_receives_user_format_and_citation_bearing_excerpt_after_runtime_limit(monkeypatch):
    """真实超限后的摘要压缩必须透传格式，并保留带引用事实句。"""
    captured = {}
    def prompt(_name, data):
        captured.update(data)
        return [{"role": "system", "content": __import__("json").dumps(data, ensure_ascii=False)}]
    invoke = AsyncMock(side_effect=[
        RuntimeError("context_length_exceeded"),
        {"content": "<executive_summary>- 核心。[citation:1]</executive_summary>"},
    ])
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.apply_system_prompt", prompt)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke)
    request = BriefSummaryRequest(
        llm=object(), title="x", language="en-US", user_format="Use bullets",
        chapters=[BriefChapter(section_id="1", raw_markdown="## Heading\n\nFirst paragraph.[citation:1]\n\n" + "Filler. " * 300 + "Fact retained.[citation:1]")],
        section_evidence={}, citation_registry=_registry(),
    )

    await generate_brief_summary(request)

    assert captured["user_format"] == "Use bullets"
    assert "## Heading" in captured["messages"][0]["content"]
    assert "Fact retained.[citation:1]" in captured["messages"][0]["content"]
def test_assembly_orders_summary_then_sections_then_references():
    result=assemble_brief_report(BriefAssemblyRequest(title="测试",language="zh-CN",executive_summary="- 核心。[citation:1] 未注册。[citation:9]",chapters=_chapters(),citation_registry=_registry(),section_order={"1":0,"2":1}))
    assert result.report_content.startswith("# 测试\n\n## 核心摘要") and result.report_content.endswith("## 参考文章")
    assert result.report_content.count("[来源](https://e/a)") == 3
    assert "[citation:9]" not in result.report_content
