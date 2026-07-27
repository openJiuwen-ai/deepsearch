import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.user_feedback_processor.truth_verification import (
    TruthVerificationProcessor,
)


def _build_report_context():
    report_content = "# 1. 总览\n前文\n\n## 2. 市场规模\n目标段落第一句\n目标段落第二句\n"
    section_content = MagicMock()
    section_content.classified_content = [
        {
            "title": "行业白皮书",
            "url": "https://example.com/a",
            "original_content": "目标段落第一句有依据",
        }
    ]
    sub_report = MagicMock()
    sub_report.section_task = "2. 市场规模"
    sub_report.content = section_content
    current_report = MagicMock()
    current_report.sub_reports = [sub_report]
    current_report.all_classified_contents = [section_content.classified_content]
    return report_content, current_report


def test_extract_verified_paragraph_uses_text_before_first_newline():
    assert TruthVerificationProcessor.extract_verified_paragraph("第一句\n第二句") == "第一句"
    assert TruthVerificationProcessor.extract_verified_paragraph("单行内容") == "单行内容"
    assert TruthVerificationProcessor.extract_verified_paragraph("\n第一句\n第二句") == "第一句"
    assert TruthVerificationProcessor.extract_verified_paragraph("\n\n") == ""


def test_prepare_doc_infos_for_assessment_maps_content_to_original_content():
    prepared = TruthVerificationProcessor._prepare_doc_infos_for_assessment(
        [{"title": "示例", "url": "https://example.com", "content": "正文内容"}]
    )
    assert prepared[0]["original_content"] == "正文内容"


def test_collect_section_doc_infos_matches_parent_section_for_subsection_selection():
    processor = TruthVerificationProcessor(llm_model_name="mock")
    report_content = (
        "# 1. 总览\n"
        "前文\n\n"
        "## 2. 市场分析\n"
        "章节导语\n\n"
        "### 2.1 市场规模\n"
        "用户选中的段落\n"
    )
    section_content = MagicMock()
    section_content.classified_content = [
        {
            "title": "行业白皮书",
            "url": "https://example.com/a",
            "original_content": "用户选中的段落有依据",
        }
    ]
    sub_report = MagicMock()
    sub_report.section_task = "2. 市场分析"
    sub_report.content = section_content
    current_report = MagicMock()
    current_report.sub_reports = [sub_report]
    current_report.all_classified_contents = [section_content.classified_content]

    selected_text = "用户选中的段落"
    start_offset = report_content.index(selected_text)
    end_offset = start_offset + len(selected_text)

    section_heading, doc_infos = processor._collect_section_doc_infos(
        report_content=report_content,
        feedback={
            "start_offset": start_offset,
            "end_offset": end_offset,
        },
        current_report=current_report,
    )

    assert section_heading == "2.1 市场规模"
    assert len(doc_infos) == 1
    assert doc_infos[0]["title"] == "行业白皮书"


@pytest.mark.asyncio
async def test_assess_paragraph_with_docs_fallback_when_display_text_empty():
    processor = TruthVerificationProcessor(llm_model_name="mock")
    with patch.object(
        processor,
        "_invoke_prompt",
        new_callable=AsyncMock,
        return_value=json.dumps(
            {
                "display_text": "",
                "conclusion": "supported",
                "need_more_search": False,
            },
            ensure_ascii=False,
        ),
    ):
        result = await processor._assess_paragraph_with_docs(
            verified_paragraph="段落",
            section_heading="章节",
            user_instruction="",
            doc_infos=[],
            language="en-US",
        )

    assert result["display_text"].startswith("**Verification conclusion**:")
    assert "substantiated" in result["display_text"]


@pytest.mark.asyncio
async def test_truth_verification_falls_back_to_search_when_section_docs_insufficient():
    processor = TruthVerificationProcessor(llm_model_name="mock")
    report_content, current_report = _build_report_context()
    selected_text = "目标段落第一句\n目标段落第二句"
    start_offset = report_content.index("目标段落第一句")
    end_offset = start_offset + len(selected_text)

    async def invoke_prompt_side_effect(prompt_name, context_vars, agent_name_or_suffix):
        if prompt_name == "truth_verification_assessment":
            if context_vars["doc_infos"] and len(context_vars["doc_infos"]) == 1:
                return json.dumps(
                    {
                        "display_text": "章节资料不足，需要补充搜索。",
                        "conclusion": "insufficient_evidence",
                        "need_more_search": True,
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "display_text": "补充搜索后有足够证据。",
                    "conclusion": "supported",
                    "need_more_search": False,
                },
                ensure_ascii=False,
            )
        if prompt_name == "truth_verification_search_task":
            return "检索市场规模权威统计并核对目标段落中的关键数据。"
        raise AssertionError(f"Unexpected prompt name: {prompt_name}")

    with patch.object(
        processor,
        "_invoke_prompt",
        new_callable=AsyncMock,
        side_effect=invoke_prompt_side_effect,
    ), patch.object(
        processor,
        "_run_collection",
        new_callable=AsyncMock,
        return_value={
            "doc_infos": [
                {
                    "title": "统计公报",
                    "url": "https://example.com/b",
                    "original_content": "目标段落第一句的权威数据。",
                }
            ]
        },
    ):
        result = await processor.truth_verification(
            feedback={
                "action": "truth_verification",
                "selected_text": selected_text,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "user_instruction": "请核验真实性",
            },
            final_result={"response_content": report_content},
            current_report=current_report,
            language="zh-CN",
        )

    assert result["read_only_result"] is True
    verification_result = result["verification_result"]
    assert verification_result["display_text"] == "补充搜索后有足够证据。"


@pytest.mark.asyncio
async def test_truth_verification_skips_initial_assessment_when_section_docs_empty():
    processor = TruthVerificationProcessor(llm_model_name="mock")
    report_content = "# 1. 总览\n\n## 2. 新章节标题\n目标段落第一句\n"
    selected_text = "目标段落第一句"
    start_offset = report_content.index(selected_text)
    end_offset = start_offset + len(selected_text)
    current_report = MagicMock()
    current_report.sub_reports = []
    current_report.all_classified_contents = []

    assessment_calls = []

    async def invoke_prompt_side_effect(prompt_name, context_vars, agent_name_or_suffix):
        if prompt_name == "truth_verification_assessment":
            assessment_calls.append(context_vars["doc_infos"])
            return json.dumps(
                {
                    "display_text": "补充搜索后有足够证据。",
                    "conclusion": "supported",
                    "need_more_search": False,
                },
                ensure_ascii=False,
            )
        if prompt_name == "truth_verification_search_task":
            return "检索目标段落相关权威资料。"
        raise AssertionError(f"Unexpected prompt name: {prompt_name}")

    with patch.object(
        processor,
        "_invoke_prompt",
        new_callable=AsyncMock,
        side_effect=invoke_prompt_side_effect,
    ), patch.object(
        processor,
        "_run_collection",
        new_callable=AsyncMock,
        return_value={
            "doc_infos": [
                {
                    "title": "统计公报",
                    "url": "https://example.com/b",
                    "original_content": "目标段落第一句的权威数据。",
                }
            ]
        },
    ):
        result = await processor.truth_verification(
            feedback={
                "action": "truth_verification",
                "selected_text": selected_text,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "user_instruction": "",
            },
            final_result={"response_content": report_content},
            current_report=current_report,
            language="zh-CN",
        )

    assert len(assessment_calls) == 1
    assert len(assessment_calls[0]) == 1
    assert result["verification_result"]["display_text"] == "补充搜索后有足够证据。"


@pytest.mark.asyncio
async def test_truth_verification_returns_fallback_when_search_still_empty():
    processor = TruthVerificationProcessor(llm_model_name="mock")
    report_content = "# 1. 总览\n\n## 2. 新章节标题\n目标段落第一句\n"
    selected_text = "目标段落第一句"
    start_offset = report_content.index(selected_text)
    end_offset = start_offset + len(selected_text)
    current_report = MagicMock()
    current_report.sub_reports = []
    current_report.all_classified_contents = []

    assessment_calls = []

    async def invoke_prompt_side_effect(prompt_name, context_vars, agent_name_or_suffix):
        if prompt_name == "truth_verification_assessment":
            assessment_calls.append(context_vars)
        if prompt_name == "truth_verification_search_task":
            return "检索目标段落相关权威资料。"
        raise AssertionError(f"Unexpected prompt name: {prompt_name}")

    with patch.object(
        processor,
        "_invoke_prompt",
        new_callable=AsyncMock,
        side_effect=invoke_prompt_side_effect,
    ), patch.object(
        processor,
        "_run_collection",
        new_callable=AsyncMock,
        return_value={"doc_infos": []},
    ):
        result = await processor.truth_verification(
            feedback={
                "action": "truth_verification",
                "selected_text": selected_text,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "user_instruction": "",
            },
            final_result={"response_content": report_content},
            current_report=current_report,
            language="zh-CN",
        )

    assert assessment_calls == []
    assert result["verification_result"]["display_text"].startswith("**核验结论**：")
    assert "证据不足" in result["verification_result"]["display_text"]


@pytest.mark.asyncio
async def test_truth_verification_does_not_search_when_initial_evidence_is_sufficient():
    processor = TruthVerificationProcessor(llm_model_name="mock")
    report_content, current_report = _build_report_context()
    selected_text = "目标段落第一句\n目标段落第二句"
    start_offset = report_content.index("目标段落第一句")
    end_offset = start_offset + len(selected_text)

    with patch.object(
        processor,
        "_invoke_prompt",
        new_callable=AsyncMock,
        return_value=json.dumps(
            {
                "display_text": "现有章节资料可以支持主要观点。",
                "conclusion": "partially_supported",
                "need_more_search": False,
            },
            ensure_ascii=False,
        ),
    ), patch.object(processor, "_run_collection", new_callable=AsyncMock) as mock_run_collection:
        result = await processor.truth_verification(
            feedback={
                "action": "truth_verification",
                "selected_text": selected_text,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "user_instruction": "",
            },
            final_result={"response_content": report_content},
            current_report=current_report,
            language="zh-CN",
        )

    assert result["verification_result"]["display_text"] == "现有章节资料可以支持主要观点。"
    mock_run_collection.assert_not_awaited()
