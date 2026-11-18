"""Tests for :func:`collect_repo_diff` (no git-index side effects)."""

import subprocess

from openjiuwen_codesearch.framework.openjiuwen.steps import collect_repo_diff


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _init_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def test_collect_repo_diff_includes_modifications_and_untracked_files(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")

    (repo / "a.txt").write_text("a changed\n", encoding="utf-8")
    (repo / "new.txt").write_text("new file\n", encoding="utf-8")

    patch = collect_repo_diff(str(repo))

    assert "a changed" in patch
    assert "new file" in patch


def test_collect_repo_diff_keeps_user_staged_changes(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "committed.txt").write_text("v1\n", encoding="utf-8")
    (repo / "staged.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")

    # 用户先暂存一处改动，再产生一处工作区改动
    (repo / "staged.txt").write_text("staged v2\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")
    (repo / "committed.txt").write_text("v2\n", encoding="utf-8")

    before = _git(repo, "diff", "--cached", "--name-only").stdout
    patch = collect_repo_diff(str(repo))
    after = _git(repo, "diff", "--cached", "--name-only").stdout

    assert before == "staged.txt\n"
    assert after == before
    assert "+staged v2" in patch
    assert "+v2" in patch


def test_collect_repo_diff_works_without_commits(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    patch = collect_repo_diff(str(repo))

    assert "staged" in patch
    assert "untracked" in patch
