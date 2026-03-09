# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import logging
from typing import List

from openjiuwen.core.common.constants.constant import INPUTS_KEY
from openjiuwen.core.workflow.components.flow.end_comp import End
from openjiuwen.core.workflow.components.flow.start_comp import Start
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.workflow import Workflow

from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.editor_team_nodes import (
    BasePlanReasoningNode,
    InfoCollectorNode,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.reasoning_writing_graph.section_context import (
    SectionReasoningContext,
)
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Message, Step, Plan
from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import init_router
from openjiuwen_deepsearch.utils.debug_utils.node_debug import add_debug_log_wrapper, NodeType, NodeDebugData
from openjiuwen_deepsearch.utils.common_utils.llm_utils import messages_to_json
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

logger = logging.getLogger(__name__)


class SectionReasoningStartNode(Start):
    """
    依赖驱动任务规划子图起始节点，初始化inputs到子图runtime的section_state中
    """

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """执行起始节点"""
        log_prefix = f"[{self.__class__.__name__}]"
        logger.info(f"{log_prefix} | Start {self.__class__.__name__}")

        added_completed_steps = inputs.get("parent_section_steps") or []
        plan_background_knowledge = _extract_plan_background_knowledge(added_completed_steps)
        step_background_knowledge = _extract_step_background_knowledge(added_completed_steps)

        # 初始化section_context和config
        section_context = SectionReasoningContext(
            language=inputs.get("language", "zh-CN"),
            messages=inputs.get("messages", []),
            section_idx=inputs.get("section_idx", '1'),
            plan_background_knowledge=plan_background_knowledge,
            step_background_knowledge=step_background_knowledge,
        )
        config = inputs.get("config")
        session.update_global_state({"section_context": section_context.model_dump(),
                                     "config": config})
        logger.info(f"{log_prefix} | "
                    f"End {self.__class__.__name__} in section_idx: {section_context.section_idx}")

        return inputs


class DependencyPlanReasoningNode(BasePlanReasoningNode):

    def __init__(self):
        super().__init__()
        self.prompt = "dep_driving_planner"
        self.log_prefix = ""

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state('section_context.section_idx')
        self.log_prefix = f"section_idx: {section_idx} | [{self.__class__.__name__}] "
        logger.info(f"{self.log_prefix} | Start {self.__class__.__name__}")

        current_inputs = super()._pre_handle(inputs, session, context)

        current_inputs["plan_background_knowledge"] = session.get_global_state(
            "section_context.plan_background_knowledge"
        )
        
        return current_inputs

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        section_idx = session.get_global_state("section_context.section_idx")
        plan_executed_num = session.get_global_state("section_context.plan_executed_num") + 1
        plan_background_knowledge = session.get_global_state("section_context.plan_background_knowledge")
        messages = session.get_global_state("section_context.messages")
        plan = algorithm_output.get("plan")
        response_messages = algorithm_output.get("response_messages")
        plan_success = algorithm_output.get("success", False)

        # 1. plan生成成功，进一步判断
        if plan_success and plan:
            plan.id = f"{section_idx}-{plan_executed_num}"
            plan.background_knowledge = plan_background_knowledge
            debug_info = messages_to_json(response_messages)
            # 信息收集不足，则收集信息
            if not plan.is_research_completed:
                next_node = NodeId.INFO_COLLECTOR.value
                logger.info(f"{self.log_prefix} | Research not completed, go to info_collector")
            # 信息收集足够，则结束工作流
            else:
                next_node = NodeId.END.value
                logger.info(f"{self.log_prefix} | Research completed, go to end")
        # 2. plan生成失败，结束工作流
        else:
            debug_info = algorithm_output.get("error_msg")
            next_node = NodeId.END.value
            logger.info(f"{self.log_prefix} | Plan failed, go to end")

        add_debug_log_wrapper(session, NodeDebugData(NodeId.PLAN_REASONING.value, 0, NodeType.SUB.value,
                                                     output_content=debug_info))

        session.update_global_state({
            "section_context.current_plan": plan,
            "section_context.messages": messages + response_messages,
            "section_context.plan_executed_num": plan_executed_num,
            "section_context.added_completed_steps": [],
            "section_context.current_plan_is_completed": False,
        })

        return dict(next_node=next_node)


class DependencyInfoCollectorNode(InfoCollectorNode):

    def __init__(self):
        super().__init__()
        self.log_prefix = None

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        section_idx = session.get_global_state("section_context.section_idx")
        current_plan = session.get_global_state("section_context.current_plan")
        self.log_prefix = f"section_idx: {section_idx} | plan_id: {current_plan.id} | [{self.__class__.__name__}] "
        logger.info(f"{self.log_prefix} | Start {self.__class__.__name__}")
        added_completed_steps = session.get_global_state("section_context.added_completed_steps")
        current_plan_is_completed = session.get_global_state("section_context.current_plan_is_completed")

        current_inputs = super()._pre_handle(inputs, session, context)
        current_inputs["plan_background_knowledge"] = session.get_global_state(
            "section_context.plan_background_knowledge")
        current_inputs["step_background_knowledge"] = session.get_global_state(
            "section_context.step_background_knowledge")
        current_inputs["history_plans"] = session.get_global_state("section_context.history_plans")
        current_inputs["added_completed_steps"] = added_completed_steps
        current_inputs["current_plan_is_completed"] = current_plan_is_completed
        
        return current_inputs

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        section_state = self._pre_handle(inputs, session, context)
        kb_display = '*' if LogManager.is_sensitive() else section_state.get('plan_background_knowledge')
        logger.info(f"{self.log_prefix} | plan background knowledge is: {kb_display}")

        current_plan = section_state.get("current_plan")
        step_background_knowledge = section_state.get("step_background_knowledge")
        # 当前 plan 已经完成的 steps
        added_completed_steps = [step.id for step in section_state.get("added_completed_steps", [])]

        # 筛选需要执行的步骤
        plan_executed_steps = []
        async_collecting_list = []
        for step in current_plan.steps:
            if (step.id and step.id not in added_completed_steps and
                    # 检查步骤依赖是否就绪
                    all(parent_id in step_background_knowledge for parent_id in step.parent_ids)):
                for parent_id in step.parent_ids:
                    if parent_id in step_background_knowledge:
                        step.background_knowledge.append(step_background_knowledge.get(parent_id, ""))

                collector_inputs = self._build_collector_input(current_plan, step, section_state)
                plan_executed_steps.append(step)
                inputs.update({INPUTS_KEY: collector_inputs})
                task = asyncio.create_task(self._run_collector_graph(inputs, session, context))
                async_collecting_list.append(task)

        # 并行执行收集任务
        collector_results = []
        if async_collecting_list:
            logger.info(
                f"{self.log_prefix} | Start steps {[step.id for step in plan_executed_steps]}: "
                f"{'*' if LogManager.is_sensitive() else plan_executed_steps}"
            )
            collector_results = await asyncio.gather(*async_collecting_list)
        section_state = self._update_section_state(section_state, plan_executed_steps, collector_results)
        result = self._post_handle(inputs, section_state, session, context)
        return result

    def _post_handle(self, inputs: Input, algorithm_output: dict, session: Session, context: ModelContext):
        current_plan_is_completed = algorithm_output.get('current_plan_is_completed')
        if current_plan_is_completed:
            session.update_global_state(
                {"section_context.plan_background_knowledge": algorithm_output.get("plan_background_knowledge")})
            session.update_global_state({"section_context.history_plans": algorithm_output.get("history_plans")})
        else:
            session.update_global_state(
                {"section_context.added_completed_steps": algorithm_output.get("added_completed_steps")})
            session.update_global_state(
                {"section_context.step_background_knowledge": algorithm_output.get("step_background_knowledge")})
            session.update_global_state({"section_context.messages": algorithm_output.get("messages")})

        add_debug_log_wrapper(session, NodeDebugData(NodeId.INFO_COLLECTOR.value, 0, NodeType.SUB.value,
                                                     output_content=str(algorithm_output).replace("\\n", "\n")))
        logger.info(f"{self.log_prefix} |  End {self.__class__.__name__}")

        if current_plan_is_completed:
            return dict(next_node=NodeId.PLAN_REASONING.value)
        return dict(next_node=NodeId.INFO_COLLECTOR.value)

    def _build_collector_input(self, plan: Plan, step: Step, state: dict):
        initial_search_query_count = state.get("initial_search_query_count", 2)
        max_research_loops = state.get("max_research_loops", 2)
        max_react_recursion_limit = state.get("max_react_recursion_limit", 8)
        plan_idx = plan.id.split("-")[-1] if plan.id and "-" in plan.id else "1"
        step_idx = step.id.split("-")[-1] if step.id and "-" in step.id else "1"

        message = f"Now deal with the task: \n"
        message += f"You should focus on [Topic]: {plan.title}\n"
        message += f"pay attention to [Condition]: {plan.thought}\n"
        message += f"[Task Title]: {step.title}\n[Problem]: {step.description}\n"
        message += f"[Applicable Background Knowledge]: {step.background_knowledge}\n\n"
        message += "Please analyze this task and start your ReAct process:\n"
        message += "1. Reason about what information you need to gather\n"
        message += "2. Use appropriate tools to get that information\n"
        message += "3. Make full use of the applicable background knowledge\n"
        message += "4. Continue reasoning and acting until you have sufficient information\n"
        message += "5. Call info_seeker_task_done when ready to provide your complete findings\n\n"
        message += "Begin with your initial reasoning about the task."

        collector_agent_input = {
            "language": state.get("language", "zh-CN"),
            "messages": [Message(role="user", content=message)],
            "section_idx": state.get("section_idx", '1'),
            "plan_idx": plan_idx,
            "step_idx": step_idx,
            "step_title": step.title,
            "step_description": step.description,
            "step_background_knowledge": step.background_knowledge or [],
            "initial_search_query_count": initial_search_query_count,
            "max_research_loops": max_research_loops,
            "max_react_recursion_limit": max_react_recursion_limit,
        }

        return collector_agent_input

    def _update_section_state(self, state: dict, plan_executed_steps: list, collector_results: list):
        plan_background_knowledge = state.get("plan_background_knowledge", {})
        plan_completed_steps = state.get("added_completed_steps", [])
        current_plan = state.get("current_plan")
        messages = state.get("messages", [])
        current_doc_num = 0

        # 1. 没有任务执行，则所有任务都已执行完成
        if not plan_executed_steps:
            # 填充执行完的steps到current plan，并保存在history plans
            plan_background_knowledge.update(_extract_plan_background_knowledge(plan_completed_steps))
            history_plans = state.get("history_plans", [])
            current_plan.steps = plan_completed_steps
            history_plans.append(current_plan)
            state["history_plans"] = history_plans
            state["plan_background_knowledge"] = plan_background_knowledge
            state["current_plan_is_completed"] = True
            return state

        # 2. 执行任务后，获取任务执行完成的结果
        for step, collector_context in zip(plan_executed_steps, collector_results):
            plan_completed_steps.append(step)
            step.retrieval_queries = collector_context.get("history_queries")
            step.step_result = collector_context.get("info_summary")
            step.evaluation = collector_context.get("evaluation")
            current_doc_num += len(collector_context.get("doc_infos", []))
            messages.append(
                Message(
                    role="assistant",
                    content=step.step_result,
                )
            )

            if not LogManager.is_sensitive():
                logger.info(f"{self.log_prefix} | Step {step.id} have completed: \n"
                            f"step title: {step.title} \n"
                            f"step description: {step.description} \n"
                            f"step summary result ：{step.step_result}\n"
                            f"step evaluation: {step.evaluation}")
            else:
                logger.info(f"{self.log_prefix} | Step {step.id} have completed")

        if current_doc_num == 0:
            collector_warning = (f"[{StatusCode.INFO_COLLECTING_EMPTY.code}] {self.log_prefix} "
                                 f"{StatusCode.INFO_COLLECTING_EMPTY.errmsg}")
            warning_infos = state.get("warning_infos", [])
            warning_infos.append(collector_warning)
            logger.warning(collector_warning)

        state["collected_doc_num"] = state.get("collected_doc_num", 0) + current_doc_num

        # 记录执行结果
        recording_data = {
            "plan_background_knowledge": list(plan_background_knowledge.keys()),
            "plan_generated_steps": [step.id for step in current_plan.steps],
            "plan_executed_steps": [step.id for step in plan_executed_steps],
            "plan_completed_steps": [step.id for step in plan_completed_steps],
        }
        logger.info(f"{self.log_prefix} | completed progress: \n{recording_data}")

        state["messages"] = messages
        state["added_completed_steps"] = plan_completed_steps
        step_background_knowledge = state.get("step_background_knowledge", {})
        step_background_knowledge.update(_extract_step_background_knowledge(plan_executed_steps))
        state["step_background_knowledge"] = step_background_knowledge

        return state


class SectionReasoningEndNode(End):
    """
    依赖驱动任务规划子图结束节点，将子图的runtime中的section_state的内容返回到主图
    """

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """
        执行结束节点
        """
        section_idx = session.get_global_state("section_context.section_idx")
        log_prefix = f"section_idx: {section_idx} | Start [{self.__class__.__name__}]"
        logger.info(f"{log_prefix} | Start {self.__class__.__name__}")
        section_state = {
            "plans": session.get_global_state("section_context.history_plans"),
        }
        logger.info(f"{log_prefix} | End {self.__class__.__name__}")

        return section_state


def _extract_plan_background_knowledge(steps: List[Step]):
    """提取任务规划生成的背景知识"""
    plan_background_knowledge = {}
    for step in steps:
        plan_background_knowledge[step.id] = (
            f"[Step id] : {step.id}; "
            f"[Step title] : {step.title}; "
            f"[Step description] : {step.description}; "
            f"[Step evaluation] : {step.evaluation};"
        )
    return plan_background_knowledge


def _extract_step_background_knowledge(steps: List[Step]):
    """提取步骤总结的背景知识"""
    step_background_knowledge = {}
    for step in steps:
        step_background_knowledge[step.id] = (
            f"[title] : {step.title}; "
            f"[description] : {step.description}; "
            f"[content] : {step.step_result};"
        )
    return step_background_knowledge


def build_dependency_reasoning_workflow():
    """ 创建依赖驱动的任务规划子图工作流 """
    sub_workflow = Workflow()

    sub_workflow.set_start_comp(
        NodeId.START.value,
        SectionReasoningStartNode(),
        inputs_schema={
            "language": "${language}",
            "messages": "${messages}",
            "section_idx": "${section_idx}",
            "parent_section_steps": "${parent_section_steps}",
            "config": "${config}",
        }
    )

    sub_workflow.add_workflow_comp(NodeId.PLAN_REASONING.value, DependencyPlanReasoningNode())
    sub_workflow.add_workflow_comp(NodeId.INFO_COLLECTOR.value, DependencyInfoCollectorNode())

    plan_reasoning_router = init_router(NodeId.PLAN_REASONING.value,
                                        [NodeId.INFO_COLLECTOR.value, NodeId.END.value])
    info_collector_router = init_router(NodeId.INFO_COLLECTOR.value,
                                        [NodeId.PLAN_REASONING.value, NodeId.INFO_COLLECTOR.value])

    sub_workflow.add_connection(NodeId.START.value, NodeId.PLAN_REASONING.value)
    sub_workflow.add_conditional_connection(NodeId.PLAN_REASONING.value, router=plan_reasoning_router)
    sub_workflow.add_conditional_connection(NodeId.INFO_COLLECTOR.value, router=info_collector_router)

    sub_workflow.set_end_comp(NodeId.END.value, SectionReasoningEndNode())

    return sub_workflow
