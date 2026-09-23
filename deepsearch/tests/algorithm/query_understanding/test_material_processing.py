"""用户素材预处理与证据解析的非 LLM 单测（分流/缓存/预算降级/证据构建）。"""

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from openjiuwen_deepsearch.algorithm.query_understanding import (
    material_processing as mp,
)
from openjiuwen_deepsearch.algorithm.query_understanding.material_processing import (
    MaterialAnalysis,
    MaterialManifestItem,
    MaterialRelevance,
    UserMaterial,
    apply_material_relevance_map,
    build_material_evidence_items,
    build_fallback_material_relevance_map,
    build_material_prompt_context,
    build_section_material_coverage,
    content_hash,
    estimate_text_tokens,
    extract_material_ids,
    filter_materials_by_query,
    format_section_material_bindings,
    is_material_first_request,
    normalize_material_bindings,
    normalize_user_materials,
    rank_materials_by_query,
    resolve_material_evidence,
    resolve_material_usage_mode,
    restore_material_analysis,
)


class TestTokenEstimateAndHash:
    def test_estimate_text_tokens(self):
        assert estimate_text_tokens("") == 0
        assert estimate_text_tokens("测试") == 2  # CJK 一字一 token
        assert estimate_text_tokens("abcdef") == 2  # (6+2)//3

    def test_content_hash_stable_and_distinct(self):
        assert content_hash("abc") == content_hash("abc")
        assert content_hash("abc") != content_hash("abd")
        assert content_hash(None) == content_hash("")


class TestUserMaterial:
    def test_requires_content(self):
        with pytest.raises(ValidationError):
            UserMaterial()
        with pytest.raises(ValidationError):
            UserMaterial(title="T")
        with pytest.raises(ValidationError):
            UserMaterial(url="https://example.com/a")
        with pytest.raises(ValidationError):
            UserMaterial(content="   ")  # strip 后为空同样拒绝

    def test_strips_and_has_content(self):
        material = UserMaterial(title="  标题  ", content="  正文  ")
        assert material.title == "标题"
        assert material.content == "正文"
        assert material.has_content


class TestNormalizeUserMaterials:
    def test_non_list_input(self):
        materials, dropped = normalize_user_materials("not-a-list")
        assert materials == []
        assert dropped == [{"index": 0, "reason": "not_a_list"}]
        materials, dropped = normalize_user_materials(None)
        assert materials == [] and dropped == []

    def test_drops_invalid_and_duplicates(self):
        raw = [
            {"title": "A", "url": "https://example.com/a", "content": "x"},
            "junk",
            {},
            {"title": "T"},  # 无 content → 校验拒绝
            {"title": "A", "url": "https://example.com/a", "content": "y"},  # 重复 url
            {"title": "B", "content": "shared"},
            {"title": "C", "content": "shared"},  # 重复正文
        ]
        materials, dropped = normalize_user_materials(raw)
        assert [m.title for m in materials] == ["A", "B"]
        assert [d["reason"] for d in dropped] == [
            "not_a_dict", "invalid_or_empty", "invalid_or_empty", "duplicate_url", "duplicate_content",
        ]

    def test_dedup_priority_url_then_title_then_hash(self):
        # 去重顺序：url → title → hash；标题相同即使正文不同也判重
        raw = [
            {"title": "A", "url": "https://example.com/a", "content": "x"},
            {"title": "A", "content": "different-body"},   # 标题重复，优先于正文 hash
            {"title": "B", "content": "shared"},
            {"title": "C", "content": "shared"},            # 正文 hash 重复
        ]
        materials, dropped = normalize_user_materials(raw)
        assert [m.title for m in materials] == ["A", "B"]
        assert [d["reason"] for d in dropped] == ["duplicate_title", "duplicate_content"]

    def test_drops_duplicate_explicit_material_id(self):
        materials, dropped = normalize_user_materials([
            {"material_id": "paper-1", "title": "A", "content": "first"},
            {"material_id": "paper-1", "title": "B", "content": "second"},
        ])
        assert [material.material_id for material in materials] == ["paper-1"]
        assert dropped == [{"index": 1, "reason": "duplicate_material_id"}]

    def test_soft_truncates_exceeding_max_count(self):
        raw = [{"title": f"T{i}", "content": f"body-{i}"} for i in range(55)]
        materials, dropped = normalize_user_materials(raw)
        assert len(materials) == 50
        assert [m.title for m in materials] == [f"T{i}" for i in range(50)]
        assert [d["reason"] for d in dropped] == ["exceeds_max_count"] * 5
        assert [d["index"] for d in dropped] == list(range(50, 55))

    def test_max_count_env_override(self, monkeypatch):
        monkeypatch.setattr(mp, "MATERIAL_MAX_COUNT", 2)
        raw = [{"title": "A", "content": "x"}, {"title": "B", "content": "y"}, {"title": "C", "content": "z"}]
        materials, dropped = normalize_user_materials(raw)
        assert len(materials) == 2
        assert [m.title for m in materials] == ["A", "B"]
        assert [d["reason"] for d in dropped] == ["exceeds_max_count"]
        assert dropped[0]["index"] == 2

    def test_truncates_after_dedup_keeps_valid_first(self):
        # 截断发生在去重之后：53 条全部有效（title 均不重复），截到 50 条
        raw = [{"title": f"T{i}", "content": f"body-{i}"} for i in range(50)]
        raw.append({"title": "X0", "content": "body-0-dup-url-less"})
        raw.append({"title": "T100", "content": "body-100"})
        raw.append({"title": "T101", "content": "body-101"})
        materials, dropped = normalize_user_materials(raw)
        assert len(materials) == 50
        assert materials[-1].title == "T49"  # 按有效序列取前 50，其后 3 条有效素材被截断
        assert [d["reason"] for d in dropped] == ["exceeds_max_count"] * 3
        assert [d["index"] for d in dropped] == [50, 51, 52]


class TestRestoreAndExtract:
    def test_restore_material_analysis_variants(self):
        assert restore_material_analysis(None) is None
        assert restore_material_analysis("junk") is None
        obj = MaterialAnalysis(items=[MaterialManifestItem(material_id="M1")])
        assert restore_material_analysis(obj) is obj
        restored = restore_material_analysis(obj.model_dump())
        assert isinstance(restored, MaterialAnalysis)
        assert restored.items[0].material_id == "M1"

    def test_extract_material_ids_objects_and_dicts(self):
        analysis = MaterialAnalysis(items=[MaterialManifestItem(material_id="M1"), MaterialManifestItem()])
        context = build_material_prompt_context(analysis)
        assert extract_material_ids(context) == ["M1", "M2"]  # 空ID按位次兜底
        dict_context = {"_analysis_items": [item.model_dump() for item in analysis.items]}
        assert extract_material_ids(dict_context) == ["M1", "M2"]
        assert extract_material_ids({}) == []


class TestBuildMaterialPromptContext:
    def test_empty_analysis(self):
        context = build_material_prompt_context(None)
        assert context["has_materials"] is False
        assert context["materials_count"] == 0
        assert context["materials_manifest_text"] == ""

    def test_manifest_and_analysis_injection(self):
        analysis = MaterialAnalysis(items=[
            MaterialManifestItem(material_id="M1", title="T1", summary="摘要内容", summary_kind="summary"),
        ])
        context = build_material_prompt_context(analysis)
        assert context["has_materials"] is True
        assert context["materials_count"] == 1
        assert "[M1]" in context["materials_manifest_text"]
        assert "摘要内容" in context["materials_analysis_text"]

        no_analysis = build_material_prompt_context(analysis, include_analysis=False)
        assert no_analysis["materials_analysis_text"] == ""

    def test_relevance_map_is_filtered_to_known_materials_and_rendered(self):
        analysis = MaterialAnalysis(items=[MaterialManifestItem(material_id="M1", summary_kind="summary")])
        updated = apply_material_relevance_map(
            analysis,
            [
                MaterialRelevance(material_id="M1", relevance="direct", supported_claims=[{"claim": "Key fact"}]),
                MaterialRelevance(material_id="unknown", relevance="direct"),
            ],
        )
        context = build_material_prompt_context(updated, include_analysis=False)
        assert [entry.material_id for entry in updated.relevance_map] == ["M1"]
        assert "[M1] relevance=direct" in context["materials_relevance_text"]
        assert "claim: Key fact" in context["materials_relevance_text"]


class TestMaterialUsagePolicy:
    def test_material_first_markers_cover_british_and_american_spelling(self):
        assert is_material_first_request("Summarize the provided papers", True)
        assert is_material_first_request("Summarise the provided papers", True)
        assert not is_material_first_request("Summarise the provided papers", False)

    def test_required_mode_and_fallback_relevance_records(self):
        analysis = MaterialAnalysis(items=[
            MaterialManifestItem(material_id="M1", title="Deep Research", summary="evidence", summary_kind="summary"),
        ])

        mode = resolve_material_usage_mode("请基于我提供的 Deep Research 论文总结", True)
        records = build_fallback_material_relevance_map(analysis, "Deep Research", mode)

        assert mode == "required"
        assert records[0].material_id == "M1"
        assert records[0].relevance == "partial"
        assert records[0].supported_claims


class TestBuildMaterialEvidenceItems:
    @staticmethod
    def _analysis():
        return MaterialAnalysis(items=[
            MaterialManifestItem(material_id="M1", title="T1", url="https://e.com/1",
                                 publish_time="2025-01", content_time="2024Q4", summary="全文", summary_kind="fulltext"),
            MaterialManifestItem(material_id="M2", title="T2", summary="摘要", summary_kind="summary"),
            MaterialManifestItem(material_id="M3", title="T3", summary_kind="none"),  # url-only 无注入内容
        ])

    def test_filters_and_fields(self):
        items = build_material_evidence_items(self._analysis())
        assert [i["material_id"] for i in items] == ["M1", "M2"]
        first = items[0]
        assert first["index"] == 0  # 由合并方统一编号
        assert first["source"] == "user_material"
        assert first["is_fulltext"] is True
        assert first["original_content"] == "全文"
        assert first["passage_text"] == "全文"
        assert first["url"] == "https://e.com/1"
        assert first["publish_time"] == "2025-01"
        assert first["content_time"] == "2024Q4"

    def test_filter_by_ids_and_dict_input(self):
        items = build_material_evidence_items(self._analysis().model_dump(), ["M2"])
        assert [i["material_id"] for i in items] == ["M2"]
        assert build_material_evidence_items(None) == []
        assert build_material_evidence_items({"items": "bad"}) == []
        assert build_material_evidence_items(self._analysis(), []) == []


class TestResolveMaterialEvidence:
    def _inputs(self, **extra):
        analysis = MaterialAnalysis(items=[
            MaterialManifestItem(material_id="M1", title="T1", summary="全文", summary_kind="fulltext"),
            MaterialManifestItem(material_id="M2", title="T2", summary="摘要", summary_kind="summary"),
        ])
        return {"material_analysis": analysis.model_dump(), **extra}

    def test_declared_ids_exact_filter(self):
        resolved = resolve_material_evidence(self._inputs(use_material_ids=["M2"]))
        assert [i["material_id"] for i in resolved] == ["M2"]

    def test_material_bindings_select_declared_materials(self):
        resolved = resolve_material_evidence(self._inputs(material_bindings=[
            {"material_id": "M1", "role": "primary_evidence", "claims_to_use": "result"},
        ]))
        assert [i["material_id"] for i in resolved] == ["M1"]

    def test_no_declaration_no_flag(self):
        assert resolve_material_evidence(self._inputs()) == []
        assert resolve_material_evidence({}) == []


class TestFormatSectionMaterialBindings:
    def test_scopes_contract_to_selected_material_ids(self):
        rendered = format_section_material_bindings(
            [
                {"material_id": "M1", "role": "background", "claims_to_use": "Context only"},
                {"material_id": "M2", "role": "primary_evidence", "claims_to_use": "Outcome A"},
            ],
            ["M2"],
        )
        assert "M2" in rendered
        assert "primary_evidence" in rendered
        assert "Outcome A" in rendered
        assert "M1" not in rendered


class TestNormalizeMaterialBindings:
    def test_normalizes_without_a_known_id_allowlist(self):
        bindings = normalize_material_bindings([
            {"material_id": "M1", "role": "primary"},
            {"material_id": "M1", "claims_to_use": "duplicate"},
            {"material_id": " M2 ", "claims_to_use": "claim"},
            "invalid",
        ])

        assert bindings == [
            {"material_id": "M1", "role": "primary", "claims_to_use": ""},
            {"material_id": "M2", "role": "supporting_evidence", "claims_to_use": "claim"},
        ]

    def test_normalizes_and_filters_with_a_known_id_allowlist(self):
        bindings = normalize_material_bindings([
            {"material_id": "[M1]"},
            {"material_id": "unknown"},
        ], ["M1"])

        assert bindings == [
            {"material_id": "M1", "role": "supporting_evidence", "claims_to_use": ""},
        ]


class TestRankMaterialsByQuery:
    def test_relevance_ordering(self):
        materials = [
            UserMaterial(title="新能源汽车销量", content="2025年各品牌销量数据……"),
            UserMaterial(title="红烧肉做法", content="五花肉焯水后翻炒糖色……"),
        ]
        scores = rank_materials_by_query(materials, "新能源汽车市场分析")
        assert scores[0] > scores[1] >= 0.0
        assert all(score == 0.0 for score in rank_materials_by_query(materials, ""))

    def test_filters_irrelevant_materials_before_downstream_processing(self):
        materials = [
            UserMaterial(title="新能源汽车销量", content="新能源汽车市场数据"),
            UserMaterial(title="红烧肉做法", content="五花肉烹饪步骤"),
        ]
        kept, dropped = filter_materials_by_query(materials, "新能源汽车市场分析")
        assert [item.title for item in kept] == ["新能源汽车销量"]
        assert dropped == [{"index": 1, "reason": "irrelevant_to_query"}]

    def test_keeps_cross_language_materials_for_intent_recognition(self):
        materials = [
            UserMaterial(
                title="Deep Research Agents",
                content="Training methods and evaluation of autonomous research agents.",
            ),
        ]

        kept, dropped = filter_materials_by_query(materials, "总结深度研究智能体的训练方法")

        assert kept == materials
        assert dropped == []

    def test_material_first_preserves_same_language_zero_overlap_materials(self):
        materials = [UserMaterial(title="Cooking notes", content="recipe temperature and seasoning")]

        kept, dropped = filter_materials_by_query(
            materials,
            "summarize the provided materials",
            preserve_all=True,
        )

        assert kept == materials
        assert dropped == []


class TestSectionMaterialCoverage:
    def test_requires_direct_or_partial_relevance(self):
        analysis = MaterialAnalysis(
            items=[MaterialManifestItem(
                material_id="M1", summary="Unrelated material", summary_kind="summary",
            )],
            relevance_map=[MaterialRelevance(material_id="M1", relevance="irrelevant")],
        )

        coverage = build_section_material_coverage(
            analysis,
            [{"material_id": "M1", "claims_to_use": "A claimed conclusion"}],
        )

        assert coverage["has_bound_materials"] is True
        assert coverage["is_sufficient"] is False
        assert "query relevance not sufficient" in coverage["text"]


class TestChunkSplitting:
    def test_preserves_all_characters_for_a_sentence_larger_than_a_chunk(self):
        text = "x" * 250
        chunks = mp._split_into_chunks(text, max_tokens=2, overlap_tokens=0)

        assert "".join(chunks) == text
        assert all(len(chunk) <= 100 for chunk in chunks)

    def test_preserves_the_entire_next_unit_when_overlap_is_enabled(self):
        next_unit = "".join(chr(0x4E00 + index) for index in range(95))
        text = "A" * 80 + "\n\n" + next_unit

        chunks = mp._split_into_chunks(text, max_tokens=2, overlap_tokens=10)

        assert all(char in "".join(chunks) for char in next_unit)
        assert all(len(chunk) <= 100 for chunk in chunks)


class TestPrepareMaterialAnalysis:
    @pytest.mark.asyncio
    async def test_auto_generated_ids_do_not_collide_with_explicit_ids(self):
        analysis = await mp.prepare_material_analysis([
            {"title": "Unlabelled", "content": "first"},
            {"material_id": "M1", "title": "Explicit", "content": "second"},
        ])
        assert [item.material_id for item in analysis.items] == ["M2", "M1"]

    @pytest.mark.asyncio
    async def test_short_content_direct_fulltext_without_llm(self):
        with patch.object(mp, "llm_context"), \
             patch.object(mp.llm_utils, "ainvoke_llm_with_stats", new_callable=AsyncMock) as mock_invoke:
            analysis = await mp.prepare_material_analysis(
                [{"title": "短文", "content": "很短"}], llm_model_name="basic", query="短文",
            )
        item = analysis.items[0]
        assert item.material_id == "M1"
        assert item.summary_kind == "fulltext"
        assert item.summary == "很短"
        mock_invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_long_content_summarized_via_llm(self):
        long_content = "长" * 5000  # 约 5000 token，超过直通阈值
        with patch.object(mp, "llm_context"), \
             patch.object(mp.llm_utils, "ainvoke_llm_with_stats",
                          new_callable=AsyncMock, return_value={"content": "模拟摘要"}) as mock_invoke:
            analysis = await mp.prepare_material_analysis(
                [{"title": "论文", "content": long_content}], llm_model_name="basic",
            )
        item = analysis.items[0]
        assert item.summary_kind == "summary"
        assert item.summary == "模拟摘要"
        assert mock_invoke.await_count == 1

    @pytest.mark.asyncio
    async def test_reuses_cached_summary_by_content_hash(self):
        content = "缓存内容" * 10
        previous = [MaterialManifestItem(
            material_id="M1", content_hash=content_hash(content),
            summary="上轮摘要", summary_kind="summary",
        )]
        with patch.object(mp, "llm_context"), \
             patch.object(mp.llm_utils, "ainvoke_llm_with_stats", new_callable=AsyncMock) as mock_invoke:
            analysis = await mp.prepare_material_analysis(
                [{"title": "论文", "content": content}], llm_model_name="basic", previous_items=previous,
            )
        item = analysis.items[0]
        assert item.summary == "上轮摘要"
        assert item.summary_kind == "summary"
        mock_invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_downgrades_over_budget_to_digest(self, monkeypatch):
        monkeypatch.setattr(mp, "MATERIAL_INJECTION_BUDGET_TOKENS", 0)
        raw = [{"title": "A", "content": "内容A" * 100}, {"title": "B", "content": "内容B" * 100}]
        analysis = await mp.prepare_material_analysis(raw, llm_model_name="basic")
        assert analysis.downgraded_ids == ["M1", "M2"]
        assert all(item.summary_kind == "digest" for item in analysis.items)
        assert analysis.items[0].summary.endswith("…")  # 超过单行 digest 长度截断

    @pytest.mark.asyncio
    async def test_falls_back_to_truncated_kind_on_llm_failure(self, monkeypatch):
        """摘要 LLM 失败 → 头部截断降级，summary_kind 记为 truncated。"""
        monkeypatch.setattr(mp, "MATERIAL_DIRECT_TOKEN_LIMIT", 10)  # 强制走 LLM 摘要路径
        long_content = "长" * 200
        with patch.object(mp, "llm_context"), \
             patch.object(mp.llm_utils, "ainvoke_llm_with_stats",
                          new_callable=AsyncMock, side_effect=RuntimeError("llm down")):
            analysis = await mp.prepare_material_analysis(
                [{"title": "论文", "content": long_content}], llm_model_name="basic",
            )
        item = analysis.items[0]
        assert item.summary_kind == "truncated"
        assert item.summary == long_content  # 正文不足保留上限时全量保留
