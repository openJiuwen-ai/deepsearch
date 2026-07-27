import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace import (
    ChangeKind,
    LocalTraceResult,
    append_reference_entries,
    apply_local_source_trace_to_action_result,
    apply_global_citation_numbering,
    build_diff_segments,
    collect_existing_citation_candidates,
    convert_doc_infos_to_search_records,
    extract_reference_map,
    run_local_source_trace,
)
from openjiuwen_deepsearch.algorithm.user_feedback_processor.report_edit_utils import (
    RemovedCitation,
    strip_markup_in_range_with_metadata,
)


def test_build_diff_segments_preserves_equal_markup_and_marks_changed_text():
    raw = "第一句[checked_citation:1][[1]](https://a.com)。第二句。"
    strip_result = strip_markup_in_range_with_metadata(raw, 0, len(raw))
    rewritten = "第一句。第二句新增。"

    segments = build_diff_segments(
        raw_text=raw,
        original_text_clean=strip_result.text,
        rewritten_text=rewritten,
        clean_boundary_to_raw_boundary=strip_result.clean_boundary_to_raw_boundary,
    )

    assert segments[0].kind == ChangeKind.EQUAL
    assert segments[0].text == "第一句[checked_citation:1][[1]](https://a.com)。"
    assert segments[1].kind == ChangeKind.REPLACE
    assert segments[1].text == "第二句新增。"
    assert segments[1].original_clean_start >= len("第一句。")


def test_build_diff_segments_treats_insert_as_changed_text():
    raw = "第一句。"
    strip_result = strip_markup_in_range_with_metadata(raw, 0, len(raw))

    segments = build_diff_segments(
        raw_text=raw,
        original_text_clean=strip_result.text,
        rewritten_text="第一句。新增句。",
        clean_boundary_to_raw_boundary=strip_result.clean_boundary_to_raw_boundary,
    )

    assert [segment.kind for segment in segments] == [ChangeKind.EQUAL, ChangeKind.INSERT]
    assert segments[1].text == "新增句。"


def test_build_diff_segments_keeps_pure_format_changes_from_old_text():
    raw = "A\n\nB"
    strip_result = strip_markup_in_range_with_metadata(raw, 0, len(raw))

    segments = build_diff_segments(
        raw_text=raw,
        original_text_clean=strip_result.text,
        rewritten_text="A\nB",
        clean_boundary_to_raw_boundary=strip_result.clean_boundary_to_raw_boundary,
    )

    assert "".join(segment.text for segment in segments) == "A\nB"


@pytest.mark.asyncio
async def test_apply_local_source_trace_preserves_internal_whitespace_rewrite():
    final_result = {
        "response_content": "A B.",
        "citation_messages": {"data": []},
    }
    action_result = {
        "new_report": "A  B.",
        "original_text": "A B.",
        "original_start_offset": 0,
        "original_end_offset": len("A B."),
        "original_text_clean": "A B.",
        "rewritten_text": "A  B.",
        "rewritten_start_offset": 0,
        "rewritten_end_offset": len("A  B."),
    }

    updated = await apply_local_source_trace_to_action_result(
        feedback={"action": "polish"},
        action_result=action_result,
        final_result=final_result,
        llm_model_name="mock",
        language="zh-CN",
    )

    assert updated["rewritten_text"] == "A  B."
    assert updated["new_report"] == "A  B."


def test_collect_existing_citation_candidates_uses_removed_checked_ids():
    citation_messages = {
        "data": [
            {"id": 2, "title": "标题A", "url": "https://a.com", "content": "原文A", "chunk": "事实A"},
            {"id": 9, "title": "标题B", "url": "https://b.com", "content": "原文B", "chunk": "事实B"},
        ]
    }
    removed = [RemovedCitation(0, 10, "[checked_citation:9][[2]](https://b.com)", checked_citation_id=9)]

    candidates = collect_existing_citation_candidates(citation_messages, removed)

    assert candidates == [{"title": "标题B", "url": "https://b.com", "content": "原文B"}]


def test_collect_existing_citation_candidates_does_not_use_chunk_as_source_content():
    citation_messages = {
        "data": [
            {"id": 2, "title": "标题A", "url": "https://a.com", "content": "", "chunk": "报告中的句子A"},
        ]
    }
    removed = [RemovedCitation(0, 10, "[checked_citation:2][[1]](https://a.com)", checked_citation_id=2)]

    candidates = collect_existing_citation_candidates(citation_messages, removed)

    assert candidates == []


def test_convert_doc_infos_to_search_records_accepts_original_content_or_content():
    doc_infos = [
        {"title": "标题A", "url": "https://a.com", "original_content": "正文A"},
        {"title": "标题B", "url": "https://b.com", "content": "正文B"},
        {"title": "", "url": "https://bad.com", "content": "bad"},
    ]

    records = convert_doc_infos_to_search_records(doc_infos)

    assert records == [
        {"title": "标题A", "url": "https://a.com", "content": "正文A"},
        {"title": "标题B", "url": "https://b.com", "content": "正文B"},
    ]


def test_extract_reference_map_and_append_reference_entries_only_append_new_urls():
    report = "正文[[1]](https://a.com)\n\n[1]. [标题A](https://a.com)\n\n"
    reference_map, max_index = extract_reference_map(report)

    updated = append_reference_entries(
        report,
        new_reference_items=[
            {"reference_index": 1, "title": "标题A", "url": "https://a.com"},
            {"reference_index": 2, "title": "标题B", "url": "https://b.com"},
        ],
        existing_reference_map=reference_map,
        max_reference_index=max_index,
    )

    assert updated.count("[1]. [标题A](https://a.com)") == 1
    assert "[2]. [标题B](https://b.com)" in updated


def test_reference_map_and_global_numbering_handle_urls_with_parentheses():
    url = "https://example.com/a_(b)"
    report = f"正文[[1]]({url})\n\n[1]. [标题A]({url})\n\n"
    reference_map, max_index = extract_reference_map(report)
    text = f"新增[checked_citation:0][[1]]({url})"
    citation_data = [{"id": 0, "reference_index": 1, "title": "标题A", "url": url}]

    new_text, new_data = apply_global_citation_numbering(
        local_text=text,
        local_citation_data=citation_data,
        existing_citation_messages={"data": []},
        existing_reference_map=reference_map,
        max_reference_index=max_index,
    )
    updated_report = append_reference_entries(report, new_data, reference_map, max_index)

    assert reference_map == {url: 1}
    assert f"[checked_citation:0][[1]]({url})" in new_text
    assert new_data[0]["reference_index"] == 1
    assert updated_report.count(f"[1]. [标题A]({url})") == 1


def test_reference_map_and_global_numbering_handle_nested_and_escaped_parentheses_in_urls():
    nested_url = "https://example.com/a_(b_(c))"
    escaped_url = "https://example.com/escaped_\\(x\\)"
    report = (
        f"正文[[1]]({nested_url})和[[2]]({escaped_url})\n\n"
        f"[1]. [标题A]({nested_url})\n\n"
        f"[2]. [标题B]({escaped_url})\n\n"
    )
    reference_map, max_index = extract_reference_map(report)
    text = (
        f"新增[checked_citation:0][[1]]({nested_url})"
        f"并列[checked_citation:1][[2]]({escaped_url})"
    )
    citation_data = [
        {"id": 0, "reference_index": 1, "title": "标题A", "url": nested_url},
        {"id": 1, "reference_index": 2, "title": "标题B", "url": escaped_url},
    ]

    new_text, new_data = apply_global_citation_numbering(
        local_text=text,
        local_citation_data=citation_data,
        existing_citation_messages={"data": []},
        existing_reference_map=reference_map,
        max_reference_index=max_index,
    )
    updated_report = append_reference_entries(report, new_data, reference_map, max_index)

    assert reference_map == {nested_url: 1, escaped_url: 2}
    assert f"[checked_citation:0][[1]]({nested_url})" in new_text
    assert f"[checked_citation:1][[2]]({escaped_url})" in new_text
    assert [item["reference_index"] for item in new_data] == [1, 2]
    assert updated_report.count(f"[1]. [标题A]({nested_url})") == 1
    assert updated_report.count(f"[2]. [标题B]({escaped_url})") == 1


def test_global_numbering_leaves_unclosed_parenthesized_citation_unchanged():
    text = "新增[checked_citation:0][[1]](https://example.com/a_(b)"

    new_text, new_data = apply_global_citation_numbering(
        local_text=text,
        local_citation_data=[{"id": 0, "reference_index": 1, "title": "标题A", "url": "https://example.com/a_(b)"}],
        existing_citation_messages={"data": []},
        existing_reference_map={},
        max_reference_index=0,
    )

    assert new_text == text
    assert new_data == []


@pytest.mark.asyncio
async def test_run_local_source_trace_returns_checked_text_without_generated_reference_section():
    checker_payload = {
        "checked_trace_source_report_content": (
            "变化句[checked_citation:0][[1]](https://a.com)\n\n[1]. [标题A](https://a.com)\n\n"
        ),
        "citation_messages": {
            "data": [{"id": 0, "reference_index": 1, "title": "标题A", "url": "https://a.com"}]
        },
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace.recognize_content_to_cite",
        new_callable=AsyncMock,
        return_value=[{"sentence": "变化句"}],
    ):
        with patch(
            "openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace.match_sources",
            new_callable=AsyncMock,
            return_value=[{"sentence": "变化句", "source": "search_record", "matched_source_indices": [0]}],
        ):
            with patch(
                "openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace.CitationCheckerResearch.checker",
                new_callable=AsyncMock,
                return_value=json.dumps(checker_payload, ensure_ascii=False),
            ):
                result = await run_local_source_trace(
                    text="变化句",
                    source_records=[{"title": "标题A", "url": "https://a.com", "content": "来源正文"}],
                    llm_model_name="mock",
                )

    assert result == LocalTraceResult(
        text="变化句[checked_citation:0][[1]](https://a.com)",
        citation_data=[{"id": 0, "reference_index": 1, "title": "标题A", "url": "https://a.com"}],
        warning_info="",
    )


@pytest.mark.asyncio
async def test_run_local_source_trace_returns_warning_when_no_sources():
    result = await run_local_source_trace(text="变化句", source_records=[], llm_model_name="mock")

    assert result.text == "变化句"
    assert result.citation_data == []
    assert "No local source records" in result.warning_info


def test_apply_global_citation_numbering_offsets_ids_and_reuses_existing_reference():
    text = "新增[checked_citation:0][[1]](https://a.com)和新源[checked_citation:1][[2]](https://b.com)"
    citation_data = [
        {"id": 0, "reference_index": 1, "title": "标题A", "url": "https://a.com"},
        {"id": 1, "reference_index": 2, "title": "标题B", "url": "https://b.com"},
    ]

    new_text, new_data = apply_global_citation_numbering(
        local_text=text,
        local_citation_data=citation_data,
        existing_citation_messages={"data": [{"id": 8, "reference_index": 1, "url": "https://a.com"}]},
        existing_reference_map={"https://a.com": 1},
        max_reference_index=1,
    )

    assert "[checked_citation:9][[1]](https://a.com)" in new_text
    assert "[checked_citation:10][[2]](https://b.com)" in new_text
    assert new_data[0]["id"] == 9
    assert new_data[0]["reference_index"] == 1
    assert new_data[1]["id"] == 10
    assert new_data[1]["reference_index"] == 2


@pytest.mark.asyncio
async def test_apply_local_source_trace_preserves_equal_markup_and_traces_changed_segment():
    final_result = {
        "response_content": (
            "第一句[checked_citation:3][[1]](https://a.com)。第二句。\n\n[1]. [标题A](https://a.com)\n\n"
        ),
        "citation_messages": {
            "data": [{"id": 3, "reference_index": 1, "title": "标题A", "url": "https://a.com", "content": "来源A"}]
        },
    }
    original_text = "第一句[checked_citation:3][[1]](https://a.com)。第二句。"
    action_result = {
        "new_report": "第一句。第二句新增。\n\n[1]. [标题A](https://a.com)\n\n",
        "original_text": original_text,
        "original_start_offset": 0,
        "original_end_offset": len(original_text),
        "original_text_clean": "第一句。第二句。",
        "rewritten_text": "第一句。第二句新增。",
        "rewritten_start_offset": 0,
        "rewritten_end_offset": len("第一句。第二句新增。"),
        "source_trace_doc_infos": [{"title": "标题B", "url": "https://b.com", "original_content": "来源B"}],
    }
    with patch(
        "openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace.run_local_source_trace",
        new_callable=AsyncMock,
        return_value=LocalTraceResult(
            text="第二句新增[checked_citation:0][[1]](https://b.com)。",
            citation_data=[{"id": 0, "reference_index": 1, "title": "标题B", "url": "https://b.com"}],
        ),
    ):
        updated = await apply_local_source_trace_to_action_result(
            feedback={"action": "supplementary_search"},
            action_result=action_result,
            final_result=final_result,
            llm_model_name="mock",
            language="zh-CN",
        )

    assert "第一句[checked_citation:3][[1]](https://a.com)。" in updated["rewritten_text"]
    assert "第二句新增[checked_citation:4][[2]](https://b.com)" in updated["rewritten_text"]
    assert updated["citation_messages"]["data"][-1]["id"] == 4
    assert "[2]. [标题B](https://b.com)" in updated["new_report"]


@pytest.mark.asyncio
async def test_apply_local_source_trace_traces_changed_segments_concurrently_and_numbers_in_order():
    final_result = {
        "response_content": "开头。旧一。中间。旧二。结尾。",
        "citation_messages": {"data": []},
    }
    action_result = {
        "new_report": "开头。新一。中间。新二。结尾。",
        "original_text": "开头。旧一。中间。旧二。结尾。",
        "original_start_offset": 0,
        "original_end_offset": len("开头。旧一。中间。旧二。结尾。"),
        "original_text_clean": "开头。旧一。中间。旧二。结尾。",
        "rewritten_text": "开头。新一。中间。新二。结尾。",
        "rewritten_start_offset": 0,
        "rewritten_end_offset": len("开头。新一。中间。新二。结尾。"),
        "source_trace_doc_infos": [{"title": "来源", "url": "https://source.com", "original_content": "来源正文"}],
    }
    first_started = asyncio.Event()
    second_started = asyncio.Event()

    async def fake_run_local_source_trace(text, source_records, llm_model_name, language="zh-CN"):
        if text == "新一。":
            first_started.set()
            try:
                await asyncio.wait_for(second_started.wait(), timeout=0.05)
            except TimeoutError as exc:
                raise AssertionError("changed segments were traced serially") from exc
            return LocalTraceResult(
                text="新一[checked_citation:0][[1]](https://b.com)。",
                citation_data=[{"id": 0, "reference_index": 1, "title": "标题B", "url": "https://b.com"}],
            )
        if text == "新二。":
            second_started.set()
            await first_started.wait()
            return LocalTraceResult(
                text="新二[checked_citation:0][[1]](https://c.com)。",
                citation_data=[{"id": 0, "reference_index": 1, "title": "标题C", "url": "https://c.com"}],
            )
        raise AssertionError(f"unexpected trace segment: {text!r}")

    with patch(
        "openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace.run_local_source_trace",
        side_effect=fake_run_local_source_trace,
    ):
        updated = await apply_local_source_trace_to_action_result(
            feedback={"action": "new_task"},
            action_result=action_result,
            final_result=final_result,
            llm_model_name="mock",
            language="zh-CN",
        )

    assert (
        "开头。新一[checked_citation:0][[1]](https://b.com)。中间。"
        "新二[checked_citation:1][[2]](https://c.com)。结尾。"
    ) == updated["rewritten_text"]
    assert [item["id"] for item in updated["citation_messages"]["data"]] == [0, 1]
    assert [item["reference_index"] for item in updated["citation_messages"]["data"]] == [1, 2]
    assert "[1]. [标题B](https://b.com)" in updated["new_report"]
    assert "[2]. [标题C](https://c.com)" in updated["new_report"]


@pytest.mark.asyncio
async def test_apply_local_source_trace_uses_full_original_clean_when_action_clean_is_selection_only():
    final_result = {
        "response_content": (
            "第一句[checked_citation:3][[1]](https://a.com)。第二句。\n\n[1]. [标题A](https://a.com)\n\n"
        ),
        "citation_messages": {
            "data": [{"id": 3, "reference_index": 1, "title": "标题A", "url": "https://a.com", "content": "来源A"}]
        },
    }
    original_text = "第一句[checked_citation:3][[1]](https://a.com)。第二句。"
    action_result = {
        "new_report": "第一句。第二句新增。\n\n[1]. [标题A](https://a.com)\n\n",
        "original_text": original_text,
        "original_start_offset": 0,
        "original_end_offset": len(original_text),
        "original_text_clean": "第二句。",
        "rewritten_text": "第一句。第二句新增。",
        "rewritten_start_offset": 0,
        "rewritten_end_offset": len("第一句。第二句新增。"),
        "source_trace_doc_infos": [{"title": "标题B", "url": "https://b.com", "original_content": "来源B"}],
    }

    with patch(
        "openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace.run_local_source_trace",
        new_callable=AsyncMock,
        return_value=LocalTraceResult(
            text="第二句新增[checked_citation:0][[1]](https://b.com)。",
            citation_data=[{"id": 0, "reference_index": 1, "title": "标题B", "url": "https://b.com"}],
        ),
    ) as mock_trace:
        updated = await apply_local_source_trace_to_action_result(
            feedback={"action": "supplementary_search", "rewrite_scope": "selected_and_related"},
            action_result=action_result,
            final_result=final_result,
            llm_model_name="mock",
            language="zh-CN",
        )

    mock_trace.assert_awaited_once()
    assert mock_trace.await_args.args[0] == "第二句新增。"
    assert "第一句[checked_citation:3][[1]](https://a.com)。" in updated["rewritten_text"]
    assert "第二句新增[checked_citation:4][[2]](https://b.com)" in updated["rewritten_text"]


@pytest.mark.asyncio
async def test_apply_local_source_trace_traces_insert_only_rewrite_without_original_text():
    final_result = {
        "response_content": "## 第一章\n旧内容\n",
        "citation_messages": {"data": []},
    }
    action_result = {
        "new_report": "## 第一章\n旧内容\n\n### 新小节\n新增内容。",
        "original_text": "",
        "original_start_offset": len("## 第一章\n旧内容\n"),
        "original_end_offset": len("## 第一章\n旧内容\n"),
        "original_text_clean": "",
        "rewritten_text": "\n### 新小节\n新增内容。",
        "rewritten_start_offset": len("## 第一章\n旧内容\n"),
        "rewritten_end_offset": len("## 第一章\n旧内容\n\n### 新小节\n新增内容。"),
        "source_trace_doc_infos": [{"title": "标题B", "url": "https://b.com", "original_content": "来源B"}],
    }

    with patch(
        "openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace.run_local_source_trace",
        new_callable=AsyncMock,
        return_value=LocalTraceResult(
            text="\n### 新小节\n新增内容[checked_citation:0][[1]](https://b.com)。",
            citation_data=[{"id": 0, "reference_index": 1, "title": "标题B", "url": "https://b.com"}],
        ),
    ) as mock_trace:
        updated = await apply_local_source_trace_to_action_result(
            feedback={"action": "new_task"},
            action_result=action_result,
            final_result=final_result,
            llm_model_name="mock",
            language="zh-CN",
        )

    mock_trace.assert_awaited_once_with(
        "\n### 新小节\n新增内容。",
        [{"title": "标题B", "url": "https://b.com", "content": "来源B"}],
        "mock",
        language="zh-CN",
    )
    assert "新增内容[checked_citation:0][[1]](https://b.com)" in updated["new_report"]
    assert "[1]. [标题B](https://b.com)" in updated["new_report"]


@pytest.mark.asyncio
async def test_apply_local_source_trace_preserves_insert_boundary_before_next_heading():
    """验证零长度插入溯源后仍保留下一章节标题前的换行边界。"""
    prefix = "# 1. 第一章\n已有内容\n\n"
    inserted_text = "## 1.5 新增小节\n新增内容。\n\n"
    suffix = "# 2. 第二章\n原有内容"
    final_result = {
        "response_content": prefix + suffix,
        "citation_messages": {"data": []},
    }
    action_result = {
        "new_report": prefix + inserted_text + suffix,
        "original_text": "",
        "original_start_offset": len(prefix),
        "original_end_offset": len(prefix),
        "original_text_clean": "",
        "rewritten_text": inserted_text,
        "rewritten_start_offset": len(prefix),
        "rewritten_end_offset": len(prefix) + len(inserted_text),
        "source_trace_doc_infos": [{"title": "标题B", "url": "https://b.com", "original_content": "来源B"}],
    }

    with patch(
        "openjiuwen_deepsearch.algorithm.user_feedback_processor.local_source_trace.run_local_source_trace",
        new_callable=AsyncMock,
        return_value=LocalTraceResult(
            text="## 1.5 新增小节\n新增内容[checked_citation:0][[1]](https://b.com)。",
            citation_data=[{"id": 0, "reference_index": 1, "title": "标题B", "url": "https://b.com"}],
        ),
    ):
        updated = await apply_local_source_trace_to_action_result(
            feedback={"action": "new_task"},
            action_result=action_result,
            final_result=final_result,
            llm_model_name="mock",
            language="zh-CN",
        )

    assert "新增内容[checked_citation:0][[1]](https://b.com)。\n\n# 2. 第二章" in updated["new_report"]
    assert "新增内容[checked_citation:0][[1]](https://b.com)。# 2. 第二章" not in updated["new_report"]
    assert updated["rewritten_text"].endswith("\n\n")
