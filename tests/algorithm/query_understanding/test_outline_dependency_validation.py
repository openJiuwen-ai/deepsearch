# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""测试依赖驱动大纲的依赖验证和修复功能"""

import pytest
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Section
from openjiuwen_deepsearch.algorithm.query_understanding.outliner import (
    validate_section_dependencies,
    sync_relationships_with_parent_ids,
    fix_section_dependency_issues,
    _is_reverse_dependency,
    validate_section_id_format,
    fix_section_ids,
)


class TestValidateSectionIdFormat:
    """测试 Section ID 格式验证"""

    def test_valid_simple_id(self):
        assert validate_section_id_format("1") == True
        assert validate_section_id_format("2") == True
        assert validate_section_id_format("10") == True

    def test_valid_hierarchical_id(self):
        assert validate_section_id_format("1.1") == True
        assert validate_section_id_format("1.2.3") == True
        assert validate_section_id_format("10.20.30") == True

    def test_invalid_empty_string(self):
        assert validate_section_id_format("") == False

    def test_invalid_none(self):
        assert validate_section_id_format(None) == False

    def test_invalid_format_with_letters(self):
        assert validate_section_id_format("1a") == False
        assert validate_section_id_format("a1") == False

    def test_invalid_format_with_special_chars(self):
        assert validate_section_id_format("1-1") == False
        assert validate_section_id_format("1_1") == False
        assert validate_section_id_format("1.") == False

    def test_invalid_format_starts_with_dot(self):
        assert validate_section_id_format(".1") == False


class TestFixSectionIds:
    """测试 Section ID 修复兜底逻辑"""

    def test_fix_sequential_ids(self):
        """测试按顺序重新编号"""
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2",
                title="S2",
                description="D2",
                parent_ids=["1"],
                relationships=["依赖1"],
            ),
            Section(
                id="3",
                title="S3",
                description="D3",
                parent_ids=["1", "2"],
                relationships=["依赖1", "依赖2"],
            ),
        ]
        fixed = fix_section_ids(sections)
        assert [s.id for s in fixed] == ["1", "2", "3"]

    def test_fix_invalid_ids_to_sequential(self):
        """测试无效ID转换为顺序编号"""
        sections = [
            Section(
                id="abc", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="xyz",
                title="S2",
                description="D2",
                parent_ids=["abc"],
                relationships=["依赖1"],
            ),
        ]
        fixed = fix_section_ids(sections)
        assert [s.id for s in fixed] == ["1", "2"]
        # parent_ids 应该被映射到新的 ID
        assert fixed[1].parent_ids == ["1"]

    def test_fix_empty_ids_to_sequential(self):
        """测试空ID转换为顺序编号"""
        sections = [
            Section(
                id="", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="",
                title="S2",
                description="D2",
                parent_ids=[],
                relationships=["依赖1"],
            ),
        ]
        fixed = fix_section_ids(sections)
        assert [s.id for s in fixed] == ["1", "2"]
        assert fixed[1].parent_ids == []

    def test_fix_mixed_valid_invalid_ids(self):
        """测试混合有效和无效ID"""
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="invalid",
                title="S2",
                description="D2",
                parent_ids=["1"],
                relationships=["依赖1"],
            ),
            Section(
                id="3.1",
                title="S3",
                description="D3",
                parent_ids=["1", "invalid"],
                relationships=["依赖1", "依赖2"],
            ),
        ]
        fixed = fix_section_ids(sections)
        print(fixed)
        # 所有 ID 都被重新编号为 1, 2, 3
        assert [s.id for s in fixed] == ["1", "2", "3"]
        # parent_ids 应该被正确映射
        assert fixed[1].parent_ids == ["1"]
        assert fixed[2].parent_ids == ["1", "2"]

    def test_fix_deduplicate_parent_ids(self):
        """测试去重 parent_ids"""
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2",
                title="S2",
                description="D2",
                parent_ids=["1", "1", "1"],
                relationships=["依赖1"],
            ),
        ]
        fixed = fix_section_ids(sections)
        # 去重后应该只有一个 parent_id
        assert fixed[1].parent_ids == ["1"]
        assert len(fixed[1].parent_ids) == 1

    def test_fix_parent_ids_not_in_mapping(self):
        """测试移除不在映射中的 parent_ids"""
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2",
                title="S2",
                description="D2",
                parent_ids=["99", "1"],
                relationships=["依赖1"],
            ),
        ]
        fixed = fix_section_ids(sections)
        # 99 不在映射中，应该被移除
        assert fixed[1].parent_ids == ["1"]


class TestIsReverseDependency:
    """测试反向依赖检测"""

    def test_detect_reverse_dependency(self):
        """测试检测反向依赖"""
        assert _is_reverse_dependency("2", "3") == True
        assert _is_reverse_dependency("1", "2") == True

    def test_no_reverse_dependency(self):
        """测试无反向依赖"""
        assert _is_reverse_dependency("2", "1") == False
        assert _is_reverse_dependency("3", "1") == False

    def test_same_id_not_reverse(self):
        """测试相同ID不算反向依赖"""
        assert _is_reverse_dependency("1", "1") == False


class TestValidateSectionDependencies:
    def test_validate_valid_sections(self):
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2",
                title="S2",
                description="D2",
                parent_ids=["1"],
                relationships=["框架支撑"],
            ),
            Section(
                id="3",
                title="S3",
                description="D3",
                parent_ids=["1", "2"],
                relationships=["基础依赖", "信息整合"],
            ),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == True

    def test_validate_no_deps_allowed(self):
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2", title="S2", description="D2", parent_ids=[], relationships=[]
            ),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == True

    def test_validate_reverse_dep(self):
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2",
                title="S2",
                description="D2",
                parent_ids=["3"],
                relationships=["框架支撑"],
            ),
            Section(
                id="3", title="S3", description="D3", parent_ids=[], relationships=[]
            ),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == False
        assert any("reverse dependency" in err for err in result["errors"])


class TestFixSectionDependencyIssues:
    def test_fix_reverse_dep(self):
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2",
                title="S2",
                description="D2",
                parent_ids=["3"],
                relationships=["框架支撑"],
            ),
            Section(
                id="3", title="S3", description="D3", parent_ids=[], relationships=[]
            ),
        ]
        fixed = fix_section_dependency_issues(sections)
        s2 = next(s for s in fixed if s.id == "2")
        assert len(s2.parent_ids) == 0
        assert len(s2.relationships) == 0

    def test_fix_sync(self):
        sections = [
            Section(
                id="3",
                title="S3",
                description="D3",
                parent_ids=["1", "2", "5"],
                relationships=["a", "b", "c"],
            ),
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2", title="S2", description="D2", parent_ids=[], relationships=[]
            ),
        ]
        fixed = fix_section_dependency_issues(sections)
        s3 = next(s for s in fixed if s.id == "3")
        assert len(s3.parent_ids) == 2
        assert len(s3.relationships) == 2


class TestSyncRelationshipsWithParentIds:
    """测试 relationships 与 parent_ids 同步逻辑"""

    def test_sync_no_parents_no_relationships(self):
        """测试无父节点无关系时保持空"""
        section = Section(
            id="1", title="S1", description="D1", parent_ids=[], relationships=[]
        )
        modified = sync_relationships_with_parent_ids(section)
        assert modified == False
        assert section.relationships == []

    def test_sync_with_parents_empty_relationships(self):
        """测试有父节点但无关系时填充默认值"""
        section = Section(
            id="2", title="S2", description="D2", parent_ids=["1"], relationships=[]
        )
        modified = sync_relationships_with_parent_ids(section)
        assert modified == True
        assert len(section.relationships) == 1

    def test_sync_with_relationships_fewer_than_parents(self):
        """测试关系数量少于父节点时填充"""
        section = Section(
            id="3",
            title="S3",
            description="D3",
            parent_ids=["1", "2"],
            relationships=["关系1"],
        )
        modified = sync_relationships_with_parent_ids(section)
        assert modified == True
        assert len(section.relationships) == 2

    def test_sync_with_relationships_more_than_parents(self):
        """测试关系数量多于父节点时截断"""
        section = Section(
            id="3",
            title="S3",
            description="D3",
            parent_ids=["1"],
            relationships=["关系1", "关系2"],
        )
        modified = sync_relationships_with_parent_ids(section)
        assert modified == True
        assert len(section.relationships) == 1
        assert section.relationships == ["关系1"]

    def test_sync_with_none_values(self):
        """测试空列表处理"""
        section = Section(
            id="1", title="S1", description="D1", parent_ids=[], relationships=[]
        )
        modified = sync_relationships_with_parent_ids(section)
        assert modified == False

    def test_sync_with_exact_match(self):
        """测试数量匹配时无需修改"""
        section = Section(
            id="2",
            title="S2",
            description="D2",
            parent_ids=["1"],
            relationships=["关系1"],
        )
        modified = sync_relationships_with_parent_ids(section)
        assert modified == False
        assert section.relationships == ["关系1"]


class TestValidateSectionDependenciesEdgeCases:
    """测试 validate_section_dependencies 边界情况"""

    def test_validate_missing_id(self):
        """测试缺失 ID 的情况"""
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="", title="S2", description="D2", parent_ids=[], relationships=[]
            ),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == False
        assert any("missing ID" in err for err in result["errors"])

    def test_validate_duplicate_id(self):
        """测试重复 ID 的情况"""
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="1", title="S2", description="D2", parent_ids=[], relationships=[]
            ),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == False
        assert any("Duplicate" in err for err in result["errors"])

    def test_validate_self_dependency(self):
        """测试自依赖的情况"""
        sections = [
            Section(
                id="1",
                title="S1",
                description="D1",
                parent_ids=["1"],
                relationships=["自依赖"],
            ),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == False
        assert any("Self-dependency" in err for err in result["errors"])

    def test_validate_nonexistent_parent(self):
        """测试不存在的父节点"""
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2",
                title="S2",
                description="D2",
                parent_ids=["99"],
                relationships=["不存在"],
            ),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == False
        assert any("non-existent" in err for err in result["errors"])

    def test_validate_parent_relationship_count_mismatch(self):
        """测试父节点数量与关系数量不匹配"""
        sections = [
            Section(
                id="1", title="S1", description="D1", parent_ids=[], relationships=[]
            ),
            Section(
                id="2",
                title="S2",
                description="D2",
                parent_ids=["1", "1"],
                relationships=["关系1"],
            ),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == False
        assert any(
            "parent_ids" in err and "relationships" in err for err in result["errors"]
        )
