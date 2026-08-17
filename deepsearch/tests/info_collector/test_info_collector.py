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
            "research_loop_count": None,
            "max_research_loops": None,
            "search_method": "web",
            "web_search_engine_name": SearchEngine.PETAL.value,
            "local_search_engine_name": LocalSearch.OPENAPI.value,
            "api_tools_config": {},
            "research_intent": {},
            "evidence_ledger": {},
        }
        assert result == expected_state

        # 验证正确的全局状态被获取
        mock_session.get_global_state.assert_any_call("collector_context.search_queries")
        mock_session.get_global_state.assert_any_call("collector_context.max_tool_call_turns_per_query")
        mock_session.get_global_state.assert_any_call("collector_context.research_loop_count")
        mock_session.get_global_state.assert_any_call("collector_context.max_research_loops")

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
        """测试 _collector_main 方法成功执行（走 LLM tool-calling 路径）"""
        state = {
            "section_idx": 0,
            "step_title": "测试步骤",
            "search_query": "测试查询",
            "max_tool_call_turns_per_query": 2,
            "search_method": "web",
            "web_search_engine_name": "tavily",
            "api_tools_config": {
                "collector_tools": [{"name": "custom_tool"}]
            },
            "research_intent": {},
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
                [{"document_index": "0", "scores": {"relevance": 0.9}}],
                {"web_1": "正文"},
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
            assert result["source_store"] == {"web_1": "正文"}

            # 验证调用了相关方法
            mock_collector_llm.assert_called_once()
            mock_structure.assert_called_once()
            mock_process.assert_called_once()

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
                "temporal_scope": {"constraint_type": "source_date", "end_date": "2020-12-31"}
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
            return_value=([], {"web_search_tool": tool}),
        ), patch.object(
            info_collector_node,
            '_structure_result',
            new=AsyncMock(return_value=([], [], {})),
        ) as mock_structure, patch.object(
            info_collector_node,
            '_process_post_process_result',
            return_value=[],
        ):
            result = await info_collector_node.collector_main(state)

        web_records = mock_structure.call_args.args[0]
        assert [record["title"] for record in web_records] == ["In range", "Unknown"]

    @pytest.mark.asyncio
    async def test_collector_llm_success(self, info_collector_node):
        """测试 _collector_llm 方法成功执行"""
        state = {
            "section_idx": 0,
            "step_title": "测试步骤",
            "max_tool_call_turns_per_query": 2
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

            # 验证 LLM 被调用了 max_tool_call_turns_per_query 次
            assert mock_llm.call_count == 2

            # 验证处理响应被调用
            assert mock_process.call_count == 2

            # 验证返回结果
            assert result_state == state
            assert "web_page_search_record" in result_agent_input

    @pytest.mark.asyncio
    async def test_collector_llm_no_tool_calls(self, info_collector_node):
        """测试 _collector_llm 方法没有工具调用的情况"""
        state = {"max_tool_call_turns_per_query": 3}
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
    async def test_collector_llm_rejects_none_tool_call_turns(self, info_collector_node):
        """非法的工具调用轮次配置不应被静默改写成默认值。"""
        state = {"max_tool_call_turns_per_query": None}
        agent_input = {"messages": [], "remaining_steps": None}

        with pytest.raises(TypeError):
            await info_collector_node.collector_llm(state, agent_input, [], {})

    @pytest.mark.asyncio
    async def test_structure_result_with_records(self, info_collector_node, sample_web_record):
        """测试 _structure_result 方法有记录的情况"""
        web_record = sample_web_record
        local_record = []
        query = "测试查询"

        with patch(f'{self.MODULE_PATH}.run_doc_evaluation') as mock_eval:
            # Mock 文档评估结果
            mock_eval.return_value = [
                {
                    "document_index": "0",
                    "scores": {"authority": 0.8, "relevance": 0.9, "answerability": 0.7},
                    "doc_time": "2024-01-01"
                },
                {
                    "document_index": "1",
                    "scores": {"authority": 0.7, "relevance": 0.8, "answerability": 0.6},
                    "doc_time": "2024-01-02"
                }
            ]

            doc_infos, scored_result, source_store = await info_collector_node.structure_result(
                web_record, local_record, query
            )

            # 验证返回结果
            assert len(doc_infos) == 2
            assert len(scored_result) == 2
            assert "doc_id" in doc_infos[0]
            assert "source_id" in doc_infos[0]
            assert "content_ref" in doc_infos[0]
            assert "snippet" not in doc_infos[0]
            assert "summary" not in doc_infos[0]
            assert "key_passages" in doc_infos[0]
            assert "original_content" in doc_infos[0]
            assert doc_infos[0]["source_id"] in source_store
            assert "original_content" not in str(mock_eval.call_args.kwargs["documents"])

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

        doc_infos, scored_result, source_store = await info_collector_node.structure_result(
            web_record, local_record, query
        )

        # 验证返回空结果
        assert doc_infos == []
        assert scored_result == []
        assert source_store == {}

    @pytest.mark.asyncio
    async def test_structure_result_truncates_original_content(self, info_collector_node):
        """_structure_result should keep collector LLM input under the shared content limit."""
        web_record = [
            {
                "url": "http://example.com/large",
                "title": "Large page",
                "content": "A" * (MAX_COLLECTOR_DOC_CONTENT_LENGTH + 1),
            }
        ]

        with patch(f'{self.MODULE_PATH}.run_doc_evaluation') as mock_eval:
            mock_eval.return_value = []

            doc_infos, _, source_store = await info_collector_node.structure_result(
                web_record, [], "large query"
            )

        assert len(doc_infos) == 1
        assert len(doc_infos[0]["original_content"]) == MAX_COLLECTOR_DOC_CONTENT_LENGTH
        assert len(source_store[doc_infos[0]["source_id"]]) == MAX_COLLECTOR_DOC_CONTENT_LENGTH
        mock_eval.assert_called_once()
        assert "documents" in mock_eval.call_args.kwargs
        assert "contents" not in mock_eval.call_args.kwargs
        assert len(str(mock_eval.call_args.kwargs["documents"])) < MAX_COLLECTOR_DOC_CONTENT_LENGTH

    def test_process_post_process_result_success(self, info_collector_node):
        """测试 _process_post_process_result 方法成功执行"""
        scored_result = [
            {
                "document_index": "0",
                "scores": {"authority": 0.8, "relevance": 0.9, "answerability": 0.7},
                "doc_time": "2024-01-01",
            },
            {
                "document_index": "1",
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
        assert result[0]["scores"]["authority"] == 0.8
        assert result[0]["scores"]["relevance"] == 0.9
        assert result[0]["scores"]["answerability"] == 0.7
        assert "source_authority" not in result[0]
        assert "_legacy_compatibility_fields" not in result[0]
        assert "task_relevance" not in result[0]
        assert "information_richness" not in result[0]
        assert "data_density" not in result[0]
        assert "doc_time" in result[0]

        # 验证分数被正确格式化

    def test_process_post_process_result_prefers_publish_time(self, info_collector_node):
        """evaluator 同时返回 publish_time 和 doc_time 时应优先使用规范字段。"""
        scored_result = [{
            "document_index": "0",
            "scores": {"relevance": 0.9},
            "publish_time": "2024-02",
            "doc_time": "2024-01",
        }]
        doc_infos = [{"url": "http://example.com/1", "title": "标题1"}]

        result = info_collector_node.process_post_process_result(scored_result, doc_infos, section_idx=0)

        assert result[0]["publish_time"] == "2024-02"
        assert result[0]["doc_time"] == "2024-02"

    def test_process_post_process_result_invalid_index(self, info_collector_node):
        """测试 _process_post_process_result 方法索引无效的情况"""
        scored_result = [
            {
                "document_index": "invalid",  # 无效的索引
                "scores": {"authority": 0.8, "relevance": 0.9, "answerability": 0.7}
            }
        ]

        doc_infos = [{"url": "http://example.com/1", "title": "标题1"}]

        result = info_collector_node.process_post_process_result(scored_result, doc_infos, section_idx=0)

        # 验证即使索引无效也不会崩溃
        assert len(result) == 1
        assert "scores" not in result[0]

    def test_process_post_process_result_logs_non_dict_item_type(self, info_collector_node, caplog):
        """非 dict 评分项日志应准确指出类型问题。"""
        result = info_collector_node.process_post_process_result(
            ["invalid"],
            [{"url": "http://example.com/1", "title": "标题1"}],
            section_idx=0,
        )

        assert "scores" not in result[0]
        assert "Score result is not a dict (type=str)" in caplog.text

    def test_process_post_process_result_continues_after_invalid_items(self, info_collector_node):
        """无效评分项不应导致后续有效 document_index 被截断丢弃。"""
        scored_result = [
            {"document_index": "invalid", "scores": {"relevance": 1}},
            {"document_index": "0", "scores": {"relevance": 8}},
            {"document_index": "1", "scores": {"relevance": 9}},
        ]
        doc_infos = [
            {"url": "http://example.com/1", "title": "标题1"},
            {"url": "http://example.com/2", "title": "标题2"},
        ]

        result = info_collector_node.process_post_process_result(scored_result, doc_infos, section_idx=0)

        assert result[0]["scores"]["relevance"] == 8.0
        assert result[1]["scores"]["relevance"] == 9.0

    def test_process_post_process_result_rejects_legacy_content_index(self, info_collector_node):
        """拒绝 evaluator 返回旧 content 索引字段。"""
        scored_result = [{
            "content": "0",
            "scores": {"authority": 0.8, "relevance": 0.9, "answerability": 0.7},
            "doc_time": "2024-01-01",
        }]
        doc_infos = [{"url": "http://example.com/1", "title": "标题1"}]

        result = info_collector_node.process_post_process_result(scored_result, doc_infos, section_idx=0)

        assert "scores" not in result[0]
        assert "doc_time" not in result[0]

    def test_prepare_collector_tool_web(self, info_collector_node):
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

            tool_list, tool_dict = info_collector_node.prepare_collector_tool(state)

            # 验证只包含 web 工具
            assert tool_list == ["web_tool_info"]
            assert "web_search_tool" in tool_dict
            assert "local_search_tool" not in tool_dict

    def test_prepare_collector_tool_local(self, info_collector_node):
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

            tool_list, tool_dict = info_collector_node.prepare_collector_tool(state)

            # 验证只包含 local 工具
            assert tool_list == ["local_tool_info"]
            assert "local_search_tool" in tool_dict
            assert "web_search_tool" not in tool_dict

    def test_prepare_collector_tool_both(self, info_collector_node):
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

            tool_list, tool_dict = info_collector_node.prepare_collector_tool(state)

            # 验证包含两种工具
            assert len(tool_list) == 2
            assert "web_tool_info" in tool_list
            assert "local_tool_info" in tool_list
            assert "web_search_tool" in tool_dict
            assert "local_search_tool" in tool_dict

    def test_prepare_collector_tool_with_api_tools_config(self, info_collector_node):
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

            tool_list, tool_dict = info_collector_node.prepare_collector_tool(state)

        tool_names = [
            tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", tool)
            for tool in tool_list
        ]
        assert "web_tool_info" in tool_list
        assert "runtime_collector_tool" in tool_names
        assert "web_search_tool" in tool_dict
        assert "runtime_collector_tool" in tool_dict

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

        with patch(f'{self.MODULE_PATH}.ainvoke_llm_with_stats', new_callable=AsyncMock) as mock_llm_call:
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

        with patch(f'{self.MODULE_PATH}.ainvoke_llm_with_stats', new_callable=AsyncMock) as mock_llm_call:
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

        with patch(f'{self.MODULE_PATH}.process_tool_call') as mock_process:
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


class TestEditorTeamInfoCollectorNode:
    MODULE_PATH = "openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes"

    @pytest.fixture
    def editor_info_collector_node(self):
        return InfoCollectorNode()

    @staticmethod
    def _make_session(plan):
        session = MagicMock()
        state_map = {
            "section_context.section_idx": 1,
            "section_context.language": "zh-CN",
            "section_context.messages": [],
            "section_context.current_plan": plan,
            "section_context.history_plans": [],
            "section_context.collected_doc_num": 0,
            "section_context.warning_infos": [],
            "config.info_collector_max_search_query_count": 2,
            "config.info_collector_max_research_loops": 2,
            "config.info_collector_max_tool_call_turns_per_query": 3,
            "config": {"mock": True},
        }
        session.get_global_state = MagicMock(side_effect=state_map.get)
        session.update_global_state = MagicMock()
        return session

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
