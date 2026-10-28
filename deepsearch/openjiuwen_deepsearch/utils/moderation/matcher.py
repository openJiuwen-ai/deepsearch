"""Leftmost-longest literal matching without a large regex alternation."""

from collections.abc import Iterator
import string


_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_WORD_CHARS = frozenset(string.ascii_letters + string.digits + "_")
_TERMINAL = ""


class TextMatcher:
    """Separate case-sensitive literals and ASCII-folded, word-bounded terms.

    A node's empty key marks a terminal. Matching follows only shared prefixes,
    keeping the longest valid terminal before advancing past each match.
    """

    def __init__(self):
        self._literal = {}
        self._english = {}

    def add(self, term: str, *, english: bool) -> None:
        node = self._english if english else self._literal
        text = term.translate(_ASCII_LOWER) if english else term
        for char in text:
            node = node.setdefault(char, {})
        node[_TERMINAL] = True

    @staticmethod
    def _longest_end(root: dict, text: str, start: int, end: int, *, bounded: bool) -> int:
        node = root
        best = start
        for position in range(start, end):
            node = node.get(text[position])
            if node is None:
                break
            if _TERMINAL not in node:
                continue
            stop = position + 1
            if not bounded or stop == end or text[stop] not in _WORD_CHARS:
                best = stop
        return best

    def find_spans(self, text: str, start: int = 0, end: int | None = None) -> Iterator[tuple[int, int]]:
        stop = len(text) if end is None else end
        origin = start
        folded = text[start:stop].translate(_ASCII_LOWER) if self._english else ""
        while start < stop:
            best = self._longest_end(self._literal, text, start, stop, bounded=False)
            if self._english and (start == 0 or text[start - 1] not in _WORD_CHARS):
                english_end = self._longest_end(
                    self._english, folded, start - origin, stop - origin, bounded=True,
                )
                best = max(best, english_end + origin)
            if best > start:
                yield start, best
                start = best
            else:
                start += 1
