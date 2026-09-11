"""Brief 数据模型的可观察契约测试。"""

from openjiuwen_deepsearch.algorithm.brief_report.models import BriefOutline, BriefWorkflowState


def test_brief_outline_matches_section_titles_with_numbering_noise():
    """标题一致性校验：数量/顺序/标题（含编号噪声归一化）必须一致。"""
    brief = BriefOutline.model_validate(
        {
            "title": "测试升级",
            "sections": [
                {
                    "id": "1",
                    "title": "背景",
                    "goal": "梳理背景",
                    "research_steps": [
                        {"id": "1-1", "requirement": "检索行业资料"},
                        {"id": "1-2", "requirement": "归纳现状"},
                    ],
                },
                {
                    "id": "2",
                    "title": "结论",
                    "goal": "给出结论",
                    "research_steps": [
                        {"id": "2-1", "requirement": "汇总证据"},
                        {"id": "2-2", "requirement": "形成判断"},
                    ],
                },
            ],
        }
    )

    # 生成标题带编号噪声时归一化后仍视为一致
    assert brief.matches_section_titles(["背景", "2. 结论"]) is True
    # 标题不同则不一致
    assert brief.matches_section_titles(["背景", "风险"]) is False


def test_brief_workflow_state_serializes_collection_context_and_review():
    """审阅与补搜之间必须保留可序列化的运行时搜索上下文和写作指引。"""
    state = BriefWorkflowState.model_validate(
        {
            "collection_context": {
                "executed_queries": ["新能源汽车 月度销量"],
                "search_results": [
                    {
                        "source_id": "s1",
                        "title": "来源",
                        "url": "https://e/1",
                        "snippet": "月度销量数据",
                        "search_rank": 1,
                        "section_ids": ["1"],
                        "step_ids": ["1-1"],
                    }
                ],
            },
            "evidence_review": {
                "writing_guidance": {
                    "report_strategy": "先比较月度趋势，再解释品牌差异。",
                    "section_guidance": [
                        {"section_id": "1", "guidance": "优先呈现可比月度数据。"}
                    ],
                },
                "blocking_gaps": [],
            },
        }
    )

    assert state.collection_context.executed_queries == ["新能源汽车 月度销量"]
    assert state.evidence_review.writing_guidance.section_guidance[0].section_id == "1"


def test_final_result_defaults_to_markdown_content_type():
    """FinalResult 未显式设置时保持 markdown 类型，向后兼容现有调用方。"""
    from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import FinalResult

    result = FinalResult()
    assert result.response_content_type == "text/markdown"


def test_final_result_accepts_html_content_type_and_report_keeps_html_field():
    """HTML 产物可显式标记类型；Report 的 html 中间态字段默认为空。"""
    from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import FinalResult, Report

    result = FinalResult(response_content="<html></html>", response_content_type="text/html")
    assert result.response_content_type == "text/html"
    report = Report(report_content="# md", checked_trace_source_report_content="# md")
    assert report.report_html == ""
