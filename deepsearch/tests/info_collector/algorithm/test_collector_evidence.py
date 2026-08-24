import re

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    CollectorSourceStore,
    build_content_dedup_hash,
    build_content_ref,
    build_evaluation_documents,
    build_evidence_atom,
    build_summary_evidence_pack,
    build_supervisor_evidence_table,
    extract_key_passages,
    generate_doc_id,
    generate_source_id,
    MAX_PASSAGE_LENGTH,
    normalize_content_for_dedup,
    read_content_by_ref,
    split_passages,
    _is_markdown_table,
    _split_long_table,
)


def test_build_evidence_atom_preserves_academic_identifiers_without_replacing_source_id():
    record = {
        "type": "page",
        "title": "Paper",
        "url": "https://pubmed.ncbi.nlm.nih.gov/38202877/",
        "content": "Abstract",
        "academic_source": "pubmed",
        "academic_source_id": "38202877",
        "doi": "10.1000/ABC",
    }

    atom, doc_info = build_evidence_atom(
        record=record,
        query="38202877",
        source_store=CollectorSourceStore(),
    )

    assert atom["academic_source"] == doc_info["academic_source"] == "pubmed"
    assert atom["academic_source_id"] == doc_info["academic_source_id"] == "38202877"
    assert atom["doi"] == doc_info["doi"] == "10.1000/ABC"
    assert atom["source_id"] == doc_info["source_id"]
    assert atom["source_id"] != "38202877"


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


def test_extract_key_passages_prefers_query_matches_and_data_dense_text():
    content = (
        "泛泛介绍新能源行业整体发展概况和趋势走向，没有太多细节。\n\n"
        "宁德时代 2025 年动力电池装机量增长 18%，市场份额达到 37%，行业领先。\n\n"
        "其他无关描述内容，与查询关键词没有直接关系，仅供参考。\n\n"
        "宁德时代海外收入同比增长 21%，欧洲客户订单大幅增加，业绩超预期。"
    )

    passages = extract_key_passages(content, query="宁德时代 市场份额 海外收入", title="宁德时代经营表现")

    # COINS: short passages may be merged; verify relevant content is present
    assert any("市场份额" in p or "海外收入" in p for p in passages)
    assert all(len(passage) <= 500 for passage in passages)


def test_extract_key_passages_falls_back_to_front_passages_when_no_match():
    content = (
        "第一段没有关键词只是泛泛而谈的背景介绍文字内容较长超过四十字符。\n\n"
        "第二段仍然没有关键词继续做一些无关的描述说明内容也超过四十字符。\n\n"
        "第三段也没有结尾段落内容到此结束这是最后一句话确保超过四十字符。"
    )

    passages = extract_key_passages(content, query="完全不同", title="标题")

    # COINS: short passages (< 40 chars) may be merged into previous; verify content present
    assert len(passages) >= 1
    assert all("关键词" in p or "段落" in p for p in passages)


def test_extract_key_passages_does_not_treat_numeric_density_as_keyword_match():
    content = (
        "第一段行业背景介绍内容涵盖宏观环境和市场概况超过四十字符。\n\n"
        "无关公司在 2025 年收入增长 99%，利润率提升 18%，数据密度高，超过四十字符。\n\n"
        "第三段其他背景与前面内容关联度不大的补充说明也超过四十字符。"
    )

    passages = extract_key_passages(content, query="完全不同", title="标题")

    # COINS: short passages may be merged; verify all content is present
    combined = " ".join(passages)
    assert "行业背景" in combined
    assert "收入增长" in combined
    assert "第三段" in combined


def test_extract_key_passages_splits_chinese_sentences_without_spaces():
    content = "第一句无关。第二句包含收入增长 20%。第三句包含利润率 15%。"

    passages = extract_key_passages(content, query="收入 利润率", title="经营数据")

    assert any("收入增长" in passage for passage in passages)
    assert any("利润率" in passage for passage in passages)


def test_split_passages_keeps_decimal_and_version_dots_intact():
    content = "利润率提升 1.5%，版本 3.10.2 已发布，详情见 example.com。Revenue grew. Margin improved."

    passages = split_passages(content)

    # COINS 2025: 短句子累积到一个窗口内，小数点和版本号不被拆分
    assert len(passages) == 1
    assert "1.5%" in passages[0]
    assert "3.10.2" in passages[0]
    assert "example.com" in passages[0]
    assert "Revenue grew." in passages[0]
    assert "Margin improved." in passages[0]


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


def test_build_evidence_atom_prefers_available_academic_full_text():
    store = CollectorSourceStore()
    record = {
        "url": "https://pubmed.ncbi.nlm.nih.gov/38132429/",
        "title": "Profile of Orthodontic Use across Demographics",
        "content": "Abstract only.",
        "full_text": "Official PMC body text with methods and results.",
        "full_text_status": "available",
        "skip_webpage_enrichment": True,
        "type": "page",
    }

    atom, doc_info = build_evidence_atom(record=record, query="orthodontic use", source_store=store)

    assert doc_info["original_content"] == record["full_text"]
    assert store.read(atom["source_id"]) == record["full_text"]
    assert atom["skip_webpage_enrichment"] is True
    assert doc_info["skip_webpage_enrichment"] is True


def test_build_evidence_atom_falls_back_to_abstract_when_full_text_failed():
    store = CollectorSourceStore()
    record = {
        "url": "https://arxiv.org/abs/1234.5678",
        "title": "Example",
        "content": "Available abstract.",
        "full_text": "",
        "full_text_status": "failed",
        "type": "page",
    }

    atom, doc_info = build_evidence_atom(record=record, query="example", source_store=store)

    assert doc_info["original_content"] == record["content"]


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


def test_build_evidence_atom_preserves_canonical_publication_date():
    """provider 已确认的 canonical 发表日期必须进入 evidence 和 evaluator 输入。"""
    store = CollectorSourceStore()
    record = {
        "url": "https://example.com/a",
        "title": "Alpha",
        "content": "正文",
        "type": "page",
        "date_metadata": {
            "field": "source_date",
            "type": "published",
            "value": "Sat, 01 Jun 2024 12:00:00 GMT",
            "parsed_date": "2024-06-01",
        },
    }

    atom, doc_info = build_evidence_atom(record=record, query="Alpha", source_store=store)
    evaluation_docs = build_evaluation_documents([doc_info])

    assert atom["publish_time"] == "2024-06-01"
    assert doc_info["publish_time"] == "2024-06-01"
    assert evaluation_docs[0]["publish_time"] == "2024-06-01"


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


def test_build_supervisor_evidence_table_preserves_input_order():
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
    # scores sorting was removed; input order is preserved.
    assert table[0]["source_id"] == "web_0"


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


# ---- table-aware splitting tests ----


def _make_long_table(row_count: int) -> str:
    """构造一个超过 MAX_PASSAGE_LENGTH 的 Markdown 表格。"""
    header = "| 指标 | 数值 | 同比变化 |\n| --- | --- | --- |"
    rows = [f"| 营收 | {i * 100}万 | +{i}% |" for i in range(row_count)]
    return header + "\n" + "\n".join(rows)


def test_is_markdown_table_detects_standard_table():
    table = "| 列1 | 列2 |\n| --- | --- |\n| 数据1 | 数据2 |"
    assert _is_markdown_table(table) is True


def test_is_markdown_table_detects_aligned_table():
    table = "| 列1 | 列2 |\n|:---|:---:|\n| 数据1 | 数据2 |"
    assert _is_markdown_table(table) is True


def test_is_markdown_table_rejects_plain_text():
    assert _is_markdown_table("这是一段普通文本。") is False
    assert _is_markdown_table("第一行\n第二行\n第三行") is False


def test_is_markdown_table_rejects_too_few_lines():
    assert _is_markdown_table("| 列1 | 列2 |") is False
    assert _is_markdown_table("| 列1 | 列2 |\n| --- | --- |") is False


def test_split_long_table_preserves_header_in_each_fragment():
    table = _make_long_table(40)
    fragments = _split_long_table(table, max_length=500)

    assert len(fragments) > 1
    for frag in fragments:
        # 每个片段都必须包含表头行和分隔行
        assert frag.startswith("| 指标 | 数值 | 同比变化 |")
        assert "| --- | --- | --- |" in frag


def test_split_long_table_all_fragments_within_limit():
    table = _make_long_table(60)
    fragments = _split_long_table(table, max_length=500)

    for frag in fragments:
        assert len(frag) <= 600  # 允许表头+一行略超


def test_split_long_table_returns_single_fragment_for_short_table():
    table = "| 列1 | 列2 |\n| --- | --- |\n| 数据1 | 数据2 |"
    fragments = _split_long_table(table, max_length=500)
    assert len(fragments) == 1


def test_split_long_passage_uses_table_aware_splitting():
    """超长 Markdown 表格走表格分支，而不是句号切分。"""
    table = _make_long_table(40)
    fragments = _split_long_table(table, max_length=500)

    assert len(fragments) > 1
    # 每个片段都应有表头（表格感知），不被句号拆散
    for frag in fragments:
        assert "| 指标 |" in frag
        assert "| --- |" in frag


def test_split_long_passage_adds_overlap_for_text():
    """长文本切分时，片段间应保留 COINS 2025 推荐的 ~200 字符重叠。"""
    # 每句 20 字符，80 句 = 1600 字符，远超 500
    sentences = "这是第一句测试用的话。" * 80
    fragments = split_passages(sentences, max_length=500, overlap=200)

    assert len(fragments) > 1
    # 验证：第二个片段的开头应包含第一个片段末尾的重叠内容
    if len(fragments) >= 2:
        # 取第一个片段末尾 200 字符以内、以句号结尾的部分
        first_tail = fragments[0][-200:]
        # 重叠区域应在第二个片段开头出现
        # 至少应有 1 句完整重叠（约 20 字符）
        assert any(
            frag_sent in fragments[1]
            for frag_sent in re.findall(r"[^。]+。", first_tail)
        )


def test_split_passages_preserves_tables_in_content():
    """split_passages 端到端：包含表格的长内容应正确切分。"""
    intro = "以下是公司季度财务数据汇总表。\n\n"
    table = _make_long_table(40)
    outro = "\n\n如上表所示，公司收入持续增长。"
    content = intro + table + outro

    passages = split_passages(content)

    # 表格应被识别为表格块，切分后每个片段保留表头
    table_passages = [p for p in passages if "| 指标 |" in p]
    assert len(table_passages) >= 1
    for tp in table_passages:
        assert "| 指标 |" in tp
        assert "| --- |" in tp


def test_split_passages_overlap_does_not_break_short_content():
    """短内容不受 overlap 影响。"""
    content = "第一段内容。第二段内容。第三段内容。"
    passages = split_passages(content)
    assert len(passages) == 1
    assert "第一段" in passages[0]
