from unittest.mock import Mock, AsyncMock, patch, MagicMock

import pytest
from openjiuwen.core.workflow.base import Workflow

from jiuwen_deepsearch.framework.jiuwen.agent.collector_graph.graph_builder import SearchQueryList, Reflection, Summary, \
    CollectorContext, StartNode, GenerateQueryNode, SupervisorNode, SummaryNode, \
    ProgrammerNode, GraphEndNode, build_info_collector_sub_graph, get_research_record, llm_context
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import RetrievalQuery

module_path = "jiuwen_deepsearch.framework.jiuwen.agent.collector_graph.graph_builder"


class TestSearchQueryList:
    """测试 SearchQueryList 数据模型"""

    def test_search_query_list_creation(self):
        """测试 SearchQueryList 创建"""
        query_list = SearchQueryList(
            queries=["test query 1", "test query 2"],
            description="测试查询描述"
        )

        assert query_list.queries == ["test query 1", "test query 2"]
        assert query_list.description == "测试查询描述"


class TestReflection:
    """测试 Reflection 数据模型"""

    def test_reflection_creation(self):
        """测试 Reflection 创建"""
        reflection = Reflection(
            is_sufficient=True,
            knowledge_gap="需要更多信息",
            next_queries=["follow up query 1", "follow up query 2"]
        )

        assert reflection.is_sufficient is True
        assert reflection.knowledge_gap == "需要更多信息"
        assert reflection.next_queries == ["follow up query 1", "follow up query 2"]


class TestSummary:
    """测试 Summary 数据模型"""

    def test_summary_creation(self):
        """测试 Summary 创建"""
        summary = Summary(
            need_programmer=True,
            programmer_task="编写数据处理脚本",
            info_summary="收集到的信息总结",
            evaluation=""
        )

        assert summary.need_programmer is True
        assert summary.programmer_task == "编写数据处理脚本"
        assert summary.info_summary == "收集到的信息总结"


class TestResearchRecord:
    """测试研究记录获取函数"""

    def test_get_research_record_single_message(self):
        """测试单条消息的研究记录获取"""
        messages = [{"content": "用户查询内容"}]

        result = get_research_record(messages)

        assert result == "用户查询内容"

    def test_get_research_record_multiple_message(self):
        """测试多条消息的研究记录获取"""
        messages = [
            {"role": "user", "content": "第一条消息"},
            {"role": "assistant", "content": "助手回复"},
            {"role": "user", "content": "第二条消息"}
        ]

        result = get_research_record(messages)

        expected = "User: 第一条消息\nUser: 第二条消息\n"
        assert result == expected


@pytest.fixture
def mock_runtime():
    runtime = Mock()
    runtime.get_global_state = Mock(return_value={})
    runtime.update_global_state = Mock()
    return runtime


@pytest.fixture
def mock_context():
    return Mock()


class TestStartNode:
    """测试 StartNode"""

    @pytest.fixture
    def start_node(self):
        return StartNode()

    @pytest.fixture
    def mock_runtime(self):
        runtime = Mock()
        runtime.get_global_state = Mock(return_value={})
        runtime.update_global_state = Mock()
        return runtime

    @pytest.fixture
    def mock_context(self):
        return Mock()

    @pytest.mark.asyncio
    async def test_start_node_invoke_success(self, start_node, mock_runtime, mock_context):
        """测试 StartNode 成功调用"""
        inputs = {
            "language": "zh-CN",
            "messages": [{"role": "user", "content": "测试消息"}],
            "section_idx": 0,
            "step_title": "测试步骤",
            "step_description": "步骤描述",
            "initial_search_query_count": 3,
            "max_research_loops": 2,
            "max_react_recursion_limit": 5
        }

        result = await start_node.invoke(inputs, mock_runtime, mock_context)

        # 验证返回结果
        assert result == inputs

        # 验证全局状态更新
        mock_runtime.update_global_state.assert_called_once()
        call_args = mock_runtime.update_global_state.call_args[0][0]
        assert "collector_context" in call_args

        collector_context = CollectorContext(**call_args["collector_context"])
        assert collector_context.language == "zh-CN"
        assert collector_context.section_idx == 0
        assert collector_context.research_loop_count == 0


class TestGenerateQueryNode:
    """测试 GenerateQueryNode"""

    @pytest.fixture
    def generate_query_node(self):
        return GenerateQueryNode()

    @pytest.fixture
    def mock_runtime(self):
        runtime = Mock()
        runtime.get_global_state = Mock(side_effect=self._mock_get_global_state)
        runtime.update_global_state = Mock()
        return runtime

    def _mock_get_global_state(self, key):
        """模拟全局状态获取"""
        state_map = {
            "collector_context.section_idx": 0,
            "collector_context.step_title": "测试步骤",
            "collector_context.messages": [{"role": "user", "content": "测试消息"}],
            "collector_context.initial_search_query_count": 2,
            "collector_context.language": "zh-CN",
            "collector_context.max_research_loops": 2,
            "collector_context.max_react_recursion_limit": 6
        }
        return state_map.get(key)

    @pytest.fixture
    def mock_context(self):
        return Mock()

    @pytest.mark.asyncio
    async def test_generate_query_node_success(self, generate_query_node, mock_runtime, mock_context):
        """测试 GenerateQueryNode 成功生成查询"""
        inputs = {}

        # 创建 mock 的上下文字典，其 get 方法返回任意 mock 对象（实际 LLM 不会被使用）
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            # 仅保留对 _invoke_llm_with_retry 的 patch
            with patch.object(generate_query_node, '_invoke_llm_with_retry') as mock_llm:
                queries = ["查询1", "查询2", "查询3"]
                description = "测试查询描述"
                mock_llm.return_value = SearchQueryList(
                    queries=queries,  # 故意超过限制数量
                    description=description
                )

                result = await generate_query_node.invoke(inputs, mock_runtime, mock_context)

                # 验证 update_global_state 被调用了两次
                assert mock_runtime.update_global_state.call_count == 2

                # 验证第一次调用是设置 max_tool_steps
                mock_runtime.update_global_state.assert_any_call({
                    "collector_context.max_tool_steps": 1  # (6-2)//2-1 = 1
                })

                # 验证第二次调用是设置 search_query (查询被正确截断)
                search_queries = [RetrievalQuery(query=query, description=description) for query in queries[:2]]
                mock_runtime.update_global_state.assert_any_call({
                    "collector_context.search_queries": search_queries  # 从3个截断到2个
                })

                # 验证返回结果
                assert result == {}
        finally:
            # 清理 contextvar，防止影响其他异步测试
            llm_context.reset(token)

    @pytest.mark.asyncio
    async def test_generate_query_node_llm_failure(self, generate_query_node, mock_runtime, mock_context):
        """测试 GenerateQueryNode LLM 调用失败"""
        inputs = {}

        # 创建 mock 的上下文字典
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 仅用于赋值，不参与后续逻辑

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(generate_query_node, '_invoke_llm_with_retry') as mock_llm:
                queries = ["测试步骤"]
                description = "Error when generate search query, use step title as query"
                mock_llm.return_value = SearchQueryList(
                    queries=queries,
                    description=description,
                )

                await generate_query_node.invoke(inputs, mock_runtime, mock_context)

                # 验证第一次调用是设置 max_tool_steps
                mock_runtime.update_global_state.assert_any_call({
                    "collector_context.max_tool_steps": 1  # (6-2)//2-1 = 1
                })

                # 验证使用了默认查询
                search_queries = [RetrievalQuery(query=query, description=description) for query in queries]
                mock_runtime.update_global_state.assert_any_call({
                    "collector_context.search_queries": search_queries
                })
        finally:
            llm_context.reset(token)


class TestSupervisorNode:
    """测试 SupervisorNode"""

    @pytest.fixture
    def supervisor_node(self):
        return SupervisorNode()

    @pytest.fixture
    def mock_runtime(self):
        runtime = Mock()
        runtime.get_global_state = Mock(side_effect=self._mock_get_global_state)
        runtime.update_global_state = Mock()
        runtime.write_custom_stream = AsyncMock()
        return runtime

    def _mock_get_global_state(self, key):
        """模拟全局状态获取"""
        state_map = {
            "collector_context.section_idx": 0,
            "collector_context.step_title": "测试步骤",
            "collector_context.step_description": "步骤描述",
            "collector_context.initial_search_query_count": 2,
            "collector_context.language": "zh-CN",
            "collector_context.doc_infos": [
                {"url": "http://example.com", "title": "示例标题", "query": "示例查询"},
            ],
            "collector_context.new_doc_infos_current_loop": [
                {"url": "http://example.com", "title": "示例标题", "query": "示例查询"},
            ],
            "collector_context.research_loop_count": 1,
            "collector_context.max_tool_steps": 3,
            "collector_context.max_research_loops": 3
        }
        return state_map.get(key)

    @pytest.mark.asyncio
    async def test_supervisor_node_sufficient(self, supervisor_node, mock_runtime, mock_context):
        """测试 SupervisorNode 信息充足的情况"""
        inputs = {}

        # 创建一个 mock 的上下文字典，其 get 方法返回任意对象（因后续 LLM 调用已被 mock）
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 实际不会被使用，但赋值需要成功

        # 设置 contextvar 的值
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(supervisor_node, '_invoke_llm_with_retry') as mock_llm:
                mock_llm.return_value = Reflection(
                    is_sufficient=True,
                    knowledge_gap="",
                    next_queries=[]
                )

                result = await supervisor_node.invoke(inputs, mock_runtime, mock_context)

                # 验证下一个节点是 SUMMARY
                assert result["next_node"] == "collector_summary"

                # 验证研究循环计数增加
                mock_runtime.update_global_state.assert_any_call({
                    "collector_context.research_loop_count": 2
                })
        finally:
            # 清理 contextvar，避免影响其他测试
            llm_context.reset(token)

    @pytest.mark.asyncio
    async def test_supervisor_node_insufficient(self, supervisor_node, mock_runtime, mock_context):
        """测试 SupervisorNode 信息不足的情况"""
        inputs = {}

        # 创建 mock 的上下文字典
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 实际未使用，仅用于赋值成功

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(supervisor_node, '_invoke_llm_with_retry') as mock_llm:
                knowledge_gap = "需要更多技术细节"
                next_queries = ["跟进查询1", "跟进查询2"]
                mock_llm.return_value = Reflection(
                    is_sufficient=False,
                    knowledge_gap=knowledge_gap,
                    next_queries=next_queries
                )

                result = await supervisor_node.invoke(inputs, mock_runtime, mock_context)

                # 验证下一个节点是 INFO_COLLECTOR
                assert result["next_node"] == "collector_info_retrieval"

                # 验证查询被更新
                search_queries = [RetrievalQuery(query=query, description=knowledge_gap) for query in next_queries]
                mock_runtime.update_global_state.assert_any_call({
                    "collector_context.search_queries": search_queries,
                })
        finally:
            # 清理 contextvar
            llm_context.reset(token)


class TestSummaryNode:
    """测试 SummaryNode"""

    @pytest.fixture
    def summary_node(self):
        return SummaryNode()

    @pytest.fixture
    def mock_runtime(self):
        runtime = Mock()
        runtime.get_global_state = Mock(side_effect=self._mock_get_global_state)
        runtime.update_global_state = Mock()
        return runtime

    def _mock_get_global_state(self, key):
        """模拟全局状态获取"""
        state_map = {
            "collector_context.section_idx": 0,
            "collector_context.step_title": "测试步骤",
            "collector_context.step_description": "步骤描述",
            "collector_context.language": "zh-CN",
            "collector_context.doc_infos": [
                {"url": "http://example.com", "title": "示例标题", "content": "示例查询"},
            ],
            "config.info_collector_allow_programmer": True
        }
        return state_map.get(key)

    @pytest.mark.asyncio
    async def test_summary_node_without_programmer(self, summary_node, mock_runtime, mock_context):
        """测试 SummaryNode 不需要程序员的情况"""
        inputs = {}

        # 创建 mock 的上下文字典
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 实际 LLM 对象不会被使用

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(summary_node, '_invoke_llm_with_retry') as mock_llm:
                mock_llm.return_value = Summary(
                    need_programmer=False,
                    programmer_task="",
                    info_summary="信息总结内容",
                    evaluation=""
                )

                result = await summary_node.invoke(inputs, mock_runtime, mock_context)

                # 验证下一个节点是 END
                assert result["next_node"] == "collector_end"
        finally:
            # 清理 contextvar，避免影响其他测试
            llm_context.reset(token)


class TestProgrammerNode:
    """测试 ProgrammerNode"""

    @pytest.fixture
    def programmer_node(self):
        return ProgrammerNode()

    @pytest.mark.asyncio
    async def test_programmer_node(self, programmer_node, mock_runtime, mock_context):
        """测试 ProgrammerNode"""
        inputs = {}

        result = await programmer_node._do_invoke(inputs, mock_runtime, mock_context)

        assert result == {}


class TestGraphEndNode:
    """测试 GraphEndNode"""

    @pytest.fixture
    def graph_end_node(self):
        return GraphEndNode()

    @pytest.fixture
    def mock_runtime(self):
        runtime = Mock()
        runtime.get_global_state = Mock(side_effect=self._mock_get_global_state)
        runtime.update_global_state = Mock()
        runtime.write_custom_stream = AsyncMock()
        return runtime

    def _mock_get_global_state(self, key):
        """模拟全局状态获取"""
        state_map = {
            "collector_context.section_idx": 0,
            "collector_context.step_title": "测试步骤",
            "collector_context.info_summary": "最终信息总结",
            "collector_context.doc_infos": [],
            "collector_context.gathered_info": [],
            "collector_context.messages": []
        }
        return state_map.get(key)

    @pytest.mark.asyncio
    async def test_graph_end_node(self, graph_end_node, mock_runtime, mock_context):
        """测试 GraphEndNode"""
        inputs = {}

        result = await graph_end_node._do_invoke(inputs, mock_runtime, mock_context)

        # 验证消息流写入
        mock_runtime.write_custom_stream.assert_called_once()

        # 验证消息列表更新
        mock_runtime.update_global_state.assert_called_once()


def test_build_info_collector_sub_graph():
    """测试子图构建"""
    collector_graph = build_info_collector_sub_graph()
    assert isinstance(collector_graph, Workflow)


# 测试工具函数
@pytest.mark.parametrize("messages,expected", [
    # 单条消息
    ([{"content": "test"}], "test"),
    # 多条用户消息
    ([
         {"role": "user", "content": "msg1"},
         {"role": "assistant", "content": "resp1"},
         {"role": "user", "content": "msg2"},
     ], "User: msg1\nUser: msg2\n"),
    # 空消息列表
    ([], ""),
])
def test_get_research_record(messages, expected):
    """参数化测试研究记录获取"""
    result = get_research_record(messages)
    assert result == expected
