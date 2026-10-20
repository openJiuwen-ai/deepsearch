# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

from datetime import datetime, timezone

import pytest

from openjiuwen_deepsearch.algorithm.prompts import message_builder
from openjiuwen_deepsearch.algorithm.prompts.message_builder import (
    PromptBuildOptions,
    build_prompt_messages,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode


@pytest.fixture
def prompt_root(tmp_path, monkeypatch):
    prompt = tmp_path / "sample"
    prompt.mkdir()
    (prompt / "system.md").write_text("Stable rules", encoding="utf-8")
    (prompt / "user.md").write_text("Query: {{ query }}", encoding="utf-8")
    monkeypatch.setattr(message_builder, "PROMPT_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def date_prompt_root(tmp_path, monkeypatch):
    prompt = tmp_path / "dated"
    prompt.mkdir()
    (prompt / "system.md").write_text("Stable rules", encoding="utf-8")
    (prompt / "user.md").write_text("Date: {{ current_date }}", encoding="utf-8")
    monkeypatch.setattr(message_builder, "PROMPT_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def image_prompt_root(tmp_path, monkeypatch):
    prompt = tmp_path / "vision"
    prompt.mkdir()
    (prompt / "system.md").write_text("Stable vision rules", encoding="utf-8")
    (prompt / "user.md").write_text("Inspect image", encoding="utf-8")
    monkeypatch.setattr(message_builder, "PROMPT_ROOT", tmp_path)
    return tmp_path


def test_builds_system_prior_and_user_without_mutation(prompt_root):
    context = {"query": "alpha"}
    prior = [{"role": "assistant", "content": "earlier"}]

    result = build_prompt_messages(
        "sample", context, options=PromptBuildOptions(prior_messages=prior)
    )

    assert result == [
        {"role": "system", "content": "Stable rules"},
        prior[0],
        {"role": "user", "content": "Query: alpha"},
    ]
    assert context == {"query": "alpha"}
    assert prior == [{"role": "assistant", "content": "earlier"}]


def test_system_is_context_independent(prompt_root):
    messages = build_prompt_messages("sample", {"query": "a"})

    assert messages[0] == {"role": "system", "content": "Stable rules"}


def test_explicit_current_date_wins(date_prompt_root):
    messages = build_prompt_messages("dated", {"current_date": "2030-01-02"})

    assert messages[-1]["content"] == "Date: 2030-01-02"


def test_declared_current_date_is_injected(date_prompt_root):
    messages = build_prompt_messages("dated")

    assert messages[-1]["content"] == (
        f"Date: {datetime.now(timezone.utc).date().isoformat()}"
    )


def test_missing_user_variable_uses_jinja_default_undefined(prompt_root):
    messages = build_prompt_messages("sample")

    assert messages[-1]["content"] == "Query: "


def test_images_are_attached_only_to_user(image_prompt_root):
    messages = build_prompt_messages(
        "vision", {}, options=PromptBuildOptions(images=("YWJj",))
    )

    assert messages[0] == {"role": "system", "content": "Stable vision rules"}
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == [
        {"type": "text", "text": "Inspect image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}},
    ]


@pytest.mark.parametrize(
    ("name", "files", "context", "options"),
    [
        ("../escape", {}, {}, None),
        ("bad/name", {}, {}, None),
        ("missing", {}, {}, None),
        ("dynamic", {"system.md": "Rule: {{ query }}"}, {}, None),
        (
            "included_dynamic",
            {"system.md": "{% include 'included_dynamic/common.md' %}", "common.md": "{{ query }}"},
            {},
            None,
        ),
        ("sample", {"system.md": "Rules"}, {"messages": []}, None),
        ("image_only", {"system.md": "Rules"}, {}, PromptBuildOptions(images=("YWJj",))),
    ],
)
def test_invalid_prompt_inputs_raise_custom_value_exception(
    tmp_path, monkeypatch, name, files, context, options
):
    for filename, content in files.items():
        path = tmp_path / name / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(message_builder, "PROMPT_ROOT", tmp_path)

    with pytest.raises(CustomValueException):
        build_prompt_messages(name, context, options=options)


def test_missing_prompt_reports_directory_template_path(tmp_path, monkeypatch):
    monkeypatch.setattr(message_builder, "PROMPT_ROOT", tmp_path)

    with pytest.raises(CustomValueException) as caught:
        build_prompt_messages("missing")

    assert caught.value.error_code == StatusCode.FILE_NOT_FOUND_ERROR_PROMPT.code
    assert caught.value.message == "Prompt file missing/system.md not found."


def test_reserved_context_error_reports_directory_and_key(prompt_root):
    with pytest.raises(CustomValueException) as caught:
        build_prompt_messages("sample", {"messages": []})

    assert caught.value.error_code == StatusCode.APPLY_SYSTEM_PROMPT_FAILED.code
    assert "sample/" in caught.value.message
    assert "reserved context keys: ['messages']" in caught.value.message
    assert "sample.md" not in caught.value.message


def test_dynamic_system_error_reports_template_file(tmp_path, monkeypatch):
    prompt = tmp_path / "dynamic"
    prompt.mkdir()
    (prompt / "system.md").write_text("Rule: {{ query }}", encoding="utf-8")
    monkeypatch.setattr(message_builder, "PROMPT_ROOT", tmp_path)

    with pytest.raises(CustomValueException) as caught:
        build_prompt_messages("dynamic", {"query": "research"})

    assert caught.value.error_code == StatusCode.APPLY_SYSTEM_PROMPT_FAILED.code
    assert caught.value.message == "Applying DeepResearch prompt template dynamic/system.md failed"
