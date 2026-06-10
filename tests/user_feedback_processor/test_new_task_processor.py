import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.user_feedback_processor.new_task_processor import (
    NewTaskProcessor,
    NewTaskAssetAssessment,
    SectionHistoricalAssets,
    NewTaskTargetSection,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Outline,
    Plan,
    Report,
    RetrievalQuery,
    Section,
    Step,
    StepType,
    SubReport,
    SubReportContent,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName


def test_resolve_target_section_strips_citation_and_infer_markup():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    report = (
        "# 总览\n"
        "## 第一章\n"
        "原文[checked_citation: 1][[1]](https://a.com)"
        "[推理结论](#inference:3)\n"
        "## 第二章\n"
        "其他内容"
    )
    feedback = {
        "selected_text": "原文[checked_citation: 1][[1]](https://a.com)[推理结论](#inference:3)",
        "start_offset": report.index("原文"),
        "end_offset": report.index("\n## 第二章"),
        "user_instruction": "补充行业背景",
    }

    target = processor.resolve_target_section(report_content=report, feedback=feedback)

    assert target.section_title == "第一章"
    assert target.clean_section_text == "## 第一章\n原文推理结论\n"
    assert target.clean_selected_text == "原文推理结论"


def test_resolve_target_section_keeps_numbered_major_chapter_context():
    """验证 new_task 会保留选区所属的编号大章节上下文。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    report = (
        "# 1. 第一大章\n"
        "## 1.1 已有小节\n"
        "已有内容\n"
        "## 1.2 目标小节\n"
        "目标内容\n"
        "# 2. 第二大章\n"
        "其他内容"
    )
    selected_text = "目标内容"
    feedback = {
        "selected_text": selected_text,
        "start_offset": report.index(selected_text),
        "end_offset": report.index(selected_text) + len(selected_text),
        "user_instruction": "补充新的对比维度",
    }

    target = processor.resolve_target_section(report_content=report, feedback=feedback)

    assert target.section_title == "1.2 目标小节"
    assert target.major_section_title == "1. 第一大章"
    assert target.major_section_start_offset == 0
    assert target.major_section_end_offset == report.index("# 2. 第二大章")
    assert "## 1.1 已有小节" in target.clean_major_section_text
    assert "## 1.2 目标小节" in target.clean_major_section_text


def test_validate_new_subsection_normalizes_llm_heading_to_expected_title():
    """验证新增小节会规范化 LLM 返回的标题。"""
    target = NewTaskTargetSection(
        section_title="1.2 目标小节",
        section_text="## 1.2 目标小节\n目标内容",
        clean_section_text="## 1.2 目标小节\n目标内容",
        clean_selected_text="目标内容",
        section_start_offset=0,
        section_end_offset=15,
        major_section_title="1. 第一大章",
        major_section_text="# 1. 第一大章\n## 1.1 已有小节\n内容\n",
        clean_major_section_text="# 1. 第一大章\n## 1.1 已有小节\n内容\n",
        major_section_start_offset=0,
        major_section_end_offset=40,
        major_heading_level=1,
    )

    normalized = NewTaskProcessor._validate_new_subsection(
        target=target,
        subsection_text="## 第二名城市对比分析\n新增小节内容",
        subsection_title="1.5 第二名城市对比分析",
    )

    assert normalized == "## 1.5 第二名城市对比分析\n新增小节内容"


def test_validate_new_subsection_extracts_expected_block_from_full_major_output():
    """验证新增小节会从误返回的大章节中截取目标小节块。"""
    target = NewTaskTargetSection(
        section_title="1.2 目标小节",
        section_text="## 1.2 目标小节\n目标内容",
        clean_section_text="## 1.2 目标小节\n目标内容",
        clean_selected_text="目标内容",
        section_start_offset=0,
        section_end_offset=15,
        major_section_title="1. 第一大章",
        major_section_text="# 1. 第一大章\n## 1.1 已有小节\n内容\n## 1.2 目标小节\n目标内容\n",
        clean_major_section_text="# 1. 第一大章\n## 1.1 已有小节\n内容\n## 1.2 目标小节\n目标内容\n",
        major_section_start_offset=0,
        major_section_end_offset=50,
        major_heading_level=1,
    )

    normalized = NewTaskProcessor._validate_new_subsection(
        target=target,
        subsection_text=(
            "# 1. 第一大章\n"
            "大章导语\n"
            "## 1.1 已有小节\n"
            "已有内容\n"
            "## 1.5 第二名城市对比分析\n"
            "新增小节内容\n"
            "### 对比维度\n"
            "细分内容\n"
            "## 1.6 其他小节\n"
            "不应保留"
        ),
        subsection_title="1.5 第二名城市对比分析",
    )

    assert normalized == "## 1.5 第二名城市对比分析\n新增小节内容\n### 对比维度\n细分内容"


def test_collect_section_assets_matches_outline_section_and_aggregates_doc_infos():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容",
        clean_section_text="## 第一章\n旧章节内容",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=11,
    )
    outline = Outline(
        thought="",
        title="报告",
        sections=[
            Section(
                id="1",
                title="第一章",
                description="",
                plans=[
                    Plan(
                        title="plan",
                        thought="",
                        is_research_completed=False,
                        steps=[
                            Step(
                                type=StepType.INFO_COLLECTING,
                                title="step",
                                description="desc",
                                retrieval_queries=[
                                    RetrievalQuery(
                                        query="q1",
                                        doc_infos=[
                                            {"title": "文档A", "url": "https://a.com"},
                                            {"title": "文档B", "url": "https://b.com"},
                                        ],
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )
    report = Report(
        title="",
        report_task="报告",
        sub_reports=[
            SubReport(
                section_id="1",
                section_task="第一章",
                content=SubReportContent(
                    classified_content=[{"title": "文档A", "url": "https://a.com"}],
                    sub_report_content_text="旧章节内容",
                ),
            )
        ],
    )

    assets = processor.collect_section_assets(target=target, current_outline=outline)

    assert assets.section_id == "1"
    assert assets.match_mode == "title_exact"
    assert assets.historical_doc_infos == [
        {"title": "文档A", "url": "https://a.com"},
        {"title": "文档B", "url": "https://b.com"},
    ]
    assert not hasattr(assets, "historical_classified_content")


def test_collect_section_assets_treats_none_list_fields_as_empty():
    """验证历史结构化状态中的空列表字段为 None 时会按空资产处理。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容",
        clean_section_text="## 第一章\n旧章节内容",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=11,
    )
    outline = SimpleNamespace(sections=None)
    report = SimpleNamespace(sub_reports=None)

    assets = processor.collect_section_assets(target=target, current_outline=outline)

    assert assets.section_id is None
    assert assets.match_mode == "none"
    assert assets.historical_doc_infos == []


def test_collect_section_assets_filters_malformed_doc_infos(caplog):
    """验证历史文档去重会跳过结构异常的 doc_info。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容",
        clean_section_text="## 第一章\n旧章节内容",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=11,
    )
    valid_doc = {"title": "文档A", "url": "https://a.com"}
    outline = SimpleNamespace(
        sections=[
            SimpleNamespace(
                id="1",
                title="第一章",
                plans=[
                    SimpleNamespace(
                        steps=[
                            SimpleNamespace(
                                retrieval_queries=[
                                    SimpleNamespace(
                                        doc_infos=[
                                            valid_doc,
                                            {"title": "缺少 URL"},
                                            {"url": "https://missing-title.com"},
                                            None,
                                            "not a dict",
                                            {"title": "文档A", "url": "https://a.com", "extra": "later duplicate"},
                                        ]
                                    )
                                ]
                            )
                        ]
                    )
                ],
            )
        ]
    )
    report = SimpleNamespace(
        sub_reports=[
            SimpleNamespace(
                section_id="1",
                content=None,
                background_knowledge=None,
            )
        ]
    )

    with caplog.at_level(logging.WARNING):
        assets = processor.collect_section_assets(target=target, current_outline=outline)

    assert assets.historical_doc_infos == [
        {"title": "文档A", "url": "https://a.com"}
    ]
    assert any("filtered malformed doc_infos" in record.message for record in caplog.records)


def test_new_task_deduplicate_doc_infos_keeps_same_url_different_sources():
    docs = [
        {
            "title": "文档A",
            "url": "https://example.com/a",
            "source_id": "source-a",
            "original_content": "content A",
        },
        {
            "title": "文档A",
            "url": "https://example.com/a",
            "source_id": "source-b",
            "original_content": "content B",
        },
    ]

    result = NewTaskProcessor._deduplicate_doc_infos(docs)

    assert [doc["source_id"] for doc in result] == ["source-a", "source-b"]


def test_collect_section_assets_matches_by_normalized_title():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    target = NewTaskTargetSection(
        section_title="1. 第一章：",
        section_text="## 1. 第一章：\n旧章节内容",
        clean_section_text="## 1. 第一章：\n旧章节内容",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=15,
    )
    outline = Outline(
        thought="",
        title="报告",
        sections=[Section(id="1", title="第一章", description="", plans=[])],
    )
    report = Report(
        title="",
        report_task="报告",
        sub_reports=[
            SubReport(
                section_id="1",
                section_task="第一章",
                content=SubReportContent(
                    classified_content=[{"title": "文档A", "url": "https://a.com"}],
                    sub_report_content_text="旧章节内容",
                ),
            )
        ],
    )

    assets = processor.collect_section_assets(target=target, current_outline=outline)

    assert assets.section_id == "1"
    assert assets.match_mode == "title_normalized"


def test_collect_section_assets_does_not_match_by_section_order():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    target = NewTaskTargetSection(
        section_title="第二章 当前分析",
        section_text="## 第二章 当前分析\n旧章节内容",
        clean_section_text="## 第二章 当前分析\n旧章节内容",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=18,
    )
    outline = Outline(
        thought="",
        title="报告",
        sections=[
            Section(id="1", title="背景", description="", plans=[]),
            Section(id="2", title="市场现状", description="", plans=[]),
        ],
    )
    report = Report(
        title="",
        report_task="报告",
        sub_reports=[
            SubReport(
                section_id="1",
                section_task="背景",
                content=SubReportContent(classified_content=[], sub_report_content_text="背景章节"),
            ),
            SubReport(
                section_id="2",
                section_task="市场现状",
                content=SubReportContent(classified_content=[], sub_report_content_text="旧章节内容"),
            ),
        ],
    )

    assets = processor.collect_section_assets(target=target, current_outline=outline)

    assert assets.section_id is None
    assert assets.match_mode == "none"


def test_collect_section_assets_does_not_match_by_content_similarity():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    target = NewTaskTargetSection(
        section_title="完全不同的标题",
        section_text="## 完全不同的标题\n行业规模在 2024 年显著增长，企业上云比例继续提升。",
        clean_section_text="## 完全不同的标题\n行业规模在 2024 年显著增长，企业上云比例继续提升。",
        clean_selected_text="行业规模在 2024 年显著增长，企业上云比例继续提升。",
        section_start_offset=0,
        section_end_offset=38,
    )
    outline = Outline(
        thought="",
        title="报告",
        sections=[
            Section(id="1", title="背景", description="", plans=[]),
            Section(id="2", title="市场现状", description="", plans=[]),
        ],
    )
    report = Report(
        title="",
        report_task="报告",
        sub_reports=[
            SubReport(
                section_id="1",
                section_task="背景",
                content=SubReportContent(classified_content=[], sub_report_content_text="这是一段完全无关的内容"),
            ),
            SubReport(
                section_id="2",
                section_task="市场现状",
                content=SubReportContent(
                    classified_content=[{"title": "文档B", "url": "https://b.com"}],
                    sub_report_content_text="行业规模在 2024 年显著增长，企业上云比例继续提升。",
                ),
            ),
        ],
    )

    assets = processor.collect_section_assets(target=target, current_outline=outline)

    assert assets.section_id is None
    assert assets.match_mode == "none"


@pytest.mark.asyncio
async def test_assessment_not_sufficient_builds_incremental_plan_from_missing_aspects():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="## 第一章\n旧章节内容",
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )

    with patch.object(processor, "assess_section_assets", new_callable=AsyncMock) as mock_assess:
        mock_assess.return_value = NewTaskAssetAssessment(
            relevant_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
            is_sufficient=False,
            missing_aspects=["行业背景", "市场规模数据"],
            reasoning_summary="现有资料缺少行业背景与定量数据",
        )
        plan = await processor.build_incremental_plan(
            assets=assets,
            feedback={"user_instruction": "补充行业背景"},
            language="zh-CN",
        )

    assert plan.title == "NEW_TASK incremental research"
    assert len(plan.steps) == 1
    assert plan.steps[0].type == StepType.INFO_COLLECTING
    assert "行业背景" in plan.steps[0].description
    assert "市场规模数据" in plan.steps[0].description


@pytest.mark.asyncio
async def test_run_new_task_merges_new_docs_and_rewrites_entire_section(caplog):
    processor = NewTaskProcessor(llm_model_name="mock_model")
    caplog.set_level(logging.INFO)
    final_result = {
        "response_content": "## 第一章\n旧章节内容\n## 第二章\n其他内容",
        "citation_messages": {"data": []},
        "infer_messages": [],
    }
    feedback = {
        "action": "new_task",
        "selected_text": "旧章节内容",
        "start_offset": 6,
        "end_offset": 11,
        "user_instruction": "补充行业背景",
    }
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容\n",
        clean_section_text="## 第一章\n旧章节内容\n",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=12,
    )
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="## 第一章\n旧章节内容\n",
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )
    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
        is_sufficient=False,
        missing_aspects=["行业背景"],
        reasoning_summary="还缺行业背景",
    )

    with patch.object(processor, "resolve_target_section", return_value=target):
        with patch.object(processor, "collect_section_assets", return_value=assets):
            with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, return_value=assessment):
                with patch.object(processor, "build_incremental_plan", new_callable=AsyncMock) as mock_build_plan:
                    mock_build_plan.return_value = Plan(
                        id="",
                        language="zh-CN",
                        title="NEW_TASK incremental research",
                        thought="",
                        is_research_completed=False,
                        steps=[Step(type=StepType.INFO_COLLECTING, title="Collect", description="补充行业背景")],
                    )
                    with patch.object(processor, "run_incremental_collection", new_callable=AsyncMock) as mock_collect:
                        mock_collect.return_value = {
                            "info_summary": "补充了行业背景",
                            "doc_infos": [{"title": "文档B", "url": "https://b.com"}],
                        }
                        with patch.object(processor, "rewrite_section_with_assets", new_callable=AsyncMock) as mock_rewrite:
                            mock_rewrite.return_value = "## 第一章\n新章节内容\n"
                            result = await processor.run_new_task(
                                feedback=feedback,
                                final_result=final_result,
                                language="zh-CN",
                            )

    assert result["new_report"] == "## 第一章\n新章节内容\n## 第二章\n其他内容"
    assert result["used_historical_doc_count"] == 1
    assert result["used_new_doc_count"] == 1
    assert result["incremental_doc_infos"] == [{"title": "文档B", "url": "https://b.com"}]
    assert result["assessment_summary"] == "还缺行业背景"
    assert any("incremental collection started" in record.message for record in caplog.records)
    assert any("incremental collection completed" in record.message for record in caplog.records)
    assert any("edit_strategy=modify_existing_subsection" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_run_new_task_keeps_newline_before_next_heading():
    """验证整章改写后保留下一章节标题前的原始空行。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    report = "## 第一章\n旧章节内容\n本章结尾。\n\n## 第二章\n其他内容"
    selected_text = "旧章节内容"
    final_result = {
        "response_content": report,
        "citation_messages": {"data": []},
        "infer_messages": [],
    }
    feedback = {
        "action": "new_task",
        "selected_text": selected_text,
        "start_offset": report.index(selected_text),
        "end_offset": report.index(selected_text) + len(selected_text),
        "user_instruction": "补充行业背景",
    }
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容\n本章结尾。\n\n",
        clean_section_text="## 第一章\n旧章节内容\n本章结尾。\n\n",
        clean_selected_text=selected_text,
        section_start_offset=0,
        section_end_offset=len("## 第一章\n旧章节内容\n本章结尾。\n\n"),
    )
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text=target.clean_section_text,
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )
    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
        is_sufficient=True,
        missing_aspects=[],
        reasoning_summary="历史资料足够",
    )

    with patch.object(processor, "resolve_target_section", return_value=target):
        with patch.object(processor, "collect_section_assets", return_value=assets):
            with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, return_value=assessment):
                with patch.object(
                    processor,
                    "rewrite_section_with_assets",
                    new_callable=AsyncMock,
                    return_value="## 第一章\n改写后的章节内容",
                ):
                    result = await processor.run_new_task(
                        feedback=feedback,
                        final_result=final_result,
                        language="zh-CN",
                    )

    assert "改写后的章节内容\n\n## 第二章" in result["new_report"]
    assert "改写后的章节内容## 第二章" not in result["new_report"]


@pytest.mark.asyncio
async def test_run_new_task_appends_new_subsection_to_numbered_major_chapter(caplog):
    """验证新增任务可以在编号大章节末尾追加新的小章节。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    caplog.set_level(logging.INFO)
    report = (
        "# 1. 第一大章\n"
        "## 1.1 已有小节\n"
        "已有内容\n\n"
        "## 1.2 目标小节\n"
        "目标内容\n\n"
        "# 2. 第二大章\n"
        "其他内容"
    )
    selected_text = "目标内容"
    final_result = {
        "response_content": report,
        "citation_messages": {"data": []},
        "infer_messages": [],
    }
    feedback = {
        "action": "new_task",
        "selected_text": selected_text,
        "start_offset": report.index(selected_text),
        "end_offset": report.index(selected_text) + len(selected_text),
        "user_instruction": "新增一个小节分析第二名城市差距",
    }
    target = processor.resolve_target_section(report_content=report, feedback=feedback)
    assets = SectionHistoricalAssets(
        section_id="1.2",
        match_mode="title_exact",
        section_title=target.section_title,
        current_section_text=target.clean_major_section_text,
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )
    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
        is_sufficient=True,
        missing_aspects=[],
        reasoning_summary="需要新增独立小节承载新维度",
        edit_strategy="append_new_subsection",
        subsection_title="第二名城市差距分析",
    )

    with patch.object(processor, "collect_section_assets", return_value=assets):
        with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, return_value=assessment):
            with patch.object(
                processor,
                "generate_new_subsection_with_assets",
                new_callable=AsyncMock,
                return_value="## 1.3 第二名城市差距分析\n新增小节内容",
            ) as mock_generate:
                result = await processor.run_new_task(
                    feedback=feedback,
                    final_result=final_result,
                    language="zh-CN",
                )

    mock_generate.assert_awaited_once()
    assert "## 1.2 目标小节\n目标内容" in result["new_report"]
    assert "## 1.3 第二名城市差距分析\n新增小节内容\n\n# 2. 第二大章" in result["new_report"]
    assert result["edit_strategy"] == "append_new_subsection"
    assert result["new_subsection_title"] == "1.3 第二名城市差距分析"
    assert any("incremental collection skipped" in record.message for record in caplog.records)
    assert any("edit_strategy=append_new_subsection" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_run_new_task_preserves_major_chapter_markup_before_appending_subsection():
    """验证 NEW_TASK 新增小节不会清理目标大章节内已有溯源标记。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    report = (
        "# 1. 第一大章\n"
        "## 1.1 已有小节\n"
        "已有内容[checked_citation: 1][[1]](https://a.com)\n"
        "推理[结论](#inference:7)\n\n"
        "## 1.2 目标小节\n"
        "目标内容[checked_citation: 2][[2]](https://b.com)\n\n"
        "# 2. 第二大章\n"
        "其他内容[checked_citation: 3][[3]](https://c.com)"
    )
    selected_text = "目标内容[checked_citation: 2][[2]](https://b.com)"
    final_result = {
        "response_content": report,
        "citation_messages": {"data": []},
        "infer_messages": [],
    }
    feedback = {
        "action": "new_task",
        "selected_text": selected_text,
        "start_offset": report.index(selected_text),
        "end_offset": report.index(selected_text) + len(selected_text),
        "user_instruction": "新增一个小节分析第二名城市差距",
    }
    captured_targets = []

    def fake_collect_section_assets(target, current_outline):
        captured_targets.append(target)
        return SectionHistoricalAssets(
            section_id="1.2",
            match_mode="title_exact",
            section_title=target.section_title,
            current_section_text=target.clean_major_section_text,
            historical_plans=[],
            historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
        )

    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
        is_sufficient=True,
        missing_aspects=[],
        reasoning_summary="需要新增独立小节承载新维度",
        edit_strategy="append_new_subsection",
        subsection_title="新增分析",
    )

    with patch.object(processor, "collect_section_assets", side_effect=fake_collect_section_assets):
        with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, return_value=assessment):
            with patch.object(
                processor,
                "generate_new_subsection_with_assets",
                new_callable=AsyncMock,
                return_value="## 1.3 新增分析\n新增小节内容",
            ):
                result = await processor.run_new_task(
                    feedback=feedback,
                    final_result=final_result,
                    language="zh-CN",
                )

    target = captured_targets[0]
    first_major_chapter = result["new_report"].split("# 2. 第二大章", 1)[0]
    assert "[checked_citation: 1][[1]](https://a.com)" in first_major_chapter
    assert "[结论](#inference:7)" in first_major_chapter
    assert "[checked_citation: 2][[2]](https://b.com)" in first_major_chapter
    assert "其他内容[checked_citation: 3][[3]](https://c.com)" in result["new_report"]
    assert "[checked_citation:" not in target.clean_major_section_text
    assert "(#inference:" not in target.clean_major_section_text
    assert "## 1.3 新增分析\n新增小节内容\n\n# 2. 第二大章" in result["new_report"]


@pytest.mark.asyncio
async def test_run_new_task_preserves_plain_trace_links_before_appending_subsection():
    """验证 NEW_TASK 新增小节不会清理目标大章节内的双中括号溯源链接。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    report = (
        "# 1. 第一大章\n"
        "## 1.1 已有小节\n"
        "已有内容[[1]](https://a.com)\n\n"
        "## 1.2 目标小节\n"
        "目标内容[[2]](https://b.com)\n\n"
        "# 2. 第二大章\n"
        "其他内容[[3]](https://c.com)"
    )
    selected_text = "目标内容[[2]](https://b.com)"
    final_result = {
        "response_content": report,
        "citation_messages": {"data": []},
        "infer_messages": [],
    }
    feedback = {
        "action": "new_task",
        "selected_text": selected_text,
        "start_offset": report.index(selected_text),
        "end_offset": report.index(selected_text) + len(selected_text),
        "user_instruction": "新增一个小节分析第二名城市差距",
    }
    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
        is_sufficient=True,
        missing_aspects=[],
        reasoning_summary="需要新增独立小节承载新维度",
        edit_strategy="append_new_subsection",
        subsection_title="新增分析",
    )

    with patch.object(processor, "collect_section_assets") as mock_collect:
        mock_collect.side_effect = lambda target, current_outline: SectionHistoricalAssets(
            section_id="1.2",
            match_mode="title_exact",
            section_title=target.section_title,
            current_section_text=target.clean_major_section_text,
            historical_plans=[],
            historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
        )
        with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, return_value=assessment):
            with patch.object(
                processor,
                "generate_new_subsection_with_assets",
                new_callable=AsyncMock,
                return_value="## 1.3 新增分析\n新增小节内容",
            ):
                result = await processor.run_new_task(
                    feedback=feedback,
                    final_result=final_result,
                    language="zh-CN",
                )

    first_major_chapter = result["new_report"].split("# 2. 第二大章", 1)[0]
    reconstructed_report = (
        report[: result["original_start_offset"]]
        + result["rewritten_text"]
        + report[result["original_end_offset"] :]
    )
    assert "[[1]](https://a.com)" in first_major_chapter
    assert "[[2]](https://b.com)" in first_major_chapter
    assert "其他内容[[3]](https://c.com)" in result["new_report"]
    assert reconstructed_report == result["new_report"]


@pytest.mark.asyncio
async def test_run_new_task_can_modify_existing_subsection_other_than_selection():
    """验证修改已有内容时可改写同一大章节内更匹配的小章节。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    report = (
        "# 1. 第一大章\n"
        "## 1.1 政策背景\n"
        "政策旧内容\n\n"
        "## 1.2 产能分析\n"
        "产能内容\n\n"
        "# 2. 第二大章\n"
        "其他内容"
    )
    selected_text = "产能内容"
    final_result = {
        "response_content": report,
        "citation_messages": {"data": []},
        "infer_messages": [],
    }
    feedback = {
        "action": "new_task",
        "selected_text": selected_text,
        "start_offset": report.index(selected_text),
        "end_offset": report.index(selected_text) + len(selected_text),
        "user_instruction": "补充政策支持内容",
    }
    target = processor.resolve_target_section(report_content=report, feedback=feedback)
    assets = SectionHistoricalAssets(
        section_id="1.1",
        match_mode="title_exact",
        section_title=target.section_title,
        current_section_text=target.clean_major_section_text,
        historical_plans=[],
        historical_doc_infos=[{"title": "政策文档", "url": "https://policy.example.com"}],
    )
    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[{"title": "政策文档", "url": "https://policy.example.com"}],
        is_sufficient=True,
        missing_aspects=[],
        reasoning_summary="政策内容更适合更新 1.1",
        edit_strategy="modify_existing_subsection",
        target_subsection_title="1.1 政策背景",
    )

    with patch.object(processor, "collect_section_assets", return_value=assets):
        with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, return_value=assessment):
            with patch.object(
                processor,
                "rewrite_section_with_assets",
                new_callable=AsyncMock,
                return_value="## 1.1 政策背景\n政策新内容\n",
            ) as mock_rewrite:
                result = await processor.run_new_task(
                    feedback=feedback,
                    final_result=final_result,
                    language="zh-CN",
                )

    rewrite_target = mock_rewrite.await_args.kwargs["target"]
    assert rewrite_target.section_title == "1.1 政策背景"
    assert "## 1.1 政策背景\n政策新内容\n\n## 1.2 产能分析\n产能内容" in result["new_report"]
    assert result["edit_strategy"] == "modify_existing_subsection"


@pytest.mark.asyncio
async def test_run_new_task_retargets_assets_when_modifying_another_subsection():
    """验证跨小节改写会使用实际被改写小节的历史资产和 section_id。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    report = (
        "# 1. 第一大章\n"
        "## 1.1 政策背景\n"
        "政策旧内容\n\n"
        "## 1.2 产能分析\n"
        "产能内容\n"
    )
    selected_text = "产能内容"
    feedback = {
        "action": "new_task",
        "selected_text": selected_text,
        "start_offset": report.index(selected_text),
        "end_offset": report.index(selected_text) + len(selected_text),
        "user_instruction": "补充政策支持内容",
    }
    policy_doc = {"title": "政策文档", "url": "https://policy.example.com"}
    capacity_doc = {"title": "产能文档", "url": "https://capacity.example.com"}
    outline = Outline(
        thought="",
        title="报告",
        sections=[
            Section(
                id="1.1",
                title="1.1 政策背景",
                description="",
                plans=[
                    Plan(
                        title="policy",
                        thought="",
                        is_research_completed=False,
                        steps=[
                            Step(
                                type=StepType.INFO_COLLECTING,
                                title="step",
                                description="desc",
                                retrieval_queries=[
                                    RetrievalQuery(query="policy", doc_infos=[policy_doc])
                                ],
                            )
                        ],
                    )
                ],
            ),
            Section(
                id="1.2",
                title="1.2 产能分析",
                description="",
                plans=[
                    Plan(
                        title="capacity",
                        thought="",
                        is_research_completed=False,
                        steps=[
                            Step(
                                type=StepType.INFO_COLLECTING,
                                title="step",
                                description="desc",
                                retrieval_queries=[
                                    RetrievalQuery(query="capacity", doc_infos=[capacity_doc])
                                ],
                            )
                        ],
                    )
                ],
            ),
        ],
    )
    async def fake_assess(assets, feedback, language):
        if assets.section_id == "1.1":
            return NewTaskAssetAssessment(
                relevant_doc_infos=[policy_doc],
                is_sufficient=True,
                missing_aspects=[],
                reasoning_summary="政策小节资料足够",
                edit_strategy="modify_existing_subsection",
                target_subsection_title="1.1 政策背景",
            )
        return NewTaskAssetAssessment(
            relevant_doc_infos=[capacity_doc],
            is_sufficient=True,
            missing_aspects=[],
            reasoning_summary="政策内容更适合更新 1.1",
            edit_strategy="modify_existing_subsection",
            target_subsection_title="1.1 政策背景",
        )

    with patch.object(processor, "_load_current_outline", return_value=outline):
        with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, side_effect=fake_assess):
            with patch.object(
                processor,
                "rewrite_section_with_assets",
                new_callable=AsyncMock,
                return_value="## 1.1 政策背景\n政策新内容\n",
            ) as mock_rewrite:
                result = await processor.run_new_task(
                    feedback=feedback,
                    final_result={"response_content": report},
                    language="zh-CN",
                )

    rewrite_kwargs = mock_rewrite.await_args.kwargs
    assert rewrite_kwargs["target"].section_title == "1.1 政策背景"
    assert rewrite_kwargs["doc_infos"] == [policy_doc]
    assert result["matched_section_id"] == "1.1"
    assert result["section_title"] == "1.1 政策背景"


@pytest.mark.asyncio
async def test_run_new_task_modify_existing_subsection_cleans_only_rewritten_subsection_markup():
    """验证修改已有小节时只清理被改写小节的溯源标记。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    report = (
        "# 1. 第一大章\n"
        "## 1.1 已有小节\n"
        "已有内容[[1]](https://a.com)\n\n"
        "## 1.2 目标小节\n"
        "目标内容[checked_citation: 2][[2]](https://b.com)\n"
        "推理[结论](#inference:7)\n\n"
        "# 2. 第二大章\n"
        "其他内容[[3]](https://c.com)"
    )
    selected_text = "目标内容[checked_citation: 2][[2]](https://b.com)"
    final_result = {
        "response_content": report,
        "citation_messages": {"data": []},
        "infer_messages": [],
    }
    feedback = {
        "action": "new_task",
        "selected_text": selected_text,
        "start_offset": report.index(selected_text),
        "end_offset": report.index(selected_text) + len(selected_text),
        "user_instruction": "修改目标小节内容",
    }
    target = processor.resolve_target_section(report_content=report, feedback=feedback)
    assets = SectionHistoricalAssets(
        section_id="1.2",
        match_mode="title_exact",
        section_title=target.section_title,
        current_section_text=target.clean_major_section_text,
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )
    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
        is_sufficient=True,
        missing_aspects=[],
        reasoning_summary="需要修改目标小节",
        edit_strategy="modify_existing_subsection",
    )

    with patch.object(processor, "collect_section_assets", return_value=assets):
        with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, return_value=assessment):
            with patch.object(
                processor,
                "rewrite_section_with_assets",
                new_callable=AsyncMock,
                return_value="## 1.2 目标小节\n目标新内容\n",
            ) as mock_rewrite:
                result = await processor.run_new_task(
                    feedback=feedback,
                    final_result=final_result,
                    language="zh-CN",
                )

    rewrite_target = mock_rewrite.await_args.kwargs["target"]
    reconstructed_report = (
        report[: result["original_start_offset"]]
        + result["rewritten_text"]
        + report[result["original_end_offset"] :]
    )
    assert "[[1]](https://a.com)" in result["new_report"]
    assert "[[3]](https://c.com)" in result["new_report"]
    assert "[checked_citation: 2]" not in result["new_report"]
    assert "(#inference:7)" not in result["new_report"]
    assert "[checked_citation: 2]" not in rewrite_target.section_text
    assert "(#inference:7)" not in rewrite_target.section_text
    assert result["original_text"] == target.section_text
    assert reconstructed_report == result["new_report"]


@pytest.mark.asyncio
async def test_run_new_task_raises_when_no_historical_or_incremental_docs_available():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    final_result = {
        "response_content": "## 第一章\n旧章节内容\n## 第二章\n其他内容",
        "citation_messages": {"data": []},
        "infer_messages": [],
    }
    feedback = {
        "action": "new_task",
        "selected_text": "旧章节内容",
        "start_offset": 6,
        "end_offset": 11,
        "user_instruction": "补充行业背景",
    }
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容\n",
        clean_section_text="## 第一章\n旧章节内容\n",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=12,
    )
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="## 第一章\n旧章节内容\n",
        historical_plans=[],
        historical_doc_infos=[],
    )
    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[],
        is_sufficient=False,
        missing_aspects=["行业背景"],
        reasoning_summary="还缺行业背景",
    )

    with patch.object(processor, "resolve_target_section", return_value=target):
        with patch.object(processor, "collect_section_assets", return_value=assets):
            with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, return_value=assessment):
                with patch.object(processor, "build_incremental_plan", new_callable=AsyncMock) as mock_build_plan:
                    mock_build_plan.return_value = Plan(
                        id="",
                        language="zh-CN",
                        title="NEW_TASK incremental research",
                        thought="",
                        is_research_completed=False,
                        steps=[Step(type=StepType.INFO_COLLECTING, title="Collect", description="补充行业背景")],
                    )
                    with patch.object(processor, "run_incremental_collection", new_callable=AsyncMock) as mock_collect:
                        mock_collect.return_value = {
                            "info_summary": "",
                            "doc_infos": [],
                        }
                        with patch.object(processor, "rewrite_section_with_assets", new_callable=AsyncMock) as mock_rewrite:
                            with pytest.raises(CustomValueException) as exc_info:
                                await processor.run_new_task(
                                    feedback=feedback,
                                    final_result=final_result,
                                    language="zh-CN",
                                )

    assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code
    assert "No evidence available" in exc_info.value.message
    mock_rewrite.assert_not_awaited()


@pytest.mark.asyncio
async def test_assess_section_assets_parses_structured_llm_response():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    doc_info = {
        "doc_id": "web_1",
        "source_id": "web_1_p123",
        "title": "文档A",
        "url": "https://a.com",
        "publish_time": "2025-05",
        "original_content": "原文A",
        "content_ref": {"type": "source_store", "source_id": "web_1_p123"},
        "scores": {"authority": 8, "relevance": 9, "answerability": 7, "data_density": 6},
        "key_passages": ["关键段落"],
    }
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="## 第一章\n旧章节内容",
        historical_plans=[],
        historical_doc_infos=[doc_info],
    )
    llm_response = json.dumps(
        {
            "relevant_doc_indices": [1],
            "is_sufficient": True,
            "missing_aspects": [],
            "reasoning_summary": "历史资料足够支撑补写",
        },
        ensure_ascii=False,
    )

    with patch.object(processor, "_invoke_prompt", new_callable=AsyncMock, return_value=llm_response) as mock_invoke:
        result = await processor.assess_section_assets(
            assets=assets,
            feedback={"user_instruction": "补充行业背景", "selected_text": "旧章节内容"},
            language="zh-CN",
    )

    assert result.is_sufficient is True
    assert result.relevant_doc_infos == [doc_info]
    assert result.reasoning_summary == "历史资料足够支撑补写"
    prompt_vars = mock_invoke.await_args.args[1]
    assert prompt_vars["historical_doc_infos"] == [
        {
            "doc_time": "2025-05",
            "source_authority": "",
            "task_relevance": "",
            "original_content": "原文A",
            "url": "https://a.com",
            "information_richness": "",
            "data_density": "",
            "title": "文档A",
            "query": "",
        }
    ]
    assert mock_invoke.await_args.args[2] == AgentLlmName.USER_FEEDBACK_PROCESSOR_NEW_TASK_ASSESSMENT.value


@pytest.mark.asyncio
async def test_assess_section_assets_defaults_to_modify_when_llm_omits_edit_strategy():
    """验证编辑策略只由 LLM 结构化字段决定，不再依赖用户指令关键词。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="# 1. 第一章\n## 1.1 已有小节\n旧内容",
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )
    llm_response = json.dumps(
        {
            "relevant_doc_indices": [1],
            "is_sufficient": True,
            "missing_aspects": [],
            "reasoning_summary": "历史资料足够支撑补写",
        },
        ensure_ascii=False,
    )

    with patch.object(processor, "_invoke_prompt", new_callable=AsyncMock, return_value=llm_response):
        result = await processor.assess_section_assets(
            assets=assets,
            feedback={"user_instruction": "请新增一个小节分析第二名城市", "selected_text": "旧内容"},
            language="zh-CN",
        )

    assert result.edit_strategy == "modify_existing_subsection"


@pytest.mark.asyncio
async def test_assess_section_assets_logs_and_falls_back_when_llm_response_is_invalid_json(caplog):
    processor = NewTaskProcessor(llm_model_name="mock_model")
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="## 第一章\n旧章节内容",
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )

    with patch.object(processor, "_invoke_prompt", new_callable=AsyncMock, return_value="{invalid json"):
        with caplog.at_level(logging.WARNING):
            result = await processor.assess_section_assets(
                assets=assets,
                feedback={"user_instruction": "补充行业背景", "selected_text": "旧章节内容"},
                language="zh-CN",
            )

    assert result.is_sufficient is False
    assert result.relevant_doc_infos == []
    assert result.missing_aspects == ["补充行业背景"]
    assert result.reasoning_summary == "无法稳定解析评估结果，已降级为资料不足。"
    assert any("failed to parse LLM JSON" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_assess_section_assets_logs_filtered_invalid_relevant_doc_indices(caplog):
    processor = NewTaskProcessor(llm_model_name="mock_model")
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="## 第一章\n旧章节内容",
        historical_plans=[],
        historical_doc_infos=[
            {"title": "文档A", "url": "https://a.com"},
            {"title": "文档B", "url": "https://b.com"},
        ],
    )
    llm_response = json.dumps(
        {
            "relevant_doc_indices": [1, 0, -1, 3, "2", True, 2],
            "is_sufficient": True,
            "missing_aspects": [],
            "reasoning_summary": "混合返回有效与无效索引",
        },
        ensure_ascii=False,
    )

    with patch.object(processor, "_invoke_prompt", new_callable=AsyncMock, return_value=llm_response):
        with caplog.at_level(logging.WARNING):
            result = await processor.assess_section_assets(
                assets=assets,
                feedback={"user_instruction": "补充行业背景", "selected_text": "旧章节内容"},
                language="zh-CN",
            )

    assert result.relevant_doc_infos == [
        {"title": "文档A", "url": "https://a.com"},
        {"title": "文档B", "url": "https://b.com"},
    ]
    assert any("filtered invalid relevant_doc_indices" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_assess_section_assets_downgrades_sufficient_result_without_valid_docs(caplog):
    """验证 LLM 误判资料足够但没有有效文档时会降级为资料不足。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="## 第一章\n旧章节内容",
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )
    llm_response = json.dumps(
        {
            "relevant_doc_indices": [2],
            "is_sufficient": True,
            "missing_aspects": [],
            "reasoning_summary": "历史资料足够支撑补写",
        },
        ensure_ascii=False,
    )

    with patch.object(processor, "_invoke_prompt", new_callable=AsyncMock, return_value=llm_response):
        with caplog.at_level(logging.WARNING):
            result = await processor.assess_section_assets(
                assets=assets,
                feedback={"user_instruction": "补充行业背景", "selected_text": "旧章节内容"},
                language="zh-CN",
            )

    assert result.is_sufficient is False
    assert result.relevant_doc_infos == []
    assert any("downgraded sufficient assessment" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_assess_section_assets_treats_string_false_as_insufficient(caplog):
    """验证 LLM 返回字符串 false 时不会被 Python truthiness 误判为资料充足。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="## 第一章\n旧章节内容",
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )
    llm_response = json.dumps(
        {
            "relevant_doc_indices": [1],
            "is_sufficient": "false",
            "missing_aspects": ["行业规模数据"],
            "reasoning_summary": "资料不足",
        },
        ensure_ascii=False,
    )

    with patch.object(processor, "_invoke_prompt", new_callable=AsyncMock, return_value=llm_response):
        with caplog.at_level(logging.WARNING):
            result = await processor.assess_section_assets(
                assets=assets,
                feedback={"user_instruction": "补充行业背景", "selected_text": "旧章节内容"},
                language="zh-CN",
            )

    assert result.is_sufficient is False
    assert result.relevant_doc_infos == [{"title": "文档A", "url": "https://a.com"}]
    assert any("invalid is_sufficient" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_run_new_task_skips_incremental_collection_when_historical_assets_are_sufficient():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    final_result = {
        "response_content": "## 第一章\n旧章节内容\n## 第二章\n其他内容",
        "citation_messages": {"data": []},
        "infer_messages": [],
    }
    feedback = {
        "action": "new_task",
        "selected_text": "旧章节内容",
        "start_offset": 6,
        "end_offset": 11,
        "user_instruction": "补充行业背景",
    }
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容\n",
        clean_section_text="## 第一章\n旧章节内容\n",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=12,
    )
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="第一章",
        current_section_text="## 第一章\n旧章节内容\n",
        historical_plans=[],
        historical_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
    )
    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[{"title": "文档A", "url": "https://a.com"}],
        is_sufficient=True,
        missing_aspects=[],
        reasoning_summary="历史资料足够支撑补写",
    )

    with patch.object(processor, "resolve_target_section", return_value=target):
        with patch.object(processor, "collect_section_assets", return_value=assets):
            with patch.object(processor, "assess_section_assets", new_callable=AsyncMock, return_value=assessment):
                with patch.object(processor, "build_incremental_plan", new_callable=AsyncMock) as mock_build_plan:
                    with patch.object(
                        processor,
                        "run_incremental_collection",
                        new_callable=AsyncMock,
                    ) as mock_collect:
                        with patch.object(processor, "rewrite_section_with_assets", new_callable=AsyncMock) as mock_rewrite:
                            mock_rewrite.return_value = "## 第一章\n新章节内容\n"
                            result = await processor.run_new_task(
                                feedback=feedback,
                                final_result=final_result,
                                language="zh-CN",
                            )

    mock_build_plan.assert_not_awaited()
    mock_collect.assert_not_awaited()
    assert result["new_report"] == "## 第一章\n新章节内容\n## 第二章\n其他内容"
    assert result["used_historical_doc_count"] == 1
    assert result["used_new_doc_count"] == 0
    assert result["incremental_plan"] is None
    assert result["incremental_doc_infos"] == []
    assert result["assessment_summary"] == "历史资料足够支撑补写"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("1. 第一章：", "第一章"),
        ("第2章 市场-现状", "市场现状"),
        (" 结论/建议 ", "结论建议"),
    ],
)
def test_normalize_section_title(title, expected):
    assert NewTaskProcessor._normalize_section_title(title) == expected


def test_build_next_subsection_title_extracts_explicit_title_from_instruction():
    """验证新增小节兜底标题可从用户指令中提取。"""
    target = NewTaskTargetSection(
        section_title="1.2 目标小节",
        section_text="## 1.2 目标小节\n目标内容",
        clean_section_text="## 1.2 目标小节\n目标内容",
        clean_selected_text="目标内容",
        section_start_offset=0,
        section_end_offset=15,
        major_section_title="1. 第一大章",
        major_section_text="# 1. 第一大章\n## 1.1 已有小节\n内容\n## 1.2 目标小节\n目标内容\n",
        clean_major_section_text="# 1. 第一大章\n## 1.1 已有小节\n内容\n## 1.2 目标小节\n目标内容\n",
        major_section_start_offset=0,
        major_section_end_offset=50,
        major_heading_level=1,
    )

    title = NewTaskProcessor._build_next_subsection_title(
        target,
        "请在本章新增一个小节，标题为“第二名城市对比分析”，补充产量差距。",
    )

    assert title == "1.3 第二名城市对比分析"


def test_build_next_subsection_title_renumbers_llm_numbered_title_under_major_chapter():
    """验证 LLM 返回带编号标题时仍按当前大章节追加下一个小节号。"""
    target = NewTaskTargetSection(
        section_title="1.2 目标小节",
        section_text="## 1.2 目标小节\n目标内容",
        clean_section_text="## 1.2 目标小节\n目标内容",
        clean_selected_text="目标内容",
        section_start_offset=0,
        section_end_offset=15,
        major_section_title="1. 第一大章",
        major_section_text="# 1. 第一大章\n## 1.1 已有小节\n内容\n## 1.2 目标小节\n目标内容\n",
        clean_major_section_text="# 1. 第一大章\n## 1.1 已有小节\n内容\n## 1.2 目标小节\n目标内容\n",
        major_section_start_offset=0,
        major_section_end_offset=50,
        major_heading_level=1,
    )

    title = NewTaskProcessor._build_next_subsection_title(
        target,
        "1.2.1 第二名城市对比分析",
    )

    assert title == "1.3 第二名城市对比分析"


def test_build_next_subsection_title_uses_english_fallback_and_extracts_english_title():
    """验证英文报告新增小节标题不会退化为中文兜底文案。"""
    target = NewTaskTargetSection(
        section_title="1.2 Target Subsection",
        section_text="## 1.2 Target Subsection\nTarget content",
        clean_section_text="## 1.2 Target Subsection\nTarget content",
        clean_selected_text="Target content",
        section_start_offset=0,
        section_end_offset=40,
        major_section_title="1. Market Overview",
        major_section_text="# 1. Market Overview\n## 1.1 Current State\nContent\n## 1.2 Target Subsection\nTarget content\n",
        clean_major_section_text="# 1. Market Overview\n## 1.1 Current State\nContent\n## 1.2 Target Subsection\nTarget content\n",
        major_section_start_offset=0,
        major_section_end_offset=90,
        major_heading_level=1,
    )

    extracted_title = NewTaskProcessor._build_next_subsection_title(
        target,
        'Please add a subsection titled "Competitive Risk".',
        language="en",
    )
    fallback_title = NewTaskProcessor._build_next_subsection_title(target, "", language="en")

    assert extracted_title == "1.3 Competitive Risk"
    assert fallback_title == "1.3 Additional Analysis"
    assert "新增" not in fallback_title


@pytest.mark.asyncio
async def test_build_incremental_plan_uses_english_description_labels():
    """验证英文报告增量采集计划不会使用中文字段标签。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    assets = SectionHistoricalAssets(
        section_id="1",
        match_mode="title_exact",
        section_title="Market Overview",
        current_section_text="## Market Overview\nOld content",
        historical_plans=[],
        historical_doc_infos=[],
    )
    assessment = NewTaskAssetAssessment(
        relevant_doc_infos=[],
        is_sufficient=False,
        missing_aspects=["market size", "competitor risk"],
        reasoning_summary="Need more evidence.",
    )

    plan = await processor.build_incremental_plan(
        assets=assets,
        feedback={"user_instruction": "Add competitor risk analysis."},
        language="en",
        assessment=assessment,
    )

    description = plan.steps[0].description
    assert "User request:" in description
    assert "Section title:" in description
    assert "Missing information:" in description
    assert "用户要求" not in description
    assert "章节标题" not in description
    assert "待补充信息" not in description


@pytest.mark.asyncio
async def test_rewrite_section_with_assets_returns_clean_section_text():
    processor = NewTaskProcessor(llm_model_name="mock_model")
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容",
        clean_section_text="## 第一章\n旧章节内容",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=11,
    )

    with patch.object(
        processor,
        "_invoke_prompt",
        new_callable=AsyncMock,
        return_value="## 第一章\n新章节内容\n",
    ) as mock_invoke:
        result = await processor.rewrite_section_with_assets(
            target=target,
            feedback={"user_instruction": "补充行业背景"},
            doc_infos=[
                {
                    "doc_id": "web_1",
                    "source_id": "web_1_p123",
                    "title": "文档A",
                    "url": "https://a.com",
                    "publish_time": "2025-05",
                    "original_content": "原文A",
                    "content_ref": {"type": "source_store", "source_id": "web_1_p123"},
                    "scores": {"authority": 8},
                    "key_passages": ["关键段落"],
                }
            ],
            language="zh-CN",
        )

    assert result == "## 第一章\n新章节内容"
    prompt_vars = mock_invoke.await_args.args[1]
    assert prompt_vars["doc_infos"] == [
        {
            "doc_time": "2025-05",
            "source_authority": "",
            "task_relevance": "",
            "original_content": "原文A",
            "url": "https://a.com",
            "information_richness": "",
            "data_density": "",
            "title": "文档A",
            "query": "",
        }
    ]
    assert mock_invoke.await_args.args[2] == AgentLlmName.USER_FEEDBACK_PROCESSOR_NEW_TASK_REWRITE_SECTION.value


def test_validate_rewritten_section_rejects_preamble_before_heading():
    """验证改写已有小节时不允许在目标标题前输出解释性文本。"""
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容",
        clean_section_text="## 第一章\n旧章节内容",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=11,
    )

    with pytest.raises(CustomValueException) as exc_info:
        NewTaskProcessor._validate_rewritten_section(
            target=target,
            rewritten_section="说明如下\n## 第一章\n新章节内容",
        )

    assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code
    assert "must start with original section title" in exc_info.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("llm_output", "message_fragment"),
    [
        ("", "empty"),
        ("新章节内容", "missing original section title"),
        ("# 总览\n## 第一章\n新章节内容", "unexpected higher-level headings"),
    ],
)
async def test_rewrite_section_with_assets_rejects_invalid_structure(llm_output, message_fragment):
    processor = NewTaskProcessor(llm_model_name="mock_model")
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容",
        clean_section_text="## 第一章\n旧章节内容",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=11,
    )

    with patch.object(processor, "_invoke_prompt", new_callable=AsyncMock, return_value=llm_output):
        with pytest.raises(CustomValueException) as exc_info:
            await processor.rewrite_section_with_assets(
                target=target,
                feedback={"user_instruction": "补充行业背景"},
                doc_infos=[{"title": "文档A", "url": "https://a.com"}],
                language="zh-CN",
            )

    assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code
    assert message_fragment in exc_info.value.message


@pytest.mark.asyncio
async def test_rewrite_section_with_assets_rejects_heading_level_drift():
    """验证整章改写不能改变原章节标题层级。"""
    processor = NewTaskProcessor(llm_model_name="mock_model")
    target = NewTaskTargetSection(
        section_title="第一章",
        section_text="## 第一章\n旧章节内容",
        clean_section_text="## 第一章\n旧章节内容",
        clean_selected_text="旧章节内容",
        section_start_offset=0,
        section_end_offset=11,
    )

    with patch.object(processor, "_invoke_prompt", new_callable=AsyncMock, return_value="### 第一章\n新章节内容"):
        with pytest.raises(CustomValueException) as exc_info:
            await processor.rewrite_section_with_assets(
                target=target,
                feedback={"user_instruction": "补充行业背景"},
                doc_infos=[{"title": "文档A", "url": "https://a.com"}],
                language="zh-CN",
            )

    assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code
    assert "heading level" in exc_info.value.message
