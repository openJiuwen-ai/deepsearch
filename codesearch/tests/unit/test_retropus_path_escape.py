# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model-supplied repo-relative paths must not escape the checked-out repository."""

from tests.unit.retropus_fixtures import make_retropus_tools


def test_add_context_rejects_parent_directory_escape(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("outside the repository\n", encoding="utf-8")

    tools = make_retropus_tools(repo)

    message = tools.add_context("../secret.txt", 1, 1)

    assert "Recorded" not in message
    assert tools.final_spans() == []
