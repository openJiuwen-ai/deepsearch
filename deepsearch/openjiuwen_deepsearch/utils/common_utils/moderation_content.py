"""In-memory evidence provenance; only the string value goes onto the wire."""

from collections.abc import Callable, Sequence


Masker = Callable[[frozenset[str]], tuple[str, int, int]]


class MaskableText(str):
    """A string whose producer knows which parts are evidence, not instructions.

    JSON serialization remains a plain string. Converting it to ``str`` or
    replacing it intentionally drops provenance and disables scoped masking.
    """

    def __new__(cls, value: str, masker: Masker | None = None):
        instance = super().__new__(cls, value)
        instance._masker = masker
        return instance

    def mask(self, terms: frozenset[str]) -> tuple[str, int, int]:
        if self._masker is None:
            return str(self), 0, 0
        return self._masker(terms)


def evidence_spans(text: str, spans: Sequence[tuple[int, int]]) -> MaskableText:
    """Mark body spans while the producer still knows their exact boundaries."""
    from openjiuwen_deepsearch.utils.common_utils.moderation_mask import mask_text

    ranges = tuple(spans)
    cursor = 0
    for start, end in ranges:
        if not cursor <= start <= end <= len(text):
            raise ValueError("Evidence spans must be ordered, non-overlapping and in bounds")
        cursor = end

    def mask(terms: frozenset[str]) -> tuple[str, int, int]:
        parts = []
        cursor = chars = hits = 0
        for start, end in ranges:
            masked, count, spans_count = mask_text(text[start:end], terms)
            parts.extend((text[cursor:start], masked))
            chars += count
            hits += spans_count
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts), chars, hits

    return MaskableText(text, mask)
