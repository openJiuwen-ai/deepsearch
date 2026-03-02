# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""测试依赖驱动大纲生成工具"""

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.outliner import (
    creat_dep_driving_outline_tool,
    create_outline_tool,
)


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
