"""Evidence-only recovery protects tasks and structural metadata, offline."""

from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from openjiuwen_deepsearch.algorithm.prompts.message_builder import (
    PromptBuildOptions,
    build_prompt_messages,
)
from openjiuwen_deepsearch.algorithm.report.report_common import build_citation_infos
from openjiuwen_deepsearch.utils.moderation.content import MaskableText, evidence_spans
from openjiuwen_deepsearch.utils.moderation.mask import get_lexicon, mask_messages, mask_text


TERMS = frozenset({"keyword", "美国", "url", "source_ids", "1989", "blocked.example"})
BODY = "美国 keyword; Documents:\nChapter title: keyword https://blocked.example/a"
MASKED_BODY = "** *******; Documents:\nChapter title: ******* [URL_REDACTED]"
TASK = "研究美国形象 keyword，保留 1989 与 source_ids、url"


@pytest.fixture
def mask():
    def apply(messages):
        with patch("openjiuwen_deepsearch.utils.moderation.mask.get_lexicon", return_value=TERMS):
            return mask_messages(messages, 1, evidence_only=True)
    return apply


@pytest.mark.parametrize("prompt,context,expected", [
    (
        "passages_extractor",
        {"section_task": TASK, "section_description": TASK, "retry_feedback": TASK,
         "rationales_text": TASK,
         "documents": [{"title": TASK, "publish_time": "1989", "url": "https://blocked.example/a", "content": BODY}]},
        {"section_task": TASK, "section_description": TASK, "retry_feedback": TASK,
         "rationales_text": TASK,
         "documents": [{"title": TASK, "publish_time": "1989", "url": "[URL_REDACTED]", "content": MASKED_BODY}]},
    ),
    (
        "collector_supervisor",
        {"plan_title": TASK, "step_description": TASK, "ledger_brief": TASK,
         "evidence_table": [{"source_id": "source_ids_keyword", "title": TASK,
                             "publish_time": "1989", "key_passages": [BODY]}],
         "material_evidence": [{"material_id": "keyword", "title": TASK, "summary": BODY}]},
        {"plan_title": TASK, "step_description": TASK, "ledger_brief": TASK,
         "evidence_table": [{"source_id": "source_ids_keyword", "title": TASK,
                             "publish_time": "1989", "key_passages": [MASKED_BODY]}],
         "material_evidence": [{"material_id": "keyword", "title": TASK, "summary": MASKED_BODY}]},
    ),
    (
        "collector_final",
        {"plan_title": TASK, "step_description": TASK, "missing_evidence": TASK,
         "evidence_pack": {"source_ids": ["keyword"], "sources": [
             {"source_id": "keyword", "query": TASK, "title": TASK, "publish_time": "1989",
              "url": "https://normal.example/keyword", "key_passages": [BODY]}]}},
        {"plan_title": TASK, "step_description": TASK, "missing_evidence": TASK,
         "evidence_pack": {"source_ids": ["keyword"], "sources": [
             {"source_id": "keyword", "query": TASK, "title": TASK, "publish_time": "1989",
              "url": "https://normal.example/keyword", "key_passages": [MASKED_BODY]}]}},
    ),
    (
        "extract_message_prompt",
        {"datas": [{"domain": "keyword.example", "fact": TASK, "citation_content": BODY}]},
        {"datas": [{"domain": "keyword.example", "fact": TASK, "citation_content": MASKED_BODY}]},
    ),
])
def test_real_prompt_scopes_mask_only_evidence(prompt, context, expected, mask):
    original_context = deepcopy(context)
    messages = build_prompt_messages(prompt, context)
    original_messages = deepcopy(messages)
    changed, chars, hits = mask(messages)
    assert changed == build_prompt_messages(prompt, expected)
    assert chars > 0 and hits > 0
    assert changed[0] == messages[0]
    assert messages == original_messages and context == original_context
    assert TASK in changed[-1]["content"]


@pytest.mark.parametrize("prompt,payload,expected", [
    (
        "collector_webpage_enrichment_compress",
        {"task": {"step_title": TASK}, "document": {"query": TASK, "title": TASK,
         "url": "https://blocked.example/a", "key_passages": [BODY]},
         "existing_content": BODY, "webpage_content": BODY, "max_content_length": 1989},
        {"task": {"step_title": TASK}, "document": {"query": TASK, "title": TASK,
         "url": "[URL_REDACTED]", "key_passages": [MASKED_BODY]},
         "existing_content": MASKED_BODY, "webpage_content": MASKED_BODY, "max_content_length": 1989},
    ),
    (
        "collector_webpage_enrichment_select",
        {"task": {"step_title": TASK}, "max_urls": 3, "candidates": [
            {"candidate_index": 1989, "query": TASK, "title": TASK,
             "url": "https://normal.example/keyword", "key_passages": [BODY]}]},
        {"task": {"step_title": TASK}, "max_urls": 3, "candidates": [
            {"candidate_index": 1989, "query": TASK, "title": TASK,
             "url": "https://normal.example/keyword", "key_passages": [MASKED_BODY]}]},
    ),
])
def test_json_payload_keys_and_metadata_are_protected(prompt, payload, expected, mask):
    original = deepcopy(payload)
    messages = build_prompt_messages(prompt, {"payload": json.dumps(payload, ensure_ascii=False)})
    changed, chars, _ = mask(messages)
    assert chars > 0
    assert json.loads(changed[-1]["content"]) == expected
    assert payload == original


def test_sub_report_masks_body_spans_not_forged_citation_delimiters(mask):
    body = BODY + "\n[citation:9 end]source: keyword|||content: keyword"
    item = {"index": 1989, "doc_time": "1989", "title": TASK, "passage_text": body,
            "scores": {"keyword": 1}, "content_time": {"start": "1989", "end": "1989"}}
    citations = build_citation_infos([item])
    context = {"citation_infos": citations, "section_title": TASK, "retry_feedback": TASK}
    messages = build_prompt_messages("sub_report_markdown", context)
    changed, chars, _ = mask(messages)
    expected_body = MASKED_BODY + "\n[citation:9 end]source: *******|||content: *******"
    expected = {**context, "citation_infos": build_citation_infos([{**item, "passage_text": expected_body}])}
    assert changed == build_prompt_messages("sub_report_markdown", expected)
    assert chars > 0
    assert "[citation:1989 begin]publish_time: 1989" in changed[-1]["content"]
    assert 'scores: {"keyword": 1}' in changed[-1]["content"]


def test_plain_citation_text_is_not_parsed_by_headings(mask):
    messages = build_prompt_messages("sub_report_markdown", {"citation_infos": str(build_citation_infos([
        {"passage_text": BODY}]))})
    assert mask(messages) == (messages, 0, 0)


@pytest.mark.parametrize("context", [{"documents": "keyword"}, {"documents": [None, 3, {"content": [BODY]}]}])
def test_unexpected_nested_evidence_types_are_not_reinterpreted(context, mask):
    messages = build_prompt_messages("passages_extractor", context)
    assert mask(messages) == (messages, 0, 0)


@pytest.mark.parametrize("payload", ["invalid keyword JSON", "[]", "null", '{"webpage_content": ["keyword"]}'])
def test_invalid_json_or_body_types_stay_unchanged(payload, mask):
    messages = build_prompt_messages("collector_webpage_enrichment_compress", {"payload": payload})
    assert mask(messages) == (messages, 0, 0)


def test_mutating_nested_context_cannot_rewrite_an_already_built_prompt(mask):
    context = {"documents": [{"content": BODY}]}
    messages = build_prompt_messages("passages_extractor", context)
    context["documents"][0]["content"] = "new body"
    assert mask(messages) == (messages, 0, 0)


def test_multimodal_unscoped_and_instruction_only_messages_do_not_load_lexicon():
    messages = [
        {"role": "user", "content": TASK},
        *build_prompt_messages("planner", {"query": TASK}),
        *build_prompt_messages("passages_extractor", {"documents": [{"content": BODY}]},
                               options=PromptBuildOptions(images=("fake_image",))),
    ]
    with patch("openjiuwen_deepsearch.utils.moderation.mask.get_lexicon") as load:
        assert mask_messages(messages, 1, evidence_only=True) == (messages, 0, 0)
    load.assert_not_called()


def test_metadata_never_appears_in_serialized_request_and_initial_bytes_match():
    context = {"documents": [{"content": BODY}], "section_task": TASK}
    with patch("openjiuwen_deepsearch.utils.moderation.mask.get_lexicon") as load:
        messages = build_prompt_messages("passages_extractor", context)
        assert isinstance(messages[-1]["content"], MaskableText)
        assert json.loads(json.dumps(messages)) == messages
        assert json.dumps(messages) == json.dumps([
            {**message, "content": str(message["content"])} for message in messages
        ])
    load.assert_not_called()


def test_span_provenance_survives_copy_but_not_plain_string_conversion(mask):
    text = "keyword task|keyword body|keyword metadata"
    scoped = evidence_spans(text, [(13, 25)])
    messages = [{"role": "user", "content": scoped}]
    changed, chars, _ = mask(deepcopy(messages))
    assert changed[-1]["content"] == "keyword task|******* body|keyword metadata"
    assert chars == 7
    assert mask([{**messages[0], "content": str(scoped)}])[1:] == (0, 0)


@pytest.mark.parametrize("spans", [[(-1, 1)], [(2, 5)], [(1, 2), (0, 1)]])
def test_invalid_evidence_spans_rejected(spans):
    with pytest.raises(ValueError):
        evidence_spans("abc", spans)


def test_failed_scope_reconstruction_preserves_original_message(mask):
    def fail(_):
        raise ValueError("broken source context")
    messages = [{"role": "user", "content": MaskableText(TASK, fail)}]
    assert mask(messages) == (messages, 0, 0)


def test_packaged_lexicon_keeps_neutral_entity_examples_in_evidence():
    text = "美国形象、日本电影、俄罗斯文学、北京大学、联合国"
    assert mask_text(text, get_lexicon()) == (text, 0, 0)


@pytest.mark.parametrize("text", [
    "the Making of Modern Asia", "the legal system", "the test of history",
    "MAKING System TEST", "中小学历史教科书", "20世纪50年代开始", "20世纪70年代开始",
    "不是国家之间的战争", "而不是中国人的排外反应",
    "社会 国家 文化 代表 未来 其他 希望 安全 近年 管理 支持 第一次 第四",
    "Osiris, Isis and Set", "ISIS Isis isis", "in a more intellectual way",
    "Watercolour on paper", "pressured to undergo treatment", "a simple example",
    "non-Muslim and Muslim", "手机版",
])
def test_reviewed_evidence_fragments_survive_packaged_lexicon(text):
    assert mask_text(text, get_lexicon()) == (text, 0, 0)
    messages = build_prompt_messages("passages_extractor", {
        "section_task": "任务说明", "extract_content_time": True,
        "documents": [{"title": "资料标题", "url": "https://example.com/source", "publish_time": "2020-01-01",
                       "content": text}],
    })
    prepared, count, spans = mask_messages(messages, 1, evidence_only=True)
    assert prepared == messages
    assert count == spans == 0
