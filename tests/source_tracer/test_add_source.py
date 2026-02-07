import logging

from jiuwen_deepsearch.algorithm.source_trace.add_source import (
    SourceReferenceProcessor,
    add_source_references,
    generate_source_datas,
    merge_source_datas,
    insert_source_info,
    extract_source_item_info,
    _remove_md_references_from_chunk,
    _merge_source_infos,
    _filter_valid_references,
    remove_trailing_spaces_and_punctuation,
    _escape_html_special_chars
)


class TestSourceReferenceProcessor:
    """Test cases for SourceReferenceProcessor class."""

    def test_init(self):
        """Test initialization of SourceReferenceProcessor."""
        preprocessed_report = "这是一个示例报告。"
        search_record = {"web_search": [{"title": "测试标题", "url": "http://test.com"}]}
        
        processor = SourceReferenceProcessor(preprocessed_report, search_record)
        
        assert processor.preprocessed_report == preprocessed_report
        assert processor.search_record == search_record
        assert processor.all_data_items == []

    def test_extract_source_info(self):
        """Test extracting source info from trace result."""
        preprocessed_report = "这是一个关于人工智能的测试句子。"
        search_record = {
            "web_search": [
                {"title": "AI研究", "url": "http://ai.com", "content": "AI很有趣"},
                {"title": "机器学习基础", "url": "http://ml.com", "content": "机器学习很重要"}
            ]
        }
        processor = SourceReferenceProcessor(preprocessed_report, search_record)
        
        trace_result = {
            "sentence": "这是一个关于人工智能的测试句子。",
            "matched_source_indices": [0],
            "source": "web_search"
        }
        
        source_info, data_items = processor.extract_source_info(trace_result)
        
        assert source_info != ""
        assert len(data_items) == 1
        assert data_items[0]["title"] == "AI研究"
        assert data_items[0]["url"] == "http://ai.com"

    def test_extract_source_info_invalid_trace_result(self):
        """Test extracting source info with invalid trace result."""
        preprocessed_report = "这是一个关于人工智能的测试句子。"
        search_record = {
            "web_search": [
                {"title": "AI研究", "url": "http://ai.com", "content": "AI很有趣"}
            ]
        }
        processor = SourceReferenceProcessor(preprocessed_report, search_record)
        
        trace_result = {
            "sentence": "这个句子不在报告中。",
            "matched_source_indices": [0],
            "source": "web_search"
        }
        
        source_info, data_items = processor.extract_source_info(trace_result)
        
        assert source_info == ""
        assert data_items == []

    def test_validate_trace_result(self):
        """Test validation of trace result."""
        preprocessed_report = "这是一个关于人工智能的测试句子。"
        search_record = {
            "web_search": [
                {"title": "AI研究", "url": "http://ai.com", "content": "AI很有趣"}
            ]
        }
        processor = SourceReferenceProcessor(preprocessed_report, search_record)
        
        # 有效的trace result
        valid_result = processor._validate_trace_result(
            "这是一个关于人工智能的测试句子。",
            [0],
            "web_search"
        )
        assert valid_result is True
        
        # 无效的trace result - 句子不在报告中
        invalid_result = processor._validate_trace_result(
            "这个句子不在报告中。",
            [0],
            "web_search"
        )
        assert invalid_result is False
        
        # 无效的trace result - 源类型不存在
        invalid_result2 = processor._validate_trace_result(
            "这是一个关于人工智能的测试句子。",
            [0],
            "nonexistent_source"
        )
        assert invalid_result2 is False


class TestAddSourceReferences:
    """Test cases for add_source_references function."""

    def test_add_source_references_basic(self):
        """Test basic functionality of adding source references."""
        preprocessed_report = "这是一个测试句子。这是另一个句子。"
        source_references = [
            {
                "chunk": "这是一个测试句子",
                "title": "测试来源",
                "url": "http://test.com"
            }
        ]

        modified_report, updated_references = add_source_references(
            preprocessed_report, source_references
        )

        assert "[source_tracer_result][测试来源](http://test.com)" in modified_report
        assert len(updated_references) == 1

    def test_add_source_references_empty_input(self):
        """Test adding source references with empty input."""
        preprocessed_report = "这是一个测试句子。"
        source_references = []

        modified_report, updated_references = add_source_references(
            preprocessed_report, source_references
        )

        assert modified_report == preprocessed_report
        assert updated_references == []

    def test_add_source_references_origin_data(self):
        """Test handling of origin data."""
        preprocessed_report = "这是一个测试句子。"
        source_references = [
            {
                "_is_origin_data": True,
                "chunk": "这是一个测试句子",
                "title": "测试来源"
            }
        ]

        modified_report, updated_references = add_source_references(
            preprocessed_report, source_references
        )

        assert len(updated_references) == 1
        assert updated_references[0]["_is_origin_data"] is True

    def test_add_source_references_sentence_not_found(self):
        """Test handling of sentences not found in report."""
        preprocessed_report = "这是一个测试句子。"
        source_references = [
            {
                "chunk": "这个句子不在报告中",
                "title": "测试来源",
                "url": "http://test.com"
            }
        ]

        modified_report, updated_references = add_source_references(
            preprocessed_report, source_references
        )

        assert modified_report == preprocessed_report  # 报告没有改变
        assert len(updated_references) == 0

    def test_add_source_references_multiple_references_same_sentence(self):
        """Test adding multiple references to the same sentence."""
        preprocessed_report = "这是一个关于人工智能的测试句子。"
        source_references = [
            {
                "chunk": "这是一个关于人工智能的测试句子",
                "title": "AI来源1",
                "url": "http://ai1.com"
            },
            {
                "chunk": "这是一个关于人工智能的测试句子",  # 同一句子
                "title": "AI来源2",
                "url": "http://ai2.com"
            }
        ]

        modified_report, updated_references = add_source_references(
            preprocessed_report, source_references
        )

        # 检查是否合并了多个引用
        assert "[AI来源1](http://ai1.com)" in modified_report
        assert "[AI来源2](http://ai2.com)" in modified_report
        assert len(updated_references) == 2


class TestRemoveMdReferencesFromChunk:
    """Test cases for _remove_md_references_from_chunk function."""

    def test_remove_md_references_from_chunk_basic(self):
        """Test basic functionality of removing MD references from chunk."""
        data_item = {
            "chunk": "这是一个测试句子 [source_tracer_result][测试](http://test.com)"
        }
        
        _remove_md_references_from_chunk(data_item)
        
        assert data_item["chunk"] == "这是一个测试句子"

    def test_remove_md_references_from_chunk_multiple_refs(self):
        """Test removing multiple MD references from chunk."""
        data_item = {
            "chunk": "这是一个测试句子 [source_tracer_result][测试1](http://test1.com) 和 [测试2](http://test2.com)"
        }

        _remove_md_references_from_chunk(data_item)

        assert data_item["chunk"] == "这是一个测试句子 和"

    def test_remove_md_references_from_chunk_no_refs(self):
        """Test handling chunk without MD references."""
        data_item = {
            "chunk": "这是一个没有引用的测试句子。"
        }

        _remove_md_references_from_chunk(data_item)

        assert data_item["chunk"] == "这是一个没有引用的测试句子。"

    def test_remove_md_references_from_chunk_no_chunk_field(self):
        """Test handling data item without chunk field."""
        data_item = {
            "title": "测试标题"
        }

        _remove_md_references_from_chunk(data_item)

        assert "title" in data_item
        assert "chunk" not in data_item

    def test_remove_md_references_from_chunk_non_string_chunk(self):
        """Test handling non-string chunk field."""
        data_item = {
            "chunk": 123
        }

        _remove_md_references_from_chunk(data_item)

        assert data_item["chunk"] == 123


class TestMergeSourceInfos:
    """Test cases for _merge_source_infos function."""
    
    def test_merge_source_infos_basic(self):
        """Test basic functionality of merging source infos."""
        ref_infos = [
            {"title": "Source 1", "url": "http://source1.com"},
            {"title": "Source 2", "url": "http://source2.com"}
        ]

        result = _merge_source_infos(ref_infos)

        assert "[source_tracer_result][Source 1](http://source1.com)" in result
        assert "[source_tracer_result][Source 2](http://source2.com)" in result

    def test_merge_source_infos_with_title_only(self):
        """Test merging source infos with title only."""
        ref_infos = [
            {"title": "Source 1", "url": ""},
            {"title": "Source 2", "url": "http://source2.com"}
        ]

        result = _merge_source_infos(ref_infos)

        assert "[source_tracer_result][Source 1](Source 1)" in result
        assert "[source_tracer_result][Source 2](http://source2.com)" in result

    def test_merge_source_infos_no_title(self):
        """Test handling source info without title."""
        ref_infos = [
            {"title": "", "url": "http://source1.com"},
            {"title": "Source 2", "url": "http://source2.com"}
        ]

        result = _merge_source_infos(ref_infos)

        # 第一个没有标题的应该被跳过
        assert "[source_tracer_result][Source 2](http://source2.com)" in result
        assert "source1.com" not in result

    def test_merge_source_infos_empty_list(self):
        """Test merging empty source infos list."""
        ref_infos = []

        result = _merge_source_infos(ref_infos)

        assert result == ""

    
class TestGenerateSourceDatas:
    """Test cases for generate_source_datas function."""
    
    def test_generate_source_datas_basic(self):
        """Test basic functionality of generating source datas."""
        preprocessed_report = "这是一个关于人工智能的测试句子。"
        search_record = {
            "web_search": [
                {"title": "AI研究", "url": "http://ai.com", "content": "AI很有趣"}
            ]
        }
        trace_results = [
            {
                "sentence": "这是一个关于人工智能的测试句子",
                "matched_source_indices": [0],
                "source": "web_search"
            }
        ]

        result = generate_source_datas(preprocessed_report, search_record, trace_results)

        assert len(result) == 1
        assert result[0]["title"] == "AI研究"
        assert result[0]["url"] == "http://ai.com"

    def test_generate_source_datas_sentence_not_found(self):
        """Test handling sentences not found in report."""
        preprocessed_report = "这是一个关于人工智能的测试句子。"
        search_record = {
            "web_search": [
                {"title": "AI研究", "url": "http://ai.com", "content": "AI很有趣"}
            ]
        }
        trace_results = [
            {
                "sentence": "这个句子不在报告中",
                "matched_source_indices": [0],
                "source": "web_search"
            }
        ]

        result = generate_source_datas(preprocessed_report, search_record, trace_results)

        assert len(result) == 0

    
class TestFilterValidReferences:
    """Test cases for _filter_valid_references function."""

    def test_filter_valid_references_basic(self):
        """Test basic functionality of filtering valid references."""
        report = "这是一个测试句子。这是另一个句子。"
        references = [
            {"chunk": "这是一个测试句子", "title": "测试来源"},
            {"chunk": "这个不在报告中", "title": "无效来源"}
        ]

        result = _filter_valid_references(report, references)

        # 无效的引用会被跳过，但不会添加到结果中
        # 但找不到的引用如果非_origin_data会被跳过，所以结果应该是1
        assert len(result) == 1
        assert result[0]["chunk"] == "这是一个测试句子"
        assert result[0]["title"] == "测试来源"

    def test_filter_valid_references_origin_data(self):
        """Test handling origin data that may not be found in report."""
        report = "这是一个测试句子。"
        references = [
            {"chunk": "这个不在报告中", "title": "无效来源", "_is_origin_data": True}
        ]

        result = _filter_valid_references(report, references)

        assert len(result) == 1
        assert result[0]["_is_origin_data"] is True

    def test_filter_valid_references_no_chunk(self):
        """Test handling references without chunk field."""
        report = "这是一个测试句子。"
        references = [
            {"title": "没有chunk字段的测试来源"}
        ]

        result = _filter_valid_references(report, references)

        assert len(result) == 0


class TestMergeSourceDatas:
    """Test cases for merge_source_datas function."""
    
    def test_merge_source_datas_basic(self):
        """Test basic functionality of merging source datas."""
        report = "这是一个测试句子。"
        datas = [
            {"chunk": "这是一个测试句子", "title": "生成的来源"}
        ]
        origin_datas = [
            {"chunk": "这是一个测试句子", "title": "原始来源", "_is_origin_data": True}
        ]

        result = merge_source_datas(report, datas, origin_datas)

        # 验证合并结果，两个数据都应保留
        assert len(result) == 2
        titles = [item["title"] for item in result]
        assert "生成的来源" in titles
        assert "原始来源" in titles
    
    def test_merge_source_datas_empty_inputs(self):
        """Test merging with empty inputs."""
        report = "这是一个测试句子。"

        result = merge_source_datas(report, [], [])

        assert result == []

    def test_merge_source_datas_one_empty(self):
        """Test merging with one empty input."""
        report = "这是一个测试句子。"
        datas = [
            {"chunk": "这是一个测试句子", "title": "生成的来源"}
        ]

        result = merge_source_datas(report, datas, [])

        assert len(result) == 1
        assert result[0]["title"] == "生成的来源"


class TestInsertSourceInfo:
    """Test cases for insert_source_info function."""
    
    def test_insert_source_info_basic(self):
        """Test basic functionality of inserting source info."""
        report = "这是一个测试句子。这是另一个句子。"
        sentence = "这是一个测试句子"
        source_info = "[source_tracer_result][测试](http://test.com)"
        
        success, modified_report = insert_source_info(report, sentence, source_info)

        assert success is True
        assert "[source_tracer_result][测试](http://test.com)" in modified_report

    def test_insert_source_info_sentence_not_found(self):
        """Test handling sentence not found in report."""
        report = "这是一个测试句子。"
        sentence = "这个句子不在报告中"
        source_info = "[source_tracer_result][测试](http://test.com)"

        success, modified_report = insert_source_info(report, sentence, source_info)

        assert success is False
        assert modified_report == report

    def test_insert_source_info_empty_inputs(self):
        """Test handling empty inputs."""
        report = ""
        sentence = "这是一个测试句子"
        source_info = "[source_tracer_result][测试](http://test.com)"

        success, modified_report = insert_source_info(report, sentence, source_info)

        assert success is False
        assert modified_report == ""

        # Test with empty sentence
        success2, modified_report2 = insert_source_info("这是一个测试句子。", "", source_info)

        assert success2 is False
        assert modified_report2 == "这是一个测试句子。"


class TestExtractSourceItemInfo:
    """Test cases for extract_source_item_info function."""

    def test_extract_source_item_info_basic(self):
        """Test basic functionality of extracting source item info."""
        source_list = [
            {"title": "测试标题", "url": "http://test.com", "content": "测试内容"}
        ]
        index = 0
        sentence = "这是一个测试句子。"

        source_info, data = extract_source_item_info(source_list, index, sentence)

        assert source_info != ""
        assert data["title"] == "测试标题"
        assert data["url"] == "http://test.com"
        assert data["content"] == "测试内容"
        assert data["chunk"] == "这是一个测试句子。"

    def test_extract_source_item_info_index_out_of_range(self):
        """Test handling index out of range."""
        source_list = [
            {"title": "测试标题", "url": "http://test.com", "content": "测试内容"}
        ]
        index = 5  # 超出范围
        sentence = "这是一个测试句子。"
        
        source_info, data = extract_source_item_info(source_list, index, sentence)

        assert source_info == ""
        assert data == {}

    def test_extract_source_item_info_missing_title(self):
        """Test handling source item without title."""
        source_list = [
            {"url": "http://test.com", "content": "测试内容"}  # 缺少title
        ]
        index = 0
        sentence = "这是一个测试句子。"
        
        source_info, data = extract_source_item_info(source_list, index, sentence)

        assert source_info == ""
        assert data == {}

    def test_extract_source_item_info_missing_content(self):
        """Test handling source item without content."""
        source_list = [
            {"title": "测试标题", "url": "http://test.com"}  # 缺少content
        ]
        index = 0
        sentence = "这是一个测试句子。"
        
        source_info, data = extract_source_item_info(source_list, index, sentence)

        assert source_info == ""
        assert data == {}

    def test_extract_source_item_info_no_url(self):
        """Test handling source item without URL."""
        source_list = [
            {"title": "测试标题", "content": "测试内容"}  # 没有URL
        ]
        index = 0
        sentence = "这是一个测试句子。"
        
        source_info, data = extract_source_item_info(source_list, index, sentence)

        assert source_info != ""
        assert data["title"] == "测试标题"
        assert data["url"] == "测试标题"  # 使用标题作为URL
        assert data["content"] == "测试内容"


class TestRemoveTrailingSpacesAndPunctuation:
    """Test cases for remove_trailing_spaces_and_punctuation function."""
    
    def test_remove_trailing_spaces_and_punctuation_basic(self):
        """Test basic functionality of removing trailing spaces and punctuation."""
        result = remove_trailing_spaces_and_punctuation("这是一个测试句子。 ")
        assert result == "这是一个测试句子"

    def test_remove_trailing_chinese_punctuation(self):
        """Test removing Chinese punctuation."""
        result = remove_trailing_spaces_and_punctuation("这是一个测试句子。")
        assert result == "这是一个测试句子"

        result = remove_trailing_spaces_and_punctuation("这是一个测试句子！")
        assert result == "这是一个测试句子"

        result = remove_trailing_spaces_and_punctuation("这是一个测试句子？")
        assert result == "这是一个测试句子"

        result = remove_trailing_spaces_and_punctuation("这是一个测试句子；")
        assert result == "这是一个测试句子"

        result = remove_trailing_spaces_and_punctuation("这是一个测试句子：")
        assert result == "这是一个测试句子"

    def test_remove_trailing_english_punctuation(self):
        """Test removing English punctuation."""
        result = remove_trailing_spaces_and_punctuation("This is a test sentence. ")
        assert result == "This is a test sentence"

        result = remove_trailing_spaces_and_punctuation("This is a test sentence!")
        assert result == "This is a test sentence"

        result = remove_trailing_spaces_and_punctuation("This is a test sentence?")
        assert result == "This is a test sentence"

        result = remove_trailing_spaces_and_punctuation("This is a test sentence;")
        assert result == "This is a test sentence"

        result = remove_trailing_spaces_and_punctuation("This is a test sentence:")
        assert result == "This is a test sentence"

    def test_remove_multiple_trailing_punctuation(self):
        """Test removing multiple trailing punctuation marks."""
        result = remove_trailing_spaces_and_punctuation("这是一个测试句子。。。 ")
        assert result == "这是一个测试句子"

        result = remove_trailing_spaces_and_punctuation("This is a test sentence!!!")
        assert result == "This is a test sentence"

    def test_remove_citation_marks(self):
        """Test removing citation marks."""
        result = remove_trailing_spaces_and_punctuation("这是一个测试句子 [citation: 123]")
        assert result == "这是一个测试句子"  # 去除citation后再次去除尾部空格

        result = remove_trailing_spaces_and_punctuation("这是一个测试句子[citation:123] ")
        assert result == "这是一个测试句子"  # 先去除尾部空格，再去除citation

        result = remove_trailing_spaces_and_punctuation("这是一个测试句子 [ citation: 456 ]")
        assert result == "这是一个测试句子"  # 去除citation后再次去除尾部空格

    def test_no_trailing_punctuation(self):
        """Test with no trailing punctuation."""
        result = remove_trailing_spaces_and_punctuation("这是一个测试句子")
        assert result == "这是一个测试句子"

    def test_empty_string(self):
        """Test with empty string."""
        result = remove_trailing_spaces_and_punctuation("")
        assert result == ""
    
    def test_none_input(self):
        """Test with None input."""
        result = remove_trailing_spaces_and_punctuation(None)
        assert result is None
    
    def test_non_string_input(self):
        """Test with non-string input."""
        result = remove_trailing_spaces_and_punctuation(123)
        assert result == 123

    def test_only_punctuation(self):
        """Test with only punctuation."""
        result = remove_trailing_spaces_and_punctuation("。！？ ；：")
        assert result == ""

    def test_mixed_punctuation_and_spaces(self):
        """Test with mixed punctuation and spaces."""
        result = remove_trailing_spaces_and_punctuation("这是一个测试句子 ！。 ")
        assert result == "这是一个测试句子"

class TestEscapeHtmlSpecialChars:
    """Test cases for _escape_html_special_chars function."""

    def test_escape_html_special_chars_basic(self):
        """Test basic functionality of escaping HTML special characters."""
        result = _escape_html_special_chars("&lt;script&gt;alert('xss')&lt;/script&gt;")
        assert result == "&amp;lt;script&amp;gt;alert(&#39;xss&#39;)&amp;lt;/script&amp;gt;"

    def test_escape_ampersand(self):
        """Test escaping ampersand character."""
        result = _escape_html_special_chars("a & b")
        assert result == "a &amp; b"

    def test_escape_less_than_greater_than(self):
        """Test escaping less than and greater than characters."""
        result = _escape_html_special_chars("5 < 10 > 3")
        assert result == "5 &lt; 10 &gt; 3"

    def test_escape_quotes(self):
        """Test escaping quote characters."""
        result = _escape_html_special_chars('He said "Hello"')
        assert result == "He said &quot;Hello&quot;"

        result = _escape_html_special_chars("It's a test")
        assert result == "It&#39;s a test"

    def test_escape_mixed_special_chars(self):
        """Test escaping mixed special characters."""
        input_text = 'He said "5 < 10 & 10 > 5" and it\'s true'
        expected = "He said &quot;5 &lt; 10 &amp; 10 &gt; 5&quot; and it&#39;s true"
        result = _escape_html_special_chars(input_text)
        assert result == expected
    
    def test_escape_empty_string(self):
        """Test with empty string."""
        result = _escape_html_special_chars("")
        assert result == ""

    def test_escape_none_input(self):
        """Test with None input."""
        result = _escape_html_special_chars(None)
        assert result == ""

    def test_escape_no_special_chars(self):
        """Test with text that has no special characters."""
        result = _escape_html_special_chars("This is a normal text")
        assert result == "This is a normal text"

    def test_escape_all_special_chars(self):
        """Test escaping all HTML special characters."""
        input_text = '& < > " \''
        expected = '&amp; &lt; &gt; &quot; &#39;'
        result = _escape_html_special_chars(input_text)
        assert result == expected
