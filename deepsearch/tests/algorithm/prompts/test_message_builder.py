from datetime import datetime, timezone

import pytest

from openjiuwen_deepsearch.algorithm.prompts import message_builder
from openjiuwen_deepsearch.algorithm.prompts.message_builder import (
    PromptBuildOptions,
    build_prompt_messages,
)
from openjiuwen_deepsearch.common.exception import CustomValueException


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
