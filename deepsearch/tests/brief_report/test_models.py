"""Brief 数据模型的可观察契约测试。"""

from openjiuwen_deepsearch.algorithm.brief_report.models import BriefWorkflowState


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
