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
)


class TestValidateSectionDependencies:
    def test_validate_valid_sections(self):
        sections = [
            Section(id="1", title="S1", description="D1", parent_ids=[], relationships=[]),
            Section(id="2", title="S2", description="D2", parent_ids=["1"], relationships=["框架支撑"]),
            Section(id="3", title="S3", description="D3", parent_ids=["1", "2"], relationships=["基础依赖", "信息整合"]),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == True

    def test_validate_no_deps_allowed(self):
        sections = [
            Section(id="1", title="S1", description="D1", parent_ids=[], relationships=[]),
            Section(id="2", title="S2", description="D2", parent_ids=[], relationships=[]),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == True

    def test_validate_reverse_dep(self):
        sections = [
            Section(id="1", title="S1", description="D1", parent_ids=[], relationships=[]),
            Section(id="2", title="S2", description="D2", parent_ids=["3"], relationships=["框架支撑"]),
            Section(id="3", title="S3", description="D3", parent_ids=[], relationships=[]),
        ]
        result = validate_section_dependencies(sections)
        assert result["is_valid"] == False
        assert any("reverse dependency" in err for err in result["errors"])


class TestFixSectionDependencyIssues:
    def test_fix_reverse_dep(self):
        sections = [
            Section(id="1", title="S1", description="D1", parent_ids=[], relationships=[]),
            Section(id="2", title="S2", description="D2", parent_ids=["3"], relationships=["框架支撑"]),
            Section(id="3", title="S3", description="D3", parent_ids=[], relationships=[]),
        ]
        fixed = fix_section_dependency_issues(sections)
        s2 = next(s for s in fixed if s.id == "2")
        assert len(s2.parent_ids) == 0
        assert len(s2.relationships) == 0

    def test_fix_sync(self):
        sections = [
            Section(id="3", title="S3", description="D3", parent_ids=["1", "2", "5"], relationships=["a", "b", "c"]),
            Section(id="1", title="S1", description="D1", parent_ids=[], relationships=[]),
            Section(id="2", title="S2", description="D2", parent_ids=[], relationships=[]),
        ]
        fixed = fix_section_dependency_issues(sections)
        s3 = next(s for s in fixed if s.id == "3")
        assert len(s3.parent_ids) == 2
        assert len(s3.relationships) == 2
