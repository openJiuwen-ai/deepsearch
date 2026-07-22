import logging
import json
from contextvars import Context
from unittest.mock import AsyncMock, Mock, patch

import pytest
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.base import WorkflowCard
from openjiuwen.core.workflow.workflow import Workflow

from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.editor_team_manager_node import (
    EditorTeamNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import (
    EndNode,
    IntentRecognitionNode,
    FeedbackHandlerNode,
    GenerateQuestionsNode,
    OutlineInteractionNode,
    DependencyOutlineNode,
    OutlineNode,
    StartNode,
    UserFeedbackProcessorNode,
)
from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import IntentRecognitionResult
from openjiuwen_deepsearch.config.config import OUTLINER_SECTION_NUM_MAX
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import \
    build_editor_team_workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Outline,
    ResearchIntent,
    Section,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context
from tests.utils.mock_config import get_default_agent_config

logger = logging.getLogger(__name__)


# 公共执行逻辑：运行 agent 并返回所有 chunk（可选）
async def _run_agent_with_mocks(pre_handle_return, sub_graph_return):
    with patch.object(EditorTeamNode, '_pre_handle', return_value=pre_handle_return):
        with patch.object(
                EditorTeamNode,
                '_run_section_sub_graph_await',
                new_callable=AsyncMock,
                return_value=sub_graph_return
        ):
            with patch(
                "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.create_llm_obj",
                return_value={"model": Mock(), "model_name": "mock_model"},
            ):
                agent = MockAgent()
                agent_config = get_default_agent_config()
                chunks = []
                async for chunk in agent.run(
                        message='杭州的天气怎么样',
                        conversation_id="default_session_id",
                        report_template="",
                        interrupt_feedback="",
                        agent_config=agent_config
                ):
                    chunks.append(chunk)
                    # 如果只是触发逻辑，不关心 chunk，可省略收集
                return chunks


@pytest.mark.asyncio
async def test_agent_node_missing_outline(caplog):
    """异常情况：缺少 outline 字段"""
    mocked_pre_handle = {
        "messages": "杭州的天气怎么样"
    }
    mocked_sub_graph = (["info1"], "report", [], [])

    with caplog.at_level(logging.ERROR):
        await _run_agent_with_mocks(mocked_pre_handle, mocked_sub_graph)

    # 验证是否记录了预期的 error
    assert any("outline" in record.message and record.levelno == logging.ERROR
               for record in caplog.records)


@pytest.mark.asyncio
async def test_agent_node_missing_sections(caplog):
    """异常情况：outline 存在但缺少 sections"""
    mocked_pre_handle = {
        "messages": "杭州的天气怎么样",
        "outline": Outline(
            language='zh-CN',
            thought='...',
            title='报告',
        )
    }
    mocked_sub_graph = (["info1"], "report", [], [])

    with caplog.at_level(logging.ERROR):
        await _run_agent_with_mocks(mocked_pre_handle, mocked_sub_graph)

    assert any("sections" in record.message and record.levelno == logging.ERROR
               for record in caplog.records)


@pytest.mark.asyncio
async def test_agent_node_with_interrupt_feedback():
    agent = MockAgent()
    agent_config = get_default_agent_config()
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.create_llm_obj",
        return_value={"model": Mock(), "model_name": "mock_model"},
    ):
        async for chunk in agent.run(message='杭州的天气怎么样', conversation_id="default_session_id",
                                     report_template="", interrupt_feedback="accepted",
                                     agent_config=agent_config):
            logger.debug("[Stream message from node: %s]", chunk)


def test_create_section_state():
    editor_team_node = TestEditorTeamNode()
    outline_section = Section(
        title='当前天气状况',
        description='提供杭州市当前的天气情况，包括天气状况描述、当前温度、风力风向、湿度等基本气象数据。',
        is_core_section=False)
    outline = Outline(
        id="1", thought="mock though", title="mock title", sections=[outline_section]
    )
    search_context = {'session_id': 'default_session_id', 'original_query': '杭州的天气怎么样',
                      'messages': [{...}],
                      'language': 'zh-CN', 'plan_executed_num': 0, 'current_plan': None,
                      'duplicated_search_queries': {}, 'duplicated_search_items': {}, 'final_report_path': '',
                      'final_result': {'response_content': '', 'citation_messages': {}, 'exception_info': ''},
                      'report_generated_num': 0, 'report_evaluation': '',
                      'sub_report_content': '', 'evaluation_details': '', 'sub_evaluation_details': '',
                      'sub_report_evaluate_num': 0, 'sub_evaluation_result': '', 'report_template': '', 'questions': '',
                      'user_feedback': '', 'current_node': None, 'answer': '', 'answer_generated_num': 0,
                      'answer_evaluation': '', 'current_outline': Outline(language='zh-CN',
                                                                          thought='用户需要杭州当前天气情况的详细信息，包括温度、湿度、风力、天气状况等关键数据。根据任务规划文档，我需要创建一个包含这些关键信息的结构化报告大纲。',
                                                                          title='杭州实时天气情况报告',
                                                                          sections=[Section(
                                                                              title='当前天气状况',
                                                                              description='提供杭州当前的天气状况描述，包括天气现象（晴、雨、多云等）和能见度等基本信息',
                                                                              is_core_section=False),
                                                                              Section(title='温度与湿度数据',
                                                                                      description='详细记录杭州当前的气温和相对湿度数据，包括体感温度和舒适度指数',
                                                                                      is_core_section=False),
                                                                              Section(title='风力与风向信息',
                                                                                      description='记录当前风力等级、风向和风速数据，以及阵风情况',
                                                                                      is_core_section=False),
                                                                              Section(title='空气质量指数',
                                                                                      description='提供杭州当前的空气质量指数（AQI）和主要污染物浓度数据',
                                                                                      is_core_section=False),
                                                                              Section(title='天气预报摘要',
                                                                                      description='提供未来24小时的天气预报概要，包括温度变化趋势和天气变化预测',
                                                                                      is_core_section=False)]),
                      'outline_executed_num': 0, 'report_task': '', 'section_task': '', 'section_description': '',
                      'section_idx': 0, 'section_iscore': False, 'sub_section_outline': '',
                      'sub_section_references': [], 'classified_content': [], 'sub_section_core_content': [],
                      'search_mode': 'research', 'current_step': None, 'planner_agent_messages': None,
                      'source_tracer': '', 'trace_source_datas': [], 'merged_trace_source_datas': [],
                      'all_classified_contents': [], 'doc_infos': [], 'gathered_info': [],
                       'debug_pre_step': 'outline-c615f84c-d865-41f6-b7c3-354703c51732', 'debug_cur_step': 'outline-c615f84c-d865-41f6-b7c3-354703c51732'}
    search_context["research_intent"] = {
        "section_count": 4,
        "audience_role": "企业CTO",
        "tone": "analytical",
    }
    section_state = editor_team_node.create_section_state_from_state(search_context, outline, outline_section)
    assert section_state["research_intent"]["section_count"] == 4
    assert section_state["research_intent"]["audience_role"] == "企业CTO"
    assert section_state["research_intent"]["tone"] == "analytical"


def test_create_section_state_builds_section_local_contract():
    editor_team_node = TestEditorTeamNode()
    outline_section = Section(
        title="战略行动建议与未来两年优先投入区域研判",
        description="综合比较结果，给出区域投入排序与行动建议。",
        is_core_section=True,
        section_focus="recommendation_and_ranking",
        focus_dimensions=["recommendation", "ranking"],
    )
    outline = Outline(
        id="1",
        thought="mock thought",
        title="AI PC 市场对比报告",
        sections=[outline_section],
    )
    search_context = {
        "messages": [{"role": "user", "content": "compare"}],
        "language": "zh-CN",
        "report_template": "",
        "config": {},
        "parent_section_steps": [],
        "session_id": "default_session_id",
        "report_type_policy": {},
        "research_intent": {
            "task_type": "comparison",
            "required_dimensions": ["market_size", "vendors", "risks"],
        },
    }

    section_state = editor_team_node.create_section_state_from_state(
        search_context, outline, outline_section
    )

    contract = section_state["section_local_contract"]
    assert contract["section_focus"] == "recommendation_and_ranking"
    assert contract["is_final_decision_section"] is True
    assert "forbidden_dimensions" not in contract


def test_build_section_local_contract_uses_llm_focus_for_use_cases():
    section = Section(
        title="落地场景分化：消费级与企业级应用在中美欧的渗透深度对比",
        description=(
            "按场景维度拆解三大市场AI PC的实际使用率与商业化成熟度，"
            "重点分析消费端44%渗透率、企业端采购渗透率与垂直行业落地路径。"
        ),
        is_core_section=True,
        section_focus="use_cases_and_commercialization",
        focus_dimensions=["use_cases", "commercialization", "adoption"],
    )

    contract = EditorTeamNode._build_section_local_contract(section)

    assert contract["section_focus"] == "use_cases_and_commercialization"
    assert contract["allowed_dimensions"] == [
        "use_cases",
        "commercialization",
        "adoption",
    ]
    assert contract["is_final_decision_section"] is False
    assert "forbidden_dimensions" not in contract


def test_build_section_local_contract_uses_llm_focus_for_risks():
    section = Section(
        title="系统性风险与挑战：政策壁垒、供应链脆弱性与技术代差评估",
        description=(
            "识别并量化政策、供应链与技术层面的差异化风险矩阵，"
            "评估各市场抗风险韧性。"
        ),
        is_core_section=True,
        section_focus="risks_and_barriers",
        focus_dimensions=["risks", "regulation", "barriers"],
    )

    contract = EditorTeamNode._build_section_local_contract(section)

    assert contract["section_focus"] == "risks_and_barriers"
    assert contract["allowed_dimensions"] == [
        "risks",
        "regulation",
        "barriers",
    ]
    assert contract["is_final_decision_section"] is False
    assert "forbidden_dimensions" not in contract


class TestEditorTeamNode(EditorTeamNode):
    async def run_section_sub_graph(self, workflow_session, sub_workflow, input_state):
        return await self._run_section_sub_graph_await(
            workflow_session, sub_workflow, input_state
        )

    def pre_handle(self, inputs, session, context):
        return self._pre_handle(inputs, session, context)

    def create_section_state_from_state(self, state, outline, section):
        return self._create_section_state_from_state(state, outline, section)


class _SessionAwareNode(BaseNode):
    """用于验证 BaseNode.invoke 会注入 session_context 的测试节点。"""

    def _pre_handle(self, inputs, session, context):
        """测试节点不需要预处理，直接返回空输入。"""
        return {}

    async def _do_invoke(self, inputs, session, context):
        """读取 session_context 并回传是否为当前 session。"""
        return {"same_session": session_context.get() is session}

    def _post_handle(self, inputs, algorithm_output, session, context):
        """测试节点无需后处理，直接透传结果。"""
        return algorithm_output


@pytest.mark.asyncio
async def test_run_sub_graph():
    try:
        editor_team_node = TestEditorTeamNode()
        sub_workflow = build_editor_team_workflow()

        workflow_session = AsyncMock(spec=Session)
        await editor_team_node.run_section_sub_graph(workflow_session, sub_workflow, {})
    except Exception as e:
        logger.error(f"fail to test_run_sub_graph: {e}")


@pytest.mark.asyncio
async def test_base_node_invoke_injects_session_context():
    """验证 BaseNode.invoke 会在执行前注入当前 session_context。"""
    node = _SessionAwareNode()
    session = AsyncMock(spec=Session)
    token = session_context.set(object())
    try:
        output = await node.invoke({}, session, Context())
    finally:
        session_context.reset(token)

    assert output["same_session"] is True


@pytest.mark.asyncio
async def test_intent_recognition_node_updates_context_and_routes_to_outline():
    """验证 IntentRecognitionNode 合并路由与意图识别后，研究请求路由到大纲节点。"""
    session = AsyncMock(spec=Session)
    original_query = "请写一份正式报告：AI Agent 趋势"
    messages = [{"role": "user", "content": original_query}]
    intent_result = IntentRecognitionResult(
        original_query=original_query,
        research_query="AI Agent 趋势",
        research_intent=ResearchIntent(
            section_count=5,
            audience_role="研发负责人",
            tone="formal",
            include_domains=["example.com"],
            exclude_domains=["bad.com"],
        ),
        lang="zh-CN",
    )
    web_search_engine_config = Mock()
    web_search_engine_config.search_engine_name = "tavily"

    def _get_global_state(key):
        return {
            "search_context.original_query": original_query,
            "search_context.messages": messages,
            "config.web_search_engine_config": web_search_engine_config,
            "config.workflow_human_in_the_loop": False,
            "config.outliner_max_section_num": 10,
        }.get(key)

    session.get_global_state.side_effect = _get_global_state
    session.update_global_state = Mock()
    node = IntentRecognitionNode()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.adapt_llm_model_name",
        return_value="basic",
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.classify_and_recognize_intent",
        new_callable=AsyncMock,
        return_value=intent_result,
    ) as mock_classify, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.web_search_for_query",
        new_callable=AsyncMock,
        return_value={"search_results": [{"title": "test"}], "error_msg": ""},
    ) as mock_web_search, patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.apply_web_search_domain_constraints",
    ) as mock_apply_domain_constraints:
        output = await node.invoke({}, session, Context())

    assert output["next_node"] == NodeId.OUTLINE.value
    mock_classify.assert_awaited_once_with({
        "original_query": original_query,
        "messages": messages,
        "llm_model_name": "basic",
        "execution_method": ExecutionMethod.PARALLEL.value,
        "human_in_the_loop": False,
        "web_search_engine_config": web_search_engine_config,
        "info_collector_search_method": "web",
    })
    mock_web_search.assert_awaited_once_with({
        "query": "AI Agent 趋势",
        "web_search_engine_name": "tavily",
    })
    update_payloads = [call.args[0] for call in session.update_global_state.call_args_list]
    assert {"search_context.outline_execution_method": ExecutionMethod.PARALLEL.value} in update_payloads
    intent_update = next(
        payload for payload in update_payloads
        if "search_context.original_query" in payload and "search_context.research_intent" in payload
    )
    assert intent_update["search_context.original_query"] == original_query
    assert "search_context.research_query" not in intent_update
    assert intent_update["search_context.research_intent"] == intent_result.research_intent.model_dump()
    assert "search_context.report_type_policy" in intent_update
    mock_apply_domain_constraints.assert_called_once_with(
        search_engine_name="tavily",
        include_domains=["example.com"],
        exclude_domains=["bad.com"],
    )


@pytest.mark.asyncio
async def test_intent_recognition_node_calls_outline_mode_router_for_hybrid_and_saves_result():
    """hybrid 模式下，IntentRecognitionNode 调用大纲模式 router 并保存实际大纲执行方式。"""
    node = IntentRecognitionNode()
    session = Mock(spec=Session)
    session.update_global_state = Mock()
    current_inputs = {
        "execution_method": ExecutionMethod.HYBRID.value,
        "original_query": "请先诊断问题，再给出改进方案",
        "messages": [{"role": "user", "content": "备用问题"}],
        "llm_model_name": "basic",
    }

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.route_outline_execution_method",
        new=AsyncMock(return_value=ExecutionMethod.DEPENDENCY_DRIVING.value),
    ) as mock_router:
        selected_method = await node._resolve_outline_execution_method(current_inputs, session)

    assert selected_method == ExecutionMethod.DEPENDENCY_DRIVING.value
    mock_router.assert_awaited_once_with("请先诊断问题，再给出改进方案", "basic")
    session.update_global_state.assert_called_once_with({
        "search_context.outline_execution_method": ExecutionMethod.DEPENDENCY_DRIVING.value
    })


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_method, expected_method",
    [
        (ExecutionMethod.PARALLEL.value, ExecutionMethod.PARALLEL.value),
        (ExecutionMethod.DEPENDENCY_DRIVING.value, ExecutionMethod.DEPENDENCY_DRIVING.value),
        ("", ExecutionMethod.PARALLEL.value),
    ],
)
async def test_intent_recognition_node_resolves_fixed_outline_methods_without_router(
    execution_method,
    expected_method,
):
    """非 hybrid 模式不调用 LLM router，直接保存固定的大纲执行方式。"""
    node = IntentRecognitionNode()
    session = Mock(spec=Session)
    session.update_global_state = Mock()
    current_inputs = {
        "execution_method": execution_method,
        "original_query": "生成市场格局综述",
        "messages": [],
        "llm_model_name": "basic",
    }

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.route_outline_execution_method",
        new_callable=AsyncMock,
    ) as mock_router:
        selected_method = await node._resolve_outline_execution_method(current_inputs, session)

    assert selected_method == expected_method
    mock_router.assert_not_called()
    session.update_global_state.assert_called_once_with({
        "search_context.outline_execution_method": expected_method
    })


def test_outline_pre_handle_resolves_max_section_num_from_section_count():
    """OutlineNode 应从 research_intent.section_count 计算 section_num（目标章节数）。"""
    session = Mock(spec=Session)
    research_intent = {"section_count": 4, "audience_role": "CTO", "tone": "formal"}

    def _get_global_state(key):
        mapping = {
            "search_context.language": "zh-CN",
            "search_context.messages": [],
            "search_context.questions": "",
            "search_context.user_feedback": "",
            "config.outliner_max_section_num": 10,
            "config.outliner_max_generate_outline_retry_num": 1,
            "search_context.report_template": "",
            "search_context.outline_interactions": [],
            "search_context.current_outline": None,
            "config.outline_interaction_enabled": False,
            "config.api_tools_config": {},
            "search_context.entry_search_results": [],
            "search_context.report_type_policy": {},
            "search_context.research_intent": research_intent,
        }
        return mapping.get(key)

    session.get_global_state.side_effect = _get_global_state
    node = OutlineNode()
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.adapt_llm_model_name",
        return_value="basic",
    ):
        current_inputs = node._pre_handle({}, session, Context())

    assert current_inputs["section_num"] == 4
    assert current_inputs["max_section_num"] == OUTLINER_SECTION_NUM_MAX


def test_outline_pre_handle_exposes_task_contract_prompt_context():
    session = Mock(spec=Session)
    research_intent = {
        "section_count": 4,
        "audience_role": "CTO",
        "tone": "formal",
        "task_type": "comparison",
        "required_dimensions": ["growth", "dividend"],
        "comparison_targets": ["AIA", "Ping An"],
    }

    def _get_global_state(key):
        mapping = {
            "search_context.language": "zh-CN",
            "search_context.messages": [],
            "search_context.questions": "",
            "search_context.user_feedback": "",
            "config.outliner_max_section_num": 10,
            "config.outliner_max_generate_outline_retry_num": 1,
            "search_context.report_template": "",
            "search_context.outline_interactions": [],
            "search_context.current_outline": None,
            "config.outline_interaction_enabled": False,
            "config.api_tools_config": {},
            "search_context.entry_search_results": [],
            "search_context.report_type_policy": {},
            "search_context.research_intent": research_intent,
        }
        return mapping.get(key)

    session.get_global_state.side_effect = _get_global_state
    node = OutlineNode()
    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.adapt_llm_model_name",
        return_value="basic",
    ):
        current_inputs = node._pre_handle({}, session, Context())

    assert current_inputs["task_type"] == "comparison"
    assert current_inputs["required_dimensions_text"] == "growth, dividend"
    assert current_inputs["comparison_targets_text"] == "AIA, Ping An"

def test_outline_pre_handle_reads_outline_execution_method_from_session():
    """OutlineNode 应从 session 读取意图识别节点写入的大纲执行模式。"""
    session = Mock(spec=Session)
    research_intent = {"section_count": 4}

    def _get_global_state(key):
        mapping = {
            "search_context.language": "zh-CN",
            "search_context.messages": [],
            "search_context.questions": "",
            "search_context.user_feedback": "",
            "config.outliner_max_section_num": 10,
            "config.outliner_max_generate_outline_retry_num": 1,
            "search_context.report_template": "",
            "search_context.outline_interactions": [],
            "search_context.current_outline": None,
            "config.outline_interaction_enabled": False,
            "config.api_tools_config": {},
            "search_context.entry_search_results": [],
            "search_context.report_type_policy": {},
            "search_context.research_intent": research_intent,
            "search_context.outline_execution_method": ExecutionMethod.DEPENDENCY_DRIVING.value,
        }
        return mapping.get(key)

    session.get_global_state.side_effect = _get_global_state
    node = OutlineNode()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.adapt_llm_model_name",
        return_value="basic",
    ):
        current_inputs = node._pre_handle({}, session, Context())

    assert current_inputs["outline_execution_method"] == ExecutionMethod.DEPENDENCY_DRIVING.value


@pytest.mark.parametrize(
    "selected_method, expected_prompt",
    [
        (ExecutionMethod.PARALLEL.value, "outliner_interaction"),
        (ExecutionMethod.DEPENDENCY_DRIVING.value, "dep_driving_outliner_interaction"),
    ],
)
def test_outline_node_revise_comment_prompt_follows_selected_method(selected_method, expected_prompt):
    """大纲交互修改时，OutlineNode 应按首次路由结果选择普通或依赖交互 prompt。"""
    node = OutlineNode()
    current_inputs = {
        "outline_execution_method": selected_method,
        "outline_interaction_mode": "revise_comment",
        "user_feedback": "请调整章节顺序",
        "report_template": "",
    }

    assert node._select_prompt_name(current_inputs) == expected_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selected_method, expected_prompt, expected_with_dep_driving, expected_next_node",
    [
        (
            ExecutionMethod.PARALLEL.value,
            "outliner",
            False,
            NodeId.EDITOR_TEAM.value,
        ),
        (
            ExecutionMethod.DEPENDENCY_DRIVING.value,
            "dep_driving_outliner",
            True,
            NodeId.DEPENDENCY_EDITOR_TEAM.value,
        ),
    ],
)
async def test_outline_node_sets_prompt_and_tool_schema_branch_from_selected_method(
    selected_method,
    expected_prompt,
    expected_with_dep_driving,
    expected_next_node,
):
    """OutlineNode 应按 session 中的大纲模式设置 prompt，并通过 with_dep_driving 选择工具 schema 分支。"""
    session = Mock(spec=Session)
    session.update_global_state = Mock()
    created_outliners = []

    class FakeOutliner:
        def __init__(self, llm_model_name, prompt_name):
            self.llm_model_name = llm_model_name
            self.prompt_name = prompt_name
            self.with_dep_driving = False
            self.generated_with_dep_driving = None
            created_outliners.append(self)

        async def generate_outline(self, current_inputs):
            self.generated_with_dep_driving = self.with_dep_driving
            return {
                "success_flag": True,
                "error_msg": "",
                "current_outline": Outline(
                    language="zh-CN",
                    title="测试大纲",
                    thought="mock",
                    sections=[
                        Section(
                            title="章节一",
                            description="测试说明",
                            is_core_section=False,
                        )
                    ],
                ),
            }

    def _get_global_state(key):
        mapping = {
            "search_context.language": "zh-CN",
            "search_context.messages": [],
            "search_context.questions": "",
            "search_context.user_feedback": "",
            "config.outliner_max_section_num": 5,
            "config.outliner_max_generate_outline_retry_num": 1,
            "search_context.report_template": "",
            "search_context.outline_interactions": [],
            "search_context.current_outline": None,
            "config.outline_interaction_enabled": False,
            "config.api_tools_config": {},
            "search_context.entry_search_results": [],
            "search_context.report_type_policy": {},
            "search_context.research_intent": {},
            "search_context.outline_execution_method": selected_method,
        }
        return mapping.get(key)

    session.get_global_state.side_effect = _get_global_state
    node = OutlineNode()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.adapt_llm_model_name",
        return_value="basic",
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.Outliner",
        new=FakeOutliner,
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.custom_stream_output",
        new_callable=AsyncMock,
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.add_debug_log_wrapper",
    ):
        result = await node._do_invoke({}, session, Context())

    assert created_outliners[0].prompt_name == expected_prompt
    assert created_outliners[0].generated_with_dep_driving is expected_with_dep_driving
    assert result["next_node"] == expected_next_node


def test_dependency_outline_node_with_dep_driving_ignores_session_outline_method():
    """DependencyOutlineNode 固定依赖驱动，不应被 session 中的 outline_execution_method=parallel 覆盖。"""
    node = DependencyOutlineNode()

    current_inputs = {
        "outline_execution_method": ExecutionMethod.PARALLEL.value,
    }

    assert node._get_with_dep_driving(current_inputs) is True
    assert node._select_prompt_name(current_inputs) == "dep_driving_outliner"


@pytest.mark.parametrize(
    "current_inputs, expected_prompt",
    [
        (
            {
                "outline_execution_method": ExecutionMethod.DEPENDENCY_DRIVING.value,
                "report_template": "模板内容",
                "outline_interaction_mode": "",
            },
            "outliner_template",
        ),
        (
            {
                "outline_execution_method": ExecutionMethod.DEPENDENCY_DRIVING.value,
                "outline_interaction_mode": "revise_outline",
                "user_feedback": '{"title": "用户大纲", "thought": "mock", "sections": []}',
                "report_template": "",
            },
            "outliner_user_revised",
        ),
    ],
)
def test_outline_node_parallel_contract_prompts_use_general_tool_schema(current_inputs, expected_prompt):
    """普通章节契约 prompt 不应搭配依赖驱动工具 schema。"""
    node = OutlineNode()

    prompt_name, with_dep_driving, selected_method = node._select_prompt_and_dep_driving(current_inputs)

    assert prompt_name == expected_prompt
    assert with_dep_driving is False
    assert selected_method == ExecutionMethod.PARALLEL.value


def test_outline_node_parallel_contract_prompt_resets_session_outline_method():
    """特殊 prompt 强制使用普通工具 schema 时，应同步重置 session 中的大纲模式。"""
    node = OutlineNode()
    session = Mock(spec=Session)
    session.update_global_state = Mock()
    current_inputs = {
        "outline_execution_method": ExecutionMethod.DEPENDENCY_DRIVING.value,
        "report_template": "template",
        "outline_interaction_mode": "",
    }

    prompt_name, with_dep_driving, selected_method = node._select_prompt_and_dep_driving(current_inputs)
    node._sync_outline_execution_method(current_inputs, session, selected_method)

    assert prompt_name == "outliner_template"
    assert with_dep_driving is False
    assert selected_method == ExecutionMethod.PARALLEL.value
    assert current_inputs["outline_execution_method"] == ExecutionMethod.PARALLEL.value
    session.update_global_state.assert_called_once_with(
        {"search_context.outline_execution_method": ExecutionMethod.PARALLEL.value}
    )


def test_generate_questions_keeps_prompt_generated_questions_when_report_type_unspecified():
    session = Mock(spec=Session)
    session.update_global_state = Mock()
    node = GenerateQuestionsNode()
    output_lines = "1. 关注哪些关键企业？\n2. 重点看哪些政策？\n3. 需要哪些数据口径？"

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.add_debug_log_wrapper"
    ):
        output = node._post_handle(
            {"report_type": None, "language": "zh-CN"},
            {"result": output_lines},
            session,
            Context(),
        )

    assert output["next_node"] == NodeId.FEEDBACK_HANDLER.value
    session.update_global_state.assert_any_call({"search_context.questions": output_lines})


def test_generate_questions_keeps_original_questions_when_report_type_present():
    session = Mock(spec=Session)
    session.update_global_state = Mock()
    node = GenerateQuestionsNode()
    output_lines = "1. 核心市场边界如何定义？\n2. 时间范围是否限定近三年？\n3. 是否需要国际对比？"

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.add_debug_log_wrapper"
    ):
        output = node._post_handle(
            {"report_type": "professional", "language": "zh-CN"},
            {"result": output_lines},
            session,
            Context(),
        )

    assert output["next_node"] == NodeId.FEEDBACK_HANDLER.value
    session.update_global_state.assert_any_call({"search_context.questions": output_lines})


def test_feedback_handler_merges_reparsed_intent_and_updates_report_policy():
    session = Mock(spec=Session)
    session.update_global_state = Mock()

    def _get_global_state(key):
        mapping = {
            "search_context.original_query": "低空经济发展趋势",
            "search_context.research_intent": {
                "section_count": 5,
                "audience_role": "投资人",
                "tone": "analytical",
                "report_type": "professional",
                "include_url": [],
                "exclude_url": [],
                "include_domains": ["example.com"],
                "exclude_domains": [],
            },
            "config.web_search_engine_config": Mock(search_engine_name="tavily"),
            "search_context.messages": [{"role": "user", "content": "低空经济发展趋势"}],
        }
        return mapping.get(key)

    session.get_global_state.side_effect = _get_global_state
    node = FeedbackHandlerNode()
    reparsed_intent = {
        "original_query": "请研究低空经济",
        "research_intent": {
            "section_count": None,
            "audience_role": None,
            "tone": None,
            "report_type": "brief",
            "include_url": [],
            "exclude_url": [],
            "include_domains": ["gov.cn"],
            "exclude_domains": [],
        },
    }

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.add_debug_log_wrapper"
    ), patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.apply_web_search_domain_constraints"
    ) as mock_apply_domains:
        output = node._post_handle(
            {},
            {"user_feedback": "我要精简版", "reparsed_intent": reparsed_intent},
            session,
            Context(),
        )

    assert output["next_node"] == NodeId.OUTLINE.value
    update_payloads = [call.args[0] for call in session.update_global_state.call_args_list]
    merged_payload = next(
        payload for payload in update_payloads if "search_context.research_intent" in payload
    )
    assert merged_payload["search_context.research_intent"]["report_type"] == "brief"
    assert "gov.cn" in merged_payload["search_context.research_intent"]["include_domains"]
    assert merged_payload["search_context.report_type_policy"]["report_type"] == "brief"
    mock_apply_domains.assert_called_once()


@pytest.mark.asyncio
async def test_pre_handle():
    try:
        editor_team_node = TestEditorTeamNode()
        workflow_session = AsyncMock()  # Use mock for session
        await editor_team_node.pre_handle({}, workflow_session, Context())
    except Exception as e:
        logger.error(f"fail to test_pre_handle: {e}")


@pytest.mark.asyncio
async def test_start_node_merges_agent_llm_timeouts_into_session_config():
    """验证 StartNode 会将 agent_llm_timeouts 合并进 session 配置快照。

    Returns:
        None.
    """
    node = StartNode()
    session = Mock()
    session.update_global_state = Mock()

    await node.invoke(
        {
            "query": "hello",
            "thread_id": "thread-1",
            "agent_config": {
                "llm_config": {"general": {"model_name": "demo"}},
                "web_search_engine_config": {"search_engine_name": "tavily"},
                "local_search_engine_config": {"search_engine_name": "openapi"},
                "agent_llm_timeouts": {"default": 300, "sub_reporter": 120},
            },
        },
        session,
        Context(),
    )

    search_context = session.update_global_state.call_args_list[0][0][0]["search_context"]
    assert search_context["original_query"] == "hello"
    assert "research_query" not in search_context

    merged_config = session.update_global_state.call_args_list[-1][0][0]["config"]
    assert merged_config["agent_llm_timeouts"] == {"default": 300, "sub_reporter": 120}


@pytest.mark.asyncio
async def test_end_node_writes_workflow_llm_usage_when_stats_enabled():
    """验证 EndNode 在开启统计时会写入 workflow 级 token 汇总。"""
    session = AsyncMock(spec=Session)
    final_result = {"response_content": "ok", "exception_info": ""}
    workflow_usage = {
        "input_tokens": 10,
        "output_tokens": 6,
        "total_tokens": 16,
        "llm_call_count": 3,
        "agent_name_token_usage": [
            {
                "agent_name": "entry",
                "input_tokens": 10,
                "output_tokens": 6,
                "total_tokens": 16,
                "llm_call_count": 3,
            }
        ],
    }

    def _get_global_state(key):
        if key == "search_context.final_result":
            return final_result
        if key == "config.stats_info_llm":
            return True
        if key == "config.thread_id":
            return "test-thread-id"
        return None

    session.get_global_state.side_effect = _get_global_state
    session.write_custom_stream = AsyncMock()
    session.update_global_state = Mock()
    node = EndNode()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.get_effective_workflow_llm_usage",
        return_value=workflow_usage,
    ) as mock_get_workflow_usage:
        output = await node.invoke({}, session, Context())

    mock_get_workflow_usage.assert_called_once_with(session_id="test-thread-id", session=session)
    session.update_global_state.assert_any_call(
        {"search_context.final_result.workflow_llm_token_usage": workflow_usage}
    )
    result_data = json.loads(output["final_result"])
    assert result_data["workflow_llm_token_usage"] == workflow_usage


@pytest.mark.asyncio
async def test_end_node_skips_workflow_llm_usage_when_stats_disabled():
    """验证 EndNode 在关闭统计时不会注入 workflow 级 token 汇总。"""
    session = AsyncMock(spec=Session)
    final_result = {"response_content": "ok", "exception_info": ""}

    def _get_global_state(key):
        if key == "search_context.final_result":
            return final_result
        if key == "config.stats_info_llm":
            return False
        if key == "config.thread_id":
            return "test-thread-id"
        return None

    session.get_global_state.side_effect = _get_global_state
    session.write_custom_stream = AsyncMock()
    session.update_global_state = Mock()
    node = EndNode()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.get_effective_workflow_llm_usage"
    ) as mock_get_workflow_usage:
        output = await node.invoke({}, session, Context())

    mock_get_workflow_usage.assert_not_called()
    result_data = json.loads(output["final_result"])
    assert "workflow_llm_token_usage" not in result_data


@pytest.mark.asyncio
async def test_end_node_falls_back_to_persisted_usage_when_local_empty():
    """验证 EndNode 在本地累计为空时会回退 session 快照。"""
    session = AsyncMock(spec=Session)
    final_result = {"response_content": "ok", "exception_info": ""}
    persisted_usage = {
        "input_tokens": 8,
        "output_tokens": 4,
        "total_tokens": 12,
        "llm_call_count": 2,
        "agent_name_token_usage": [
            {
                "agent_name": "outline",
                "input_tokens": 8,
                "output_tokens": 4,
                "total_tokens": 12,
                "llm_call_count": 2,
            }
        ],
    }

    def _get_global_state(key):
        if key == "search_context.final_result":
            return final_result
        if key == "config.stats_info_llm":
            return True
        if key == "config.thread_id":
            return "test-thread-id"
        if key == "search_context.final_result.workflow_llm_token_usage":
            return persisted_usage
        return None

    session.get_global_state.side_effect = _get_global_state
    session.write_custom_stream = AsyncMock()
    session.update_global_state = Mock()
    node = EndNode()
    local_empty_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "llm_call_count": 0,
        "agent_name_token_usage": [],
    }

    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.get_workflow_llm_usage",
        return_value=local_empty_usage,
    ):
        output = await node.invoke({}, session, Context())

    result_data = json.loads(output["final_result"])
    assert result_data["workflow_llm_token_usage"] == persisted_usage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_factory", "method_name", "method_args", "interact_payload", "assert_output", "thread_id"),
    [
        (
            FeedbackHandlerNode,
            "_get_user_feedback",
            ("web",),
            '{"feedback":"ok"}',
            lambda output: output == "ok",
            "tid-1",
        ),
        (
            OutlineInteractionNode,
            "_get_user_input",
            ("web", "1"),
            '{"interrupt_feedback":"accepted","feedback":"ok"}',
            lambda output: output["interrupt_feedback"] == "accepted",
            "tid-2",
        ),
        (
            UserFeedbackProcessorNode,
            "_get_user_feedback",
            ("web",),
            '{"action":"finish"}',
            lambda output: output == '{"action":"finish"}',
            "tid-3",
        ),
    ],
)
async def test_web_interaction_nodes_persist_usage_before_interact(
    node_factory,
    method_name,
    method_args,
    interact_payload,
    assert_output,
    thread_id,
):
    """验证各类 web 交互节点在 interact 前会持久化 token 累计。"""
    session = AsyncMock(spec=Session)
    session.get_global_state.side_effect = lambda key: {
        "config.stats_info_llm": True,
        "config.thread_id": thread_id,
    }.get(key)
    session.interact = AsyncMock(return_value=interact_payload)
    node = node_factory()

    with patch(
        "openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes.save_workflow_llm_usage_to_session"
    ) as mock_save:
        output = await getattr(node, method_name)(*method_args, session)

    assert assert_output(output)
    mock_save.assert_called_once_with(session=session, session_id=thread_id)


class MockAgent(DeepresearchAgent):
    def __init__(self):
        super().__init__()

    def _build_research_workflow(self, has_template=False):
        _id = self.research_name
        name = self.research_name
        version = self.version
        # workflow配置
        card = WorkflowCard(
            id=_id,
            version=version,
            name=name,
        )
        # workflow
        flow = Workflow(card=card)
        # 添加node
        flow.set_start_comp(
            start_comp_id=NodeId.START.value,
            component=StartNode(),
            inputs_schema=self.startnode_input_schema
        )
        flow.add_workflow_comp(NodeId.EDITOR_TEAM.value, EditorTeamNode())
        flow.set_end_comp(NodeId.END.value, EndNode())
        # 添加边 add_connection
        flow.add_connection(NodeId.START.value, NodeId.EDITOR_TEAM.value)
        flow.add_connection(NodeId.EDITOR_TEAM.value, NodeId.END.value)
        return flow
