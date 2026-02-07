# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging

from openjiuwen.core.utils.tool.function.function import LocalFunction
from openjiuwen.core.utils.tool.param import Param
from pydantic import BaseModel, Field

from jiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import Plan, StepType, Step
from jiuwen_deepsearch.utils.constants_utils.runtime_contextvars import llm_context
from jiuwen_deepsearch.utils.common_utils.llm_utils import messages_to_json, ainvoke_llm_with_stats
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

logger = logging.getLogger(__name__)


def generate_plan(language: str, title: str, thought: str, is_research_completed: bool,
                  steps: list[Step] = None) -> Plan:
    """从FunctionCall封装plan"""
    plan = Plan(
        language=language,
        title=title,
        thought=thought,
        is_research_completed=is_research_completed,
        steps=[
            Step(type=StepType.INFO_COLLECTING, title=step.get("title", ""), description=step.get("description", ""))
            for step in (steps or [])
        ],
    )

    return plan


def create_plan_tool(max_step_num: int):
    """获取plan生成工具"""
    plan_tool = LocalFunction(
        name="generate_plan",
        description="Generate a research plan for one section of the Systematic Research Report.",
        params=[
            Param(
                name="language",
                description="Output language, e.g. 'zh-CN' or 'en-US'",
                param_type="string",
                required=True
            ),
            Param(
                name="title",
                description="Title of the plan, summarizing the overall objectives.",
                param_type="string",
                required=True
            ),
            Param(
                name="thought",
                description="The thought process behind the plan, explaining the "
                            "sequence of steps and the reasons for the choices.",
                param_type="string",
                required=True
            ),
            Param(
                name="is_research_completed",
                description="Is the information sufficient? Has the information collection been completed?",
                param_type="boolean",
                required=True
            ),
            Param(
                name="steps",
                description=f"Detailed list of step-by-step tasks if information is still insufficient. "
                            f"(Maximum number of steps: {max_step_num})",
                param_type="array<object>",
                required=False,
                schema=[
                    Param(
                        name="type",
                        description=f"Step Type (Enumeration Value: {StepType.INFO_COLLECTING.value})",
                        param_type="string",
                        required=True
                    ),
                    Param(
                        name="title",
                        description="The title of the task, summarizing the content of this step.",
                        param_type="string",
                        required=True
                    ),
                    Param(
                        name="description",
                        description="Detailed instructions for this step, clearly specifying "
                                    "the data or content that needs to be collected.",
                        param_type="string",
                        required=True
                    ),
                ]
            ),
        ],
        func=generate_plan
    )

    return plan_tool


class PlannerConfig(BaseModel):
    """初始化配置"""
    llm: object = Field(default=None, description="调用大模型的实例")
    prompt: str = Field(default="planner", description="prompt模版名称")
    max_retry_num: int = Field(default=1, description="失败自重试次数")
    sleep_interval: int = Field(default=2, description="失败自重试时间间隔（单位：s）")
    llm_model_name: str = Field(default="basic", description="大模型名称")


class PlannerResult(BaseModel):
    plan_success: bool = Field(default=False, description="生成计划是否成功")
    plan: Plan | None = Field(default=None, description="生成的计划实例")
    response_messages: list = Field(default=[], description="响应的消息列表")
    error_msg: str = Field(default="", description="错误信息（如果有）")
    extra_body: dict = Field(default=None, description="其它额外的自定义信息（如果有）")


class Planner:
    def __init__(self, config: PlannerConfig = PlannerConfig()):
        self.config = config

        # default llm
        if not self.config.llm:
            self.config.llm = llm_context.get().get(config.llm_model_name)

    async def generate_plan(self, current_inputs: dict) -> PlannerResult:
        """Generating a complete plan."""
        log_prefix = (f"section_idx: {current_inputs.get('section_idx')} | "
                      f"Round {current_inputs.get('plan_executed_num', -1) + 1}/"
                      f"{current_inputs.get('max_plan_executed_num')} | ")
        logger.info(f"{log_prefix}Planner starting")
        prompt = apply_system_prompt(self.config.prompt, current_inputs)
        if LogManager.is_sensitive():
            logger.info(f"{log_prefix}The planner invoke messages is ready.")
        else:
            logger.info(f"{log_prefix}planner invoke messages: %s", messages_to_json(prompt))

        planner_result = PlannerResult()
        tool = create_plan_tool(current_inputs.get("max_step_num"))
        stream_meta = {"plan_idx": str(current_inputs.get("plan_executed_num", 0) + 1)}
        # 重试机制
        max_retries = self.config.max_retry_num
        for attempt in range(max_retries):
            progress_bar = f"({attempt + 1}/{max_retries})"  # 重试进度
            try:
                # invoke LLM
                response = await ainvoke_llm_with_stats(
                    llm=self.config.llm,
                    messages=prompt,
                    tools=[tool.get_tool_info()],
                    agent_name=NodeId.PLAN_REASONING.value,
                    need_stream_out=False,
                    stream_meta=stream_meta
                )

                tool_calls = response.get('tool_calls', [])
                check_tool_call(tool, tool_calls)

                for tool_call in tool_calls:
                    plan = await tool.ainvoke(tool_call.get("args"))
                    # 规划成功
                    planner_result.plan_success = True
                    planner_result.plan = plan
                    # toolcall和结果应该成对出现
                    planner_result.response_messages.append(response)
                    planner_result.response_messages.append(
                        {
                            "name": tool.name,
                            "role": "tool",
                            "content": f"{plan.model_dump_json()}",
                            "tool_call_id": tool_call.get("id"),
                        }
                    )

                    logger.info(
                        f"{log_prefix}The plan generation is completed{progress_bar}: "
                        f"{'**' if LogManager.is_sensitive() else plan.model_dump_json(indent=4)}")
                    break  # only one toolcall

                break  # Success, exit retry loop
            except Exception as e:
                msg = (f"{log_prefix}Error when Planner generating a plan. retry {progress_bar}."
                       f"error: {'**' if LogManager.is_sensitive() else e}")
                if attempt + 1 < max_retries:
                    logger.warning(msg)
                else:
                    logger.error(msg)
                planner_result.error_msg = msg

        return planner_result


def check_tool_call(tool: LocalFunction, tool_calls: list):
    """
        Args:
            tool: 定义的 plan FunctionCall
            tool_calls: 模型实际的给出的 tool_calls
    """
    is_sensitive = LogManager.is_sensitive()
    if not tool_calls:
        raise CustomValueException(StatusCode.PLANNER_GENERATE_ERROR.code, "No plan tool calls found in response")
    if len(tool_calls) > 1:
        logger.error("Multiple tool calls found in response")
    for tool_call in tool_calls:
        tool_name = tool_call.get("name", "")
        arguments = tool_call.get("args", {})
        if tool_name != tool.name:
            # 手动纠正工具名
            tool_call["name"] = tool.name
            logger.error(f"Tool name is not match({tool.name}): {'**' if is_sensitive else tool_name}")
        if not arguments:
            raise CustomValueException(
                StatusCode.PLANNER_GENERATE_ERROR.code,
                f"No arguments found in tool call: {'**' if is_sensitive else tool_call}"
            )
        if not isinstance(arguments, dict):
            raise CustomValueException(
                StatusCode.PLANNER_GENERATE_ERROR.code,
                f"Args is not a dict in tool call: {'**' if is_sensitive else tool_call}"
            )
        for param in tool.params:
            if param.required and param.name not in arguments:
                raise CustomValueException(
                    StatusCode.PLANNER_GENERATE_ERROR.code,
                    f"Required param '{param.name}' not found in tool call: {'**' if is_sensitive else tool_call}"
                )

        # 信息不充足，但是没有详细的任务步骤
        if not arguments["is_research_completed"] and not arguments.get("steps"):
            raise CustomValueException(
                StatusCode.PLANNER_GENERATE_ERROR.code,
                f"Research not completed but steps are empty: {'**' if is_sensitive else tool_call}"
            )

        # 效验steps内部
        _check_steps(arguments, tool, tool_call)


def _check_steps(arguments, tool, tool_call):
    is_sensitive = LogManager.is_sensitive()
    if arguments.get("steps"):
        steps = arguments["steps"]
        if not isinstance(steps, list):
            raise CustomValueException(
                StatusCode.PLANNER_GENERATE_ERROR.code,
                f"Steps is not a list in tool call: {'**' if is_sensitive else tool_call}"
            )
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise CustomValueException(
                    StatusCode.PLANNER_GENERATE_ERROR.code,
                    f"Steps[{i}] is not a dict in tool call: {'**' if is_sensitive else tool_call}"
                )

            for param in tool.params:
                if param.name == "steps":
                    for step_param in param.schema:
                        if step_param.required and step_param.name not in step:
                            raise CustomValueException(
                                StatusCode.PLANNER_GENERATE_ERROR.code,
                                f"Required step param '{step_param.name}' not found in tool call: "
                                f"{'**' if is_sensitive else tool_call}"
                            )
