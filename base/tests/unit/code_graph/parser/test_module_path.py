"""Tests for :func:`module_path` (platform-independent POSIX module paths)."""

from openjiuwen_search_base.codegraph.parser.languages._common import module_path


def test_posix_path_for_windows_root():
    assert module_path("C:\\repo", "a/b") == "C:/repo/a/b"


def test_root_is_normalised_when_folder_rel_is_empty():
    assert module_path("C:\\repo", "") == "C:/repo"


def test_posix_path_is_kept_as_is():
    assert module_path("/root", "a/b/c") == "/root/a/b/c"


def test_empty_root_keeps_folder_rel_relative():
    assert module_path("", "a/b") == "a/b"


def test_windows_folder_rel_is_normalised():
    assert module_path("C:\\repo", "a\\b") == "C:/repo/a/b"
