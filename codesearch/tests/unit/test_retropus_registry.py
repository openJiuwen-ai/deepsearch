# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Retropus registry isolation — no tree-sitter / bm25s required."""

from types import SimpleNamespace

from openjiuwen_codesearch.algorithm.search_tools import build_default_registry
from openjiuwen_codesearch.algorithm.search_tools.retropus_registry import (
    build_retropus_registry,
)
from tests.conftest import run


CORE_SCHEMAS = [
    {"type": "function", "function": {"name": "search_code", "parameters": {}}},
    {"type": "function", "function": {"name": "search_text", "parameters": {}}},
    {"type": "function", "function": {"name": "get_repo_structure", "parameters": {}}},
    {"type": "function", "function": {"name": "read_file", "parameters": {}}},
    {"type": "function", "function": {"name": "add_context", "parameters": {}}},
    {"type": "function", "function": {"name": "finish", "parameters": {}}},
]


class FakeRetropusTools:
    def __init__(self, schemas=None):
        self._schemas = schemas or list(CORE_SCHEMAS)
        self.calls: list[tuple[str, dict]] = []

    def tool_schemas(self):
        return list(self._schemas)

    def dispatch(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        if name == "finish":
            return "Finished. Recorded 1 span(s)."
        return f"ok:{name}"


def test_default_registry_unchanged_by_retropus():
    registry = build_default_registry()
    assert set(registry) == {
        "view_repo_map",
        "search_codebase",
        "expand_context",
        "delete_snippets",
        "submit_final_snippets",
    }
    # Retropus tool names must not appear in the CodeSearch registry
    assert "search_code" not in registry
    assert "finish" not in registry


def test_retropus_registry_core_tools_only():
    tools = FakeRetropusTools()
    registry = build_retropus_registry(tools)
    assert list(registry) == [
        "search_code",
        "search_text",
        "get_repo_structure",
        "read_file",
        "add_context",
        "finish",
    ]
    # CodeSearch tools are not present
    assert "search_codebase" not in registry
    assert "submit_final_snippets" not in registry


def test_retropus_executor_dispatches_and_sets_finish():
    tools = FakeRetropusTools()
    registry = build_retropus_registry(tools)

    class Env:
        finish_requested = False
        finish_blocked = False

    env = Env()
    outcome = run(registry["search_code"].executor(env, {"query": "foo"}))
    assert outcome.message == "ok:search_code"
    assert tools.calls == [("search_code", {"query": "foo"})]

    outcome = run(registry["finish"].executor(env, {}))
    assert env.finish_requested is True
    assert env.finish_blocked is False
    assert "Finished" in outcome.message


def test_retropus_finish_blocked():
    class BlockingTools(FakeRetropusTools):
        def dispatch(self, name: str, args: dict) -> str:
            if name == "finish":
                return "finish blocked: need more spans"
            return super().dispatch(name, args)

    registry = build_retropus_registry(BlockingTools())

    class Env:
        finish_requested = False
        finish_blocked = False

    env = Env()
    run(registry["finish"].executor(env, {}))
    assert env.finish_requested is False
    assert env.finish_blocked is True


def test_retropus_registry_uses_graph_expand_specs():
    from openjiuwen_codesearch.algorithm.search_tools.graph_tools import (
        EXPAND_FILE_DEFS_SCHEMA,
        EXPAND_IMPORTS_SCHEMA,
        EXPAND_INHERITANCE_SCHEMA,
        execute_expand_file_defs,
        execute_expand_imports,
        execute_expand_inheritance,
    )

    schemas = list(CORE_SCHEMAS)
    schemas.insert(-1, EXPAND_FILE_DEFS_SCHEMA)
    schemas.insert(-1, EXPAND_INHERITANCE_SCHEMA)
    schemas.insert(-1, EXPAND_IMPORTS_SCHEMA)
    tools = FakeRetropusTools(schemas=schemas)
    registry = build_retropus_registry(tools)

    assert list(registry)[-4:] == [
        "expand_file_defs",
        "expand_inheritance",
        "expand_imports",
        "finish",
    ]
    assert registry["expand_file_defs"].executor is execute_expand_file_defs
    assert registry["expand_inheritance"].executor is execute_expand_inheritance
    assert registry["expand_imports"].executor is execute_expand_imports

    class Env:
        def __init__(self):
            self.tools = tools

    env = Env()
    outcome = run(registry["expand_file_defs"].executor(env, {"path": "a.py"}))
    assert outcome.message == "ok:expand_file_defs"
    assert ("expand_file_defs", {"path": "a.py"}) in tools.calls


def test_retropus_registry_reuses_delete_snippets_executor():
    from openjiuwen_codesearch.algorithm.search_tools.memory_tools import (
        DELETE_SCHEMA,
        execute_delete,
    )

    schemas = list(CORE_SCHEMAS)
    schemas.insert(-1, DELETE_SCHEMA)
    tools = FakeRetropusTools(schemas=schemas)
    registry = build_retropus_registry(tools)

    assert list(registry)[-2:] == ["delete_snippets", "finish"]
    assert registry["delete_snippets"].executor is execute_delete

    class Memory:
        def delete(self, snippet_ids):
            assert snippet_ids == [1, 3]
            return 2

    class Env:
        memory = Memory()
        turn = 1
        config = SimpleNamespace(agent=SimpleNamespace(max_turns=12))

    outcome = run(
        registry["delete_snippets"].executor(
            Env(), {"snippet_ids": [1, 3], "reasoning": "noise"}
        )
    )
    assert "Successfully deleted 2 snippets" in outcome.message
    assert "noise" in outcome.message
