# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unified is_test_path coverage (graph_tools + inherits share one helper)."""

from openjiuwen_codesearch.retropus.path_utils import is_test_path


def test_is_test_path_directories_and_conftest():
    assert is_test_path("tests/test_foo.py")
    assert is_test_path("pkg/testing/helper.py")
    assert is_test_path("conftest.py")
    assert is_test_path("pkg/conftest.py")
    assert not is_test_path("pkg/main.py")


def test_is_test_path_union_extensions():
    # Formerly only in graph_tools
    assert is_test_path("test_app.rb")
    assert is_test_path("app_test.rb")
    # Formerly only in inherits
    assert is_test_path("test_comp.tsx")
    assert is_test_path("foo_test.tsx")
    assert is_test_path("test_util.cpp")
    assert is_test_path("util_test.cc")
    assert is_test_path("pkg/foo_test.go")
