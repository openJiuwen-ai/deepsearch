import pytest

from openjiuwen_deepsearch.utils.common_utils import moderation_mask


@pytest.fixture
def sample_lexicon(monkeypatch):
    monkeypatch.setattr(
        moderation_mask, "_load_lexicon_bundled",
        lambda: frozenset({"敏感", "sex", "blocked.example"}),
    )


def test_is_moderation_error_detects_input_block():
    assert moderation_mask.is_moderation_error(
        "status code is 400 - Input data may contain inappropriate content"
    )


def test_is_moderation_error_detects_output_block():
    assert moderation_mask.is_moderation_error(
        "Output data may contain inappropriate content. data_inspection_failed"
    )


def test_is_moderation_error_detects_data_inspection_code():
    assert moderation_mask.is_moderation_error('{"code":"data_inspection_failed"}')


def test_is_moderation_error_non_moderation():
    assert not moderation_mask.is_moderation_error("context length exceeded")
    assert not moderation_mask.is_moderation_error("rate limit exceeded")
    assert not moderation_mask.is_moderation_error("")


def test_is_moderation_error_via_cause_chain():
    class _Inner(Exception):
        pass

    cause = _Inner("upstream: Output data may contain inappropriate content")
    assert moderation_mask.is_moderation_error("status code is 400", cause=cause)


def test_mask_text_replaces_terms_with_mask_char():
    terms = frozenset({"敏感", "禁忌词"})
    s = "这是敏感内容，含禁忌词。"
    masked, nchars, nspans = moderation_mask.mask_text(s, terms)
    assert "敏感" not in masked
    assert "禁忌词" not in masked
    assert len(masked) == len(s)
    assert nchars == 5  # 敏感(2) + 禁忌词(3)
    assert nspans == 2


def test_mask_text_empty_or_no_terms():
    assert moderation_mask.mask_text("", frozenset({"example"}))[0] == ""
    masked, n, _ = moderation_mask.mask_text("abc", frozenset())
    assert masked == "abc" and n == 0


def test_mask_messages_level1_user_only(sample_lexicon):
    msgs = [
        {"role": "system", "content": "你是敏感助手"},
        {"role": "user", "content": "讨论敏感话题"},
    ]
    out, nchars, nspans = moderation_mask.mask_messages(msgs, 1)
    # user 被掩
    assert "敏感" not in out[1]["content"]
    # system 不被掩（level 1）
    assert out[0]["content"] == "你是敏感助手"
    assert nchars == 2


def test_mask_messages_level2_user_and_system(sample_lexicon):
    msgs = [
        {"role": "system", "content": "你是敏感助手"},
        {"role": "user", "content": "讨论敏感话题"},
    ]
    out, nchars, nspans = moderation_mask.mask_messages(msgs, 2)
    assert "敏感" not in out[0]["content"]
    assert "敏感" not in out[1]["content"]
    assert nchars == 4  # system 2 + user 2


def test_mask_messages_preserves_non_target_roles(sample_lexicon):
    msgs = [
        {"role": "tool", "content": "敏感工具输出"},
        {"role": "user", "content": "敏感"},
    ]
    out, _, _ = moderation_mask.mask_messages(msgs, 2)
    # tool 角色不被掩
    assert out[0]["content"] == "敏感工具输出"
    # user 被掩
    assert out[1]["content"] == "**"


def test_mask_messages_does_not_mutate_input(sample_lexicon):
    msgs = [{"role": "user", "content": "敏感"}]
    moderation_mask.mask_messages(msgs, 1)
    assert msgs[0]["content"] == "敏感"  # 原列表未改


def test_bundled_lexicon_loads_nonempty():
    """打包的默认词库能加载且非空（smoke test）。"""
    terms = moderation_mask.get_lexicon()
    assert len(terms) > 1000, "打包词库应含大量词条"


def test_bundled_lexicon_excludes_decimal_entries():
    terms = moderation_mask.get_lexicon()
    assert not any(term.isdecimal() for term in terms)


@pytest.mark.parametrize("text", [
    "year=2024; amount=189; count=65",
    "web_31ec7f2479ec60e7_pa0e88d32d13bf01b",
    "web_ad16af38ba218943_p39d7e9cc010b6e1f",
    "24 64 189 ２４",
])
def test_bundled_lexicon_does_not_mask_numbers_or_source_ids(text):
    assert moderation_mask.mask_text(text, moderation_mask.get_lexicon()) == (text, 0, 0)


@pytest.mark.parametrize("text", [
    "url URL www WWW wikipedia Wikipedia is IS this BC Bc",
    "没有 一个 时间 进行 处理 信息 背景 第一 第二 联系 制作 计划",
    "2020年 1970年 1978年 2013年12月26日 2016年08月17日 2019年7月9日 20周年 20年前",
])
def test_bundled_lexicon_preserves_reviewed_false_positives(text):
    assert moderation_mask.mask_text(text, moderation_mask.get_lexicon()) == (text, 0, 0)


def test_bundled_lexicon_keeps_negation():
    text, _, _ = moderation_mask.mask_text("基本上没有受到挑战", moderation_mask.get_lexicon())
    assert "没有受到挑战" in text


def test_bundled_lexicon_preserves_embedded_url_and_mapping_keys():
    import json

    original = {"document": {"url": "https://example.invalid/page", "source_ids": {
        "www": "web_123_p456", "en": {"wikipedia": "web_456_p789"},
    }}}
    text, _, _ = moderation_mask.mask_text(json.dumps(original), moderation_mask.get_lexicon())
    assert json.loads(text) == original


def test_get_lexicon_only_uses_bundled_resource(monkeypatch):
    expected = frozenset({"example"})
    monkeypatch.setattr(moderation_mask, "_load_lexicon_bundled", lambda: expected)
    assert moderation_mask.get_lexicon() is expected


def test_configuration_does_not_expose_external_lexicon_directory():
    from openjiuwen_deepsearch.config.config import LLMConfig

    assert "moderation_mask_lexicon_dir" not in LLMConfig.model_fields
    assert "moderation_mask_lexicon_dir" not in LLMConfig().model_dump()


@pytest.mark.parametrize("text", ["sex", "Sex", "SEX", "(Sex)", "性教育Sex相关"])
def test_english_case_and_boundaries(text):
    masked, count, _ = moderation_mask.mask_text(text, frozenset({"sex"}))
    assert "***" in masked
    assert count == 3


@pytest.mark.parametrize("text", ["Sussex University", "Essex", "sexual health", "sex_1", "1sex"])
def test_english_does_not_mask_inside_words(text):
    assert moderation_mask.mask_text(text, frozenset({"sex"})) == (text, 0, 0)


def test_english_phrase_longest_match_and_literal_spacing():
    terms = frozenset({"bad", "bad word"})
    assert moderation_mask.mask_text("BAD WORD", terms) == ("*" * 8, 8, 1)
    assert moderation_mask.mask_text("bad  word", terms) == ("***  word", 3, 1)


def test_chinese_english_and_mixed_terms_use_same_mask_character():
    text = "敏感 SQL注入 Sex"
    assert moderation_mask.mask_text(text, frozenset({"敏感", "SQL注入", "sex"})) == (
        "** ***** ***", 10, 3,
    )


@pytest.mark.parametrize("address", [
    "https://blocked.example/path?q=1#part", "HTTP://BLOCKED.EXAMPLE:443/path",
    "https://sub.blocked.example", "www.blocked.example/path", "blocked.example",
    "sub.blocked.example:8080/path", "https://blocked.example./path",
])
def test_blocked_domain_replaces_entire_address(address):
    result, chars, spans = moderation_mask.mask_text(address, frozenset({"blocked.example"}))
    assert result == moderation_mask.URL_PLACEHOLDER
    assert chars == len(address)
    assert spans == 1


@pytest.mark.parametrize("address", [
    "https://notblocked.example", "https://blocked.example.safe.example",
    "https://safe.example/blocked.example", "https://safe.example/?q=blocked.example",
    "https://sussex.ac.uk/sex", "www.sussex.ac.uk", "sussex.ac.uk",
    "sex@blocked.example", "https://user@blocked.example/path",
    "https://blocked.example:bad/path", "ftp://blocked.example/path",
    "blocked.example:bad/path",
])
def test_normal_or_invalid_addresses_are_not_partially_masked(address):
    terms = frozenset({"sex", "blocked.example"})
    assert moderation_mask.mask_text(address, terms) == (address, 0, 0)


def test_markdown_url_preserves_label_delimiters_and_punctuation():
    address = "https://blocked.example/a_(b)"
    text = f"[source]({address}). Sex"
    masked, count, spans = moderation_mask.mask_text(text, frozenset({"sex", "blocked.example"}))
    assert masked == "[source]([URL_REDACTED]). ***"
    assert count == len(address) + 3
    assert spans == 2


def test_url_terms_with_paths_are_not_promoted_to_domain_rules():
    terms = frozenset({"https://blocked.example/private", "other.example/a?b=1"})
    text = "https://blocked.example/private https://blocked.example/public other.example/a?b=1"
    assert moderation_mask.mask_text(text, terms) == (text, 0, 0)


def test_adjacent_markdown_addresses_are_processed_independently():
    text = "[one](https://blocked.example/a)[two](https://sussex.ac.uk/sex)"
    masked, count, spans = moderation_mask.mask_text(text, frozenset({"blocked.example", "sex"}))
    assert masked == "[one]([URL_REDACTED])[two](https://sussex.ac.uk/sex)"
    assert count == len("https://blocked.example/a")
    assert spans == 1


def test_comma_separated_urls_do_not_swallow_next_address():
    text = "https://blocked.example/a,https://sussex.ac.uk/sex"
    assert moderation_mask.mask_text(text, frozenset({"blocked.example", "sex"}))[0] == (
        "[URL_REDACTED],https://sussex.ac.uk/sex"
    )


def test_idna_domains_are_normalized():
    text = "https://例子.测试/a"
    assert moderation_mask.mask_text(text, frozenset({"xn--fsqu00a.xn--0zwm56d"})) == (
        moderation_mask.URL_PLACEHOLDER, len(text), 1,
    )


def test_bundled_domain_rules_and_message_copies(sample_lexicon):
    text = "敏感 Sex https://blocked.example/a https://sussex.ac.uk"
    messages = [{"role": role, "content": text} for role in ("system", "user", "assistant", "tool")]
    masked, count, _ = moderation_mask.mask_messages(messages, 1)
    assert masked[1]["content"] == "** *** [URL_REDACTED] https://sussex.ac.uk"
    assert all(message["content"] == text for message in messages)
    assert all(masked[i]["content"] == text for i in (0, 2, 3))
    assert count == 5 + len("https://blocked.example/a")
