# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import logging
from copy import deepcopy

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api import build_runtime_api_tools, \
    merge_runtime_api_tools
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline, Section
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def normalize_sections(args: dict) -> dict:
    """标准化大纲分节结构，统一字段与层级格式。"""
    sections = args.get("sections")
    if isinstance(sections, str):
        try:
            args["sections"] = json.loads(sections)
        except Exception as e:
            raise ValueError("sections must be a list, not a string") from e
    if not isinstance(args.get("sections"), list):
        raise ValueError("sections must be a list")

    return args


def generate_outline(
    language: str, title: str, thought: str, sections: list[Section]
) -> Outline:
    """从 FunctionCall 封装 outline"""
    sections = [
        Section(
            title=section.get("title", ""),
            description=section.get("description", ""),
            is_core_section=section.get("is_core_section", False),
            id=section.get("id", ""),
            parent_ids=section.get("parent_ids", []),
            relationships=section.get("relationships", []),
        )
        for section in sections
    ]

    outline = Outline(
        language=language,
        title=title,
        thought=thought,
        sections=sections,
    )

    # 验证 Section ID 格式是否正确
    for section in outline.sections:
        if not validate_section_id_format(section.id):
            logger.warning(f"Section ID format may be invalid: {section.id}")

    outline.sections = fix_section_ids(outline.sections)

    # 验证依赖关系是否正确
    validation = validate_section_dependencies(outline.sections)
    if not validation["is_valid"]:
        logger.warning(f"Outline has dependency issues, fixing...")
        outline.sections = fix_section_dependency_issues(outline.sections)
        validation = validate_section_dependencies(outline.sections)
        if not validation["is_valid"]:
            logger.error(f"Outline still has errors after fix: {validation['errors']}")
            # 无法修复，最终兜底：清空所有依赖关系
            for section in outline.sections:
                section.parent_ids = []
                section.relationships = []

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
                "title": {"type": "string", "description": "Final report title."},
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
                                "description": "Pure section title without numbering. Never include numbers, bullets, "
                                                "or prefixes like '1.', '2)', 'I.', '第一章'."
                            },
                            "description": {
                                "type": "string",
                                "description": "Detailed description of each research section."
                            },
                            "is_core_section": {
                                "type": "boolean",
                                "description": "Core section flag."
                            },
                            "id": {
                                "type": "string",
                                "description": "Unique identifier for the section. Following the format '1', '2', etc."
                            },
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


def creat_dep_driving_outline_tool(max_section_num: int):
    """获取依赖驱动大纲生成工具"""
    card = ToolCard(
        id="dep_driving_generate_outline",
        name="dep_driving_generate_outline",
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
                                "description": "Pure section title without numbering. Never include numbers, bullets, "
                                                "or prefixes like '1.', '2)', 'I.', '第一章'."
                            },
                            "description": {
                                "type": "string",
                                "description": "Detailed description of each research section and the explanation of "
                                               "the relationships."
                            },
                            "is_core_section": {
                                "type": "boolean",
                                "description": "Core section flag."
                            },
                            "id": {
                                "type": "string",
                                "description": "Unique identifier for the section. Following the format '1', '2', etc."
                            },
                            "parent_ids": {
                                "type": "array",
                                "description": "List of parent sections. Strictly ensure that parent IDs are smaller "
                                "than the current section's ID",
                                "items": {
                                    "type": "string"
                                }
                            },
                            "relationships": {
                                "type": "array",
                                "description": "List of relationships between parent sections and the current section.",
                                "items": {
                                    "type": "string"
                                }
                            }
                            },
                        "required": ["title", "description", "id", "parent_ids", "relationships"]
                    }
                }
            },
            "required": ["language", "title", "thought", "sections"]
        }
    )
    dep_driving_outline_tool = LocalFunction(
        card=card,
        func=generate_outline
    )

    return dep_driving_outline_tool


def check_tool_call(tool_dict: dict[str, LocalFunction] | LocalFunction, tool_calls: list):
    """
    Args:
        tool_dict: 定义的 outline FunctionCall 映射
        tool_calls: 模型实际的给出的 tool_calls
    """
    is_sensitive = LogManager.is_sensitive()
    if isinstance(tool_dict, LocalFunction):
        tool_dict = {tool_dict.card.name: tool_dict}
    if not tool_calls:
        _raise_tool_call_error("No outline tool calls found in response")
    if len(tool_calls) > 1:
        logger.error("Multiple tool calls found in response")
    for tool_call in tool_calls:
        tool_name = tool_call.get("name", "")
        tool = tool_dict.get(tool_name)
        if tool is None and len(tool_dict) == 1:
            tool = next(iter(tool_dict.values()))
        if tool is None:
            _raise_tool_call_error(
                f"Tool name '{tool_name}' not found in tool call: {'**' if is_sensitive else tool_call}"
            )
        _check_tool_name(tool, tool_call, is_sensitive)
        arguments = _check_tool_arguments(tool_call, is_sensitive)
        _check_required_params(tool, arguments, tool_call, is_sensitive)
        _check_sections(arguments, tool_call, is_sensitive)


def _raise_tool_call_error(message: str) -> None:
    raise CustomValueException(
        StatusCode.OUTLINER_GENERATE_ERROR.code,
        message,
    )


def _check_tool_name(
    tool: LocalFunction, tool_call: dict, is_sensitive: bool
) -> None:
    tool_name = tool_call.get("name", "")
    if tool_name == tool.card.name:
        return

    tool_call["name"] = tool.card.name
    logger.error(
        f"Tool name is not match({tool.card.name}): {'**' if is_sensitive else tool_name}"
    )


def _check_tool_arguments(tool_call: dict, is_sensitive: bool) -> dict:
    arguments = tool_call.get("args", {})
    if not arguments:
        _raise_tool_call_error(
            f"No arguments found in tool call: {'**' if is_sensitive else tool_call}"
        )
    if not isinstance(arguments, dict):
        _raise_tool_call_error(
            f"Args is not a dict in tool call: {'**' if is_sensitive else tool_call}"
        )

    return arguments


def _check_required_params(
    tool: LocalFunction, arguments: dict, tool_call: dict, is_sensitive: bool
) -> None:
    required_params = tool.card.input_params.get("required", [])
    for param_name in required_params:
        if param_name in arguments:
            continue
        _raise_tool_call_error(
            f"Required param '{param_name}' not found in tool call: "
            f"{'**' if is_sensitive else tool_call}"
        )


def _check_sections(arguments: dict, tool_call: dict, is_sensitive: bool) -> None:
    sections = arguments.get("sections")
    if sections is None:
        return
    if not isinstance(sections, list):
        _raise_tool_call_error(
            f"Sections is not a list in tool call: {'**' if is_sensitive else tool_call}"
        )
    for index, section in enumerate(sections):
        _check_section(section, index, tool_call, is_sensitive)


def _check_section(
    section: dict, index: int, tool_call: dict, is_sensitive: bool
) -> None:
    if not isinstance(section, dict):
        _raise_tool_call_error(
            f"Section[{index}] is not a dict in tool call: {'**' if is_sensitive else tool_call}"
        )
    if section.get("title") and section.get("description"):
        return

    _raise_tool_call_error(
        "Required section param 'title' or 'description' not found in tool call: "
        f"{'**' if is_sensitive else tool_call}"
    )


class Outliner:
    def __init__(self, llm_model_name, prompt_name):
        self.llm = llm_context.get().get(llm_model_name)
        self.prompt = prompt_name
        self.with_dep_driving = False

    async def generate_outline(self, current_inputs: dict) -> dict:
        """Generating an outline of the report."""
        logger.info("Outliner starting")
        prompt = apply_system_prompt(self.prompt, current_inputs)
        outline = {}
        error_msg = ""
        max_section_num = current_inputs.get("max_section_num")
        if self.with_dep_driving:
            default_tool = creat_dep_driving_outline_tool(max_section_num)
        else:
            default_tool = create_outline_tool(max_section_num)
        tools = [default_tool]
        api_tools = build_runtime_api_tools(
            current_inputs.get("api_tools_config", {}).get("query_understanding_tools", []),
            response_model=Outline,
        )
        tools = merge_runtime_api_tools(tools, api_tools)
        tool_dict = {tool.card.name: tool for tool in tools}
        try:
            # invoke LLM
            response = await ainvoke_llm_with_stats(
                self.llm,
                prompt,
                agent_name=AgentLlmName.OUTLINE.value,
                tools=[tool.card.tool_info() for tool in tools],
                need_stream_out=False,
            )

            tool_calls = response.get("tool_calls", [])
            for tool_call in tool_calls:
                args = tool_call.get("args", {})
                if isinstance(args, dict) and "sections" in args:
                    tool_call["args"] = normalize_sections(args)

            check_tool_call(tool_dict, tool_calls)

            for tool_call in tool_calls:
                tool = tool_dict[tool_call.get("name")]
                outline = await tool.invoke(tool_call.get("args"))
                logger.info(
                    f"The outline generation is completed: "
                    f"{'**' if LogManager.is_sensitive() else outline.model_dump_json(indent=4)}",
                    extra={"skip_truncation": True},
                )
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
            "error_msg": error_msg,
        }


def validate_section_dependencies(sections):
    """验证大纲章节依赖关系的有效性"""
    errors = []
    section_ids = {section.id for section in sections if section.id}

    for section in sections:
        if not section.id:
            errors.append(f"Section missing ID: {section.title}")
            continue

        duplicate_count = sum(1 for s in sections if s.id == section.id)
        if duplicate_count > 1:
            errors.append(f"Duplicate section ID: {section.id}")

        if section.id in section.parent_ids:
            errors.append(f"Self-dependency detected: {section.id}")

        for parent_id in section.parent_ids:
            if parent_id not in section_ids:
                errors.append(
                    f"Section '{section.id}' depends on non-existent: {parent_id}"
                )
            elif int(parent_id) > int(section.id):
                errors.append(
                    f"Section '{section.id}' has reverse dependency: {parent_id}"
                )

        parent_count = len(section.parent_ids) if section.parent_ids else 0
        relationship_count = len(section.relationships) if section.relationships else 0
        if parent_count != relationship_count:
            errors.append(
                f"Section '{section.id}': parent_ids({parent_count}) != relationships({relationship_count})"
            )

    return {"errors": errors, "is_valid": len(errors) == 0}


def _is_reverse_dependency(section_id: str, parent_id: str) -> bool:
    return int(parent_id) > int(section_id)


def sync_relationships_with_parent_ids(section):
    """同步 relationships 与 parent_ids 数量一致"""
    modified = False
    parent_ids = section.parent_ids or []
    relationships = section.relationships or []
    parent_count = len(parent_ids)
    relationship_count = len(relationships)

    if parent_count == 0:
        if relationships:
            section.relationships = []
            modified = True
    elif relationship_count == 0:
        section.relationships = ["基础依赖"] * parent_count
        modified = True
    elif relationship_count < parent_count:
        last_rel = relationships[-1] if relationships else "基础依赖"
        section.relationships = relationships + [last_rel] * (
            parent_count - relationship_count
        )
        modified = True
    elif relationship_count > parent_count:
        section.relationships = relationships[:parent_count]
        modified = True

    return modified


def fix_section_dependency_issues(sections):
    """自动修复 Section 依赖关系问题"""
    fixed_sections = deepcopy(sections)
    valid_ids = {section.id for section in fixed_sections if section.id}

    for section in fixed_sections:
        if not section.id:
            continue

        if section.id in section.parent_ids:
            section.parent_ids.remove(section.id)

        original_deps = section.parent_ids.copy()
        section.parent_ids = [
            pid
            for pid in section.parent_ids
            if pid in valid_ids and not _is_reverse_dependency(section.id, pid)
        ]
        removed_deps = set(original_deps) - set(section.parent_ids)
        if removed_deps:
            logger.warning(
                f"Section {section.id}: removed invalid deps: {removed_deps}"
            )

    for section in fixed_sections:
        if section.id:
            sync_relationships_with_parent_ids(section)

    return fixed_sections


def validate_section_id_format(section_id):
    """验证 Section ID 格式"""
    import re

    pattern = r"^\d+(\.\d+)*$"
    return bool(re.match(pattern, section_id)) if section_id else False


def fix_section_ids(sections):
    """Section ID 兜底逻辑（仅处理 id 重排 + parent_ids 去重）"""

    fixed_sections = deepcopy(sections)
    id_mapping = {}

    # 1 重新编号 section.id
    for index, section in enumerate(fixed_sections, start=1):
        old_id = section.id
        new_id = str(index)

        if old_id:
            id_mapping[old_id] = new_id

        section.id = new_id

    # 2 修复 parent_ids
    for section in fixed_sections:
        new_parents = []

        for pid in section.parent_ids:
            if not pid:
                continue
            if pid in id_mapping:
                mapped_id = id_mapping[pid]
                if mapped_id != section.id:
                    new_parents.append(mapped_id)

        # 去重 + 保序
        section.parent_ids = list(dict.fromkeys(new_parents))

    return fixed_sections
