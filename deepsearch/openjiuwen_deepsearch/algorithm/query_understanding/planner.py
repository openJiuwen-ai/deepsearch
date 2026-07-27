# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from pydantic import BaseModel, Field

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode, format_exception_info
from openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api import build_runtime_api_tools, \
    merge_runtime_api_tools
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Plan, StepType, Step
from openjiuwen_deepsearch.utils.common_utils.llm_utils import messages_to_json, ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

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


def generate_dependency_plan(language: str, title: str, thought: str, is_research_completed: bool,
                             steps: list[Step] = None) -> Plan:
    """从FunctionCall封装dependency plan"""
    plan = Plan(
        language=language,
        title=title,
        thought=thought,
        is_research_completed=is_research_completed,
        steps=[
            Step(type=StepType.INFO_COLLECTING,
                 title=step.get("title", ""),
                 description=step.get("description", ""),
                 id=step.get("id", ""),
                 parent_ids=step.get("parent_ids", []),
                 relationships=step.get("relationships", []))
            for step in (steps or [])
        ],
    )

    return plan


def create_plan_tool(state: dict, prompt_template: str):
    """获取plan生成工具"""
    section_idx = state.get("section_idx", '1')
    max_step_num = state.get("max_step_num")
    plan_idx = state.get("plan_executed_num", 0) + 1

    card = ToolCard(
        id="generate_plan",
        name="generate_plan",
        description="Generate a research plan for one section of the Systematic Research Report.",
        input_params={
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "Output language, e.g. 'zh-CN' or 'en-US'"
                },
                "title": {
                    "type": "string",
                    "description": "Title of the plan without numbering, summarizing the overall objectives. Never "
                                   "include numbers, bullets, or prefixes like '1.', '2)', 'I.', '一、'."
                },
                "thought": {
                    "type": "string",
                    "description": (
                        "The thought process behind the plan, explaining the sequence of steps "
                        "and the reasons for the choices."
                    )
                },
                "is_research_completed": {
                    "type": "boolean",
                    "description": "Is the information sufficient? Has the information collection been completed?"
                },
                "steps": {
                    "type": "array",
                    "description": (
                        "Detailed list of step-by-step tasks if information is still insufficient. "
                        f"(Maximum number of steps: {max_step_num})"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "description": (
                                    "Step Type (Enumeration Value: "
                                    f"{StepType.INFO_COLLECTING.value})"
                                )
                            },
                            "title": {
                                "type": "string",
                                "description": (
                                    "The title of the task without numbering, summarizing the content of this step."
                                    "Never include numbers, bullets, or prefixes like '1.', '2)', 'I.', '一、'."
                                )
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "Detailed instructions for this step, clearly specifying the data "
                                    "or content that needs to be collected."
                                )
                            },
                            "id": {
                                "type": "string",
                                "description": f"Unique identifier of the step. "
                                               f"Format: '{section_idx}-{plan_idx}-sequence_number' (e.g., 3-1-2, "
                                               f"2-2-3). Only specify if this is a new step; do not recreate IDs "
                                               f"already present in Background Knowledge."
                            },
                            "parent_ids": {
                                "type": "array",
                                "description": "Array of parent step IDs that this step depends on. Empty array [] "
                                               "for root steps. Each parent ID must exist in either background "
                                               "knowledge or the current execution steps of plan.",
                                "items": {
                                    "type": "string"
                                }
                            },
                            "relationships": {
                                "type": "array",
                                "description": "Array specifying the relationship type to each corresponding parent "
                                               "step in parent_ids. Must have the same length as parent_ids array. "
                                               "Use terms like 'data correlation', 'causality', 'influence', "
                                               "'temporal', 'perspective', 'methodological', or other appropriate "
                                               "relationship descriptors.",
                                "items": {
                                    "type": "string"
                                }
                            }
                        },
                        "required": ["type", "title", "description"]
                    }
                }
            },
            "required": ["language", "title", "thought", "is_research_completed"]
        }
    )
    plan_tool = LocalFunction(
        card=card,
        func=generate_plan if prompt_template == "planner" else generate_dependency_plan
    )
    plan_tool.max_step_num = max_step_num

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
        log_prefix = (
            f"section_idx: {current_inputs.get('section_idx')} | "
            f"Round {current_inputs.get('plan_executed_num', -1) + 1}/"
            f"{current_inputs.get('max_plan_executed_num')} | "
        )
        logger.info(f"{log_prefix}Planner starting")
        prompt = apply_system_prompt(self.config.prompt, current_inputs)
        if LogManager.is_sensitive():
            logger.info(f"{log_prefix}The planner invoke messages is ready.")
        else:
            logger.info(f"{log_prefix}planner invoke messages: %s", messages_to_json(prompt))

        planner_result = PlannerResult()
        tools = [create_plan_tool(current_inputs, self.config.prompt)]
        api_tools = build_runtime_api_tools(
            current_inputs.get("api_tools_config", {}).get("query_understanding_tools", []),
            response_model=Plan,
        )
        tools = merge_runtime_api_tools(tools, api_tools)
        tool_dict = {tool.card.name: tool for tool in tools}
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
                    tools=[tool.card.tool_info() for tool in tools],
                    agent_name=AgentLlmName.PLAN_REASONING.value,
                    need_stream_out=False,
                    stream_meta=stream_meta
                )

                tool_calls = response.get('tool_calls', [])
                check_tool_call(tool_dict, tool_calls)

                for tool_call in tool_calls:
                    tool = tool_dict[tool_call.get("name")]
                    plan = await tool.invoke(tool_call.get("args"))
                    # 规划成功
                    planner_result.plan_success = True
                    planner_result.plan = plan
                    # toolcall和结果应该成对出现
                    planner_result.response_messages.append(response)
                    planner_result.response_messages.append(
                        {
                            "name": tool.card.name,
                            "role": "tool",
                            "content": f"{plan.model_dump_json()}",
                            "tool_call_id": tool_call.get("id"),
                        }
                    )

                    logger.info(
                        f"{log_prefix}The plan generation is completed{progress_bar}: "
                        f"{'**' if LogManager.is_sensitive() else plan.model_dump_json(indent=4)}",
                        extra={"skip_truncation": True},
                    )
                    break  # only one toolcall

                break  # Success, exit retry loop
            except Exception as e:
                msg = format_exception_info(
                    StatusCode.PLANNER_GENERATE_ERROR,
                    e,
                    prefix=f"{log_prefix}retry {progress_bar}. ",
                )
                if attempt + 1 < max_retries:
                    logger.warning(msg if not LogManager.is_sensitive() else f"{log_prefix}Planner retry failed.")
                else:
                    logger.error(msg if not LogManager.is_sensitive() else f"{log_prefix}Planner failed.")
                planner_result.error_msg = msg

        return planner_result


def check_tool_call(tool_dict: dict[str, LocalFunction], tool_calls: list):
    """
        Args:
            tool_dict: 定义的 plan FunctionCall 映射
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
        tool = tool_dict.get(tool_name)
        if tool is None and len(tool_dict) == 1:
            tool = next(iter(tool_dict.values()))
            tool_call["name"] = tool.card.name
            logger.error(f"Tool name is not match({tool.card.name}): {'**' if is_sensitive else tool_name}")
        elif tool is None:
            raise CustomValueException(
                StatusCode.PLANNER_GENERATE_ERROR.code,
                f"Tool name '{tool_name}' not found in tool call: {'**' if is_sensitive else tool_call}"
            )
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
        input_params = tool.card.input_params.get("properties", {})
        for param_name, _ in input_params.items():
            required = param_name in tool.card.input_params.get("required", [])
            if required and param_name not in arguments:
                raise CustomValueException(
                    StatusCode.PLANNER_GENERATE_ERROR.code,
                    f"Required param '{param_name}' not found in tool call: {'**' if is_sensitive else tool_call}"
                )

        # generate_plan 等内置工具：未完成研究且未给 steps 则报错。运行时 API 工具无该字段，跳过。
        if "is_research_completed" in input_params:
            if not arguments.get("is_research_completed") and not arguments.get("steps"):
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
        max_items = getattr(tool, "max_step_num", None)
        if isinstance(max_items, int) and len(steps) > max_items:
            raise CustomValueException(
                StatusCode.PLANNER_GENERATE_ERROR.code,
                f"Steps count exceeds max_step_num {max_items}: {'**' if is_sensitive else tool_call}"
            )
        required_steps_params = (
            tool.card.input_params.get("properties", {})
            .get("steps", {})
            .get("items", {})
            .get("required", [])
        )
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise CustomValueException(
                    StatusCode.PLANNER_GENERATE_ERROR.code,
                    f"Steps[{i}] is not a dict in tool call: {'**' if is_sensitive else tool_call}"
                )

            for param_name in required_steps_params:
                if param_name not in step:
                    raise CustomValueException(
                        StatusCode.PLANNER_GENERATE_ERROR.code,
                        f"Required step param '{param_name}' not found in tool call: "
                        f"{'**' if is_sensitive else tool_call}"
                    )
