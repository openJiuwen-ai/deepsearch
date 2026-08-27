import pytest
import json
from unittest.mock import Mock, AsyncMock, patch
from openjiuwen_deepsearch.common.common_constants import MAX_SEARCH_CONTENT_LENGTH
from openjiuwen_deepsearch.algorithm.research_collector.collector_function import \
    process_tool_call, check_agent_input, handle_single_tool_call, \
    execute_tool, process_tool_result, web_search_jiuwen, \
    process_tavily_search_result, process_google_search_result, \
    process_common_search_result, process_local_search_result, \
    process_local_search_common, remove_duplicate_items, create_tool_message, \
    filter_search_results_by_exclude_domains, filter_search_results_by_exclude_urls, \
    filter_web_records_by_temporal_scope, is_title_blocked, _normalize_web_search_item

MODULE_PATH = "openjiuwen_deepsearch.algorithm.research_collector.collector_function"


def test_common_search_preserves_academic_identifiers():
    agent_input = {"web_page_search_record": [], "research_intent": {}}

    _, updated = process_common_search_result(agent_input, [{
        "title": "Paper",
        "url": "https://pubmed.ncbi.nlm.nih.gov/38202877/",
        "content": "Abstract",
        "source": "pubmed",
        "source_id": "38202877",
        "doi": "10.1000/ABC",
    }])

    record = updated["web_page_search_record"][0]
    assert record["academic_source"] == "pubmed"
    assert record["academic_source_id"] == "38202877"
    assert record["doi"] == "10.1000/ABC"

class TestProcessToolCall:
    """测试 process_tool_call 函数"""

    def setup_method(self):
        """每个测试方法运行前都会执行"""
        # 通用的测试数据
        self.sample_agent_input = {
            "messages": [],
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": []
        }

        self.sample_tool_call = {
            "id": "call_123",
            "name": "test_tool",
            "args": {"param1": "value1"}
        }

        self.sample_response = {
            "tool_calls": [self.sample_tool_call]
        }

        self.sample_tool_dict = {
            "test_tool": Mock()
        }

        self.step_info = {
            "section_idx": 1,
            "step_title": "test_step"
        }

    @pytest.mark.asyncio
    async def test_process_tool_call_success(self):
        """测试正常的工具调用处理"""
        with patch(f"{MODULE_PATH}.check_agent_input") as mock_check, \
            patch(f"{MODULE_PATH}.handle_single_tool_call", new_callable=AsyncMock) as mock_handle:
            mock_check.return_value = self.sample_agent_input
            mock_handle.return_value = {"modified": True}

            result = await process_tool_call(
                self.sample_response,
                self.sample_agent_input,
                self.sample_tool_dict,
                self.step_info
            )

            mock_handle.assert_called_once()
            assert result == {"modified": True}

    @pytest.mark.asyncio
    async def test_process_tool_call_empty_tool_calls(self):
        """测试没有工具调用的情况"""
        response = {"tool_calls": []}

        with pytest.raises(IndexError):
            await process_tool_call(
                response,
                self.sample_agent_input,
                self.sample_tool_dict,
                self.step_info
            )

    @pytest.mark.asyncio
    async def test_process_tool_call_multiple_tool_calls(self):
        """测试多个工具调用时只取最后一个"""
        multiple_tool_calls = [
            {"id": "call_1", "name": "tool1", "args": {}},
            {"id": "call_2", "name": "tool2", "args": {}},
            self.sample_tool_call
        ]

        response = {"tool_calls": multiple_tool_calls}

        with patch(f"{MODULE_PATH}.check_agent_input") as mock_check, \
                patch(f"{MODULE_PATH}.handle_single_tool_call", new_callable=AsyncMock) as mock_handle:
            mock_check.return_value = self.sample_agent_input
            mock_handle.return_value = self.sample_agent_input

            await process_tool_call(
                response,
                self.sample_agent_input,
                self.sample_tool_dict,
                self.step_info
            )

            # 验证只处理了最后一个工具调用
            call_args = mock_handle.call_args[0]
            assert call_args[0] == self.sample_tool_call


class TestCheckAgentInput:
    """测试 check_agent_input 函数"""

    def test_check_agent_input_complete(self):
        """测试完整的agent_input"""
        complete_input = {
            "messages": ["msg1"],
            "web_page_search_record": ["record1"],
            "local_text_search_record": ["record2"],
            "other_tool_record": ["record3"]
        }

        result = check_agent_input(complete_input)

        assert result == complete_input

    def test_check_agent_input_missing_keys(self):
        """测试缺失key的agent_input"""
        incomplete_input = {"messages": []}

        result = check_agent_input(incomplete_input)

        assert "web_page_search_record" in result
        assert "local_text_search_record" in result
        assert "other_tool_record" in result
        assert isinstance(result["web_page_search_record"], list)
        assert isinstance(result["local_text_search_record"], list)
        assert isinstance(result["other_tool_record"], list)

    def test_check_agent_input_empty(self):
        """测试空的agent_input"""
        result = check_agent_input({})

        necessary_keys = ["messages", "web_page_search_record", "local_text_search_record", "other_tool_record"]
        for key in necessary_keys:
            assert key in result
            assert isinstance(result[key], list)


class TestHandleSingleToolCall:
    """测试 handle_single_tool_call 函数"""

    def setup_method(self):
        self.tool_call = {
            "id": "call_123",
            "name": "test_tool",
            "args": {}
        }
        self.agent_input = {
            "messages": [],
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": []
        }
        self.tool_dict = {"test_tool": Mock()}
        self.step_info = {
            "section_idx": 1,
            "step_title": "test_step",
            "web_search_engine_name": "web_search_tool",
            "local_search_engine_name": "local_search_tool",
        }

    @pytest.mark.asyncio
    async def test_handle_single_tool_call_success(self):
        """测试成功的单个工具调用处理"""
        with patch(f"{MODULE_PATH}.execute_tool", new_callable=AsyncMock) as mock_execute, \
                patch(f"{MODULE_PATH}.create_tool_message") as mock_create:
            mock_execute.return_value = ["result1", "result2"]
            mock_create.return_value = {"modified": True}

            result = await handle_single_tool_call(
                self.tool_call,
                self.agent_input,
                self.tool_dict,
                self.step_info
            )

            mock_execute.assert_called_once_with(
                self.tool_call, self.agent_input, self.tool_dict, self.step_info
            )
            mock_create.assert_called_once_with(
                ["result1", "result2"], self.tool_call, self.agent_input
            )
            assert result == {"modified": True}


class TestExecuteTool:
    """测试 execute_tool 函数"""

    def setup_method(self):
        self.tool_call = {
            "id": "call_123",
            "name": "test_tool",
            "args": {"key": "value"}
        }
        self.agent_input = {
            "messages": [],
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": []
        }
        self.tool_dict = {"test_tool": Mock()}
        self.step_info = {
            "section_idx": 1,
            "step_title": "步骤标题",
            "web_search_engine_name": "web_engine",
            "local_search_engine_name": "local_engine",
        }


    @pytest.mark.asyncio
    async def test_execute_tool_success(self):
        """测试成功的工具执行"""
        mock_tool = AsyncMock()
        mock_tool.invoke.return_value = {"result": "success"}
        self.tool_dict["test_tool"] = mock_tool

        with patch(f"{MODULE_PATH}.process_tool_result") as mock_process:
            mock_process.return_value = ["processed_result"]

            result = await execute_tool(
                self.tool_call,
                self.agent_input,
                self.tool_dict,
                self.step_info
            )

            mock_tool.invoke.assert_called_once_with({"key": "value"})
            mock_process.assert_called_once_with(
                "test_tool", '{\n    "result": "success"\n}', self.agent_input
            )
            assert result == ["processed_result"]

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self):
        """测试工具不存在的情况"""
        self.tool_call["name"] = "non_existent_tool"
        step_info = self.step_info
        step_info["web_search_engine_name"] = "web_search_tool"

        with patch(f"{MODULE_PATH}.logger") as mock_logger:
            result = await execute_tool(
                self.tool_call,
                self.agent_input,
                self.tool_dict,
                step_info
            )

            assert result == []
            mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_execute_tool_exception(self):
        """测试工具执行异常的情况"""
        mock_tool = AsyncMock()
        mock_tool.invoke.side_effect = Exception("Tool error")
        self.tool_dict["test_tool"] = mock_tool
        step_info = self.step_info
        step_info["local_search_engine_name"] = "local_search_tool"

        with patch(f"{MODULE_PATH}.logger") as mock_logger, \
            patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_is_sensitive:
            # 测试两种情况： 敏感模式和非敏感模式

            # 情况1： 非敏感模式（会调用 logger.exception）
            mock_is_sensitive.return_value = False

            result = await execute_tool(
                self.tool_call,
                self.agent_input,
                self.tool_dict,
                self.step_info
            )

            assert result == []
            mock_logger.exception.assert_called()

            # 重置mock
            mock_logger.reset_mock()

            # 情况2： 敏感模式（会调用 logger.error）
            mock_is_sensitive.return_value = True

            result = await execute_tool(
                self.tool_call,
                self.agent_input,
                self.tool_dict,
                self.step_info
            )

            assert result == []
            mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_execute_tool_string_args(self):
        """测试参数为字符串的情况"""
        self.tool_call["args"] = '{\"key\": \"value\"}'

        mock_tool = AsyncMock()
        mock_tool.invoke.return_value = {"result": "success"}
        self.tool_dict["test_tool"] = mock_tool

        with patch(f"{MODULE_PATH}.process_tool_result") as mock_process:
            mock_process.return_value = ["processed_result"]

            await execute_tool(
                self.tool_call,
                self.agent_input,
                self.tool_dict,
                self.step_info
            )

            # 验证字符串参数被正确解析为字典
            mock_tool.invoke.assert_called_once_with({"key": "value"})


class TestProcessToolResult:
    """测试 process_tool_result 函数"""

    def setup_method(self):
        self.agent_input = {
            "messages": [],
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": []
        }

    def test_process_web_search_tool(self):
        """测试联网增强引擎工具结果处理"""
        with patch(f"{MODULE_PATH}.web_search_jiuwen") as mock_web_search:
            mock_web_search.return_value = (["result"], {"modified": True})

            result = process_tool_result(
                "web_search_tool",
                '{"search_results": []}',
                self.agent_input
            )

            mock_web_search.assert_called_once_with(
                self.agent_input, '{"search_results": []}'
            )
            assert result == ["result"]

    def test_process_local_search_tool(self):
        """测试本地搜索工具结果处理"""
        with patch(f"{MODULE_PATH}.process_local_search_result") as mock_local_search:
            mock_local_search.return_value = (["result"], {"modified": True})

            result = process_tool_result(
                "local_search_tool",
                '{"search_results": []}',
                self.agent_input
            )

            mock_local_search.assert_called_once_with(
                self.agent_input, '{"search_results": []}'
            )
            assert result == ["result"]

    def test_process_other_tool(self):
        """测试其他工具结果处理"""
        tool_content = '{"key": "value"}'

        result = process_tool_result(
            "other_tool",
            tool_content,
            self.agent_input
        )

        # 验证结果被正确解析
        expected_result = json.loads(tool_content)
        assert result == expected_result

        # 验证记录被添加到other_tool_record
        assert len(self.agent_input["other_tool_record"]) == 1
        record = self.agent_input["other_tool_record"][0]
        assert record["tool_name"] == "other_tool"
        assert record["content"] == tool_content

    def test_process_other_tool_with_runtime_api_search_payload(self):
        """测试 API 工具返回兼容搜索结构时走搜索后处理"""
        tool_content = json.dumps({
            "search_results": [
                {
                    "title": "Runtime Result",
                    "url": "https://example.com/runtime",
                    "content": "Runtime Content",
                }
            ]
        })

        with patch(f"{MODULE_PATH}.web_search_jiuwen") as mock_web_search:
            mock_web_search.return_value = (["processed"], self.agent_input)

            result = process_tool_result(
                "runtime_api_tool",
                tool_content,
                self.agent_input,
            )

        expected_payload = json.dumps({
            "search_engine": "runtime_api",
            "search_results": [
                {
                    "title": "Runtime Result",
                    "url": "https://example.com/runtime",
                    "content": "Runtime Content",
                }
            ],
        }, ensure_ascii=False)
        mock_web_search.assert_called_once_with(self.agent_input, expected_payload)
        assert result == ["processed"]
        assert self.agent_input["other_tool_record"] == []

class TestSearchResultProcessing:
    """测试各种搜索结果处理函数"""

    def setup_method(self):
        self.agent_input = {
            "web_page_search_record": [
                {"title": "Existing", "url": "http://existing.com", "content": "Existing content"}
            ],
            "local_text_search_record": []
        }

    def test_process_tavily_search_result(self):
        """测试Tavily搜索结果处理"""
        tool_content = [
            {"title": "New1", "url": "http://new1.com", "content": "Content1"},
            {"title": "New2", "url": "http://new2.com", "content": "Content2"}
        ]

        with patch(f"{MODULE_PATH}.remove_duplicate_items") as mock_remove_dup:
            mock_remove_dup.return_value = tool_content

            result, modified_input = process_tavily_search_result(
                self.agent_input, tool_content
            )

            assert result == [
                {"type": "page", "title": "New1", "url": "http://new1.com", "content": "Content1"},
                {"type": "page", "title": "New2", "url": "http://new2.com", "content": "Content2"},
            ]
            assert "web_page_search_record" in modified_input
            mock_remove_dup.assert_called_once()

    def test_process_tavily_search_result_normalizes_records(self):
        """Tavily records stored for later LLM prompts should use the search content limit."""
        tool_content = [
            {
                "title": "Tavily title",
                "url": "http://tavily.com",
                "content": "C" * (MAX_SEARCH_CONTENT_LENGTH + 1),
                "raw_content": "raw content should not be persisted",
                "score": 0.8,
            }
        ]

        result, modified_input = process_tavily_search_result(
            self.agent_input, tool_content
        )

        added_record = modified_input["web_page_search_record"][-1]
        assert result == [added_record]
        assert added_record == {
            "type": "page",
            "title": "Tavily title",
            "url": "http://tavily.com",
            "content": "C" * MAX_SEARCH_CONTENT_LENGTH,
            "score": 0.8,
        }

    def test_process_google_search_result(self):
        """测试Google搜索结果处理"""
        tool_content = [
            {
                "title": "Google Result",
                "link": "http://google.com",
                "snippet": "Snippet",
                "source_date": "2020-01-01",
                "source_date_type": "published",
            },
        ]

        result, modified_input = process_google_search_result(
            self.agent_input, tool_content
        )

        assert result == tool_content
        assert "web_page_search_record" in modified_input

    def test_process_common_search_result(self):
        """测试通用搜索结果处理"""
        tool_content = [
            {
                "title": "Common Result",
                "url": "https://common.com",
                "content": "Content",
                "source_date": "2020-01-01",
                "source_date_type": "published",
            },
        ]

        result, modified_input = process_common_search_result(
            self.agent_input, tool_content
        )

        assert result == tool_content
        assert "web_page_search_record" in modified_input

    def test_process_common_search_result_attaches_date_metadata_for_published(self):
        """common 路径开 include_date_metadata 后，带 published 的结果进记录时带 date_metadata。"""
        agent_input = {"web_page_search_record": [], "search_query": "q"}
        tool_content = [{
            "title": "arxiv paper", "url": "https://arxiv.org/abs/1",
            "content": "abs", "published": "2023-01-15T12:00:00Z",
        }]
        _, modified_input = process_common_search_result(agent_input, tool_content)
        record = modified_input["web_page_search_record"][0]
        assert record["date_metadata"]["parsed_date"] == "2023-01-15"
        assert record["date_metadata"]["field"] == "published"

    def test_process_google_search_result_attaches_date_metadata_for_source_date(self):
        """google 路径开 include_date_metadata 后，source_date 契约结果带 date_metadata。"""
        agent_input = {"web_page_search_record": [], "search_query": "q"}
        tool_content = [{
            "title": "g", "link": "https://g.com", "snippet": "s",
            "source_date": "2020-01-01", "source_date_type": "published",
        }]
        _, modified_input = process_google_search_result(agent_input, tool_content)
        record = modified_input["web_page_search_record"][0]
        assert record["date_metadata"]["parsed_date"] == "2020-01-01"
        assert record["date_metadata"]["field"] == "source_date"

    def test_process_common_search_result_no_date_field_keeps_record_without_date_metadata(self):
        """无日期字段的 common 结果仍不带 date_metadata（行为不变）。"""
        agent_input = {"web_page_search_record": [], "search_query": "q"}
        tool_content = [{"title": "x", "url": "https://x.com", "content": "c"}]
        _, modified_input = process_common_search_result(agent_input, tool_content)
        assert "date_metadata" not in modified_input["web_page_search_record"][0]

    def test_filter_search_results_by_exclude_domains(self):
        """测试按排除域名过滤搜索结果"""
        items = [
            {"title": "Keep", "url": "https://keep.com/a", "content": "keep"},
            {"title": "Drop", "url": "https://sub.blocked.com/a", "content": "drop"},
            {"title": "No Url", "content": "keep"},
        ]

        result = filter_search_results_by_exclude_domains(items, ["blocked.com"])

        assert [item.get("title") for item in result] == ["Keep", "No Url"]

    def test_process_google_search_result_filters_exclude_domains(self):
        """测试Google搜索结果按排除域名过滤"""
        agent_input = {
            "web_page_search_record": [],
            "research_intent": {"exclude_domains": ["blocked.com"]},
        }
        tool_content = [
            {"title": "Keep", "link": "http://keep.com", "snippet": "Snippet"},
            {"title": "Drop", "link": "http://blocked.com", "snippet": "Snippet"},
        ]

        result, modified_input = process_google_search_result(agent_input, tool_content)

        assert [item.get("title") for item in result] == ["Keep"]
        assert [item.get("title") for item in modified_input["web_page_search_record"]] == ["Keep"]


    def test_process_common_search_result_filters_exclude_domains(self):
        """测试通用搜索结果按排除域名过滤"""
        agent_input = {
            "web_page_search_record": [],
            "research_intent": {"exclude_domains": ["csdn.net"]},
        }
        tool_content = [
            {"title": "Keep", "url": "https://arxiv.org/abs/1234", "content": "paper"},
            {"title": "Drop", "url": "https://blog.csdn.net/article", "content": "blog"},
        ]

        result, modified_input = process_common_search_result(agent_input, tool_content)

        assert [item.get("title") for item in result] == ["Keep"]
        assert [item.get("title") for item in modified_input["web_page_search_record"]] == ["Keep"]

    def test_process_common_search_result_field_aliases_and_invalid_items(self):
        """Common search processor should normalize aliases and skip invalid rows."""
        tool_content = [
            {
                "name": "Alias title",
                "link": "https://alias.example.com",
                "raw_content": "Raw body",
            },
            "Error when run web search",
            {
                "title": "Summary title",
                "source_url": "https://summary.example.com",
                "summary": "Summary body",
            },
            {"title": "Missing URL"},
        ]

        result, modified_input = process_common_search_result(
            self.agent_input, tool_content
        )

        expected_records = [
            {
                "type": "page",
                "title": "Alias title",
                "url": "https://alias.example.com",
                "content": "Raw body",
            },
            {
                "type": "page",
                "title": "Summary title",
                "url": "https://summary.example.com",
                "content": "Summary body",
            },
        ]
        assert result == tool_content
        assert modified_input["web_page_search_record"][-2:] == expected_records

    def test_normalize_tavily_result_consumes_canonical_source_date(self):
        """Tavily 结果应消费已归一化的来源发布日期。"""
        normalized = _normalize_web_search_item({
            "title": "Dated",
            "url": "https://example.com/dated",
            "content": "body",
            "source_date": "2020-01-02",
            "source_date_type": "published",
            "publication_date": "2020-01-02T12:30:00Z",
            "date": "2021-02-03",
            "updated_at": "2022-03-04",
        }, include_date_metadata=True)

        assert normalized["date_metadata"] == {
            "field": "source_date",
            "type": "published",
            "value": "2020-01-02",
            "parsed_date": "2020-01-02",
        }

    def test_normalize_published_iso8601_attaches_date_metadata(self):
        """原生 published 字段（ISO 8601 带时分秒，arxiv 风格）应解析并附加 date_metadata。"""
        normalized = _normalize_web_search_item({
            "title": "paper", "url": "https://arxiv.org/abs/1234",
            "content": "abstract", "published": "2023-01-15T12:00:00Z",
        }, include_date_metadata=True)
        assert normalized["date_metadata"] == {
            "field": "published", "type": "published",
            "value": "2023-01-15T12:00:00Z", "parsed_date": "2023-01-15",
        }

    def test_normalize_published_pubmed_style_attaches_date_metadata(self):
        """PubMed 风格 'YYYY Mon DD' 应解析为日期。"""
        normalized = _normalize_web_search_item({
            "title": "pm", "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
            "content": "abs", "published": "2023 Jan 15",
        }, include_date_metadata=True)
        assert normalized["date_metadata"]["parsed_date"] == "2023-01-15"
        assert normalized["date_metadata"]["field"] == "published"

    def test_normalize_published_date_aliases_priority(self):
        """published > published_at > published_date 顺序取第一个非空。"""
        normalized = _normalize_web_search_item({
            "title": "x", "url": "https://x.com", "content": "c",
            "published_at": "2022-06-30", "published_date": "2020-01-01",
        }, include_date_metadata=True)
        assert normalized["date_metadata"]["field"] == "published_at"
        assert normalized["date_metadata"]["parsed_date"] == "2022-06-30"

    def test_normalize_no_date_field_no_date_metadata(self):
        normalized = _normalize_web_search_item({
            "title": "x", "url": "https://x.com", "content": "c",
        }, include_date_metadata=True)
        assert "date_metadata" not in normalized

    def test_normalize_bare_date_key_not_read(self):
        """语义含糊的裸 date 键不被读取。"""
        normalized = _normalize_web_search_item({
            "title": "x", "url": "https://x.com", "content": "c",
            "date": "2021-02-03",
        }, include_date_metadata=True)
        assert "date_metadata" not in normalized

    def test_normalize_source_date_unparseable_no_date_metadata(self):
        """source_date 存在但解析不出（乱码）时不附加 date_metadata（行为收窄）。"""
        normalized = _normalize_web_search_item({
            "title": "x", "url": "https://x.com", "content": "c",
            "source_date": "not-a-date", "source_date_type": "published",
        }, include_date_metadata=True)
        assert "date_metadata" not in normalized

    def test_normalize_source_date_type_not_published_not_read(self):
        """source_date_type 非 published 时不走 source_date 路径；无 published* 则不附加。"""
        normalized = _normalize_web_search_item({
            "title": "x", "url": "https://x.com", "content": "c",
            "source_date": "2020-01-01", "source_date_type": "updated",
        }, include_date_metadata=True)
        assert "date_metadata" not in normalized

    def test_normalize_full_text_contract_without_source_specific_logic(self):
        normalized = _normalize_web_search_item({
            "title": "Open study",
            "url": "https://scholar.example.org/papers/1",
            "content": "Abstract remains the normal content.",
            "source": "future_scholar",
            "full_text": "Complete official article text.",
            "content_type": "full_text",
            "full_text_url": "https://scholar.example.org/papers/1/full-text",
            "full_text_format": "html",
            "full_text_status": "available",
            "full_text_truncated": False,
            "skip_webpage_enrichment": True,
        })

        assert normalized["content"] == "Abstract remains the normal content."
        assert normalized["full_text"] == "Complete official article text."
        assert normalized["content_type"] == "full_text"
        assert normalized["full_text_url"].startswith("https://scholar.example.org/")
        assert normalized["full_text_format"] == "html"
        assert normalized["full_text_status"] == "available"
        assert normalized["full_text_truncated"] is False
        assert normalized["skip_webpage_enrichment"] is True

    def test_normalize_full_text_contract_does_not_coerce_string_booleans(self):
        normalized = _normalize_web_search_item({
            "title": "Open study",
            "url": "https://scholar.example.org/papers/1",
            "content": "Abstract.",
            "full_text_status": "available",
            "full_text": "Full text.",
            "full_text_truncated": "false",
            "skip_webpage_enrichment": "true",
        })

        assert normalized["full_text_truncated"] is False
        assert "skip_webpage_enrichment" not in normalized

    def test_source_date_filter_keeps_boundaries_and_unknown_but_drops_out_of_range(self):
        """来源时间过滤应包含边界、保留未知日期并整篇删除越界文档。"""
        records = [
            _normalize_web_search_item({
                "title": "Start",
                "url": "https://example.com/start",
                "content": "start body",
                "source_date": "2020-01-01",
                "source_date_type": "published",
            }, include_date_metadata=True),
            _normalize_web_search_item({
                "title": "End",
                "url": "https://example.com/end",
                "content": "end body",
                "source_date": "2022-12-31",
                "source_date_type": "published",
            }, include_date_metadata=True),
            _normalize_web_search_item({
                "title": "Old",
                "url": "https://example.com/old",
                "content": "sensitive old body",
                "source_date": "2019-12-31",
                "source_date_type": "published",
            }, include_date_metadata=True),
            _normalize_web_search_item({
                "title": "Unknown",
                "url": "https://example.com/unknown",
                "content": "unknown body",
                "updated_at": "2018-01-01",
            }),
        ]

        kept = filter_web_records_by_temporal_scope(
            records,
            {
                "constraint_type": "source_date",
                "start_date": "2020-01-01",
                "end_date": "2022-12-31",
            },
        )

        assert [item["title"] for item in kept] == ["Start", "End", "Unknown"]

    def test_content_date_scope_and_local_results_are_not_date_filtered(self):
        """内容时间不按来源日期过滤，本地结果处理也应完全保留。"""
        web_record = _normalize_web_search_item({
            "title": "Later retrospective",
            "url": "https://example.com/later",
            "content": "body",
            "source_date": "2025-01-01",
            "source_date_type": "published",
        }, include_date_metadata=True)
        kept = filter_web_records_by_temporal_scope(
            [web_record],
            {"constraint_type": "content_date", "end_date": "2020-12-31"},
        )
        local_input = {"local_text_search_record": []}
        _, local_output = process_local_search_common(local_input, [{
            "knowledge_base_id": "kb",
            "file_id": "doc",
            "title": "Local",
            "content": "local content",
            "date": "2025-01-01",
        }])

        assert kept == [web_record]
        assert len(local_output["local_text_search_record"]) == 1

    def test_process_common_result_keeps_records_without_tavily_temporal_filter(self, caplog):
        """非 Tavily 结果即使携带日期字段也不执行时间过滤。"""
        agent_input = {
            "web_page_search_record": [],
            "search_query": "policy query",
            "research_intent": {
                "source_date_scope": {
                    "constraint_type": "source_date",
                    "end_date": "2020-12-31",
                }
            },
        }
        tool_content = [
            {
                "title": "Keep", "url": "https://keep.com", "content": "keep",
                "source_date": "2020-12-31", "source_date_type": "published",
            },
            {
                "title": "Drop", "url": "https://drop.com", "content": "secret",
                "source_date": "2021-01-01", "source_date_type": "published",
            },
            {"title": "Unknown", "url": "https://unknown.com", "content": "unknown", "date": "2019-01-01"},
        ]

        tool_view, modified_input = process_common_search_result(agent_input, tool_content)

        assert [item["title"] for item in modified_input["web_page_search_record"]] == ["Keep", "Drop", "Unknown"]
        assert [item["title"] for item in tool_view] == ["Keep", "Drop", "Unknown"]
        assert all("date_metadata" not in item for item in tool_view)
        assert "source_date filter applied" not in caplog.text
        assert "secret" not in caplog.text

    def test_tavily_temporal_filter_logs_all_out_of_range_records(self, caplog):
        """Tavily 日志中的越界文档计数应覆盖整批结果。"""
        agent_input = {
            "web_page_search_record": [],
            "search_query": "old records",
            "research_intent": {
                "source_date_scope": {
                    "constraint_type": "source_date",
                    "start_date": "2020-01-01",
                }
            },
        }
        tool_content = [
            {
                "title": f"Old {index}",
                "url": f"https://example.com/{index}",
                "content": "body",
                "source_date": "2019-01-01",
                "source_date_type": "published",
            }
            for index in range(101)
        ]

        process_tavily_search_result(agent_input, tool_content)

        assert "source_date filter applied. raw=101 kept=0 filtered_out=101 date_unknown=0" in caplog.text

class TestRemoveDuplicateItems:
    """测试 remove_duplicate_items 函数"""

    def test_remove_duplicates(self):
        """测试去重功能"""
        items = [
            {"title": "Duplicate", "url": "http://same.com", "content": "Content1"},
            {"title": "Duplicate", "url": "http://same.com", "content": "Content1"},
            {"title": "Unique", "url": "http://unique.com", "content": "Content3"}
        ]

        result = remove_duplicate_items(items)

        assert len(result) == 2
        titles = [item["title"] for item in result]
        assert "Duplicate" in titles
        assert "Unique" in titles

    def test_keeps_same_title_url_with_different_content(self):
        """同一 URL/title 的不同搜索内容不应被去重删除。"""
        items = [
            {"title": "Duplicate", "url": "http://same.com", "content": "Content1"},
            {"title": "Duplicate", "url": "http://same.com", "content": "Content2"},
        ]

        result = remove_duplicate_items(items)

        assert result == items

    def test_keeps_same_title_url_with_different_source_ids(self):
        """同一 URL/title 的不同 evidence source_id 不应被去重删除。"""
        items = [
            {"title": "Duplicate", "url": "http://same.com", "source_id": "web_1_p1"},
            {"title": "Duplicate", "url": "http://same.com", "source_id": "web_1_p2"},
        ]

        result = remove_duplicate_items(items)

        assert result == items

    def test_remove_duplicates_empty(self):
        """测试空列表去重"""
        result = remove_duplicate_items([])
        assert result == []

    def test_remove_duplicates_invalid_items(self):
        """测试包含无效项目的列表"""
        items = [
            {"title": "Valid", "url": "http://valid.com", "content": "Content"},
            {"invalid": "item"},  # 缺少title或url
            "string_item"  #  不是字典
        ]

        result = remove_duplicate_items(items)

        assert len(result) == 1
        assert result[0]["title"] == "Valid"


class TestCreateToolMessage:
    """测试 create_tool_message 函数"""

    def test_create_tool_message(self):
        """测试工具消息创建"""
        results = ["result1", "result2"]
        tool_call = {
            "id": "call_123",
            "name": "test_tool",
            "function": {"name": "test_tool"}
        }
        agent_input = {
            "messages": ["existing_message"]
        }

        result = create_tool_message(results, tool_call, agent_input)

        # 验证消息被添加到agent_input
        assert len(result["messages"]) == 2
        tool_message = result["messages"][1]

        assert tool_message["role"] == "tool"
        assert tool_message["name"] == "test_tool"
        assert tool_message["tool_call_id"] == "call_123"
        assert tool_message["content"] == json.dumps(results, ensure_ascii=False)


class TestWebSearchJiuwen:
    """测试 web_search_jiuwen 函数"""

    def setup_method(self):
        self.agent_input = {
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": []
        }

    def test_web_search_jiuwen_google_engine(self):
        """测试Google联网增强引擎处理"""
        tool_content = {
            "search_engine": "google",
            "search_results": [{"title": "Google Result", "link": "http://google.com", "snippet": "Snippet"}]
        }

        with patch(f"{MODULE_PATH}.process_google_search_result") as mock_process_google:
            mock_process_google.return_value = (["processed_result"], {"modified": True})

            tool_result, agent_input = web_search_jiuwen(
                self.agent_input, json.dumps(tool_content)
            )

            mock_process_google.assert_called_once_with(
                self.agent_input, [{"title": "Google Result", "link": "http://google.com", "snippet": "Snippet"}]
            )
            assert tool_result == ["processed_result"]
            assert agent_input == {"modified": True}

    def test_web_search_jiuwen_tavily_engine(self):
        """测试Tavily联网增强引擎处理"""
        tool_content = {
            "search_engine": "tavily",
            "search_results": [{"title": "Tavily Result", "url": "http://tavily.com", "content": "Content"}]
        }

        with patch(f"{MODULE_PATH}.process_tavily_search_result") as mock_process_tavily:
            mock_process_tavily.return_value = (["processed_result"], {"modified": True})

            tool_result, agent_input = web_search_jiuwen(
                self.agent_input, json.dumps(tool_content)
            )

            mock_process_tavily.assert_called_once_with(
                self.agent_input, [{"title": "Tavily Result", "url": "http://tavily.com", "content": "Content"}]
            )
            assert tool_result == ["processed_result"]
            assert agent_input == {"modified": True}

    def test_web_search_jiuwen_common_engine(self):
        """测试通用联网增强引擎处理"""
        tool_content = {
            "search_engine": "other_engine",
            "search_results": [{"title": "Common Result", "url": "http://common.com", "content": "Content"}]
        }

        with patch(f"{MODULE_PATH}.process_common_search_result") as mock_process_common:
            mock_process_common.return_value = (["processed_result"], {"modified": True})

            tool_result, agent_input = web_search_jiuwen(
                self.agent_input, json.dumps(tool_content)
            )

            mock_process_common.assert_called_once_with(
                self.agent_input, [{"title": "Common Result", "url": "http://common.com", "content": "Content"}]
            )
            assert tool_result == ["processed_result"]
            assert agent_input == {"modified": True}


class TestProcessLocalSearchResult:
    """测试 process_local_search_result 函数"""

    def setup_method(self):
        self.agent_input = {
            "web_page_search_record": [],
            "local_text_search_record": [
                {"title": "Existing", "url": "local://existing", "content": "Existing content"}
            ],
            "other_tool_record": []
        }

    def test_process_local_search_result_common_engine(self):
        """测试通用引擎处理"""
        tool_content = json.dumps({
            "search_engine": "other_engine",
            "search_results": [
                {"file_id": "file1", "title": "Title1", "content": "Content1", "similarity": 0.8}
            ]
        })

        with patch(f"{MODULE_PATH}.process_local_search_common") as mock_process_common, \
                patch(f"{MODULE_PATH}.remove_duplicate_items") as mock_remove_dup:
            mock_agent_input = {
                "local_text_search_record": ["new_record1", "new_record2"],
                "modified": True
            }
            mock_process_common.return_value = (["result1"], mock_agent_input)
            mock_remove_dup.return_value = ["deduplicated_result"]

            tool_result, agent_input = process_local_search_result(
                self.agent_input, tool_content
            )

            mock_process_common.assert_called_once_with(
                self.agent_input, [{"file_id": "file1", "title": "Title1", "content": "Content1", "similarity": 0.8}]
            )
            mock_remove_dup.assert_called_once_with(["new_record1", "new_record2"])
            assert agent_input["local_text_search_record"] == ["deduplicated_result"]

    def test_process_local_search_result_missing_local_text_search_record(self):
        """测试返回的agent_input缺少local_text_search_record的情况"""
        tool_content = json.dumps({
            "search_engine": "openapi",
            "search_results": []
        })

        with patch(f"{MODULE_PATH}.process_local_search_common") as mock_process_common:
            mock_agent_input = {"modified": True}  # 缺少local_text_search_record
            mock_process_common.return_value = ([], mock_agent_input)

            with pytest.raises(KeyError):
                process_local_search_result(self.agent_input, tool_content)

    def test_process_local_search_result_invalid_json(self):
        """测试无效JSON输入"""
        tool_content = "invalid json string"

        with patch(f"{MODULE_PATH}.logger") as mock_logger:
            with pytest.raises(json.JSONDecodeError):
                process_local_search_result(self.agent_input, tool_content)


class TestProcessLocalSearchCommon:
    """测试 process_local_search_common 函数"""

    def setup_method(self):
        self.agent_input = {
            "local_text_search_record": [
                {"title": "Existing", "url": "local://existing", "content": "Existing content", "type": "text"}
            ]
        }

    def test_process_local_search_common_success(self):
        """测试成功的通用本地搜索处理"""
        tool_content = [
            {
                "file_id": "file_001",
                "title": "Document Title 1",
                "content": "Document content 1",
                "similarity": 0.92
            },
            {
                "file_id": "file_002",
                "title": "Document Title 2",
                "content": "Document content 2",
                "similarity": 0.88
            }
        ]

        with patch(f"{MODULE_PATH}.remove_duplicate_items") as mock_remove_dup:
            # 模拟去重后的结果
            expected_records = [
                self.agent_input["local_text_search_record"][0],
                {"type": "text", "url": "file_001", "title": "Document Title 1", "content": "Document content 1",
                 "score": 0.92},
                {"type": "text", "url": "file_002", "title": "Document Title 2", "content": "Document content 2",
                 "score": 0.88}
            ]
            mock_remove_dup.return_value = expected_records

            tool_result, agent_input = process_local_search_common(
                self.agent_input, tool_content
            )

            assert len(tool_result) == 2
            assert tool_result[0]["file_id"] == "file_001"
            assert tool_result[1]["title"] == "Document Title 2"

            # 验证记录格式正确
            records = agent_input["local_text_search_record"]
            assert len(records) == 3
            assert records[1]["type"] == "text"
            assert records[1]["url"] == "file_001"
            assert records[1]["title"] == "Document Title 1"
            assert records[1]["content"] == "Document content 1"
            assert records[1]["score"] == 0.92

    def test_process_local_search_common_prefers_title_over_document_name(self):
        """确保本地搜索记录来源标题优先使用 title 字段"""
        tool_content = [
            {
                "knowledge_base_id": "kb_001",
                "file_id": "file_003",
                "title": "Readable Source Title",
                "document_name": "doc_id_like_name_003",
                "content": "Document content 3",
                "score": 0.77,
            }
        ]

        tool_result, agent_input = process_local_search_common(self.agent_input, tool_content)

        assert len(tool_result) == 1
        records = agent_input["local_text_search_record"]
        assert len(records) == 2
        assert records[1]["title"] == "Readable Source Title"
        assert records[1]["url"] == "localdataset://result//kb_001//file_003"

    def test_process_local_search_common_exception_during_processing(self):
        """测试处理过程中出现异常的情况"""
        tool_content = [
            {
                "file_id": "file_001",
                "title": "Valid Title",
                "content": "Valid content",
                "similarity": 0.9
            }
        ]

        # 模拟 remove_duplicate_items 抛出异常
        with patch(f"{MODULE_PATH}.logger") as mock_logger, \
                patch(f"{MODULE_PATH}.remove_duplicate_items") as mock_remove_dup:
            mock_remove_dup.side_effect = Exception("Duplicate removal failed")

            tool_result, agent_input = process_local_search_common(
                self.agent_input, tool_content
            )

            # 验证异常被捕获并记录
            mock_logger.error.assert_called()
            # 原有记录应该保持不变
            assert agent_input["local_text_search_record"] == self.agent_input["local_text_search_record"]

    def test_process_local_search_common_invalid_items(self):
        """测试包含无效项目的处理"""
        tool_content = [
            {
                "file_id": "file_001",
                "title": "Valid Title",
                "content": "Valid content",
                "similarity": 0.9
            },
            {"invalid": "item"},  # 缺少必要字段的 dict，仍会被处理（字段取默认值）
            "string_item"  # 不是字典，isinstance 保护会跳过
        ]

        with patch(f"{MODULE_PATH}.remove_duplicate_items") as mock_remove_dup:
            # Valid Title 和 {"invalid": "item"} 都会被处理（string_item 被跳过）
            # {"invalid": "item"} 会被处理但字段取默认值
            expected_records = [
                self.agent_input["local_text_search_record"][0],
                {"type": "text", "url": "localdataset://result///file_001", "title": "Valid Title", "content": "Valid content", "score": 0.9},
            ]
            mock_remove_dup.return_value = expected_records

            tool_result, agent_input = process_local_search_common(
                self.agent_input, tool_content
            )

            # string_item 被 isinstance 保护跳过处理，但 tool_result 仍包含所有原始项目
            assert len(tool_result) == 3

            # 记录中包含 existing + Valid Title（{"invalid": "item"} 的字段取默认值，
            # url 为空导致 _normalize 逻辑中可能被过滤，实际取决于 remove_duplicate_items 返回）
            records = agent_input["local_text_search_record"]
            assert len(records) == len(expected_records)

    def test_process_local_search_common_partial_field(self):
        """测试部分字段缺失的情况"""
        tool_content = [
            {
                "file_id": "file_001",
                "title": "Valid Title",
                # 缺少 content 字段
                "similarity": 0.9
            },
            {
                "file_id": "file_002",
                # 缺少 title 字段
                "content": "Some content",
                "similarity": 0.8
            }
        ]

        with patch(f"{MODULE_PATH}.remove_duplicate_items") as mock_remove_dup:
            # 只有第一个项目有足够字段会被处理
            expected_records = [
                self.agent_input["local_text_search_record"][0],
                {"type": "text", "url": "file_001", "title": "Valid Title", "content": "", "score": 0.9}
            ]
            mock_remove_dup.return_value = expected_records

            tool_result, agent_input = process_local_search_common(
                self.agent_input, tool_content
            )

            # tool_result 应该包含所有原始项目
            assert len(tool_result) == 2

            # 但只有第一个项目会被添加到记录中（第二个缺少title）
            records = agent_input["local_text_search_record"]
            assert len(records) == 2
            assert records[1]["title"] == "Valid Title"
            assert records[1]["content"] == ""  # 使用默认值

    def test_process_local_search_common_empty_results(self):
        """测试空结果处理"""
        tool_result, agent_input = process_local_search_common(
            self.agent_input, []
        )

        assert tool_result == []
        # 原有记录应该保持不变
        assert len(agent_input["local_text_search_record"]) == 1
        assert agent_input["local_text_search_record"][0]["title"] == "Existing"


class TestFilterSearchResultsByExcludeUrls:
    """测试按 exclude_url 过滤搜索结果"""

    def test_filter_search_results_by_exclude_urls(self):
        """测试按排除链接过滤搜索结果"""
        items = [
            {"title": "Keep", "url": "https://keep.com/a", "content": "keep"},
            {"title": "Drop", "url": "https://www.mdpi.com/2073-445X/11/9/1529", "content": "drop"},
            {"title": "DropVariant", "url": "http://mdpi.com/2073-445x/11/9/1529/?utm=x", "content": "drop"},
            {"title": "No Url", "content": "keep"},
        ]

        result = filter_search_results_by_exclude_urls(
            items, ["https://www.mdpi.com/2073-445X/11/9/1529"])

        assert [item.get("title") for item in result] == ["Keep", "No Url"]

    def test_filter_search_results_by_exclude_urls_passthrough(self):
        """空排除列表时原样返回"""
        items = [{"title": "Keep", "url": "https://keep.com/a"}]
        assert filter_search_results_by_exclude_urls(items, []) is items

    def test_filter_search_results_by_exclude_urls_field_alias(self):
        """URL 字段别名（link/source_url）同样被识别"""
        items = [
            {"title": "DropLink", "link": "https://www.mdpi.com/2073-445X/11/9/1529"},
            {"title": "DropSource", "source_url": "https://www.mdpi.com/2073-445X/11/9/1529"},
            "not-a-dict",
        ]
        result = filter_search_results_by_exclude_urls(
            items, ["https://www.mdpi.com/2073-445X/11/9/1529"])
        assert result == ["not-a-dict"]

    def test_process_tavily_search_result_filters_exclude_urls(self):
        """测试Tavily搜索结果按排除链接过滤，同域其他文章不误伤"""
        agent_input = {
            "web_page_search_record": [],
            "research_intent": {
                "exclude_url": [
                    "https://www.mdpi.com/2073-445X/11/9/1529",
                    "https://www.mdpi.com/2410-3888/8/2/80",
                ],
            },
        }
        tool_content = [
            {"title": "Keep", "url": "https://www.mdpi.com/2073-445X/11/9/1530", "content": "Content"},
            {"title": "Drop1", "url": "https://www.mdpi.com/2073-445X/11/9/1529", "content": "Content"},
            {"title": "Drop2", "url": "https://www.mdpi.com/2410-3888/8/2/80", "content": "Content"},
        ]

        result, modified_input = process_tavily_search_result(agent_input, tool_content)

        assert [item.get("title") for item in result] == ["Keep"]
        assert [item.get("title") for item in modified_input["web_page_search_record"]] == ["Keep"]

    def test_process_common_search_result_filters_exclude_urls(self):
        """测试通用搜索结果按排除链接过滤"""
        agent_input = {
            "web_page_search_record": [],
            "research_intent": {"exclude_url": ["https://pubmed.ncbi.nlm.nih.gov/38202877/"]},
        }
        tool_content = [
            {"title": "Keep", "url": "https://arxiv.org/abs/1234", "content": "paper"},
            {"title": "Drop", "url": "https://pubmed.ncbi.nlm.nih.gov/38202877/", "content": "paper"},
        ]

        result, modified_input = process_common_search_result(agent_input, tool_content)

        assert [item.get("title") for item in result] == ["Keep"]


class TestIsTitleBlocked:
    """测试标题匹配：镜像变体命中，相近主题不误伤"""

    BLOCKED = ["Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory"]

    def test_exact_title_hit(self):
        assert is_title_blocked(
            "Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory",
            self.BLOCKED)

    def test_html_entity_hit(self):
        """标题中间的 HTML 实体差异应命中（反转义后精确相等）"""
        assert is_title_blocked(
            "Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory &#40;Review&#41;",
            ["Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory (Review)"])

    def test_suffix_mirror_hit(self):
        """镜像站后缀（| MDPI / - ProQuest / | IDEALS 形态）剥后缀后精确命中"""
        assert is_title_blocked(
            "Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory | MDPI",
            self.BLOCKED)
        assert is_title_blocked(
            "Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory - ProQuest",
            self.BLOCKED)
        assert is_title_blocked(
            "Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory | IDEALS",
            self.BLOCKED)

    def test_ideals_suffix_hit(self):
        """IDEALS 机构知识库后缀剥离后命中"""
        blocked = ["A Survey of the Story Elements of Isekai Manga"]
        assert is_title_blocked(
            "A survey of the story elements of Isekai manga | IDEALS",
            blocked)

    def test_metadata_wrapping_hit(self):
        """被禁标题在目标标题中间位置（同一论文加元数据）应命中"""
        blocked = ["A Survey of the Story Elements of Isekai Manga"]
        assert is_title_blocked(
            "[PDF] A Survey of the Story Elements of Isekai Manga Dr. Paul S. Price",
            blocked)

    def test_same_prefix_different_paper_no_hit(self):
        """同前缀但不同论文不误伤（被禁标题是候选标题的前缀）"""
        assert not is_title_blocked("Deep learning for image recognition", ["Deep learning"])
        # 被禁标题完整，候选为被禁标题+非站点后缀（不同文献的副标题扩展）不误伤
        assert not is_title_blocked(
            "Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory: A Survey",
            self.BLOCKED)

    def test_different_paper_no_hit(self):
        """主题相近的不同论文不误伤"""
        assert not is_title_blocked("A 45nm 0.5V 8T Column-Interleaved SRAM with on-Chip Reference",
                                    self.BLOCKED)
        assert not is_title_blocked("", self.BLOCKED)
        assert not is_title_blocked("Any title", [])

    def test_filter_by_exclude_titles(self):
        """URL 不命中但标题命中（PMC 变体形态）"""
        agent_input = {
            "web_page_search_record": [],
            "research_intent": {
                "exclude_url": ["https://pubmed.ncbi.nlm.nih.gov/38202877/"],
                "exclude_titles": ["Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory"],
            },
        }
        tool_content = [
            {"title": "Keep", "url": "https://example.com/other", "content": "Content"},
            {"title": "Design of High-Speed, Low-Power Sensing Circuits for Nano-Scale Embedded Memory",
             "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10780789", "content": "Content"},
        ]

        result, modified_input = process_common_search_result(agent_input, tool_content)

        assert [item.get("title") for item in result] == ["Keep"]
