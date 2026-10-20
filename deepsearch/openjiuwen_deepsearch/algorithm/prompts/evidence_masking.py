"""Template-owned evidence scopes, independent of rendered headings or content."""

import json
from typing import Any

from jinja2 import Template

from openjiuwen_deepsearch.utils.common_utils.moderation_content import MaskableText
from openjiuwen_deepsearch.utils.common_utils.moderation_mask import mask_text


# Only known body/URL leaves are eligible. Keys, tasks, titles, IDs and dates
# remain outside the scope, even when they contain the same lexicon terms.
_EVIDENCE_PATHS = {
    "passages_extractor": (("documents", "*", "content"), ("documents", "*", "url")),
    "collector_supervisor": (
        ("evidence_table", "*", "key_passages", "*"),
        ("material_evidence", "*", "summary"),
    ),
    "collector_final": (
        ("evidence_pack", "sources", "*", "key_passages", "*"),
        ("evidence_pack", "sources", "*", "url"),
    ),
    "extract_message_prompt": (("datas", "*", "citation_content"),),
}
_JSON_PATHS = {
    "collector_webpage_enrichment_compress": (
        ("existing_content",), ("webpage_content",),
        ("document", "key_passages", "*"), ("document", "url"),
    ),
    "collector_webpage_enrichment_select": (
        ("candidates", "*", "key_passages", "*"), ("candidates", "*", "url"),
    ),
}
_ANNOTATED_FIELDS = {"sub_report_markdown": ("citation_infos",)}


def _mask_path(value: Any, path: tuple[str, ...], terms: frozenset[str]) -> tuple[Any, int, int]:
    if not path:
        return mask_text(value, terms) if isinstance(value, str) else (value, 0, 0)
    key, *remaining = path
    tail = tuple(remaining)
    if key == "*" and isinstance(value, list):
        result, chars, hits = [], 0, 0
        for item in value:
            masked, count, spans = _mask_path(item, tail, terms)
            result.append(masked)
            chars += count
            hits += spans
        return result, chars, hits
    if isinstance(value, dict) and key in value:
        masked, chars, hits = _mask_path(value[key], tail, terms)
        return {**value, key: masked}, chars, hits
    return value, 0, 0


def scoped_prompt_text(
    prompt_name: str, template: Template, context: dict, original: str,
) -> str:
    """Retain lazy masking provenance without changing initial prompt bytes."""
    paths = _EVIDENCE_PATHS.get(prompt_name, ())
    json_paths = _JSON_PATHS.get(prompt_name, ())
    annotated_fields = _ANNOTATED_FIELDS.get(prompt_name, ())
    if not (paths or json_paths or annotated_fields):
        return original

    def mask(terms: frozenset[str]) -> tuple[str, int, int]:
        # Context is shallow-copied by the builder. If a caller later mutates
        # nested data, do not silently replace the prompt with different input.
        if template.render(**context) != original:
            return original, 0, 0
        prepared, chars, hits = context, 0, 0
        for path in paths:
            prepared, count, spans = _mask_path(prepared, path, terms)
            chars += count
            hits += spans
        if json_paths and isinstance(context.get("payload"), str):
            try:
                payload = json.loads(context["payload"])
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict):
                payload_chars = payload_hits = 0
                for path in json_paths:
                    payload, count, spans = _mask_path(payload, path, terms)
                    payload_chars += count
                    payload_hits += spans
                if payload_chars:
                    prepared = {**prepared, "payload": json.dumps(payload, ensure_ascii=False)}
                    chars += payload_chars
                    hits += payload_hits
        for name in annotated_fields:
            value = context.get(name)
            if isinstance(value, MaskableText):
                masked, count, spans = value.mask(terms)
                prepared = {**prepared, name: masked}
                chars += count
                hits += spans
        return (template.render(**prepared), chars, hits) if chars else (original, 0, 0)

    return MaskableText(original, mask)
