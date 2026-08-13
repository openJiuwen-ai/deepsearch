"""Brief 并行单调用章节写作测试。"""
import asyncio
import logging
from unittest.mock import AsyncMock
import pytest
from openjiuwen_deepsearch.algorithm.brief_report.models import BriefWritingRequest, BriefOutline, BriefCollectionResult, BriefSummaryRequest, BriefChapter, BriefSectionWritingGuidance, BriefWritingGuidance
from openjiuwen_deepsearch.algorithm.brief_report.writer import _summary_prompt_input, _writing_prompt_input, build_writing_evidence, generate_brief_summary, write_brief_chapters
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt

def _request():
    outline=BriefOutline.model_validate({"title":"x","sections":[{"id":str(i),"title":f"章节 {i}","goal":"目标","research_steps":[{"id":f"{i}-1","requirement":"指标"},{"id":f"{i}-2","requirement":"差异"}]} for i in range(1,4)]})
    collection=BriefCollectionResult.model_validate({"citation_registry":[{"source_id":f"s{i}","index":i,"title":"来源","url":f"https://e/{i}","original_content":"证据"} for i in range(1,4)],"section_evidence":{str(i):{"selected_docs":[{"source_id":f"s{i}","step_ids":[f"{i}-1",f"{i}-2"],"evaluation_rank":1}],"coverage":[{"step_id":f"{i}-1","status":"covered","reason":"d"},{"step_id":f"{i}-2","status":"covered","reason":"d"}]} for i in range(1,4)}})
    return BriefWritingRequest(llm=object(),outline=outline,collection=collection)


def test_brief_sub_reporter_keeps_research_steps_internal_and_generates_reader_facing_subheadings():
    """研究步骤只能约束取证，二级标题必须由现有写作调用生成。"""
    request = _request().model_copy(
        update={"audience_role": "业务负责人", "tone": "直接、审慎", "user_format": "用表格比较关键差异"}
    )
    section = request.outline.sections[0]
    rendered = apply_system_prompt(
        "brief_sub_reporter",
        _writing_prompt_input(
            request,
            section,
            [{"index": 1, "title": "来源", "url": "https://e/1", "snippet": "证据", "step_ids": ["1-1"]}],
        ),
    )
    prompt = rendered[0]["content"]
    normalized_prompt = " ".join(prompt.split())
    collected_information = rendered[1]["content"]

    assert "concise sub report writer" in prompt
    assert "<overall_outline>" in prompt
    assert "<current_section>" in prompt
    assert "<current_chapter_outline>" in prompt
    assert "1 章节 1" in prompt
    assert "1.1 指标" not in prompt
    assert "Generate 2–4 concise reader-facing Level 2 headings" in normalized_prompt
    assert "Never use a research requirement as a heading" in normalized_prompt
    assert "Collected Information" in collected_information
    assert "[citation:1 begin]" in collected_information
    assert "[citation:1 end]" in collected_information
    assert "Target chapter length" in prompt
    assert "Every number, date, amount, percentage, ranking, company name, policy name, and table cell" in prompt
    assert "If the user requested a table" in prompt
    assert "Do not replace a required table with prose" in prompt
    assert "Conclusion sentence first" in prompt
    assert "Mathematical Formula Syntax" in prompt
    assert "Do NOT output Mermaid syntax, chart source, chart code" in prompt
    assert "<allowed_citations>" not in prompt
    assert "<user_request>" not in prompt
    assert "<section_contract>" not in prompt
    assert "<other_sections>" not in prompt
    assert "<coverage_guidance>" not in prompt
    assert "<collected_documents>" not in prompt
    assert "章节 1" in prompt
    assert "业务负责人" in prompt


def test_chapter_prompt_receives_report_and_matching_section_guidance():
    """章节仅接收报告总策略与本章对应的内部编辑指引。"""
    request = _request().model_copy(
        update={
            "writing_guidance": BriefWritingGuidance(
                report_strategy="先比较月度趋势",
                section_guidance=[
                    BriefSectionWritingGuidance(section_id="1", guidance="先给出趋势结论"),
                    BriefSectionWritingGuidance(section_id="2", guidance="不应进入本章"),
                ],
            )
        }
    )

    prompt = _writing_prompt_input(request, request.outline.sections[0], [])

    assert "报告主线：先比较月度趋势" in prompt["messages"][1]["content"]
    assert "本章指引：先给出趋势结论" in prompt["messages"][1]["content"]
    assert "不应进入本章" not in prompt["messages"][1]["content"]


def test_summary_prompt_receives_report_strategy_but_not_section_guidance():
    """核心摘要只应接收整体编辑策略，不能混入分章指引。"""
    request = BriefSummaryRequest(
        llm=object(), title="报告", language="zh-CN", chapters=[], section_evidence={},
        citation_registry=[],
        writing_guidance=BriefWritingGuidance(
            report_strategy="先比较月度趋势",
            section_guidance=[BriefSectionWritingGuidance(section_id="1", guidance="先给出趋势结论")],
        ),
    )

    prompt = _summary_prompt_input(request, [], [])

    assert "报告主线：先比较月度趋势" in prompt["messages"][0]["content"]
    assert "本章指引" not in prompt["messages"][0]["content"]


def test_brief_chapter_prompt_does_not_describe_context_that_is_not_provided():
    """Brief 写作只能声明实际传入的证据与编辑指引上下文。"""
    rendered = apply_system_prompt(
        "brief_sub_reporter",
        _writing_prompt_input(_request(), _request().outline.sections[0], []),
    )
    prompt = "\n".join(message["content"] for message in rendered)

    assert "Background Knowledge" not in prompt
    assert "structured evidence guidance" not in prompt.lower()
    assert "Chapter Writing Directive" not in prompt


def test_brief_summary_prompt_requires_the_requested_output_language():
    """摘要模板必须把请求语言作为明确的输出约束。"""
    rendered = apply_system_prompt(
        "brief_reporter",
        _summary_prompt_input(
            BriefSummaryRequest(
                llm=object(), title="报告", language="en-US", chapters=[],
                section_evidence={}, citation_registry=[],
            ),
            [],
            [],
        ),
    )

    assert "Output language must be **en-US**" in rendered[0]["content"]

@pytest.mark.asyncio
async def test_writes_all_chapters_in_parallel_once(monkeypatch, caplog):
    entered=0; release=asyncio.Event()
    async def invoke(*args, **kwargs):
        nonlocal entered
        entered += 1
        if entered==3: release.set()
        await release.wait()
        return {"content":"正文。[citation:1]"}
    mock=AsyncMock(side_effect=invoke)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats",mock)
    with caplog.at_level(logging.INFO, logger="openjiuwen_deepsearch.algorithm.brief_report.writer"):
        chapters=await write_brief_chapters(_request())
    assert [x.section_id for x in chapters]==["1","2","3"] and mock.await_count==3
    assert any(
        "[BriefWriter] Generated chapter output section_id=1 raw_markdown=" in record.getMessage()
        and "正文。[citation:1]" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_parallel_chapters_stream_tokens_with_section_identity(monkeypatch):
    """并行章节流必须传递可稳定归属的章节 ID 和显示顺序。"""
    invoke = AsyncMock(return_value={"content": "正文。[citation:1]"})
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke)

    await write_brief_chapters(_request())

    assert invoke.await_count == 3
    assert [call.kwargs["need_stream_out"] for call in invoke.await_args_list] == [True, True, True]
    assert [call.kwargs["stream_meta"] for call in invoke.await_args_list] == [
        {"section_id": "1", "section_idx": "1"},
        {"section_id": "2", "section_idx": "2"},
        {"section_id": "3", "section_idx": "3"},
    ]

def test_evidence_is_not_removed_before_model_reports_context_limit():
    """长格式约束不能触发调用前证据裁剪。"""
    request = _request().model_copy(update={"user_format": "用户约束" * 200})

    evidence = build_writing_evidence(request, request.outline.sections[0])

    assert [item["index"] for item in evidence.documents] == [1]
@pytest.mark.asyncio
async def test_chapter_context_limit_retries_with_lower_priority_evidence_removed(monkeypatch):
    """章节写作超限时，下一次请求必须移除最低优先级证据。"""
    request = _request()
    payload = request.collection.model_dump()
    payload["citation_registry"].append({"source_id": "s4", "index": 4, "title": "次要来源", "url": "https://e/4", "original_content": "次要证据"})
    payload["section_evidence"]["1"]["selected_docs"].append({"source_id": "s4", "step_ids": ["1-2"], "evaluation_rank": 2})
    request = request.model_copy(update={
        "outline": request.outline.model_copy(update={"sections": [request.outline.sections[0]]}),
        "collection": BriefCollectionResult.model_validate(payload),
    })
    document_counts = []

    async def invoke(_llm, messages, **_kwargs):
        document_counts.append(messages["messages"][0]["content"].count(" begin]"))
        if document_counts[-1] > 1:
            raise RuntimeError("context_length_exceeded")
        return {"content": "正文。[citation:1]"}

    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.apply_system_prompt", lambda _name, payload: payload)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke)

    chapters = await write_brief_chapters(request)

    assert document_counts == [2, 1]
    assert chapters[0].raw_markdown.endswith("正文。[citation:1]")


@pytest.mark.asyncio
async def test_chapter_writer_retries_when_model_outputs_mermaid(monkeypatch):
    """若写作模型仍输出 Mermaid，章节不得把图表源码带入报告。"""
    request = _request().model_copy(
        update={"outline": _request().outline.model_copy(update={"sections": [_request().outline.sections[0]]})}
    )
    responses = [
        {"content": "正文。[citation:1]\n\n```mermaid\ngraph TD\nA --> B\n```"},
        {"content": "修正后的正文。[citation:1]"},
    ]
    invoke = AsyncMock(side_effect=responses)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke)

    chapters = await write_brief_chapters(request)

    assert invoke.await_count == 2
    assert "mermaid" not in chapters[0].raw_markdown.lower()
    assert chapters[0].raw_markdown.endswith("修正后的正文。[citation:1]")


@pytest.mark.asyncio
async def test_single_long_evidence_is_omitted_after_context_limit_retries_are_exhausted(monkeypatch):
    """唯一证据连续超限时，失败章节不应被伪造成报告正文。"""
    request = _request()
    payload = request.collection.model_dump()
    payload["citation_registry"][0]["original_content"] = "超长证据" * 64
    request = request.model_copy(update={
        "outline": request.outline.model_copy(update={"sections": [request.outline.sections[0]]}),
        "collection": BriefCollectionResult.model_validate(payload),
    })
    snippets = []

    async def invoke(_llm, messages, **_kwargs):
        snippets.append(messages["messages"][0]["content"])
        raise RuntimeError("context_length_exceeded")

    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.apply_system_prompt", lambda _name, payload: payload)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke)

    chapters = await write_brief_chapters(request)

    assert len(snippets) == 3
    assert len(snippets[0]) > len(snippets[1]) > len(snippets[2])
    assert chapters == []


@pytest.mark.asyncio
async def test_context_limited_chapter_does_not_block_other_parallel_chapters(monkeypatch):
    """单个章节失败后，其他并行章节仍应正常写作并返回。"""
    request = _request()
    payload = request.collection.model_dump()
    payload["citation_registry"][0]["original_content"] = "超长证据" * 64
    request = request.model_copy(update={"collection": BriefCollectionResult.model_validate(payload)})
    attempts_by_section = {"1": 0, "2": 0, "3": 0}

    async def invoke(_llm, messages, **_kwargs):
        section_id = messages["current_section"].rsplit(" ", 1)[-1]
        attempts_by_section[section_id] += 1
        if section_id == "1":
            raise RuntimeError("context_length_exceeded")
        return {"content": f"章节 {section_id} 正文。[citation:{section_id}]"}

    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.apply_system_prompt", lambda _name, payload: payload)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke)

    chapters = await write_brief_chapters(request)

    assert attempts_by_section == {"1": 3, "2": 1, "3": 1}
    assert [chapter.section_id for chapter in chapters] == ["2", "3"]
    assert chapters[0].raw_markdown.endswith("章节 2 正文。[citation:2]")
    assert chapters[1].raw_markdown.endswith("章节 3 正文。[citation:3]")


@pytest.mark.asyncio
async def test_chapter_batch_propagates_workflow_cancellation(monkeypatch):
    """工作流取消不能被当作单章节失败吞掉。"""
    request = _request().model_copy(
        update={"outline": _request().outline.model_copy(update={"sections": [_request().outline.sections[0]]})}
    )
    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        await write_brief_chapters(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "validation"),
    [
        ("", "empty_content"),
        ("```python\nprint('x')", "unclosed_code_fence"),
        ("```mermaid\ngraph TD\nA --> B\n```", "mermaid_output_forbidden"),
    ],
)
async def test_chapter_writer_logs_response_validation_failure(monkeypatch, caplog, content, validation):
    """遗漏模型响应校验原因或响应摘要时，章节失败将无法排查。"""
    request = _request().model_copy(
        update={"outline": _request().outline.model_copy(update={"sections": [_request().outline.sections[0]]})}
    )

    monkeypatch.setattr(
        "openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats",
        AsyncMock(return_value={"content": content}),
    )

    with caplog.at_level(logging.WARNING, logger="openjiuwen_deepsearch.algorithm.brief_report.writer"):
        chapters = await write_brief_chapters(request)

    assert chapters == []
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "section_id=1" in message
        and f"validation={validation}" in message
        and "content_chars=" in message
        for message in messages
    )


@pytest.mark.asyncio
async def test_summary_context_limit_retries_with_compacted_chapters(monkeypatch):
    """摘要超限时，下一次请求必须改用标题、首段与带引用事实句。"""
    request = _request()
    summary_request = BriefSummaryRequest(
        llm=object(), title="报告", language="zh-CN",
        chapters=[BriefChapter(section_id="1", raw_markdown="## 章节\n\n首段结论。\n\n冗余背景。\n\n关键事实。[citation:1]")],
        section_evidence={"1": request.collection.section_evidence["1"]},
        citation_registry=[request.collection.citation_registry[0]],
    )
    chapter_markdowns = []

    async def invoke(_llm, messages, **_kwargs):
        chapter_markdowns.append(messages["messages"][0]["content"])
        if len(chapter_markdowns) == 1:
            raise RuntimeError("context_length_exceeded")
        return {"content": "<executive_summary>已压缩。[citation:1]</executive_summary>"}

    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.apply_system_prompt", lambda _name, payload: payload)
    monkeypatch.setattr("openjiuwen_deepsearch.algorithm.brief_report.writer.ainvoke_llm_with_stats", invoke)

    summary = await generate_brief_summary(summary_request)

    assert summary == "已压缩。[citation:1]"
    assert chapter_markdowns[0] != chapter_markdowns[1]
    assert "冗余背景" not in chapter_markdowns[1]
