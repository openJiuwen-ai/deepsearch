from unittest.mock import Mock, AsyncMock, patch, MagicMock

import pytest
from openjiuwen.core.workflow.workflow import Workflow

from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder import SearchQueryList, \
    Reflection, Summary, CollectorContext, StartNode, GenerateQueryNode, SupervisorNode, SummaryNode, \
    GraphEndNode, build_info_collector_sub_graph, get_research_record, llm_context
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import RetrievalQuery
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import EvidenceLedger

module_prefix = "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder"


class ExposedGraphEndNode(GraphEndNode):
    """用于测试的类，公开受保护的方法以遵循 G.CLS.11 规则"""

    async def do_invoke(self, *args, **kwargs):
        return await self._do_invoke(*args, **kwargs)


class TestSearchQueryList:
    """测试 SearchQueryList 数据模型"""

    def test_search_query_list_creation(self):
        """测试 SearchQueryList 创建"""
        query_list = SearchQueryList(
            queries=["test query 1", "test query 2"],
            missing_evidence=["需要测试证据"],
        )

        assert query_list.queries == ["test query 1", "test query 2"]
        assert query_list.missing_evidence == ["需要测试证据"]


class TestReflection:
    """测试 Reflection 数据模型"""

    def test_reflection_creation(self):
        """测试 Reflection 创建"""
        reflection = Reflection(
            is_sufficient=True,
            knowledge_gap="需要更多信息",
            next_queries=["follow up query 1", "follow up query 2"],
            known_facts=["事实1"],
            missing_evidence=["证据1"],
        )

        assert reflection.is_sufficient is True
        assert reflection.knowledge_gap == "需要更多信息"
        assert reflection.next_queries == ["follow up query 1", "follow up query 2"]
        assert reflection.known_facts == ["事实1"]
        assert reflection.missing_evidence == ["证据1"]


class TestSummary:
    """测试 Summary 数据模型"""

    def test_summary_creation(self):
        """测试 Summary 创建"""
        summary = Summary(
            info_summary="收集到的信息总结",
            evaluation="评估结果"
        )

        assert summary.info_summary == "收集到的信息总结"
        assert summary.evaluation == "评估结果"


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
def mock_session():
    session = Mock()
    session.get_global_state = Mock(return_value={})
    session.update_global_state = Mock()
    return session


@pytest.fixture
def mock_context():
    return Mock()


class TestStartNode:
    """测试 StartNode"""

    @pytest.fixture
    def start_node(self):
        return StartNode()

    @pytest.fixture
    def mock_session(self):
        session = Mock()
        session.get_global_state = Mock(return_value={})
        session.update_global_state = Mock()
        return session

    @pytest.fixture
    def mock_context(self):
        return Mock()

    @pytest.mark.asyncio
    async def test_start_node_invoke_success(self, start_node, mock_session, mock_context):
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

        result = await start_node.invoke(inputs, mock_session, mock_context)

        # 验证返回结果
        assert result == inputs

        # 验证全局状态更新
        mock_session.update_global_state.assert_called_once()
        call_args = mock_session.update_global_state.call_args[0][0]
        assert "collector_context" in call_args

        collector_context = CollectorContext(**call_args["collector_context"])
        assert collector_context.language == "zh-CN"
        assert collector_context.section_idx == 0
        assert collector_context.research_loop_count == 0
        assert collector_context.evidence_ledger == {}

    @pytest.mark.asyncio
    async def test_start_node_does_not_inherit_input_ledger(self, start_node, mock_session, mock_context):
        """StartNode should always start the collector step with an empty internal ledger."""
        inputs = {
            "evidence_ledger": {
                "known_facts": ["old fact"],
                "missing_evidence": ["old missing"],
                "attempted_queries": ["old query"],
            }
        }

        await start_node.invoke(inputs, mock_session, mock_context)

        call_args = mock_session.update_global_state.call_args[0][0]
        collector_context = CollectorContext(**call_args["collector_context"])
        assert collector_context.evidence_ledger == {}


class TestGenerateQueryNode:
    """测试 GenerateQueryNode"""

    @pytest.fixture
    def generate_query_node(self):
        return GenerateQueryNode()

    @pytest.fixture
    def mock_session(self):
        session = Mock()
        session.get_global_state = Mock(side_effect=self._mock_get_global_state)
        session.update_global_state = Mock()
        return session

    def _mock_get_global_state(self, key):
        """模拟全局状态获取"""
        state_map = {
            "collector_context.section_idx": 0,
            "collector_context.step_title": "测试步骤",
            "collector_context.messages": [{"role": "user", "content": "测试消息"}],
            "collector_context.initial_search_query_count": 2,
            "collector_context.language": "zh-CN",
            "collector_context.max_research_loops": 2,
            "collector_context.max_react_recursion_limit": 6,
            "collector_context.step_description": "步骤描述",
            "collector_context.evidence_ledger": {},
        }
        return state_map.get(key)

    @pytest.fixture
    def mock_context(self):
        return Mock()

    @pytest.mark.asyncio
    async def test_generate_query_node_success(self, generate_query_node, mock_session, mock_context):
        """测试 GenerateQueryNode 成功生成查询"""
        inputs = {}

        # 创建 mock 的上下文字典，其 get 方法返回任意 mock 对象（实际 LLM 不会被使用）
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            # 仅保留对 _invoke_llm_with_retry 的 patch
            with patch.object(generate_query_node, '_invoke_llm_with_retry') as mock_llm, \
                    patch(f"{module_prefix}.adapt_llm_model_name"):
                queries = ["查询1", "查询2", "查询3"]
                missing_evidence = ["需要验证的证据"]
                mock_llm.return_value = SearchQueryList(
                    queries=queries,  # 故意超过限制数量
                    missing_evidence=missing_evidence,
                )

                result = await generate_query_node.invoke(inputs, mock_session, mock_context)

                # 验证第一次调用是设置 max_tool_steps
                mock_session.update_global_state.assert_any_call({
                    "collector_context.max_tool_steps": 1  # (6-2)//2-1 = 1
                })

                # 验证第二次调用是设置 search_query (查询被正确截断)
                search_queries = [RetrievalQuery(query=query) for query in queries[:2]]
                mock_session.update_global_state.assert_any_call({
                    "collector_context.search_queries": search_queries  # 从3个截断到2个
                })
                mock_session.update_global_state.assert_any_call({
                    "collector_context.evidence_ledger": EvidenceLedger(
                        missing_evidence=missing_evidence
                    ).model_dump()
                })

                # 验证返回结果
                assert result == {}
        finally:
            # 清理 contextvar，防止影响其他异步测试
            llm_context.reset(token)

    @pytest.mark.asyncio
    async def test_generate_query_node_llm_failure(self, generate_query_node, mock_session, mock_context):
        """测试 GenerateQueryNode LLM 调用失败"""
        inputs = {}

        # 创建 mock 的上下文字典
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 仅用于赋值，不参与后续逻辑

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(generate_query_node, '_invoke_llm_with_retry') as mock_llm, \
                    patch(f"{module_prefix}.adapt_llm_model_name"):
                queries = ["测试步骤"]
                description = "Error when generate search query, use step title as query"
                mock_llm.return_value = SearchQueryList(
                    queries=queries,
                    missing_evidence=[],
                )

                await generate_query_node.invoke(inputs, mock_session, mock_context)

                # 验证第一次调用是设置 max_tool_steps
                mock_session.update_global_state.assert_any_call({
                    "collector_context.max_tool_steps": 1  # (6-2)//2-1 = 1
                })

                # 验证使用了默认查询
                search_queries = [RetrievalQuery(query=query) for query in queries]
                mock_session.update_global_state.assert_any_call({
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
    def mock_session(self):
        session = Mock()
        session.get_global_state = Mock(side_effect=self._mock_get_global_state)
        session.update_global_state = Mock()
        session.write_custom_stream = AsyncMock()
        return session

    def _mock_get_global_state(self, key):
        """模拟全局状态获取"""
        state_map = {
            "collector_context.section_idx": 0,
            "collector_context.step_title": "测试步骤",
            "collector_context.step_description": "步骤描述",
            "collector_context.initial_search_query_count": 2,
            "collector_context.language": "zh-CN",
            "collector_context.doc_infos": [
                {
                    "doc_id": "web_1",
                    "source_id": "web_1",
                    "url": "http://example.com",
                    "title": "示例标题",
                    "query": "示例查询",
                    "summary": "不应进入 supervisor prompt 的 summary",
                    "key_passages": ["关键片段"],
                    "scores": {"relevance": 8, "answerability": 7, "authority": 6, "data_density": 5},
                    "original_content": "不应进入 supervisor prompt 的长正文",
                },
            ],
            "collector_context.new_doc_infos_current_loop": [
                {
                    "doc_id": "web_1",
                    "source_id": "web_1",
                    "url": "http://example.com",
                    "title": "示例标题",
                    "query": "示例查询",
                    "summary": "不应进入 supervisor prompt 的 summary",
                    "key_passages": ["关键片段"],
                    "scores": {"relevance": 8, "answerability": 7, "authority": 6, "data_density": 5},
                    "original_content": "不应进入 supervisor prompt 的长正文",
                },
            ],
            "collector_context.research_loop_count": 1,
            "collector_context.max_tool_steps": 3,
            "collector_context.max_research_loops": 3,
            "collector_context.evidence_ledger": {
                "known_facts": ["已有事实"],
                "missing_evidence": ["旧缺口"],
                "attempted_queries": ["已查 query"],
            },
        }
        return state_map.get(key)

    @pytest.mark.asyncio
    async def test_supervisor_node_sufficient(self, supervisor_node, mock_session, mock_context):
        """测试 SupervisorNode 信息充足的情况"""
        inputs = {}

        # 创建一个 mock 的上下文字典，其 get 方法返回任意对象（因后续 LLM 调用已被 mock）
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 实际不会被使用，但赋值需要成功

        # 设置 contextvar 的值
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(supervisor_node, '_invoke_llm_with_retry') as mock_llm, \
                    patch(f"{module_prefix}.apply_system_prompt") as mock_apply_prompt, \
                    patch(f"{module_prefix}.adapt_llm_model_name"):
                mock_apply_prompt.return_value = []
                mock_llm.return_value = Reflection(
                    is_sufficient=True,
                    knowledge_gap="",
                    next_queries=[],
                    known_facts=["新增事实"],
                    missing_evidence=[],
                )

                result = await supervisor_node.invoke(inputs, mock_session, mock_context)

                # 验证下一个节点是 SUMMARY
                assert result["next_node"] == "collector_summary"

                # 验证研究循环计数增加
                mock_session.update_global_state.assert_any_call({
                    "collector_context.research_loop_count": 2
                })
                agent_input = mock_apply_prompt.call_args.args[1]
                assert "evidence_table" in agent_input
                assert "doc_infos" not in agent_input
                assert "evidence_doc_infos" not in agent_input
                assert "original_content" not in str(agent_input["evidence_table"])
                assert "不应进入 supervisor prompt 的 summary" not in str(agent_input["evidence_table"])
                assert "summary" not in agent_input["evidence_table"][0]
                assert agent_input["evidence_table"][0]["source_id"]
        finally:
            # 清理 contextvar，避免影响其他测试
            llm_context.reset(token)

    @pytest.mark.asyncio
    async def test_supervisor_node_insufficient(self, supervisor_node, mock_session, mock_context):
        """测试 SupervisorNode 信息不足的情况"""
        inputs = {}

        # 创建 mock 的上下文字典
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 实际未使用，仅用于赋值成功

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(supervisor_node, '_invoke_llm_with_retry') as mock_llm, \
                    patch(f"{module_prefix}.adapt_llm_model_name"):
                knowledge_gap = "需要更多技术细节"
                next_queries = ["跟进查询1", "跟进查询2"]
                mock_llm.return_value = Reflection(
                    is_sufficient=False,
                    knowledge_gap=knowledge_gap,
                    next_queries=next_queries,
                    known_facts=["新增事实"],
                    missing_evidence=["还缺技术细节"],
                )

                result = await supervisor_node.invoke(inputs, mock_session, mock_context)

                # 验证下一个节点是 INFO_COLLECTOR
                assert result["next_node"] == "collector_info_retrieval"

                # 验证查询被更新
                search_queries = [RetrievalQuery(query=query) for query in next_queries]
                mock_session.update_global_state.assert_any_call({
                    "collector_context.search_queries": search_queries,
                })
                mock_session.update_global_state.assert_any_call({
                    "collector_context.evidence_ledger": EvidenceLedger(
                        known_facts=["已有事实", "新增事实"],
                        missing_evidence=["还缺技术细节"],
                        attempted_queries=["已查 query"],
                    ).model_dump()
                })
        finally:
            # 清理 contextvar
            llm_context.reset(token)

    @pytest.mark.asyncio
    async def test_supervisor_node_uses_missing_evidence_when_next_queries_empty(
        self, supervisor_node, mock_session, mock_context
    ):
        """If reflection is insufficient but lacks queries, the first missing evidence becomes the follow-up query."""
        inputs = {}
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(supervisor_node, '_invoke_llm_with_retry') as mock_llm, \
                    patch(f"{module_prefix}.adapt_llm_model_name"):
                mock_llm.return_value = Reflection(
                    is_sufficient=False,
                    knowledge_gap="",
                    next_queries=[],
                    missing_evidence=["需要官方口径"],
                )

                result = await supervisor_node.invoke(inputs, mock_session, mock_context)

                assert result["next_node"] == "collector_info_retrieval"
                mock_session.update_global_state.assert_any_call({
                    "collector_context.search_queries": [RetrievalQuery(query="需要官方口径")]
                })
        finally:
            llm_context.reset(token)

    @pytest.mark.asyncio
    async def test_supervisor_node_uses_knowledge_gap_when_missing_evidence_empty(
        self, supervisor_node, mock_session, mock_context
    ):
        """If missing evidence is absent, knowledge_gap should be used as the fallback query."""
        inputs = {}
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(supervisor_node, '_invoke_llm_with_retry') as mock_llm, \
                    patch(f"{module_prefix}.adapt_llm_model_name"):
                mock_llm.return_value = Reflection(
                    is_sufficient=False,
                    knowledge_gap="需要更多市场数据",
                    next_queries=[],
                    missing_evidence=[],
                )

                result = await supervisor_node.invoke(inputs, mock_session, mock_context)

                assert result["next_node"] == "collector_info_retrieval"
                mock_session.update_global_state.assert_any_call({
                    "collector_context.search_queries": [RetrievalQuery(query="需要更多市场数据")]
                })
        finally:
            llm_context.reset(token)

    @pytest.mark.parametrize(
        "current_loop_docs, expected_evidence_table",
        [
            ([
                {
                    "doc_id": "web_new",
                    "source_id": "web_new_p1",
                    "url": "http://example.com",
                    "title": "示例标题",
                    "query": "示例查询",
                    "key_passages": ["关键片段"],
                    "scores": {"relevance": 8, "answerability": 7, "authority": 6, "data_density": 5},
                    "original_content": "不应进入 supervisor prompt 的长正文",
                }
            ],
             [{
                 "source_id": "web_new_p1",
                 "doc_id": "web_new",
                 "title": "示例标题",
                 "source": "",
                 "publish_time": "",
                 "key_passages": ["关键片段"],
                 "scores": {"relevance": 8, "answerability": 7, "authority": 6, "data_density": 5},
             }]),
            ([], []),
        ],
    )
    @pytest.mark.asyncio
    async def test_supervisor_node_prompt_uses_current_loop_evidence_table_without_full_doc_fallback(
        self, supervisor_node, mock_context, current_loop_docs, expected_evidence_table
    ):
        """Supervisor prompt should use a compact current-loop evidence table, not full doc history."""
        captured_agent_input = {}
        session = Mock()
        session.update_global_state = Mock()
        session.write_custom_stream = AsyncMock()
        session.get_global_state = Mock(side_effect=lambda key: {
            "collector_context.section_idx": 0,
            "collector_context.plan_idx": 0,
            "collector_context.step_idx": 0,
            "collector_context.step_title": "测试步骤",
            "collector_context.step_description": "步骤描述",
            "collector_context.initial_search_query_count": 2,
            "collector_context.language": "zh-CN",
            "collector_context.doc_infos": [{"url": "old", "title": "历史文档"}],
            "collector_context.new_doc_infos_current_loop": current_loop_docs,
            "collector_context.research_loop_count": 1,
            "collector_context.max_research_loops": 3,
            "collector_context.evidence_ledger": {"missing_evidence": ["旧缺口"]},
        }.get(key))
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()
        token = llm_context.set(mock_llm_dict)

        def capture_prompt(prompt_name, agent_input):
            captured_agent_input.update(agent_input)
            return ["formatted"]

        try:
            with patch.object(supervisor_node, '_invoke_llm_with_retry') as mock_llm, \
                    patch(f"{module_prefix}.adapt_llm_model_name"), \
                    patch(f"{module_prefix}.apply_system_prompt", side_effect=capture_prompt):
                mock_llm.return_value = Reflection(
                    is_sufficient=False,
                    knowledge_gap="仍缺信息",
                    next_queries=[],
                    missing_evidence=["旧缺口"],
                )

                await supervisor_node.invoke({}, session, mock_context)

                assert captured_agent_input["evidence_table"] == expected_evidence_table
                assert "original_content" not in str(captured_agent_input["evidence_table"])
                assert "doc_infos" not in captured_agent_input
                assert "evidence_doc_infos" not in captured_agent_input
                assert "new_doc_infos" not in captured_agent_input
        finally:
            llm_context.reset(token)


def test_collector_query_prompt_contract_removes_description():
    """collector_gen_query prompt should request missing_evidence and queries only."""
    prompt = open("openjiuwen_deepsearch/algorithm/prompts/collector_gen_query.md", encoding="utf-8").read()

    assert '"missing_evidence"' in prompt
    assert '"queries"' in prompt
    assert '"description"' not in prompt


def test_collector_supervisor_prompt_contract_mentions_ledger_fields():
    """collector_supervisor prompt should mention ledger fields used by the runtime loop."""
    prompt = open("openjiuwen_deepsearch/algorithm/prompts/collector_supervisor.md", encoding="utf-8").read()

    assert "known_facts" in prompt
    assert "newly confirmed facts" in prompt
    assert "supported by the ledger or gathered information" not in prompt
    assert "missing_evidence" in prompt
    assert "attempted_queries" in prompt
    assert "Compact evidence table" in prompt
    assert "{{ evidence_table }}" in prompt
    assert "key_passages and scores" in prompt
    assert "Do not assume unavailable full-text details" in prompt
    assert "evidence_doc_infos" not in prompt
    assert "new_doc_infos" not in prompt
    assert "{{ doc_infos }}" not in prompt
    assert "approximately satisfies" in prompt
    assert "partially covered" in prompt
    assert "directly resolve or narrow" in prompt
    assert "Evidence Boundary Policy" in prompt
    assert "necessary for a reliable step-level conclusion" in prompt
    assert "non-critical limitations" in prompt
    assert "materially change, complete, or correct the step-level conclusion" in prompt
    assert "minor wording changes" in prompt
    assert "multiple attempted_queries" in prompt
    assert "similar issue" in prompt
    assert "turn that unresolved item into knowledge_gap" in prompt
    assert "final evaluation" in prompt
    assert "{{ ledger_brief }}" in prompt
    assert "{{ ledger }}" not in prompt
    assert "Ledger object" not in prompt


def test_collector_summary_prompt_contract_mentions_unresolved_gaps():
    """collector_final prompt should tell summary to surface unresolved evidence gaps."""
    prompt = open("openjiuwen_deepsearch/algorithm/prompts/collector_final.md", encoding="utf-8").read()

    assert "Unresolved evidence gaps" in prompt
    assert "{{ ledger_brief }}" in prompt
    assert "{{ missing_evidence }}" in prompt
    assert "against the current task" in prompt
    assert "Do not list every gap mechanically" in prompt
    assert "step-level conclusion" in prompt


class TestSummaryNode:
    """测试 SummaryNode"""

    @pytest.fixture
    def summary_node(self):
        return SummaryNode()

    @pytest.fixture
    def mock_session(self):
        session = Mock()
        session.get_global_state = Mock(side_effect=self._mock_get_global_state)
        session.update_global_state = Mock()
        return session

    def _mock_get_global_state(self, key):
        """模拟全局状态获取"""
        state_map = {
            "collector_context.section_idx": 0,
            "collector_context.step_title": "测试步骤",
            "collector_context.step_description": "步骤描述",
            "collector_context.language": "zh-CN",
            "collector_context.doc_infos": [
                {
                    "doc_id": "web_1",
                    "source_id": "web_1",
                    "url": "http://example.com",
                    "title": "示例标题",
                    "summary": "不应进入 summary prompt 的 summary",
                    "key_passages": ["关键片段"],
                    "scores": {"relevance": 8, "answerability": 7, "authority": 6, "data_density": 5},
                    "original_content": "不应进入 summary prompt 的长正文",
                },
            ],
            "collector_context.evidence_ledger": {
                "known_facts": ["已有事实"],
                "missing_evidence": ["仍缺官方来源"],
                "attempted_queries": ["旧查询"],
            },
            "config.info_collector_allow_programmer": True
        }
        return state_map.get(key)

    @pytest.mark.asyncio
    async def test_summary_node_without_programmer(self, summary_node, mock_session, mock_context):
        """测试 SummaryNode 不需要程序员的情况"""
        inputs = {}

        # 创建 mock 的上下文字典
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 实际 LLM 对象不会被使用

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(summary_node, '_invoke_llm_with_retry') as mock_llm, \
                    patch(f"{module_prefix}.apply_system_prompt") as mock_apply_prompt, \
                    patch(f"{module_prefix}.adapt_llm_model_name"):
                mock_apply_prompt.return_value = []
                mock_llm.return_value = Summary(
                    info_summary="信息总结内容",
                    evaluation=""
                )

                result = await summary_node.invoke(inputs, mock_session, mock_context)

                # 验证下一个节点是 END
                assert result["next_node"] == "collector_end"
                agent_input = mock_apply_prompt.call_args.args[1]
                assert "evidence_pack" in agent_input
                assert "doc_infos" not in agent_input
                assert "original_content" not in str(agent_input["evidence_pack"])
                assert "不应进入 summary prompt 的 summary" not in str(agent_input["evidence_pack"])
                assert "summary" not in agent_input["evidence_pack"]["sources"][0]
                assert "source_ids" in agent_input["evidence_pack"]
        finally:
            # 清理 contextvar，避免影响其他测试
            llm_context.reset(token)

    @pytest.mark.asyncio
    async def test_summary_node_passes_unresolved_ledger_to_prompt(self, summary_node, mock_session, mock_context):
        """Summary should receive ledger brief and unresolved gaps for max-loop exits."""
        inputs = {}
        captured_agent_input = {}
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()
        token = llm_context.set(mock_llm_dict)

        def capture_prompt(prompt_name, agent_input):
            captured_agent_input.update(agent_input)
            return ["formatted"]

        try:
            with patch.object(summary_node, '_invoke_llm_with_retry') as mock_llm, \
                    patch(f"{module_prefix}.adapt_llm_model_name"), \
                    patch(f"{module_prefix}.apply_system_prompt", side_effect=capture_prompt):
                mock_llm.return_value = Summary(
                    need_programmer=False,
                    programmer_task="",
                    info_summary="信息总结内容",
                    evaluation="",
                )

                await summary_node.invoke(inputs, mock_session, mock_context)

                assert captured_agent_input["missing_evidence"] == ["仍缺官方来源"]
                assert "Missing evidence:" in captured_agent_input["ledger_brief"]
        finally:
            llm_context.reset(token)


class TestGraphEndNode:
    """测试 GraphEndNode"""

    @pytest.fixture
    def graph_end_node(self):
        return ExposedGraphEndNode()

    @pytest.fixture
    def mock_session(self):
        session = Mock()
        session.get_global_state = Mock(side_effect=self._mock_get_global_state)
        session.update_global_state = Mock()
        session.write_custom_stream = AsyncMock()
        return session

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
    async def test_graph_end_node(self, graph_end_node, mock_session, mock_context):
        """测试 GraphEndNode"""
        inputs = {}

        result = await graph_end_node.do_invoke(inputs, mock_session, mock_context)

        # 验证消息流写入
        mock_session.write_custom_stream.assert_called_once()

        # 验证消息列表更新
        mock_session.update_global_state.assert_called_once()


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
