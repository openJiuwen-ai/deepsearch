import pytest
import json
from unittest.mock import Mock, AsyncMock, patch
from jiuwen_deepsearch.algorithm.research_collector.collector_function import \
    process_tool_call, check_agent_input, handle_single_tool_call, \
    execute_tool, process_tool_result, web_search_jiuwen, \
    process_tavily_search_result, process_google_search_result, \
    process_common_search_result, process_local_search_result, \
    process_local_search_common, remove_duplicate_items, create_tool_message

module_path = "jiuwen_deepsearch.algorithm.research_collector.collector_function"

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
        with patch(f"{module_path}.check_agent_input") as mock_check, \
            patch(f"{module_path}.handle_single_tool_call", new_callable=AsyncMock) as mock_handle:
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

        with patch(f"{module_path}.check_agent_input") as mock_check, \
                patch(f"{module_path}.handle_single_tool_call", new_callable=AsyncMock) as mock_handle:
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
        with patch(f"{module_path}.execute_tool", new_callable=AsyncMock) as mock_execute, \
                patch(f"{module_path}.create_tool_message") as mock_create:
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

        with patch(f"{module_path}.process_tool_result") as mock_process:
            mock_process.return_value = ["processed_result"]

            result = await execute_tool(
                self.tool_call,
                self.agent_input,
                self.tool_dict,
                self.step_info
            )

            mock_tool.invoke.assert_called_once_with({"key": "value", "search_engine_name": ""})
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

        with patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.logger") as mock_logger, \
            patch(f"{module_path}.LogManager.is_sensitive") as mock_is_sensitive:
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

        with patch(f"{module_path}.process_tool_result") as mock_process:
            mock_process.return_value = ["processed_result"]

            await execute_tool(
                self.tool_call,
                self.agent_input,
                self.tool_dict,
                self.step_info
            )

            # 验证字符串参数被正确解析为字典
            mock_tool.invoke.assert_called_once_with({"key": "value", "search_engine_name": ""})


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
        """测试web搜索工具结果处理"""
        with patch(f"{module_path}.web_search_jiuwen") as mock_web_search:
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
        with patch(f"{module_path}.process_local_search_result") as mock_local_search:
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

        with patch(f"{module_path}.remove_duplicate_items") as mock_remove_dup:
            mock_remove_dup.return_value = tool_content

            result, modified_input = process_tavily_search_result(
                self.agent_input, tool_content
            )

            assert result == tool_content
            assert "web_page_search_record" in modified_input
            mock_remove_dup.assert_called_once()

    def test_process_google_search_result(self):
        """测试Google搜索结果处理"""
        tool_content = [
            {"title": "Google Result", "link": "http://google.com", "snippet": "Snippet"},
        ]

        result, modified_input = process_google_search_result(
            self.agent_input, tool_content
        )

        assert len(result) == 1
        assert result[0]["title"] == "Google Result"
        assert "web_page_search_record" in modified_input

    def test_process_common_search_result(self):
        """测试通用搜索结果处理"""
        tool_content = [
            {"title": "Common Result", "url": "https://common.com", "content": "Content"},
        ]

        result, modified_input = process_common_search_result(
            self.agent_input, tool_content
        )

        assert len(result) == 1
        assert result[0]["title"] == "Common Result"
        assert "web_page_search_record" in modified_input


class TestRemoveDuplicateItems:
    """测试 remove_duplicate_items 函数"""

    def test_remove_duplicates(self):
        """测试去重功能"""
        items = [
            {"title": "Duplicate", "url": "http://same.com", "content": "Content1"},
            {"title": "Duplicate", "url": "http://same.com", "content": "Content2"},
            {"title": "Unique", "url": "http://unique.com", "content": "Content3"}
        ]

        result = remove_duplicate_items(items)

        assert len(result) == 2
        titles = [item["title"] for item in result]
        assert "Duplicate" in titles
        assert "Unique" in titles

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
        """测试Google搜索引擎处理"""
        tool_content = {
            "search_engine": "google",
            "search_results": [{"title": "Google Result", "link": "http://google.com", "snippet": "Snippet"}]
        }

        with patch(f"{module_path}.process_google_search_result") as mock_process_google:
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
        """测试Tavily搜索引擎处理"""
        tool_content = {
            "search_engine": "tavily",
            "search_results": [{"title": "Tavily Result", "url": "http://tavily.com", "content": "Content"}]
        }

        with patch(f"{module_path}.process_tavily_search_result") as mock_process_tavily:
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
        """测试通用搜索引擎处理"""
        tool_content = {
            "search_engine": "other_engine",
            "search_results": [{"title": "Common Result", "url": "http://common.com", "content": "Content"}]
        }

        with patch(f"{module_path}.process_common_search_result") as mock_process_common:
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

        with patch(f"{module_path}.process_local_search_common") as mock_process_common, \
                patch(f"{module_path}.remove_duplicate_items") as mock_remove_dup:
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

        with patch(f"{module_path}.process_local_search_common") as mock_process_common:
            mock_agent_input = {"modified": True}  # 缺少local_text_search_record
            mock_process_common.return_value = ([], mock_agent_input)

            with pytest.raises(KeyError):
                process_local_search_result(self.agent_input, tool_content)

    def test_process_local_search_result_invalid_json(self):
        """测试无效JSON输入"""
        tool_content = "invalid json string"

        with patch(f"{module_path}.logger") as mock_logger:
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

        with patch(f"{module_path}.remove_duplicate_items") as mock_remove_dup:
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
        with patch(f"{module_path}.logger") as mock_logger, \
                patch(f"{module_path}.remove_duplicate_items") as mock_remove_dup:
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
            {"invalid": "item"},  # 缺少必要字段
            "string_item"  # 不是字典
        ]

        with patch(f"{module_path}.remove_duplicate_items") as mock_remove_dup:
            # 只有第一个有效项目会被处理
            expected_records = [
                self.agent_input["local_text_search_record"][0],
                {"type": "text", "url": "file_001", "title": "Valid Title", "content": "Valid content", "score": 0.9}
            ]
            mock_remove_dup.return_value = expected_records

            tool_result, agent_input = process_local_search_common(
                self.agent_input, tool_content
            )

            # tool_result 应该包含所有原始项目
            assert len(tool_result) == 3

            # 只有有效项目会被添加到记录中，且去重
            records = agent_input["local_text_search_record"]
            assert len(records) == 1

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

        with patch(f"{module_path}.remove_duplicate_items") as mock_remove_dup:
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
