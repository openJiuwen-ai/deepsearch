from unittest.mock import Mock, AsyncMock, patch, MagicMock

import pytest

from jiuwen_deepsearch.framework.jiuwen.agent.collector_graph.info_collector import InfoRetrievalNode, llm_context
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import RetrievalQuery
from jiuwen_deepsearch.utils.constants_utils.search_engine_constants import SearchEngine, LocalSearch


class ExposedInfoRetrievalNode(InfoRetrievalNode):
    """用于测试的类，公开受保护的方法以遵循 G.CLS.11 规则"""

    def pre_handle(self, *args, **kwargs):
        return self._pre_handle(*args, **kwargs)

    async def do_invoke(self, *args, **kwargs):
        return await self._do_invoke(*args, **kwargs)

    def post_handle(self, *args, **kwargs):
        return self._post_handle(*args, **kwargs)

    async def collector_main(self, *args, **kwargs):
        return await self._collector_main(*args, **kwargs)

    async def collector_llm(self, *args, **kwargs):
        return await self._collector_llm(*args, **kwargs)

    async def structure_result(self, *args, **kwargs):
        return await self._structure_result(*args, **kwargs)

    def process_post_process_result(self, *args, **kwargs):
        return self._process_post_process_result(*args, **kwargs)

    def prepare_collector_tool(self, *args, **kwargs):
        return self._prepare_collector_tool(*args, **kwargs)

    async def invoke_llm_with_retry(self, *args, **kwargs):
        return await self._invoke_llm_with_retry(*args, **kwargs)

    async def process_llm_response(self, *args, **kwargs):
        return await self._process_llm_response(*args, **kwargs)


class TestInfoCollectorNode:
    """测试 InfoCollectorNode"""

    def setup_method(self):
        """每个测试方法运行前都会执行"""
        self.module_path = "jiuwen_deepsearch.framework.jiuwen.agent.collector_graph.info_collector"

    @pytest.fixture
    def info_collector_node(self):
        return ExposedInfoRetrievalNode()

    @pytest.fixture
    def mock_session(self):
        session = Mock()
        session.get_global_state = Mock(side_effect=self._mock_get_global_state)
        session.update_global_state = Mock()
        return session

    def _mock_get_global_state(self, key):
        """模拟全局状态获取"""
        state_map = {
            "collector_context.search_queries": [RetrievalQuery(query="查询1"), RetrievalQuery(query="查询2")],
            "collector_context.history_queries": [],
            "collector_context.max_tool_steps": 3,
            "collector_context.section_idx": 0,
            "collector_context.step_title": "测试步骤",
            "config.info_collector_search_method": "web",
            "collector_context.doc_infos": [],
            "collector_context.gathered_info": []
        }
        return state_map.get(key)

    @pytest.fixture
    def mock_context(self):
        return Mock()

    @pytest.fixture
    def sample_web_record(self):
        """返回示例的网页搜索记录"""
        return [
            {
                "url": "http://example.com/1",
                "title": "示例标题1",
                "content": "示例内容1"
            },
            {
                "url": "http://example.com/2",
                "title": "示例标题2",
                "content": "示例内容2"
            }
        ]

    @pytest.fixture
    def sample_local_record(self):
        """返回示例的本地搜索记录"""
        return [
            {
                "url": "local://doc1",
                "title": "本地文档1",
                "content": "本地内容1"
            }
        ]

    @staticmethod
    def test_pre_handle(info_collector_node, mock_session, mock_context):
        """测试 _pre_handle 方法"""
        inputs = {}

        # 创建 mock 上下文字典：其 get 方法返回任意 mock 对象（用于 self.llm 赋值）
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            result = info_collector_node.pre_handle(inputs, mock_session, mock_context)
        finally:
            # 清理 contextvar，防止影响其他测试
            llm_context.reset(token)

        expected_state = {
            "search_queries": [RetrievalQuery(query="查询1"), RetrievalQuery(query="查询2")],
            "max_tool_steps": 3,
            "section_idx": 0,
            "step_title": "测试步骤",
            "search_method": "web",
            "web_search_engine_name": SearchEngine.PETAL.value,
            "local_search_engine_name": LocalSearch.OPENAPI.value
        }
        assert result == expected_state

        # 验证正确的全局状态被获取
        mock_session.get_global_state.assert_any_call("collector_context.search_queries")
        mock_session.get_global_state.assert_any_call("collector_context.max_tool_steps")

    @pytest.mark.asyncio
    async def test_do_invoke_success(self, info_collector_node, mock_session, mock_context):
        """测试 _do_invoke 方法成功执行"""
        inputs = {}

        # Mock _collector_main 返回结果
        mock_results = [
            {
                "doc_infos": [{"url": "http://example.com/1", "title": "标题1"}],
                "gathered_info": [{"url": "http://example.com/1", "title": "标题1"}],
                "web_record": [{"url": "http://example.com/1", "title": "标题1"}],
                "local_record": [],
                "search_query": "查询1"
            },
            {
                "doc_infos": [{"url": "http://example.com/2", "title": "标题2"}],
                "gathered_info": [{"url": "http://example.com/2", "title": "标题2"}],
                "web_record": [{"url": "http://example.com/2", "title": "标题2"}],
                "local_record": [],
                "search_query": "查询2"
            }
        ]

        # 创建 mock 的上下文字典：其 get 方法返回任意 mock 对象（因 LLM 实际未被使用）
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(info_collector_node, '_collector_main') as mock_collector:
                mock_collector.side_effect = mock_results

                result = await info_collector_node.invoke(inputs, mock_session, mock_context)

                # 验证为每个查询创建了任务
                assert mock_collector.call_count == 2

                # 验证调用了 _post_handle
                assert result == {}

                # 验证全局状态更新
                assert mock_session.update_global_state.call_count >= 2
        finally:
            # 清理 contextvar，避免影响其他测试
            llm_context.reset(token)

    @pytest.mark.asyncio
    async def test_do_invoke_empty_queries(self, info_collector_node, mock_session, mock_context):
        """测试没有查询的情况"""
        inputs = {}

        # Mock 返回空查询列表
        mock_session.get_global_state.return_value = []

        # 准备 mock 的上下文字典：其 get 方法返回任意对象（因 LLM 实际未被调用）
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 仅用于赋值 self.llm，不参与后续逻辑

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            # 不再 patch LLMWrapper.get_llm_model，因为它已不被调用
            result = await info_collector_node.do_invoke(inputs, mock_session, mock_context)

            # 验证没有创建任务
            assert result == {}
        finally:
            # 清理 contextvar，避免影响其他测试
            llm_context.reset(token)

    def test_post_handle(self, info_collector_node, mock_session, mock_context):
        """测试 _post_handle 方法"""
        inputs = {}

        # Mock 算法输出
        algorithm_output = [
            {
                "doc_infos": [{"url": "http://example.com/1", "title": "标题1"}],
                "gathered_info": [{"url": "http://example.com/1", "title": "标题1"}],
                "web_record": [{"url": "http://example.com/1"}],
                "local_record": [{"url": "local://doc1"}],
                "search_query": "查询1"
            },
            {
                "doc_infos": [{"url": "http://example.com/1", "title": "标题1"}],  # 重复数据
                "gathered_info": [{"url": "http://example.com/1", "title": "标题1"}],
                "web_record": [{"url": "http://example.com/1"}],
                "local_record": [],
                "search_query": "查询2"
            }
        ]

        with patch(f'{self.module_path}.remove_duplicate_items') as mock_remove_dup:
            mock_remove_dup.side_effect = lambda x: x[:1]  # 模拟去重，保留第一个

            result = info_collector_node.post_handle(inputs, algorithm_output, mock_session, mock_context)

            # 验证全局状态更新
            mock_session.update_global_state.assert_any_call({
                "collector_context.doc_infos": [{"url": "http://example.com/1", "title": "标题1"}]
            })

            # 验证返回结果
            assert result == {}

    @pytest.mark.asyncio
    async def test_collector_main_success(self, info_collector_node, sample_web_record, sample_local_record):
        """测试 _collector_main 方法成功执行"""
        state = {
            "section_idx": 0,
            "step_title": "测试步骤",
            "search_query": "测试查询",
            "max_tool_steps": 2
        }

        with patch.object(info_collector_node, '_collector_llm') as mock_collector_llm, \
                patch.object(info_collector_node, '_structure_result') as mock_structure, \
                patch.object(info_collector_node, '_process_post_process_result') as mock_process:
            # Mock LLM 收集过程
            mock_collector_llm.return_value = (
                state,
                {
                    "messages": [{"role": "user", "content": "test"}],
                    "web_page_search_record": sample_web_record,
                    "local_text_search_record": sample_local_record
                }
            )

            # Mock 结构化结果
            mock_structure.return_value = (
                [{"url": "http://example.com/1", "title": "标题1"}],  # doc_infos
                [{"content": "0", "scores": {"authority": 0.8, "relevance": 0.9, "answerability": 0.7}}]
                # scored_result
            )

            # Mock 后处理
            mock_process.return_value = [{"url": "http://example.com/1", "title": "标题1", "source_authority": "0.8"}]

            result = await info_collector_node.collector_main(state)

            # 验证返回结构
            assert "messages" in result
            assert "doc_infos" in result
            assert "web_record" in result
            assert "local_record" in result
            assert "search_query" in result

            # 验证调用了相关方法
            mock_collector_llm.assert_called_once()
            mock_structure.assert_called_once()
            mock_process.assert_called_once()

    @pytest.mark.asyncio
    async def test_collector_llm_success(self, info_collector_node):
        """测试 _collector_llm 方法成功执行"""
        state = {
            "section_idx": 0,
            "step_title": "测试步骤",
            "max_tool_steps": 2
        }

        agent_input = {
            "messages": [{"role": "user", "content": "初始消息"}],
            "remaining_steps": None,
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": [],
        }

        tool_list = ["tool1", "tool2"]
        tool_dict = {"tool1": Mock(), "tool2": Mock()}

        # Mock LLM 响应
        mock_response = {
            "tool_calls": [
                {"name": "tool1", "args": {"query": "test"}}
            ]
        }

        with patch.object(info_collector_node, '_invoke_llm_with_retry') as mock_llm, \
                patch.object(info_collector_node, '_process_llm_response') as mock_process:
            mock_llm.return_value = mock_response
            mock_process.return_value = {
                **agent_input,
                "web_page_search_record": [{"url": "http://example.com", "title": "测试"}]
            }

            result_state, result_agent_input = await info_collector_node.collector_llm(
                state, agent_input, tool_list, tool_dict
            )

            # 验证 LLM 被调用了 max_tool_steps 次
            assert mock_llm.call_count == 2

            # 验证处理响应被调用
            assert mock_process.call_count == 2

            # 验证返回结果
            assert result_state == state
            assert "web_page_search_record" in result_agent_input

    @pytest.mark.asyncio
    async def test_collector_llm_no_tool_calls(self, info_collector_node):
        """测试 _collector_llm 方法没有工具调用的情况"""
        state = {"max_tool_steps": 3}
        agent_input = {"messages": [], "remaining_steps": None}
        tool_list = []
        tool_dict = {}

        with patch.object(info_collector_node, '_invoke_llm_with_retry') as mock_llm:
            # Mock 没有工具调用的响应
            mock_llm.return_value = {"tool_calls": []}

            result_state, result_agent_input = await info_collector_node.collector_llm(
                state, agent_input, tool_list, tool_dict
            )

            # 验证只调用了一次 LLM（因为没有工具调用就退出了）
            assert mock_llm.call_count == 1

    @pytest.mark.asyncio
    async def test_structure_result_with_records(self, info_collector_node, sample_web_record):
        """测试 _structure_result 方法有记录的情况"""
        web_record = sample_web_record
        local_record = []
        query = "测试查询"

        with patch(f'{self.module_path}.run_doc_evaluation') as mock_eval:
            # Mock 文档评估结果
            mock_eval.return_value = [
                {
                    "content": "0",
                    "scores": {"authority": 0.8, "relevance": 0.9, "answerability": 0.7},
                    "doc_time": "2024-01-01"
                },
                {
                    "content": "1",
                    "scores": {"authority": 0.7, "relevance": 0.8, "answerability": 0.6},
                    "doc_time": "2024-01-02"
                }
            ]

            doc_infos, scored_result = await info_collector_node.structure_result(
                web_record, local_record, query
            )

            # 验证返回结果
            assert len(doc_infos) == 2
            assert len(scored_result) == 2

            # 验证文档信息结构
            for doc_info in doc_infos:
                assert "url" in doc_info
                assert "title" in doc_info
                assert "query" in doc_info
                assert doc_info["query"] == query

            # 验证调用了文档评估
            mock_eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_structure_result_empty_records(self, info_collector_node):
        """测试 _structure_result 方法空记录的情况"""
        web_record = []
        local_record = []
        query = "测试查询"

        doc_infos, scored_result = await info_collector_node.structure_result(
            web_record, local_record, query
        )

        # 验证返回空结果
        assert doc_infos == []
        assert scored_result == []

    def test_process_post_process_result_success(self, info_collector_node):
        """测试 _process_post_process_result 方法成功执行"""
        scored_result = [
            {
                "content": "0",
                "scores": {"authority": 0.8, "relevance": 0.9, "answerability": 0.7},
                "doc_time": "2024-01-01"
            },
            {
                "content": "1",
                "scores": {"authority": 0.7, "relevance": 0.8, "answerability": 0.6},
                "doc_time": "2024-01-02"
            }
        ]

        doc_infos = [
            {"url": "http://example.com/1", "title": "标题1"},
            {"url": "http://example.com/2", "title": "标题2"}
        ]

        result = info_collector_node.process_post_process_result(scored_result, doc_infos, section_idx=0)

        # 验证文档信息被正确更新
        assert len(result) == 2
        assert "source_authority" in result[0]
        assert "task_relevance" in result[0]
        assert "information_richness" in result[0]
        assert "doc_time" in result[0]

        # 验证分数被正确格式化
        assert "0.8" in result[0]["source_authority"]
        assert "0.9" in result[0]["task_relevance"]
        assert "0.7" in result[0]["information_richness"]

    def test_process_post_process_result_invalid_index(self, info_collector_node):
        """测试 _process_post_process_result 方法索引无效的情况"""
        scored_result = [
            {
                "content": "invalid",  # 无效的索引
                "scores": {"authority": 0.8, "relevance": 0.9, "answerability": 0.7}
            }
        ]

        doc_infos = [{"url": "http://example.com/1", "title": "标题1"}]

        result = info_collector_node.process_post_process_result(scored_result, doc_infos, section_idx=0)

        # 验证即使索引无效也不会崩溃
        assert len(result) == 1

    def test_prepare_collector_tool_web(self, info_collector_node):
        """测试 _prepare_collector_tool 方法 - web 搜索"""
        state = {"search_method": "web"}

        with patch(f'{self.module_path}.create_web_search_tool') as mock_web, \
                patch(f'{self.module_path}.create_local_search_tool') as mock_local:
            mock_web_tool = Mock()
            mock_web_tool.card.tool_info.return_value = "web_tool_info"
            mock_web.return_value = mock_web_tool

            mock_local_tool = Mock()
            mock_local_tool.card.tool_info.return_value = "local_tool_info"
            mock_local.return_value = mock_local_tool

            tool_list, tool_dict = info_collector_node.prepare_collector_tool(state)

            # 验证只包含 web 工具
            assert tool_list == ["web_tool_info"]
            assert "web_search_tool" in tool_dict
            assert "local_search_tool" not in tool_dict

    def test_prepare_collector_tool_local(self, info_collector_node):
        """测试 _prepare_collector_tool 方法 - local 搜索"""
        state = {"search_method": "local"}

        with patch(f'{self.module_path}.create_web_search_tool') as mock_web, \
                patch(f'{self.module_path}.create_local_search_tool') as mock_local:
            mock_web_tool = Mock()
            mock_web_tool.card.tool_info.return_value = "web_tool_info"
            mock_web.return_value = mock_web_tool

            mock_local_tool = Mock()
            mock_local_tool.card.tool_info.return_value = "local_tool_info"
            mock_local.return_value = mock_local_tool

            tool_list, tool_dict = info_collector_node.prepare_collector_tool(state)

            # 验证只包含 local 工具
            assert tool_list == ["local_tool_info"]
            assert "local_search_tool" in tool_dict
            assert "web_search_tool" not in tool_dict

    def test_prepare_collector_tool_both(self, info_collector_node):
        """测试 _prepare_collector_tool 方法 - 两种搜索"""
        state = {"search_method": "both"}

        with patch(f'{self.module_path}.create_web_search_tool') as mock_web, \
                patch(f'{self.module_path}.create_local_search_tool') as mock_local:
            mock_web_tool = Mock()
            mock_web_tool.card.tool_info.return_value = "web_tool_info"
            mock_web.return_value = mock_web_tool

            mock_local_tool = Mock()
            mock_local_tool.card.tool_info.return_value = "local_tool_info"
            mock_local.return_value = mock_local_tool

            tool_list, tool_dict = info_collector_node.prepare_collector_tool(state)

            # 验证包含两种工具
            assert len(tool_list) == 2
            assert "web_tool_info" in tool_list
            assert "local_tool_info" in tool_list
            assert "web_search_tool" in tool_dict
            assert "local_search_tool" in tool_dict

    @pytest.mark.asyncio
    async def test_invoke_llm_with_retry_success(self, info_collector_node):
        """测试 _invoke_llm_with_retry 方法成功"""
        tool_prompt = [{"role": "system", "content": "测试提示"}]
        tool_list = ["tool1"]
        state = {
            "section_idx": 0,
            "step_title": "测试步骤",
            "search_query": "测试查询"
        }

        with patch(f'{self.module_path}.ainvoke_llm_with_stats', new_callable=AsyncMock) as mock_llm_call:
            mock_llm_call.return_value = {"tool_calls": [{"name": "tool1"}]}

            response = await info_collector_node.invoke_llm_with_retry(tool_prompt, tool_list, state)

            # 验证 LLM 被调用
            mock_llm_call.assert_called_once()
            assert response == {"tool_calls": [{"name": "tool1"}]}

    @pytest.mark.asyncio
    async def test_invoke_llm_with_retry_failure(self, info_collector_node):
        """测试 _invoke_llm_with_retry 方法失败重试"""
        tool_prompt = [{"role": "system", "content": "测试提示"}]
        tool_list = ["tool1"]
        state = {
            "section_idx": 0,
            "step_title": "测试步骤",
            "search_query": "测试查询"
        }

        with patch(f'{self.module_path}.ainvoke_llm_with_stats', new_callable=AsyncMock) as mock_llm_call:
            # Mock 前两次失败，第三次成功
            mock_llm_call.side_effect = [
                Exception("第一次失败"),
                Exception("第二次失败"),
                {"tool_calls": [{"name": "tool1"}]}
            ]

            response = await info_collector_node.invoke_llm_with_retry(tool_prompt, tool_list, state)

            # 验证重试了3次
            assert mock_llm_call.call_count == 3
            assert response == {"tool_calls": [{"name": "tool1"}]}

    @pytest.mark.asyncio
    async def test_process_llm_response_with_tool_calls(self, info_collector_node):
        """测试 _process_llm_response 方法有工具调用"""
        response = {
            "tool_calls": [{"name": "web_search_tool", "args": {"query": "test"}}]
        }
        agent_input = {
            "messages": [],
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": []
        }
        tool_dict = {
            "web_search_tool": AsyncMock()
        }
        state = {
            "section_idx": 0,
            "step_title": "测试步骤",
            "search_query": "测试查询"
        }

        with patch(f'{self.module_path}.process_tool_call') as mock_process:
            mock_process.return_value = {
                **agent_input,
                "web_page_search_record": [{"url": "http://example.com"}]
            }

            result = await info_collector_node.process_llm_response(response, agent_input, tool_dict, state)

            # 验证调用了工具处理
            mock_process.assert_called_once()
            assert "web_page_search_record" in result

    @pytest.mark.asyncio
    async def test_process_llm_response_no_tool_calls(self, info_collector_node):
        """测试 _process_llm_response 方法没有工具调用"""
        response = {"tool_calls": []}
        agent_input = {
            "messages": [],
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": []
        }
        tool_dict = {}
        state = {}

        result = await info_collector_node.process_llm_response(response, agent_input, tool_dict, state)

        # 验证返回原始输入
        assert result == agent_input
