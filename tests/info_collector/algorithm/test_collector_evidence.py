from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    CollectorSourceStore,
    build_content_dedup_hash,
    build_content_ref,
    build_evaluation_documents,
    build_evidence_atom,
    build_legacy_doc_info_view,
    build_legacy_doc_infos_view,
    build_summary_evidence_pack,
    build_supervisor_evidence_table,
    extract_key_passages,
    generate_doc_id,
    generate_source_id,
    hydrate_legacy_doc_info_fields,
    MAX_PASSAGE_LENGTH,
    normalize_content_for_dedup,
    normalize_scores,
    read_content_by_ref,
    split_passages,
)


def test_generate_doc_id_is_stable_for_same_web_source():
    first = generate_doc_id(url="https://example.com/a?utm_source=x", title="Alpha", source_type="web")
    second = generate_doc_id(url="https://example.com/a?utm_source=y", title="Alpha", source_type="web")

    assert first == second
    assert first.startswith("web_")


def test_generate_doc_id_sorts_remaining_query_parameters():
    first = generate_doc_id(url="https://example.com/a?b=2&a=1", title="Alpha", source_type="web")
    second = generate_doc_id(url="https://example.com/a?a=1&b=2", title="Alpha", source_type="web")

    assert first == second


def test_generate_doc_id_ignores_tracking_query_case_insensitively():
    first = generate_doc_id(url="https://example.com/a?UTM_SOURCE=x&id=1", title="Alpha", source_type="web")
    second = generate_doc_id(url="https://example.com/a?id=1", title="Alpha", source_type="web")

    assert first == second


def test_generate_doc_id_normalizes_root_url_path():
    first = generate_doc_id(url="https://example.com", title="Alpha", source_type="web")
    second = generate_doc_id(url="https://example.com/", title="Alpha", source_type="web")

    assert first == second


def test_generate_doc_id_uses_local_file_identity():
    doc_id = generate_doc_id(
        url="localdataset://result//kb-1//file-9",
        title="Ignored Title",
        source_type="local",
    )

    assert doc_id.startswith("local_")
    assert "kb-1" not in doc_id
    assert "file-9" not in doc_id


def test_source_id_defaults_to_doc_id_in_phase_one():
    doc_id = "web_123"

    assert generate_source_id(doc_id=doc_id) == doc_id


def test_source_id_distinguishes_evidence_content_under_same_doc_id():
    """同一原文文档下的不同证据内容应生成不同 source_id。"""
    doc_id = "web_123"

    first = generate_source_id(doc_id=doc_id, content="第一段证据")
    second = generate_source_id(doc_id=doc_id, content="第二段证据")

    assert first != second
    assert first.startswith(f"{doc_id}_p")
    assert second.startswith(f"{doc_id}_p")


def test_source_id_uses_normalized_content_for_hashing():
    doc_id = "web_123"

    first = generate_source_id(doc_id=doc_id, content="Ａ  B\r\nC")
    second = generate_source_id(doc_id=doc_id, content="A B C")

    assert normalize_content_for_dedup("Ａ  B\r\nC") == "A B C"
    assert build_content_dedup_hash("Ａ  B\r\nC") == build_content_dedup_hash("A B C")
    assert first == second


def test_source_store_round_trip_with_content_ref():
    store = CollectorSourceStore()
    doc_id = "web_123"
    store.write(doc_id, "完整正文")
    content_ref = build_content_ref(doc_id=doc_id, stored=True)

    assert read_content_by_ref(content_ref, store, legacy_content="fallback") == "完整正文"


def test_source_store_preserves_first_content_on_duplicate_source_id(caplog):
    store = CollectorSourceStore()

    assert store.write("web_123", "第一版正文") is True
    assert store.write("web_123", "第二版正文") is True

    assert store.read("web_123") == "第一版正文"
    assert "source_store source_id conflict" in caplog.text


def test_source_store_from_dict_handles_none_and_invalid_input():
    """source_store 从无效 session 值恢复时应返回空 store。"""
    assert CollectorSourceStore.from_dict(None).to_dict() == {}
    assert CollectorSourceStore.from_dict(["invalid"]).to_dict() == {}


def test_content_ref_falls_back_to_legacy_doc_infos_when_store_missing(caplog):
    store = CollectorSourceStore()
    content_ref = build_content_ref(doc_id="web_missing", stored=True)

    assert read_content_by_ref(content_ref, store, legacy_content="兼容正文") == "兼容正文"
    assert "content_ref missing in source_store" in caplog.text


def test_content_ref_legacy_doc_infos_returns_legacy_content_directly(caplog):
    """legacy_doc_infos 引用不应尝试读取 source_store，直接返回兼容正文。"""
    store = CollectorSourceStore()
    content_ref = build_content_ref(doc_id="web_legacy", stored=False)

    assert read_content_by_ref(content_ref, store, legacy_content="兼容正文") == "兼容正文"
    assert "content_ref missing in source_store" not in caplog.text


def test_build_legacy_doc_info_view_returns_old_doc_info_shape():
    """旧报告链路视图只应包含未适配节点需要的 legacy 字段。"""
    doc_info = {
        "doc_id": "web_1",
        "source_id": "web_1_p123",
        "title": "标题",
        "url": "https://example.com",
        "source": "example.com",
        "publish_time": "2025-05",
        "doc_time": "",
        "query": "查询",
        "key_passages": ["关键段落"],
        "scores": {"authority": 8, "relevance": 9, "answerability": 7, "data_density": 6},
        "content_ref": {"type": "source_store", "source_id": "web_1_p123"},
        "original_content": "原文",
        "source_authority": "权威性说明",
        "task_relevance": "相关性说明",
        "information_richness": "丰富度说明",
        "data_density": "数据密度说明",
    }

    legacy_view = build_legacy_doc_info_view(doc_info)

    assert legacy_view == {
        "doc_time": "2025-05",
        "source_authority": "权威性说明",
        "task_relevance": "相关性说明",
        "original_content": "原文",
        "url": "https://example.com",
        "information_richness": "丰富度说明",
        "data_density": "数据密度说明",
        "title": "标题",
        "query": "查询",
    }


def test_build_legacy_doc_infos_view_does_not_mutate_source_doc_infos():
    """批量转换旧报告链路视图时不应修改原始 doc_infos。"""
    doc_infos = [{"title": "标题", "url": "https://example.com", "doc_id": "web_1"}]

    legacy_view = build_legacy_doc_infos_view(doc_infos)

    assert legacy_view == [
        {
            "doc_time": "",
            "source_authority": "",
            "task_relevance": "",
            "original_content": "",
            "url": "https://example.com",
            "information_richness": "",
            "data_density": "",
            "title": "标题",
            "query": "",
        }
    ]
    assert doc_infos == [{"title": "标题", "url": "https://example.com", "doc_id": "web_1"}]


def test_extract_key_passages_prefers_query_matches_and_data_dense_text():
    content = (
        "泛泛介绍新能源行业。\n"
        "宁德时代 2025 年动力电池装机量增长 18%，市场份额达到 37%。\n"
        "其他无关描述。\n"
        "宁德时代海外收入同比增长 21%，欧洲客户订单增加。"
    )

    passages = extract_key_passages(content, query="宁德时代 市场份额 海外收入", title="宁德时代经营表现")

    assert len(passages) == 2
    assert "市场份额" in passages[0] or "海外收入" in passages[0]
    assert all(len(passage) <= 500 for passage in passages)


def test_extract_key_passages_falls_back_to_front_passages_when_no_match():
    content = "第一段没有关键词。\n第二段仍然没有关键词。\n第三段也没有。"

    passages = extract_key_passages(content, query="完全不同", title="标题")

    assert passages == ["第一段没有关键词。", "第二段仍然没有关键词。", "第三段也没有。"]


def test_extract_key_passages_does_not_treat_numeric_density_as_keyword_match():
    content = (
        "第一段行业背景。\n"
        "无关公司在 2025 年收入增长 99%，利润率提升 18%。\n"
        "第三段其他背景。"
    )

    passages = extract_key_passages(content, query="完全不同", title="标题")

    assert passages == ["第一段行业背景。", "无关公司在 2025 年收入增长 99%，利润率提升 18%。", "第三段其他背景。"]


def test_extract_key_passages_splits_chinese_sentences_without_spaces():
    content = "第一句无关。第二句包含收入增长 20%。第三句包含利润率 15%。"

    passages = extract_key_passages(content, query="收入 利润率", title="经营数据")

    assert any("收入增长" in passage for passage in passages)
    assert any("利润率" in passage for passage in passages)


def test_split_passages_keeps_decimal_and_version_dots_intact():
    content = "利润率提升 1.5%，版本 3.10.2 已发布，详情见 example.com。Revenue grew. Margin improved."

    passages = split_passages(content)

    assert passages == [
        "利润率提升 1.5%，版本 3.10.2 已发布，详情见 example.com。",
        "Revenue grew.",
        "Margin improved.",
    ]


def test_build_evidence_atom_excludes_original_content_from_atom():
    store = CollectorSourceStore()
    record = {
        "url": "https://example.com/a",
        "title": "Alpha",
        "content": "Alpha 在 2025 年收入增长 10%。第二段。",
        "type": "page",
    }

    atom, doc_info = build_evidence_atom(record=record, query="Alpha 收入", source_store=store)

    assert atom["doc_id"] == doc_info["doc_id"]
    assert atom["source_id"] == doc_info["source_id"]
    assert atom["content_ref"]["type"] == "source_store"
    assert atom["content_ref"]["source_id"] == atom["source_id"]
    assert "original_content" not in atom
    assert doc_info["original_content"] == record["content"]
    assert store.read(atom["source_id"]) == record["content"]


def test_build_evidence_atom_keeps_distinct_content_for_same_doc_id():
    """同 URL/title 的不同 content 应保留为同 doc_id 下的不同 evidence。"""
    store = CollectorSourceStore()
    first_record = {
        "url": "https://example.com/a",
        "title": "Alpha",
        "content": "Alpha 第一段收入增长 10%。",
        "type": "page",
    }
    second_record = {
        "url": "https://example.com/a",
        "title": "Alpha",
        "content": "Alpha 第二段利润率提升 5%。",
        "type": "page",
    }

    _, first_doc = build_evidence_atom(record=first_record, query="Alpha 收入", source_store=store)
    _, second_doc = build_evidence_atom(record=second_record, query="Alpha 利润率", source_store=store)

    assert first_doc["doc_id"] == second_doc["doc_id"]
    assert first_doc["source_id"] != second_doc["source_id"]
    assert first_doc["content_ref"]["source_id"] == first_doc["source_id"]
    assert second_doc["content_ref"]["source_id"] == second_doc["source_id"]
    assert read_content_by_ref(first_doc["content_ref"], store) == first_record["content"]
    assert read_content_by_ref(second_doc["content_ref"], store) == second_record["content"]


def test_build_evidence_atom_truncates_legacy_content_to_collector_limit():
    from openjiuwen_deepsearch.common.common_constants import MAX_COLLECTOR_DOC_CONTENT_LENGTH

    store = CollectorSourceStore()
    record = {
        "url": "https://example.com/large",
        "title": "Large",
        "content": "A" * (MAX_COLLECTOR_DOC_CONTENT_LENGTH + 1),
        "type": "page",
    }

    atom, doc_info = build_evidence_atom(record=record, query="large", source_store=store)

    assert len(doc_info["original_content"]) == MAX_COLLECTOR_DOC_CONTENT_LENGTH
    assert len(store.read(atom["source_id"])) == MAX_COLLECTOR_DOC_CONTENT_LENGTH


def test_build_evidence_atom_logs_when_source_store_write_fails(monkeypatch, caplog):
    store = CollectorSourceStore()
    monkeypatch.setattr(store, "write", lambda doc_id, content: False)
    record = {"url": "https://example.com/a", "title": "Alpha", "content": "正文", "type": "page"}

    atom, doc_info = build_evidence_atom(record=record, query="Alpha", source_store=store)

    assert atom["content_ref"]["type"] == "legacy_doc_infos"
    assert doc_info["content_ref"]["type"] == "legacy_doc_infos"
    assert "failed to write source_store" in caplog.text


def test_build_prompt_views_never_include_original_content():
    doc_infos = [
        {
            "doc_id": "web_1",
            "source_id": "web_1",
            "title": "Alpha",
            "url": "https://example.com/a",
            "source": "example.com",
            "publish_time": "2025 1月",
            "snippet": "不应进入 compact view 的 snippet",
            "key_passages": ["关键片段"],
            "summary": "不应进入 compact view 的 summary",
            "scores": {"relevance": 8, "answerability": 7, "authority": 6, "data_density": 5},
            "original_content": "很长的正文",
        }
    ]

    evaluation_docs = build_evaluation_documents(doc_infos)
    supervisor_table = build_supervisor_evidence_table(doc_infos)
    summary_pack = build_summary_evidence_pack(doc_infos)

    assert "original_content" not in str(evaluation_docs)
    assert "original_content" not in str(supervisor_table)
    assert "original_content" not in str(summary_pack)
    assert "snippet" not in evaluation_docs[0]
    assert "summary" not in evaluation_docs[0]
    assert "snippet" not in supervisor_table[0]
    assert "summary" not in supervisor_table[0]
    assert "snippet" not in summary_pack["sources"][0]
    assert "summary" not in summary_pack["sources"][0]
    assert evaluation_docs[0]["key_passages"] == ["关键片段"]
    assert supervisor_table[0]["source_id"] == "web_1"
    assert summary_pack["sources"][0]["source_id"] == "web_1"


def test_build_supervisor_evidence_table_sorts_all_items():
    doc_infos = [
        {
            "doc_id": f"web_{idx}",
            "source_id": f"web_{idx}",
            "title": f"Doc {idx}",
            "scores": {"relevance": idx, "answerability": idx, "authority": 1, "data_density": idx},
            "key_passages": ["关键片段"],
        }
        for idx in range(30)
    ]

    table = build_supervisor_evidence_table(doc_infos)

    assert len(table) == 30
    assert table[0]["source_id"] == "web_29"


def test_build_evidence_views_sort_all_items():
    doc_infos = [
        {
            "doc_id": f"web_{idx}",
            "source_id": f"web_{idx}",
            "title": f"Doc {idx}",
            "scores": {"relevance": 30 - idx, "answerability": 30 - idx, "authority": 1, "data_density": 1},
            "key_passages": ["P" * 500, "Q" * 500],
        }
        for idx in range(30)
    ]

    table = build_supervisor_evidence_table(doc_infos)
    pack = build_summary_evidence_pack(doc_infos)

    assert len(table) == 30
    assert len(pack["sources"]) == 30
    assert table[0]["source_id"] == "web_0"
    assert pack["sources"][0]["source_id"] == "web_0"


def test_build_supervisor_evidence_table_truncates_single_oversized_item():
    doc_infos = [
        {
            "doc_id": "web_big",
            "source_id": "web_big",
            "title": "T" * 1000,
            "source": "S" * 1000,
            "key_passages": ["P" * 5000, "Q" * 5000],
            "scores": {"relevance": 10, "answerability": 10, "authority": 10, "data_density": 10},
        }
    ]

    table = build_supervisor_evidence_table(doc_infos)

    assert len(table) == 1
    assert len(table[0]["title"]) == 120
    assert "summary" not in table[0]
    assert all(len(passage) == MAX_PASSAGE_LENGTH for passage in table[0]["key_passages"])
    assert len(str(table)) < 2500


def test_normalize_scores_accepts_missing_and_numeric_values():
    scores = normalize_scores({"authority": "8.5", "relevance": 9, "answerability": None})

    assert scores == {
        "authority": 8.5,
        "relevance": 9.0,
        "answerability": None,
        "data_density": None,
    }


def test_hydrate_legacy_doc_info_fields_keeps_only_downstream_fields():
    doc_info = {
        "scores": {"authority": 8.0, "relevance": 9.0, "answerability": 7.5, "data_density": 6.0},
        "publish_time": "2025 5月",
    }

    hydrated = hydrate_legacy_doc_info_fields(doc_info)

    assert hydrated["source_authority"] == "该篇文章的信息来源权威性和可信度得分：8.0"
    assert hydrated["task_relevance"] == "该篇文章的内容与当前任务的相关性得分：9.0"
    assert hydrated["information_richness"] == "该篇文章的信息丰富程度与可答性得分：7.5"
    assert hydrated["data_density"] == "该篇文章的数据丰富和密集程度得分：6.0"
    assert hydrated["doc_time"] == "2025 5月"
    assert "_legacy_compatibility_fields" not in hydrated
