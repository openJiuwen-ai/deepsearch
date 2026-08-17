# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""base 包纯逻辑单测（零外部依赖；embedding/llm 适配等由消费方测试与 e2e 覆盖）。"""

import pytest

from openjiuwen_search_base.llm import (
    normalize_tool_calls,
    strip_unsupported_prompt_cache_key,
)
from openjiuwen_search_base.milvus import (
    escape_expr_string,
    hashes_filter,
    ids_filter,
    overlap_filter,
    revision_filter,
    versioned_collection_name,
)
from openjiuwen_search_base.runtime import RunRegistry


class TestExpr:
    @staticmethod
    def test_escape_quotes_backslash_newline():
        assert escape_expr_string('a"b\\c\nd') == 'a\\"b\\\\c d'

    @staticmethod
    def test_revision_filter_escaped():
        assert revision_filter('r"1') == 'ARRAY_CONTAINS(commits, "r\\"1")'

    @staticmethod
    def test_overlap_filter_coerces_ints():
        expr = overlap_filter("rev", "a.py", "3", "7")  # 字符串行号被强转 int
        assert "start_line <= 7 and end_line >= 3" in expr

    @staticmethod
    def test_hashes_and_ids_filters():
        assert hashes_filter(["h1", "h2"]) == 'file_hash in ["h1","h2"]'
        assert ids_filter([1, 2]) == "id in [1,2]"


class TestNaming:
    @staticmethod
    def test_prefix_and_version():
        assert versioned_collection_name("repo", "v1", "cs_") == "cs_repo__v1"


class TestRunRegistry:
    @staticmethod
    def test_register_get_unregister():
        reg: RunRegistry[dict] = RunRegistry()
        ctx = {"x": 1}
        run_id = reg.register(ctx)
        assert reg.get(run_id) is ctx
        reg.unregister(run_id)
        with pytest.raises(KeyError):
            reg.get(run_id)

    @staticmethod
    def test_unregister_idempotent():
        RunRegistry().unregister("nonexistent")  # 不抛异常

    @staticmethod
    def test_session_auto_unregisters():
        reg: RunRegistry[dict] = RunRegistry()
        ctx = {"x": 1}
        with reg.session(ctx) as run_id:
            assert reg.get(run_id) is ctx
        with pytest.raises(KeyError):
            reg.get(run_id)

    @staticmethod
    def test_session_unregisters_on_exception():
        reg: RunRegistry[dict] = RunRegistry()
        with pytest.raises(RuntimeError):
            with reg.session({"x": 1}) as run_id:
                raise RuntimeError("boom")
        with pytest.raises(KeyError):
            reg.get(run_id)


class TestPromptCacheKeyFallback:
    @staticmethod
    def test_type_error_strips_key():
        kwargs = {"prompt_cache_key": "retropus:abc", "temperature": 0.0}
        out = strip_unsupported_prompt_cache_key(TypeError("unexpected kw"), kwargs)
        assert out == {"temperature": 0.0}

    @staticmethod
    def test_provider_message_strips_key():
        kwargs = {"prompt_cache_key": "retropus:abc"}
        out = strip_unsupported_prompt_cache_key(
            RuntimeError("Unknown field: prompt_cache_key"), kwargs
        )
        assert out == {}

    @staticmethod
    def test_unrelated_error_returns_none():
        kwargs = {"prompt_cache_key": "retropus:abc"}
        assert strip_unsupported_prompt_cache_key(RuntimeError("timeout"), kwargs) is None

    @staticmethod
    def test_missing_key_returns_none():
        assert strip_unsupported_prompt_cache_key(TypeError("x"), {}) is None


class TestNormalizeToolCalls:
    class _Fn:
        def __init__(self, name, arguments):
            self.name, self.arguments = name, arguments

    class _Call:
        def __init__(self, fn, call_id="c1"):
            self.function, self.id = fn, call_id

    def test_openai_function_style(self):
        calls = normalize_tool_calls(
            [self._Call(self._Fn("search", '{"q": 1}'))]
        )
        assert calls[0].name == "search" and calls[0].arguments == {"q": 1}

    def test_bad_json_arguments_become_empty(self):
        calls = normalize_tool_calls([self._Call(self._Fn("t", "{bad"))])
        assert calls[0].arguments == {}

    def test_nameless_call_skipped(self):
        assert normalize_tool_calls([self._Call(self._Fn(None, "{}"))]) == []

    @staticmethod
    def test_none_input():
        assert normalize_tool_calls(None) == []


class TestExtractUsage:
    """用量以 token 上报——token 是 OpenAI 兼容端点的通用字段，金额各家不一。"""

    @staticmethod
    def test_reads_object_style_usage():
        from openjiuwen_search_base.llm import extract_usage

        class Usage:
            input_tokens, output_tokens = 13, 64

        class Resp:
            usage_metadata = Usage()

        assert extract_usage(Resp()) == (13, 64)

    @staticmethod
    def test_reads_dict_style_usage():
        from openjiuwen_search_base.llm import extract_usage

        class Resp:
            usage_metadata = {"input_tokens": 7, "output_tokens": 3}

        assert extract_usage(Resp()) == (7, 3)

    @staticmethod
    def test_missing_usage_is_zero_not_error():
        from openjiuwen_search_base.llm import extract_usage

        assert extract_usage(object()) == (0, 0)

    @staticmethod
    def test_partial_usage_defaults_to_zero():
        from openjiuwen_search_base.llm import extract_usage

        class Resp:
            usage_metadata = {"input_tokens": 5}

        assert extract_usage(Resp()) == (5, 0)
