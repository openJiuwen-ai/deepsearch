# ******************************************************************************
# Copyright (c) 2025 Huawei Technologies Co., Ltd.
# jiuwen-deepsearch is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#          http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.
# ******************************************************************************/

from openjiuwen.core.workflow.components.flow.start_comp import Start
from openjiuwen.core.workflow.workflow import Workflow
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import (SectionEndNode,
                                                                                                SubReporterNode,
                                                                                                SubSourceTracerNode)
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.section_context import SectionContext
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import init_router
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


class SectionWritingStartNode(Start):
    """
    依赖驱动任务规划子图起始节点，初始化inputs到子图runtime的section_state中
    """
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """执行起始节点"""
        # 初始化section_context
        section_context = SectionContext(
            language=inputs.get("language", "zh-CN"),
            messages=inputs.get("messages", []),
            section_idx=inputs.get("section_idx", '1'),
            current_outline=inputs.get("current_outline", {}),
            report_task=inputs.get("report_task"),
            section_task=inputs.get("section_task", ""),
            section_description=inputs.get("section_description", ""),
            section_iscore=inputs.get("section_iscore", False),
            report_template=inputs.get("report_template", ""),
            sub_report_background_knowledge=inputs.get("sub_report_background_knowledge", []),
            session_id=inputs.get("session_id", ""),
            history_plans=inputs.get("history_plans", []),
        )
        config = inputs.get("config")
        session.update_global_state({"section_context": section_context.model_dump(), "config": config})
        return inputs



def build_dependency_writing_workflow():
    """创建子图workflow"""
    sub_workflow = Workflow()

    sub_workflow.set_start_comp(NodeId.START.value, SectionWritingStartNode(),
        inputs_schema={
            "language": "${language}",
            "messages": "${messages}",
            "section_idx": "${section_idx}",
            "current_outline": "${current_outline}",
            "report_task": "${report_task}",
            "config": "${config}",
            "section_task": "${section_task}",
            "section_description": "${section_description}",
            "section_iscore": "${section_iscore}",
            "report_template": "${report_template}",
            "sub_report_background_knowledge": "${sub_report_background_knowledge}",
            "session_id": "${session_id}",
            "history_plans": "${history_plans}",
        }
    )
    sub_workflow.add_workflow_comp(NodeId.SUB_REPORTER.value, SubReporterNode())
    sub_workflow.add_workflow_comp(NodeId.SUB_SOURCE_TRACER.value, SubSourceTracerNode())
    sub_workflow.add_connection(NodeId.START.value, NodeId.SUB_REPORTER.value)
    sub_reporter_router = init_router(NodeId.SUB_REPORTER.value,
                                      [NodeId.SUB_SOURCE_TRACER.value, NodeId.END.value])
    sub_workflow.add_conditional_connection(NodeId.SUB_REPORTER.value, router=sub_reporter_router)
    sub_workflow.add_connection(NodeId.SUB_SOURCE_TRACER.value, NodeId.END.value)
    sub_workflow.set_end_comp(NodeId.END.value, SectionEndNode())
    return sub_workflow
