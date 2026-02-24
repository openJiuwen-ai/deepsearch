import logging
from contextvars import Context
from unittest.mock import AsyncMock, patch

import pytest
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.base import WorkflowCard
from openjiuwen.core.workflow.workflow import Workflow

from openjiuwen_deepsearch.framework.openjiuwen.agent.editor_team_manager_node import EditorTeamNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.main_graph_nodes import EndNode, StartNode
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import \
    build_editor_team_workflow
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline, Section
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
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
    search_context = {'session_id': 'default_session_id', 'query': '杭州的天气怎么样', 'messages': [{...}],
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
                      'debug_pre_step': 'outline-c615f84c-d865-41f6-b7c3-354703c51732', 'go_deepsearch': True,
                      'debug_cur_step': 'outline-c615f84c-d865-41f6-b7c3-354703c51732'}
    editor_team_node.create_section_state_from_state(search_context, outline, outline_section)


class TestEditorTeamNode(EditorTeamNode):
    async def run_section_sub_graph(self, workflow_session, sub_workflow, input_state):
        return await self._run_section_sub_graph_await(
            workflow_session, sub_workflow, input_state
        )

    def pre_handle(self, inputs, session, context):
        return self._pre_handle(inputs, session, context)

    def create_section_state_from_state(self, state, outline, section):
        return self._create_section_state_from_state(state, outline, section)


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
async def test_pre_handle():
    try:
        editor_team_node = TestEditorTeamNode()
        workflow_session = AsyncMock()  # Use mock for session
        await editor_team_node.pre_handle({}, workflow_session, Context())
    except Exception as e:
        logger.error(f"fail to test_pre_handle: {e}")


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
