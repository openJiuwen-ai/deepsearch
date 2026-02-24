# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import logging

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction

from jiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import Outline, Section
from jiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from jiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from jiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def normalize_sections(args: dict) -> dict:
    sections = args.get("sections")
    if isinstance(sections, str):
        try:
            args["sections"] = json.loads(sections)
        except Exception:
            raise ValueError("sections must be a list, not a string")
    if not isinstance(args.get("sections"), list):
        raise ValueError("sections must be a list")

    return args


def generate_outline(language: str, title: str, thought: str, sections: list[Section]) -> Outline:
    """从FunctionCall封装outline"""
    sections = [
        Section(
            title=section.get("title", ""),
            description=section.get("description", ""),
            is_core_section=section.get("is_core_section", False)
        )
        for section in sections
    ]
    outline = Outline(
        language=language,
        title=title,
        thought=thought,
        sections=sections,
    )

    return outline


def create_outline_tool(max_section_num: int):
    """获取outline生成工具"""

    card = ToolCard(
        id="generate_outline",
        name="generate_outline",
        description="Generating outline for a Systematic Research Report.",
        input_params={
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "Output language, e.g. 'zh-CN' or 'en-US'"
                },
                "title": {
                    "type": "string",
                    "description": "Final report title."
                },
                "thought": {
                    "type": "string",
                    "description": "Detailed thoughts on generating an outline."
                },
                "sections": {
                    "type": "array",
                    "description": f"Section list of the final report. (Maximum number of sections: {max_section_num})",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Each research section title."
                            },
                            "description": {
                                "type": "string",
                                "description": "Detailed description of each research section."
                            },
                            "is_core_section": {
                                "type": "boolean",
                                "description": "Core section flag."
                            }
                        },
                        "required": ["title", "description"]
                    }
                }
            },
            "required": ["language", "title", "thought", "sections"]
        }
    )
    outline_tool = LocalFunction(
        card=card,
        func=generate_outline
    )

    return outline_tool


def check_tool_call(tool: LocalFunction, tool_calls: list):
    """
        Args:
            tool: 定义的 outline FunctionCall
            tool_calls: 模型实际的给出的 tool_calls
    """
    is_sensitive = LogManager.is_sensitive()
    if not tool_calls:
        raise CustomValueException(StatusCode.OUTLINER_GENERATE_ERROR.code, "No outline tool calls found in response")
    if len(tool_calls) > 1:
        logger.error("Multiple tool calls found in response")
    for tool_call in tool_calls:
        tool_name = tool_call.get("name", "")
        arguments = tool_call.get("args", {})
        if tool_name != tool.card.name:
            # 手动纠正工具名
            tool_call["name"] = tool.card.name
            logger.error(f"Tool name is not match({tool.card.name}): {'**' if is_sensitive else tool_name}")
        if not arguments:
            raise CustomValueException(
                StatusCode.OUTLINER_GENERATE_ERROR.code,
                f"No arguments found in tool call: {'**' if is_sensitive else tool_call}"
            )
        if not isinstance(arguments, dict):
            raise CustomValueException(
                StatusCode.OUTLINER_GENERATE_ERROR.code,
                f"Args is not a dict in tool call: {'**' if is_sensitive else tool_call}"
            )
        input_params = tool.card.input_params.get("properties", {})
        for param_name, param_info in input_params.items():
            required = param_name in tool.card.input_params.get("required", [])
            if required and param_name not in arguments:
                raise CustomValueException(
                    StatusCode.OUTLINER_GENERATE_ERROR.code,
                    f"Required param '{param_name}' not found in tool call: {'**' if is_sensitive else tool_call}"
                )
            if param_name == "sections":
                sections = arguments[param_name]
                if not isinstance(sections, list):
                    raise CustomValueException(
                        StatusCode.OUTLINER_GENERATE_ERROR.code,
                        f"Sections is not a list in tool call: {'**' if is_sensitive else tool_call}"
                    )
                for i, section in enumerate(sections):
                    if not isinstance(section, dict):
                        raise CustomValueException(
                            StatusCode.OUTLINER_GENERATE_ERROR.code,
                            f"Section[{i}] is not a dict in tool call: {'**' if is_sensitive else tool_call}"
                        )
                    # Check items/properties if needed, but for simplicity:
                    if not section.get("title") or not section.get("description"):
                        raise CustomValueException(
                            StatusCode.OUTLINER_GENERATE_ERROR.code,
                            f"Required section param 'title' or 'description' not found in tool call: "
                            f"{'**' if is_sensitive else tool_call}"
                        )


class Outliner:
    def __init__(self, llm_model_name, prompt_name):
        self.llm = llm_context.get().get(llm_model_name)
        self.prompt_name = prompt_name

    async def generate_outline(self, current_inputs: dict) -> dict:
        """Generating an outline of the report."""
        logger.info("Outliner starting")
        prompt = apply_system_prompt(self.prompt_name, current_inputs)

        outline = {}
        error_msg = ""
        max_section_num = current_inputs.get("max_section_num")
        tool = create_outline_tool(max_section_num)
        try:
            # invoke LLM
            response = await ainvoke_llm_with_stats(
                self.llm,
                prompt,
                agent_name=NodeId.OUTLINE.value,
                tools=[tool.card.tool_info()],
                need_stream_out=False
            )

            tool_calls = response.get('tool_calls', [])
            for tool_call in tool_calls:
                tool_call["args"] = normalize_sections(tool_call.get("args", {}))

            check_tool_call(tool, tool_calls)

            for tool_call in tool_calls:
                outline = await tool.invoke(tool_call.get("args"))
                logger.info(f"The outline generation is completed: "
                            f"{'**' if LogManager.is_sensitive() else outline.model_dump_json(indent=4)}")
                break

        except Exception as e:
            error_msg = f"[{StatusCode.OUTLINER_GENERATE_ERROR.code}]{StatusCode.OUTLINER_GENERATE_ERROR.errmsg}: {e}"
            if LogManager.is_sensitive():
                logger.error("Error when Outliner generating a outline")
            else:
                logger.error(error_msg)

        success_flag = bool(outline)

        return {
            "current_outline": outline,
            "success_flag": success_flag,
            "error_msg": error_msg
        }
