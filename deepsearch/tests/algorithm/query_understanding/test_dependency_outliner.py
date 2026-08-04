# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""测试依赖驱动大纲生成工具"""

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.outliner import (
    check_tool_call,
    creat_dep_driving_outline_tool,
    create_outline_tool,
    generate_outline,
)
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.exception import CustomValueException


class TestDepDrivingOutlineTool:
    """测试依赖驱动大纲工具"""

    def test_creat_dep_driving_outline_tool_structure(self):
        """测试工具结构验证"""
        max_section_num = 10
        tool = creat_dep_driving_outline_tool(max_section_num)

        assert tool is not None
        assert hasattr(tool, "card")
        assert tool.card.name == "dep_driving_generate_outline"
        assert "Generating outline" in tool.card.description

    def test_dep_driving_outline_tool_params(self):
        """测试参数 schema 验证（必须包含 id, parent_ids, relationships）"""
        max_section_num = 8
        tool = creat_dep_driving_outline_tool(max_section_num)

        params = tool.card.input_params
        assert params is not None

        # 验证顶层参数
        properties = params.get("properties", {})
        assert "language" in properties
        assert "title" in properties
        assert "thought" in properties
        assert "sections" in properties

        # 验证 sections 参数包含依赖字段
        sections_param = properties.get("sections", {})
        items = sections_param.get("items", {})
        item_properties = items.get("properties", {})

        # 关键验证：必须包含依赖相关字段
        assert "id" in item_properties, "sections items must have 'id' field"
        assert "parent_ids" in item_properties, (
            "sections items must have 'parent_ids' field"
        )
        assert "relationships" in item_properties, (
            "sections items must have 'relationships' field"
        )

        # 验证依赖字段的描述
        assert "parent" in item_properties["parent_ids"].get("description", "").lower()
        assert (
            "relationship"
            in item_properties["relationships"].get("description", "").lower()
        )

    def test_dep_driving_outline_tool_required_fields(self):
        """测试必填字段验证"""
        max_section_num = 5
        tool = creat_dep_driving_outline_tool(max_section_num)

        params = tool.card.input_params
        required = params.get("required", [])

        # 顶层必填字段
        assert "language" in required
        assert "title" in required
        assert "thought" in required
        assert "sections" in required

        # sections 内部必填字段
        sections_param = params.get("properties", {}).get("sections", {})
        items = sections_param.get("items", {})
        required_items = items.get("required", [])

        assert "title" in required_items
        assert "description" in required_items
        # id, parent_ids, relationships 应该是必填的
        assert "id" in required_items
        assert "parent_ids" in required_items
        assert "relationships" in required_items

    def test_dependency_tool_requires_structured_section_contract(self):
        tool = creat_dep_driving_outline_tool(5)
        items = tool.card.input_params["properties"]["sections"]["items"]
        properties = items["properties"]
        required = items["required"]

        assert properties["format_requirements"]["type"] == "array"
        assert properties["format_requirements"]["items"]["type"] == "string"
        assert properties["visualization_requirements"]["type"] == "array"
        assert properties["visualization_requirements"]["items"]["type"] == "string"
        assert properties["section_focus"]["minLength"] == 1
        assert properties["focus_dimensions"]["minItems"] == 1
        assert {
            "format_requirements",
            "section_focus",
            "focus_dimensions",
            "id",
            "parent_ids",
            "relationships",
        }.issubset(required)

    def test_dependency_contract_schema_matches_general_tool(self):
        dependency_properties = (
            creat_dep_driving_outline_tool(5)
            .card.input_params["properties"]["sections"]["items"]["properties"]
        )
        general_properties = (
            create_outline_tool(5)
            .card.input_params["properties"]["sections"]["items"]["properties"]
        )

        for field_name in (
            "format_requirements",
            "visualization_requirements",
            "section_focus",
            "focus_dimensions",
        ):
            assert dependency_properties[field_name] == general_properties[field_name]

    @pytest.mark.parametrize(
        "missing_field",
        ["format_requirements", "section_focus", "focus_dimensions"],
    )
    def test_dependency_tool_call_requires_section_contract_fields(
        self, missing_field
    ):
        tool = creat_dep_driving_outline_tool(1)
        section = self._valid_dependency_section()
        section.pop(missing_field)

        with pytest.raises(CustomValueException, match=missing_field):
            check_tool_call(tool, [self._tool_call(tool, section)])

    def test_dependency_tool_call_allows_empty_format_requirements(self):
        tool = creat_dep_driving_outline_tool(1)

        check_tool_call(
            tool,
            [self._tool_call(tool, self._valid_dependency_section())],
        )

    def test_generate_outline_preserves_ordered_format_requirements(self):
        requirements = [
            "Use a Markdown table",
            "Columns: Product, Price, Risk",
            "Use official sources only",
        ]
        section = self._valid_dependency_section()
        section["format_requirements"] = requirements

        outline = generate_outline("en-US", "Comparison", "Compare", [section])

        assert outline.sections[0].format_requirements == requirements

    def test_generate_outline_preserves_visualization_requirements(self):
        section = self._valid_dependency_section()
        section["visualization_requirements"] = [
            "Show the retrieval-to-answer data flow",
            "Distinguish retrieval and generation stages",
        ]

        outline = generate_outline("en-US", "RAG", "Explain the pipeline", [section])

        assert outline.sections[0].visualization_requirements == section[
            "visualization_requirements"
        ]

    @pytest.mark.parametrize(
        "prompt_name",
        ["dep_driving_outliner", "dep_driving_outliner_interaction"],
    )
    def test_dependency_prompt_requires_structured_format_requirements(
        self, prompt_name
    ):
        rendered = str(
            apply_system_prompt(
                prompt_name,
                {
                    "questions": "Compare products in a table",
                    "user_feedback": "Keep the exact columns",
                    "section_num": 2,
                    "language": "en-US",
                },
            )
        ).lower()

        assert "format_requirements" in rendered
        assert "visualization_requirements" in rendered
        assert "exact column" in rendered
        assert "required row" in rendered
        assert "item-by-item" in rendered
        assert "source restriction" in rendered
        assert "[]" in rendered
        assert "description" in rendered

    def test_dependency_interaction_prompt_updates_structured_format_field(self):
        rendered = str(
            apply_system_prompt(
                "dep_driving_outliner_interaction",
                {
                    "questions": "Compare products",
                    "user_feedback": "Add a Risk column",
                    "section_num": 2,
                    "language": "en-US",
                },
            )
        ).lower()

        assert "feedback" in rendered
        assert "update" in rendered
        assert "format_requirements" in rendered

    @staticmethod
    def _valid_dependency_section():
        return {
            "title": "Product comparison",
            "description": "Compare product price, advantages, and risks.",
            "format_requirements": [],
            "visualization_requirements": [],
            "id": "1",
            "parent_ids": [],
            "relationships": [],
            "section_focus": "product_comparison",
            "focus_dimensions": ["price", "advantages", "risks"],
        }

    @staticmethod
    def _tool_call(tool, section):
        return {
            "name": tool.card.name,
            "args": {
                "language": "en-US",
                "title": "Comparison",
                "thought": "Compare products",
                "sections": [section],
            },
        }

    def test_comparison_with_general_outline_tool(self):
        """测试与通用大纲工具的区别"""
        dep_tool = creat_dep_driving_outline_tool(10)
        general_tool = create_outline_tool(10)

        # 工具名不同
        assert dep_tool.card.name != general_tool.card.name
        assert "dep_driving" in dep_tool.card.name

        # 依赖驱动工具包含额外的依赖字段
        dep_sections = dep_tool.card.input_params.get("properties", {}).get(
            "sections", {}
        )
        general_sections = general_tool.card.input_params.get("properties", {}).get(
            "sections", {}
        )

        dep_items = dep_sections.get("items", {}).get("properties", {})
        general_items = general_sections.get("items", {}).get("properties", {})

        # 依赖驱动工具应该有额外的字段
        assert "parent_ids" in dep_items
        # 通用工具可能没有 parent_ids
        assert "parent_ids" not in general_items or dep_items != general_items
