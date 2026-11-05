"""Compare trie and address preselection against the previous regex semantics."""

import random
import re

import pytest

from openjiuwen_deepsearch.utils.moderation import mask
from openjiuwen_deepsearch.utils.moderation.matcher import TextMatcher


def _legacy_pattern(terms):
    alternatives = []
    for term in sorted(terms, key=lambda value: (-len(value), value)):
        escaped = re.escape(term)
        if mask._is_english_term(term):
            escaped = r"(?<![A-Za-z0-9_])(?ai:" + escaped + r")(?![A-Za-z0-9_])"
        alternatives.append(escaped)
    return re.compile("|".join(alternatives))


def test_trie_matches_previous_leftmost_longest_and_ascii_boundaries():
    terms = {"ab", "abc", "ab cd", "bad", "bad word", "a+b", "a.b", "敏感", "敏感词", "SQL注入", "café", "中文AB"}
    matcher = TextMatcher()
    for term in terms:
        matcher.add(term, english=mask._is_english_term(term))
    pattern = _legacy_pattern(terms)
    randomizer = random.Random(476)
    tokens = [*sorted(terms), "BAD WORD", "CAFÉ", "Sussex", "_abc", "xabc", "abc9", "中文ab", "ABC", " ", ".", "\n"]
    samples = ["bad word bad words", "敏感词敏感", "SQL注入 sql注入", "café CAFé CAFÉ"]
    samples.extend(" ".join(randomizer.choices(tokens, k=20)) for _ in range(200))
    for text in samples:
        for start, end in ((0, len(text)), (1, len(text) - 1), (3, len(text) // 2)):
            expected = [match.span() for match in pattern.finditer(text, start, end)]
            assert list(matcher.find_spans(text, start, end)) == expected


def test_full_lexicon_trie_matches_previous_regex():
    terms = frozenset(
        term for term in mask.get_lexicon()
        if "://" not in term and not mask._address_domain(term)
    )
    pattern = _legacy_pattern(terms)
    matcher = TextMatcher()
    for term in terms:
        matcher.add(term, english=mask._is_english_term(term))
    randomizer = random.Random(476)
    sample = randomizer.sample(sorted(terms), 100)
    text = " | ".join(sample + [term.upper() for term in sample])
    assert list(matcher.find_spans(text)) == [match.span() for match in pattern.finditer(text)]


def _legacy_addresses(text):
    result = []
    cursor = 0
    while True:
        match = mask._ADDRESS.search(text, cursor)
        if match is None:
            return result
        address = mask._trim_address(match.group())
        end = match.start() + len(address)
        result.append((match.start(), end, address))
        cursor = max(end, match.start() + 1)


@pytest.mark.parametrize("text", [
    "[one](https://blocked.example/a_(b)),www.normal.example;https://next.example",
    "中文https://normal.example和资料。3http://normal.example",
    "a.b+tag@example.com @example.com prefix@example.com/path",
    "//normal.example -bad.example :bad.example /bad.example _good.example",
    "xhttps://normal.example/a]https://next.example",
    "1http://normal.example/a;www.other.example",
])
def test_address_preselection_matches_previous_search(text):
    assert list(mask._address_candidates(text)) == _legacy_addresses(text)


def test_random_address_preselection_matches_previous_search():
    randomizer = random.Random(476)
    tokens = ["https://a.example/p", "www.a.example", "abc", "1abc", "中文", "_", "-", ".", "@", "://", "/", "]", "(", " ", ";", "\n"]
    for _ in range(1000):
        text = "".join(randomizer.choices(tokens, k=20))
        assert list(mask._address_candidates(text)) == _legacy_addresses(text), text
