import json
import logging
from unittest.mock import Mock, AsyncMock, patch, MagicMock

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.collector_execution_service import (
    CollectorExecutionService,
    CollectorExecutionResult,
    CollectorRunPlanConfig,
    run_info_collector_sub_graph,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import InfoRetrievalNode, \
    llm_context
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import (
    InfoCollectorNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Message,
    Plan,
    RetrievalQuery,
    Step,
    StepType,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import EvidenceLedger
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.evidence_ledger import (
    MAX_TARGET_PAPER_ATTEMPTS,
    target_paper_key,
    target_papers_still_searchable,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector import (
    filter_confirmed_target_locators,
)
from openjiuwen_deepsearch.common.common_constants import MAX_COLLECTOR_DOC_CONTENT_LENGTH
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.search_engine_constants import SearchEngine, LocalSearch

module_prefix = "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector"


@pytest.mark.asyncio
async def test_collector_execution_reuses_target_ledger_across_plans_in_section():
    target = {"arxiv_id": "1706.03762v7"}
    confirmed_key = target_paper_key(target)
    state = {
        "section_context.report_type_policy": {},
        "section_context.research_intent": {"target_papers": [target]},
        "section_context.target_paper_ledger": {},
    }
    session = MagicMock()
    session.get_global_state.side_effect = state.get
    session.update_global_state.side_effect = lambda values: state.update(values)
    received_inputs = []

    async def run_subgraph(inputs, _session, _context):
        received_inputs.append(inputs)
        return {
            "evidence_ledger": {
                "confirmed_target_papers": [confirmed_key],
                "target_paper_attempts": {confirmed_key: 1},
            },
            "history_queries": [],
            "doc_infos": [],
            "source_store": {},
        }

    config = CollectorRunPlanConfig(
        language="zh-CN",
        section_idx=1,
        max_search_query_count=5,
        max_research_loops=2,
        max_tool_call_turns_per_query=2,
    )

    def plan(plan_id):
        return Plan(
            id=plan_id,
            title="target",
            thought="target",
            is_research_completed=False,
            steps=[Step(type=StepType.INFO_COLLECTING, title="locate", description="locate")],
        )

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
        "collector_execution_service.run_info_collector_sub_graph",
        side_effect=run_subgraph,
    ):
        service = CollectorExecutionService()
        await service.run_plan(plan("1"), config, session, MagicMock())
        await service.run_plan(plan("2"), config, session, MagicMock())

    assert received_inputs[1]["evidence_ledger"]["confirmed_target_papers"] == [confirmed_key]


def _post_handle_target_paper(target_papers, task_doc_infos, existing_ledger=None, query="38202877"):
    state = {
        "collector_context.section_idx": 0,
        "collector_context.plan_idx": "1",
        "collector_context.step_idx": "1",
        "collector_context.step_title": "Target study",
        "collector_context.research_loop_count": 0,
        "collector_context.max_research_loops": 2,
        "collector_context.doc_infos": [],
        "collector_context.search_queries": [RetrievalQuery(query=query, search_engine_name="pubmed")],
        "collector_context.history_queries": [],
        "collector_context.evidence_ledger": existing_ledger or {},
        "collector_context.source_store": {},
        "collector_context.research_intent": {"target_papers": target_papers},
    }
    session = Mock()
    session.get_global_state.side_effect = state.get

    def update(values):
        state.update(values)

    session.update_global_state.side_effect = update
    InfoRetrievalNode()._post_handle(
        {},
        [{"doc_infos": task_doc_infos, "source_store": {}, "search_query": query}],
        session,
        Mock(),
    )
    return state


def test_post_handle_records_exact_target_paper_fact():
    state = _post_handle_target_paper(
        [{"pmid": "38202877", "title": "A Full Paper Title"}],
        [{
            "source_id": "evidence-hash",
            "title": "A Full Paper Title",
            "url": "https://pubmed.ncbi.nlm.nih.gov/38202877/",
            "academic_source": "pubmed",
            "academic_source_id": "38202877",
        }],
    )

    ledger = state["collector_context.evidence_ledger"]
    assert ledger["known_facts"] == [
        "Target paper located: PMID 38202877, A Full Paper Title."
    ]
    assert ledger["attempted_queries"] == ["38202877"]
    assert ledger["target_paper_attempts"] == {target_paper_key({"pmid": "38202877", "title": "A Full Paper Title"}): 1}
    assert ledger["confirmed_target_papers"] == [
        target_paper_key({"pmid": "38202877", "title": "A Full Paper Title"})
    ]


def test_target_paper_state_stops_searching_after_confirmation_or_three_attempts():
    found = {"pmid": "38202877"}
    unresolved = {"title": "Unresolved Paper"}
    ledger = EvidenceLedger(
        confirmed_target_papers=[target_paper_key(found)],
        target_paper_attempts={target_paper_key(unresolved): MAX_TARGET_PAPER_ATTEMPTS},
    )

    assert target_papers_still_searchable([found, unresolved], ledger) == []


def test_confirmed_target_locator_is_filtered_before_execution():
    target = {"arxiv_id": "1706.03762v7", "title": "Attention Is All You Need"}
    ledger = EvidenceLedger(confirmed_target_papers=[target_paper_key(target)])
    queries = [
        RetrievalQuery(query="1706.03762", search_engine_name="arxiv"),
        RetrievalQuery(
            query="Attention Is All You Need Table 3 ablation",
            search_engine_name="arxiv",
        ),
        RetrievalQuery(query="Transformer influence on vision", search_engine_name="arxiv"),
    ]

    assert filter_confirmed_target_locators(queries, [target], ledger) == queries[1:]


def test_post_handle_does_not_consume_target_attempt_for_unrelated_query():
    state = _post_handle_target_paper(
        [{"pmid": "38202877"}],
        [],
        query="orthodontic demographics evidence",
    )
    assert state["collector_context.evidence_ledger"]["target_paper_attempts"] == {}


def test_post_handle_counts_a_canonical_target_url_locator_attempt():
    state = _post_handle_target_paper(
        [{"url": "https://journal.example.org/article/42/?utm_source=test"}],
        [],
        query="https://journal.example.org/article/42",
    )

    assert state["collector_context.evidence_ledger"]["target_paper_attempts"] == {
        target_paper_key({"url": "https://journal.example.org/article/42/?utm_source=test"}): 1
    }


def test_post_handle_does_not_confirm_mismatched_or_implicit_target():
    mismatched = _post_handle_target_paper(
        [{"pmid": "111", "title": "Same Title"}],
        [{"source_id": "one", "title": "Same Title", "academic_source": "pubmed", "academic_source_id": "222"}],
    )
    implicit = _post_handle_target_paper(
        [{"dataset": "MEPS", "data_year": "2019", "topic": "orthodontics"}],
        [{"source_id": "two", "title": "MEPS 2019 Orthodontics"}],
    )

    assert mismatched["collector_context.evidence_ledger"]["known_facts"] == []
    assert implicit["collector_context.evidence_ledger"]["known_facts"] == []


def test_post_handle_empty_results_remain_non_blocking_and_deduplicate_facts():
    empty = _post_handle_target_paper([{"pmid": "38202877"}], [])
    existing = _post_handle_target_paper(
        [{"pmid": "38202877"}],
        [{"source_id": "one", "title": "Paper", "academic_source": "pubmed", "academic_source_id": "38202877"}],
        existing_ledger={
            "known_facts": ["Target paper located: PMID 38202877, Paper."],
            "attempted_queries": [],
            "missing_evidence": [],
        },
    )

    assert empty["collector_context.evidence_ledger"]["attempted_queries"] == ["38202877"]
    assert existing["collector_context.evidence_ledger"]["known_facts"] == [
        "Target paper located: PMID 38202877, Paper."
    ]


def test_post_handle_logs_target_paper_metadata_match_and_persisted_ledger(caplog):
    caplog.set_level(logging.INFO, logger=module_prefix)
    with patch(f"{module_prefix}.LogManager.is_sensitive", return_value=False):
        _post_handle_target_paper(
            [{"pmid": "38202877"}],
            [{
                "source_id": "evidence-hash",
                "title": "A Full Paper Title",
                "url": "https://pubmed.ncbi.nlm.nih.gov/38202877/",
                "academic_source": "pubmed",
                "academic_source_id": "38202877",
                "doi": "10.1000/example",
            }],
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "[TARGET_PAPER_METADATA]" in messages
    assert '"academic_source": "pubmed"' in messages
    assert '"academic_source_id": "38202877"' in messages
    assert '"doi": "10.1000/example"' in messages
    assert "[TARGET_PAPER_MATCH] matched=true match_count=1" in messages
    assert "Target paper located: PMID 38202877, A Full Paper Title." in messages
    assert "[TARGET_PAPER_LEDGER] matched_fact_count=1" in messages
    assert "known_fact_count_before=0 known_fact_count_after=1 merged=true persisted=true" in messages


def test_post_handle_target_paper_logs_are_redacted_in_sensitive_mode(caplog):
    caplog.set_level(logging.INFO, logger=module_prefix)
    with patch(f"{module_prefix}.LogManager.is_sensitive", return_value=True):
        _post_handle_target_paper(
            [{"pmid": "38202877"}],
            [{
                "source_id": "evidence-hash",
                "title": "Secret Paper Title",
                "academic_source": "pubmed",
                "academic_source_id": "38202877",
                "doi": "10.1000/secret",
            }],
        )

    tagged_messages = "\n".join(
        record.getMessage() for record in caplog.records
        if "[TARGET_PAPER_" in record.getMessage()
    )
    assert "[TARGET_PAPER_METADATA] academic_doc_count=1" in tagged_messages
    assert "[TARGET_PAPER_MATCH] matched=true match_count=1" in tagged_messages
    assert "[TARGET_PAPER_LEDGER] matched_fact_count=1" in tagged_messages
    assert "38202877" not in tagged_messages
    assert "10.1000/secret" not in tagged_messages
    assert "Secret Paper Title" not in tagged_messages


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

    async def structure_result(self, *args, **kwargs):
        return await self._structure_result(*args, **kwargs)

    async def prepare_collector_tool(self, *args, **kwargs):
        return await self._prepare_collector_tool(*args, **kwargs)

    async def resolve_mcp_param_tool_assignments(self, *args, **kwargs):
        return await self._resolve_mcp_param_tool_assignments(*args, **kwargs)


class TestInfoCollectorNode:
    """测试 InfoCollectorNode"""

    MODULE_PATH = "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.info_collector"

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
            "collector_context.max_tool_call_turns_per_query": 3,
            "collector_context.section_idx": 0,
            "collector_context.step_title": "测试步骤",
            "collector_context.language": "en-US",
            "config.info_collector_search_method": "web",
            "collector_context.doc_infos": [],
            "collector_context.gathered_info": [],
            "collector_context.evidence_ledger": {},
            "collector_context.source_store": {},
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
            with patch(f"{module_prefix}.adapt_llm_model_name"):
                result = info_collector_node.pre_handle(inputs, mock_session, mock_context)
        finally:
            # 清理 contextvar，防止影响其他测试
            llm_context.reset(token)

        expected_state = {
            "search_queries": [RetrievalQuery(query="查询1"), RetrievalQuery(query="查询2")],
            "max_tool_call_turns_per_query": 3,
            "section_idx": 0,
            "plan_idx": None,
            "step_idx": None,
            "step_title": "测试步骤",
            "language": "en-US",
            "research_loop_count": None,
            "max_research_loops": None,
            "search_method": "web",
            "web_search_engine_name": SearchEngine.PETAL.value,
            "local_search_engine_name": LocalSearch.OPENAPI.value,
            "api_tools_config": {},
            "research_intent": {},
            "evidence_ledger": {},
            "mcp_tools": [],
            "mcp_param_tools": [],
            "exclusion_constraint_enable": False,
        }
        assert result == expected_state

        # 验证正确的全局状态被获取
        mock_session.get_global_state.assert_any_call("collector_context.search_queries")
        mock_session.get_global_state.assert_any_call("collector_context.max_tool_call_turns_per_query")
        mock_session.get_global_state.assert_any_call("collector_context.research_loop_count")
        mock_session.get_global_state.assert_any_call("collector_context.max_research_loops")
        mock_session.get_global_state.assert_any_call("collector_context.language")

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
                "search_query": "查询1",
            },
            {
                "doc_infos": [{"url": "http://example.com/2", "title": "标题2"}],
                "gathered_info": [{"url": "http://example.com/2", "title": "标题2"}],
                "web_record": [{"url": "http://example.com/2", "title": "标题2"}],
                "local_record": [],
                "search_query": "查询2",
            }
        ]

        # 创建 mock 的上下文字典：其 get 方法返回任意 mock 对象（因 LLM 实际未被使用）
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            with patch.object(info_collector_node, '_collector_main') as mock_collector, \
                    patch(f"{module_prefix}.adapt_llm_model_name"):
                mock_collector.side_effect = mock_results

                result = await info_collector_node.invoke(inputs, mock_session, mock_context)

                # 验证为每个查询创建了任务
                assert mock_collector.call_count == 2
                assert all(call.args[0]["language"] == "en-US" for call in mock_collector.call_args_list)

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

        def mock_get_empty_queries(key):
            """返回空查询列表，其他状态正常"""
            state_map = {
                "collector_context.search_queries": [],  # 空查询
                "collector_context.history_queries": [],
                "collector_context.max_tool_call_turns_per_query": 3,
                "collector_context.section_idx": 0,
                "collector_context.step_title": "测试步骤",
                "config.info_collector_search_method": "web",
                "collector_context.doc_infos": [],
                "collector_context.gathered_info": [],
                "config.web_search_engine_config": None,
                "config.local_search_engine_config": None,
                "config.api_tools_config": {},
                "search_context.research_intent": {},
            }
            return state_map.get(key)

        mock_session.get_global_state = Mock(side_effect=mock_get_empty_queries)
        mock_session.update_global_state = Mock()

        # 准备 mock 的上下文字典：其 get 方法返回任意对象（因 LLM 实际未被调用）
        mock_llm_dict = MagicMock()
        mock_llm_dict.get.return_value = MagicMock()  # 仅用于赋值 self.llm，不参与后续逻辑

        # 设置 contextvar
        token = llm_context.set(mock_llm_dict)

        try:
            with patch(f"{module_prefix}.adapt_llm_model_name"):
                result = await info_collector_node.do_invoke(inputs, mock_session, mock_context)

                # 验证没有创建任务（空查询列表）
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
                "search_query": "查询1",
                "source_store": {"web_1": "正文1"},
            },
            {
                "doc_infos": [{"url": "http://example.com/1", "title": "标题1"}],  # 重复数据
                "gathered_info": [{"url": "http://example.com/1", "title": "标题1"}],
                "web_record": [{"url": "http://example.com/1"}],
                "local_record": [],
                "search_query": "查询2",
                "source_store": {"web_2": "正文2"},
            }
        ]

        with patch(f'{self.MODULE_PATH}.remove_duplicate_items') as mock_remove_dup:
            mock_remove_dup.side_effect = lambda x: x[:1]  # 模拟去重，保留第一个

            result = info_collector_node.post_handle(inputs, algorithm_output, mock_session, mock_context)

            # 验证全局状态更新
            mock_session.update_global_state.assert_any_call({
                "collector_context.doc_infos": [{"url": "http://example.com/1", "title": "标题1"}]
            })
            mock_session.update_global_state.assert_any_call({
                "collector_context.source_store": {"web_1": "正文1", "web_2": "正文2"}
            })
            mock_session.update_global_state.assert_any_call({
                "collector_context.evidence_ledger": EvidenceLedger(
                    attempted_queries=["查询1", "查询2"]
                ).model_dump()
            })
            # 验证返回结果
            assert result == {}

    def test_post_handle_deduplicates_attempted_queries(self, info_collector_node, mock_session, mock_context):
        """Repeated executed queries should only be recorded once in the ledger."""
        inputs = {}
        search_queries = [RetrievalQuery(query="查询1"), RetrievalQuery(query="查询1")]

        def get_state(key):
            state_map = {
                "collector_context.section_idx": 0,
                "collector_context.step_title": "测试步骤",
                "collector_context.doc_infos": [],
                "collector_context.search_queries": search_queries,
                "collector_context.history_queries": [],
                "collector_context.evidence_ledger": {
                    "attempted_queries": ["历史查询"],
                },
            }
            return state_map.get(key)

        mock_session.get_global_state = Mock(side_effect=get_state)
        algorithm_output = [
            {"doc_infos": [], "search_query": "查询1"},
            {"doc_infos": [], "search_query": "查询1"},
        ]

        result = info_collector_node.post_handle(inputs, algorithm_output, mock_session, mock_context)

        assert result == {}
        mock_session.update_global_state.assert_any_call({
            "collector_context.evidence_ledger": EvidenceLedger(
                attempted_queries=["历史查询", "查询1"]
            ).model_dump()
        })

    def test_post_handle_preserves_first_source_store_entry_on_conflict(
        self,
        info_collector_node,
        mock_session,
        mock_context,
        caplog,
    ):
        """相同 source_id 的 source_store 冲突应保留首个正文并记录告警。"""
        inputs = {}
        algorithm_output = [
            {
                "doc_infos": [{"url": "http://example.com/1", "title": "标题1"}],
                "search_query": "查询1",
                "source_store": {"web_1": "第一版正文"},
            },
            {
                "doc_infos": [{"url": "http://example.com/1", "title": "标题1"}],
                "search_query": "查询2",
                "source_store": {"web_1": "第二版正文"},
            },
        ]

        with patch(f'{self.MODULE_PATH}.remove_duplicate_items') as mock_remove_dup:
            mock_remove_dup.side_effect = lambda x: x[:1]

            info_collector_node.post_handle(inputs, algorithm_output, mock_session, mock_context)

        mock_session.update_global_state.assert_any_call({
            "collector_context.source_store": {"web_1": "第一版正文"}
        })
        assert "source_store source_id conflict" in caplog.text

    def test_post_handle_keeps_same_title_url_with_different_source_ids(
        self,
        info_collector_node,
        mock_session,
        mock_context,
    ):
        """同一 URL/title 的不同 source_id 应作为不同 evidence 保留。"""
        inputs = {}
        algorithm_output = [
            {
                "doc_infos": [
                    {"url": "http://example.com/1", "title": "标题1", "source_id": "web_1_p1"},
                    {"url": "http://example.com/1", "title": "标题1", "source_id": "web_1_p2"},
                ],
                "search_query": "查询1",
                "source_store": {"web_1_p1": "第一段正文", "web_1_p2": "第二段正文"},
            }
        ]

        info_collector_node.post_handle(inputs, algorithm_output, mock_session, mock_context)

        mock_session.update_global_state.assert_any_call({
            "collector_context.doc_infos": [
                {"url": "http://example.com/1", "title": "标题1", "source_id": "web_1_p1"},
                {"url": "http://example.com/1", "title": "标题1", "source_id": "web_1_p2"},
            ]
        })
        mock_session.update_global_state.assert_any_call({
            "collector_context.new_doc_infos_current_loop": [
                {"url": "http://example.com/1", "title": "标题1", "source_id": "web_1_p1"},
                {"url": "http://example.com/1", "title": "标题1", "source_id": "web_1_p2"},
            ]
        })

    @pytest.mark.asyncio
    async def test_collector_main_success(self, info_collector_node, sample_web_record, sample_local_record):
        """测试 _collector_main 方法成功执行（走 web 直调路径，0 LLM 循环）"""
        state = {
            "section_idx": 0,
            "step_title": "测试步骤",
            "search_query": "测试查询",
            "max_tool_call_turns_per_query": 2,
            "search_method": "web",
            "web_search_engine_name": "tavily",
            "api_tools_config": {},
            "research_intent": {},
        }
        web_tool = AsyncMock()
        web_tool.invoke.return_value = {
            "search_results": [
                {"url": "http://example.com/1", "title": "标题1", "snippet": "正文1"},
            ],
        }

        with patch.object(
            info_collector_node, '_prepare_collector_tool',
            new=AsyncMock(return_value=([], {"web_search_tool": web_tool})),
        ), patch.object(info_collector_node, '_structure_result') as mock_structure:
            mock_structure.return_value = (
                [{"url": "http://example.com/1", "title": "标题1"}],
                {"web_1": "正文"},
            )

            result = await info_collector_node.collector_main(state)

            assert "messages" in result
            assert "doc_infos" in result
            assert "web_record" in result
            assert "local_record" in result
            assert "search_query" in result
            assert result["source_store"] == {"web_1": "正文"}

            web_tool.invoke.assert_awaited()
            mock_structure.assert_called_once()

    @pytest.mark.asyncio
    async def test_collector_main_filters_source_dates_before_document_evaluator(self, info_collector_node):
        """来源时间过滤必须发生在 _structure_result 调用 evaluator 之前。"""
        state = {
            "section_idx": 0,
            "step_title": "历史政策",
            "search_query": "policy before 2020",
            "max_tool_call_turns_per_query": 2,
            "search_method": "web",
            "web_search_engine_name": "tavily",
            "api_tools_config": {},
            "research_intent": {
                "source_date_scope": {"constraint_type": "source_date", "end_date": "2020-12-31"}
            },
        }
        tool = AsyncMock()
        tool.invoke.return_value = {
            "search_engine": "tavily",
            "search_results": [
                {
                    "title": "In range",
                    "link": "https://example.com/in",
                    "snippet": "kept body",
                    "source_date": "2020-12-31",
                    "source_date_type": "published",
                },
                {
                    "title": "Out of range",
                    "link": "https://example.com/out",
                    "snippet": "removed body",
                    "source_date": "2021-01-01",
                    "source_date_type": "published",
                },
                {
                    "title": "Unknown",
                    "link": "https://example.com/unknown",
                    "snippet": "unknown body",
                },
            ],
        }

        with patch.object(
            info_collector_node,
            '_prepare_collector_tool',
            new=AsyncMock(return_value=([], {"web_search_tool": tool})),
        ), patch.object(
            info_collector_node,
            '_structure_result',
            new=AsyncMock(return_value=([], {})),
        ) as mock_structure:
            result = await info_collector_node.collector_main(state)

        web_records = mock_structure.call_args.args[0]
        assert [record["title"] for record in web_records] == ["In range", "Unknown"]

    @pytest.mark.asyncio
    async def test_collector_main_direct_calls_mcp_tool_hint_without_llm_loop(
        self, info_collector_node
    ):
        """MCP query-兼容工具：query-gen 给 tool 提示时直调，不走 _collector_llm（0 额外 LLM 调用）。"""
        mcp_tool = AsyncMock()
        mcp_tool.invoke.return_value = {
            "search_results": [{"title": "mcp 命中", "link": "https://mcp.example/1", "snippet": "mcp 正文"}],
        }
        state = {
            "section_idx": 0,
            "step_title": "mcp 步骤",
            "search_query": "mcp query",
            "max_tool_call_turns_per_query": 2,
            "search_method": "web",
            "api_tools_config": {},
            "research_intent": {},
            "mcp_tools": [mcp_tool],
            "retrieval_query": RetrievalQuery(
                query="mcp query",
                primary_engine="petal",
                tool="mcp__srv__search_tool",
            ),
        }

        with patch.object(
            info_collector_node,
            '_prepare_collector_tool',
            new=AsyncMock(return_value=([], {
                "web_search_tool": AsyncMock(),
                "mcp__srv__search_tool": mcp_tool,
            })),
        ), patch.object(
            info_collector_node,
            '_structure_result',
            new=AsyncMock(return_value=([{"url": "https://mcp.example/1"}], {})),
        ):
            result = await info_collector_node.collector_main(state)

        mcp_tool.invoke.assert_awaited_once()
        called_args = mcp_tool.invoke.call_args.args[0] if mcp_tool.invoke.call_args.args else mcp_tool.invoke.call_args.kwargs
        assert called_args.get("query") == "mcp query"
        assert result["search_query"] == "mcp query"

    @pytest.mark.asyncio
    async def test_collector_main_direct_calls_mcp_param_tool_via_batch_assignment(
        self, info_collector_node
    ):
        """query-不兼容 MCP 工具：经批量参数通道直调，单次 LLM 调用产出参数。"""
        mcp_param_tool = AsyncMock()
        mcp_param_tool.card.name = "mcp__db__search"
        mcp_param_tool.invoke.return_value = {
            "search_results": [{"title": "db 命中", "link": "https://db.example/1", "snippet": "db 正文"}],
        }
        web_tool = AsyncMock()
        web_tool.invoke.return_value = {"search_results": []}
        state = {
            "section_idx": 0,
            "step_title": "db 步骤",
            "search_query": "db query",
            "max_tool_call_turns_per_query": 2,
            "search_method": "web",
            "web_search_engine_name": "petal",
            "api_tools_config": {},
            "research_intent": {},
            "retrieval_query": RetrievalQuery(query="db query", primary_engine="petal"),
            "mcp_param_assignments": [
                {"tool": "mcp__db__search", "args": {"query": "db query", "limit": 5}},
            ],
        }

        with patch.object(
            info_collector_node,
            '_prepare_collector_tool',
            new=AsyncMock(return_value=([], {
                "web_search_tool": web_tool,
                "mcp__db__search": mcp_param_tool,
            })),
        ), patch.object(
            info_collector_node,
            '_structure_result',
            new=AsyncMock(return_value=([{"url": "https://db.example/1"}], {})),
        ):
            result = await info_collector_node.collector_main(state)

        mcp_param_tool.invoke.assert_awaited_once()
        called_args = mcp_param_tool.invoke.call_args.args[0]
        assert called_args == {"query": "db query", "limit": 5}
        assert result["search_query"] == "db query"

    @pytest.mark.asyncio
    async def test_resolve_mcp_param_tool_assignments_parses_content_json(
        self, info_collector_node
    ):
        """回归：LLM 统一响应把 JSON 放在 content 字段，必须解析 content 才能取到 assignments。"""
        tool = MagicMock()
        tool.card.name = "mcp__db__search"
        tool.card.description = "db search"
        tool.card.input_params = {"type": "object", "properties": {"query": {"type": "string"}}}
        state = {"mcp_param_tools": [tool], "section_idx": 0, "step_title": "db 步骤", "language": "zh-CN"}
        content = json.dumps({
            "assignments": [
                {"query": "solid state battery", "tool": "mcp__db__search",
                 "args": {"query": "solid state battery", "limit": 5}},
            ]
        })
        info_collector_node.llm = MagicMock()

        with patch(
            f"{module_prefix}.ainvoke_llm_with_stats",
            new=AsyncMock(return_value={"role": "assistant", "content": content}),
        ):
            assignments = await info_collector_node.resolve_mcp_param_tool_assignments(
                state, [RetrievalQuery(query="solid state battery")]
            )

        assert assignments == {
            "solid state battery": [
                {"tool": "mcp__db__search", "args": {"query": "solid state battery", "limit": 5}},
            ]
        }

    @pytest.mark.asyncio
    async def test_resolve_mcp_param_tool_assignments_drops_untrusted_entries(
        self, info_collector_node
    ):
        """LLM 输出不可信：未知 tool / 非 dict args / 非 str query 一律丢弃。"""
        tool = MagicMock()
        tool.card.name = "mcp__db__search"
        tool.card.description = "db search"
        tool.card.input_params = {"type": "object", "properties": {"query": {"type": "string"}}}
        state = {"mcp_param_tools": [tool], "section_idx": 0, "step_title": "db 步骤", "language": "zh-CN"}
        content = json.dumps({
            "assignments": [
                {"query": "ok", "tool": "mcp__db__search", "args": {"query": "ok"}},
                {"query": "bad-tool", "tool": "mcp__unknown", "args": {"query": "x"}},
                {"query": "bad-args", "tool": "mcp__db__search", "args": "not-a-dict"},
                {"query": 123, "tool": "mcp__db__search", "args": {"query": "x"}},
            ]
        })
        info_collector_node.llm = MagicMock()

        with patch(
            f"{module_prefix}.ainvoke_llm_with_stats",
            new=AsyncMock(return_value={"role": "assistant", "content": content}),
        ):
            assignments = await info_collector_node.resolve_mcp_param_tool_assignments(
                state, [RetrievalQuery(query="ok")]
            )

        assert assignments == {"ok": [{"tool": "mcp__db__search", "args": {"query": "ok"}}]}

    @pytest.mark.asyncio
    async def test_resolve_mcp_param_tool_assignments_returns_empty_on_bad_content(
        self, info_collector_node
    ):
        """content 非合法 JSON 时不抛异常，返回空 assignments。"""
        tool = MagicMock()
        tool.card.name = "mcp__db__search"
        tool.card.description = "db search"
        tool.card.input_params = {"type": "object", "properties": {}}
        state = {"mcp_param_tools": [tool], "section_idx": 0, "step_title": "db 步骤", "language": "zh-CN"}
        info_collector_node.llm = MagicMock()

        with patch(
            f"{module_prefix}.ainvoke_llm_with_stats",
            new=AsyncMock(return_value={"role": "assistant", "content": "not-a-json"}),
        ):
            assignments = await info_collector_node.resolve_mcp_param_tool_assignments(
                state, [RetrievalQuery(query="q")]
            )

        assert assignments == {}

    @pytest.mark.asyncio
    async def test_structure_result_empty_records(self, info_collector_node):
        """测试 _structure_result 方法空记录的情况"""
        web_record = []
        local_record = []
        query = "测试查询"

        doc_infos, source_store = await info_collector_node.structure_result(
            web_record, local_record, query
        )

        # 验证返回空结果
        assert doc_infos == []
        assert source_store == {}

    @pytest.mark.asyncio
    async def test_prepare_collector_tool_web(self, info_collector_node):
        """测试 _prepare_collector_tool 方法 - 联网增强 搜索"""
        state = {"search_method": "web"}

        with patch(f'{self.MODULE_PATH}.create_web_search_tool') as mock_web, \
                patch(f'{self.MODULE_PATH}.create_local_search_tool') as mock_local:
            mock_web_tool = Mock()
            mock_web_tool.card.tool_info.return_value = "web_tool_info"
            mock_web.return_value = mock_web_tool

            mock_local_tool = Mock()
            mock_local_tool.card.tool_info.return_value = "local_tool_info"
            mock_local.return_value = mock_local_tool

            tool_list, tool_dict = await info_collector_node.prepare_collector_tool(state)

            # 验证只包含 web 工具
            assert tool_list == ["web_tool_info"]
            assert "web_search_tool" in tool_dict
            assert "local_search_tool" not in tool_dict

    @pytest.mark.asyncio
    async def test_prepare_collector_tool_local(self, info_collector_node):
        """测试 _prepare_collector_tool 方法 - local 搜索"""
        state = {"search_method": "local"}

        with patch(f'{self.MODULE_PATH}.create_web_search_tool') as mock_web, \
                patch(f'{self.MODULE_PATH}.create_local_search_tool') as mock_local:
            mock_web_tool = Mock()
            mock_web_tool.card.tool_info.return_value = "web_tool_info"
            mock_web.return_value = mock_web_tool

            mock_local_tool = Mock()
            mock_local_tool.card.tool_info.return_value = "local_tool_info"
            mock_local.return_value = mock_local_tool

            tool_list, tool_dict = await info_collector_node.prepare_collector_tool(state)

            # 验证只包含 local 工具
            assert tool_list == ["local_tool_info"]
            assert "local_search_tool" in tool_dict
            assert "web_search_tool" not in tool_dict

    @pytest.mark.asyncio
    async def test_prepare_collector_tool_both(self, info_collector_node):
        """测试 _prepare_collector_tool 方法 - 两种搜索"""
        state = {"search_method": "both"}

        with patch(f'{self.MODULE_PATH}.create_web_search_tool') as mock_web, \
                patch(f'{self.MODULE_PATH}.create_local_search_tool') as mock_local:
            mock_web_tool = Mock()
            mock_web_tool.card.tool_info.return_value = "web_tool_info"
            mock_web.return_value = mock_web_tool

            mock_local_tool = Mock()
            mock_local_tool.card.tool_info.return_value = "local_tool_info"
            mock_local.return_value = mock_local_tool

            tool_list, tool_dict = await info_collector_node.prepare_collector_tool(state)

            # 验证包含两种工具
            assert len(tool_list) == 2
            assert "web_tool_info" in tool_list
            assert "local_tool_info" in tool_list
            assert "web_search_tool" in tool_dict
            assert "local_search_tool" in tool_dict

    @pytest.mark.asyncio
    async def test_prepare_collector_tool_with_api_tools_config(self, info_collector_node):
        """测试 _prepare_collector_tool 方法 - 动态 API 工具"""
        state = {
            "search_method": "web",
            "api_tools_config": {
                "collector_tools": [
                    {
                        "tool_id": "tool-1",
                        "name": "runtime_collector_tool",
                        "description": "Runtime collector tool",
                        "path": "https://example.com/collect",
                        "http_method": "get",
                        "request_params": [
                            {
                                "name": "query",
                                "description": "query",
                                "send_method": "query",
                                "required": True,
                            }
                        ],
                    }
                ]
            }
        }

        with patch(f'{self.MODULE_PATH}.create_web_search_tool') as mock_web, \
                patch(f'{self.MODULE_PATH}.create_local_search_tool') as mock_local:
            mock_web_tool = Mock()
            mock_web_tool.card.tool_info.return_value = "web_tool_info"
            mock_web.return_value = mock_web_tool

            mock_local_tool = Mock()
            mock_local_tool.card.tool_info.return_value = "local_tool_info"
            mock_local.return_value = mock_local_tool

            tool_list, tool_dict = await info_collector_node.prepare_collector_tool(state)

        tool_names = [
            tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", tool)
            for tool in tool_list
        ]
        assert "web_tool_info" in tool_list
        assert "runtime_collector_tool" in tool_names
        assert "web_search_tool" in tool_dict
        assert "runtime_collector_tool" in tool_dict

    @pytest.mark.asyncio
    async def test_do_invoke_uses_collector_execution_service(self, editor_info_collector_node):
        plan = Plan(
            id="1",
            title="主题",
            thought="思路",
            is_research_completed=False,
            steps=[
                Step(
                    type=StepType.INFO_COLLECTING,
                    title="步骤1",
                    description="收集资料",
                )
            ],
        )
        session = self._make_session(plan)
        collect_step = Step(
            type=StepType.INFO_COLLECTING,
            title="步骤1",
            description="收集资料",
            id="1",
            step_result="摘要",
            evaluation="足够",
            retrieval_queries=[RetrievalQuery(query="q1")],
        )
        service_result = CollectorExecutionResult(
            collect_steps=[collect_step],
            collected_doc_num=1,
            info_summary="摘要",
            evaluation="足够",
            messages=[Message(role="assistant", content="摘要")],
        )

        with patch(f"{self.MODULE_PATH}.CollectorExecutionService", create=True) as mock_service_cls, \
                patch(f"{self.MODULE_PATH}.add_debug_log_wrapper"):
            mock_service = mock_service_cls.return_value
            mock_service.run_plan = AsyncMock(return_value=service_result)

            result = await editor_info_collector_node._do_invoke({}, session, Mock())

        assert result == {"next_node": NodeId.PLAN_REASONING.value}
        mock_service.run_plan.assert_awaited_once()
        session.update_global_state.assert_any_call(
            {"section_context.messages": [Message(role="assistant", content="摘要")]}
        )
        session.update_global_state.assert_any_call({"section_context.history_plans": [plan]})
        session.update_global_state.assert_any_call({"section_context.collected_doc_num": 1})
        session.update_global_state.assert_any_call({"section_context.warning_infos": []})

    @pytest.mark.asyncio
    async def test_run_info_collector_sub_graph_passes_agent_input_directly(self):
        session = MagicMock()
        session._inner = None
        state_map = {
            "section_context.section_idx": 1,
            "section_context.language": "zh-CN",
            "section_context.messages": [],
            "section_context.current_plan": None,
            "section_context.history_plans": [],
            "section_context.collected_doc_num": 0,
            "section_context.warning_infos": [],
            "config.info_collector_max_search_query_count": 2,
            "config.info_collector_max_research_loops": 2,
            "config.info_collector_max_tool_call_turns_per_query": 3,
            "config": {"mock": True},
            "collector_context": {},
        }
        session.get_global_state = MagicMock(side_effect=state_map.get)
        session.update_global_state = MagicMock()
        collector_graph = AsyncMock()
        collector_graph.invoke = AsyncMock()
        agent_input = {"messages": [{"role": "user", "content": "task"}]}
        context = Mock()
        runner_path = (
            "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph."
            "collector_execution_service.build_info_collector_sub_graph"
        )

        with patch(runner_path, return_value=collector_graph):
            result = await run_info_collector_sub_graph(agent_input, session, context)

        collector_graph.invoke.assert_awaited_once_with(
            agent_input,
            session,
            context,
            is_sub=True,
        )
        session.get_global_state.assert_any_call("collector_context")
        assert result == {}
