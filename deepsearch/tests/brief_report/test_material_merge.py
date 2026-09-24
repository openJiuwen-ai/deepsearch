"""Brief collector merge_material_evidence 的单测（相关性路由/空章节兜底/registry 编号）。"""

from openjiuwen_deepsearch.algorithm.brief_report.collector import merge_material_evidence
from openjiuwen_deepsearch.algorithm.brief_report.models import (
    BriefCitationRecord,
    BriefCollectionResult,
    BriefOutline,
    BriefOutlineRequest,
    BriefSectionEvidence,
    BriefSelectedDoc,
)
from openjiuwen_deepsearch.algorithm.query_understanding.material_processing import (
    MaterialAnalysis,
    MaterialManifestItem,
)


def _outline():
    return BriefOutline.model_validate({
        "title": "测试报告",
        "sections": [
            {
                "id": "1",
                "title": "新能源汽车市场",
                "goal": "分析销量走势",
                "research_steps": [
                    {"id": "1-1", "requirement": "销量数据"},
                    {"id": "1-2", "requirement": "对比差异"},
                ],
                "material_bindings": [{"material_id": "M1", "role": "primary_evidence", "claims_to_use": "sales"}],
            },
            {
                "id": "2",
                "title": "烹饪文化",
                "goal": "介绍家常菜做法",
                "research_steps": [
                    {"id": "2-1", "requirement": "菜系特点"},
                    {"id": "2-2", "requirement": "代表菜品"},
                ],
                "material_bindings": [{"material_id": "M2", "role": "primary_evidence", "claims_to_use": "recipe"}],
            },
            {
                "id": "3",
                "title": "太空探索",
                "goal": "介绍火星探测",
                "research_steps": [
                    {"id": "3-1", "requirement": "探测历史"},
                    {"id": "3-2", "requirement": "技术路线"},
                ],
            },
        ],
    })


def _collection():
    return BriefCollectionResult(
        section_evidence={
            "1": BriefSectionEvidence(selected_docs=[
                BriefSelectedDoc(source_id="s1", step_ids=["1-1"], evaluation_rank=1),
            ]),
            "2": BriefSectionEvidence(),
            "3": BriefSectionEvidence(),
        },
        citation_registry=[
            BriefCitationRecord(source_id="s1", index=1, title="网页",
                                url="https://e.com/s1", original_content="片段"),
        ],
    )


def _analysis():
    return MaterialAnalysis(items=[
        MaterialManifestItem(material_id="M1", title="新能源汽车销量报告",
                             summary="2025年新能源汽车销量同比增长", summary_kind="fulltext"),
        MaterialManifestItem(material_id="M2", title="红烧肉做法",
                             summary="家常菜烹饪步骤", summary_kind="summary"),
    ])


def test_merge_routes_relevant_materials_and_appends_registry():
    merged = merge_material_evidence(_collection(), _outline(), _analysis())

    # 章节1：素材优先，相关网页证据仍作为补充保留。
    section1 = {doc.source_id: doc for doc in merged.section_evidence["1"].selected_docs}
    assert set(section1) == {"s1", "user_material:M1"}
    assert section1["user_material:M1"].evaluation_rank == 1
    assert section1["s1"].evaluation_rank == 2

    # 章节2：按显式绑定精确路由
    assert {doc.source_id for doc in merged.section_evidence["2"].selected_docs} == {"user_material:M2"}

    # 章节3：没有绑定时不使用用户素材
    assert not merged.section_evidence["3"].selected_docs

    # registry 编号续接
    assert [record.index for record in merged.citation_registry] == [1, 2, 3]
    assert merged.citation_registry[1].source_id == "user_material:M1"
    assert merged.citation_registry[2].source_id == "user_material:M2"


def test_merge_without_materials_returns_unchanged():
    collection = _collection()
    assert merge_material_evidence(collection, _outline(), MaterialAnalysis()) is collection
    assert merge_material_evidence(collection, _outline(), None) is collection


def test_merge_skips_existing_registry_source_and_continues_index():
    collection = _collection().model_copy(update={"citation_registry": [
        *_collection().citation_registry,
        BriefCitationRecord(source_id="user_material:M1", index=2, title="新能源汽车销量报告",
                            url="", original_content="旧摘要"),
    ]})
    merged = merge_material_evidence(collection, _outline(), _analysis())

    sources = [record.source_id for record in merged.citation_registry]
    assert sources.count("user_material:M1") == 1
    m2 = next(record for record in merged.citation_registry if record.source_id == "user_material:M2")
    assert m2.index == 3  # 续接 registry 现有最大编号


def test_merge_accepts_serialized_analysis():
    merged = merge_material_evidence(_collection(), _outline(), _analysis().model_dump())
    assert "user_material:M1" in {record.source_id for record in merged.citation_registry}


def test_merge_uses_material_bindings_as_the_exact_material_route():
    outline = _outline().model_copy(deep=True)
    section = outline.sections[0]
    section.use_material_ids = ["[M1]"]
    section.material_bindings = [{
        "material_id": "M1", "role": "primary_evidence", "claims_to_use": "result",
    }]

    merged = merge_material_evidence(_collection(), outline, _analysis())

    selected = {doc.source_id for doc in merged.section_evidence[section.id].selected_docs}
    assert "user_material:M1" in selected
    assert "user_material:M2" not in selected


def test_brief_outline_request_material_context_default():
    request = BriefOutlineRequest(query="q")
    assert request.material_context == {}
    request = BriefOutlineRequest(query="q", material_context={"has_materials": True})
    assert request.material_context == {"has_materials": True}
