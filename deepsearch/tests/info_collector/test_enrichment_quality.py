"""压缩 Prompt 的事实提示清单提取测试。"""
import pytest
from decimal import InvalidOperation, localcontext
from openjiuwen_deepsearch.algorithm.research_collector.enrichment_quality import extract_fact_anchors, required_fact_anchors
from openjiuwen_deepsearch.algorithm.research_collector.webpage_enrichment import build_compression_user_payload


def document(text):
    return {"original_content": text, "key_passages": [text]}


@pytest.mark.parametrize("markup", [
    "## II. CONCLUSION\n[PDF](https://example.com/file.pdf)\nMeasured efficiency was 98.96%.",
    '<h2>III. RESULTS</h2><a href="#ftn1" id="ftnref1">[1]</a>Measured efficiency was 98.96%.',
    "See sections II and III. [1](#ftnref1) Measured efficiency was 98.96%.",
    "Measured efficiency was 98.96%. https://example.com/2019/state-of-the-art/ftn1",
    "BRIEF DESCRIPTION OF THE DRAWINGS  Measured efficiency was 98.96%.",
    "Measured efficiency was 98.96%. downloadDownload free PDFView PDFchevron\\_right",
])
def test_format_noise_is_not_required(markup):
    doc = document(markup)
    facts = required_fact_anchors(doc)
    assert [fact.text for fact in facts] == ["98.96%"]


@pytest.mark.parametrize("text", [
    "Compound II is active.", "The probability density function PDF was evaluated.",
    "C0 and C1 are capacitors.", "F-tests were used.", "A mixed-type inhibitor was studied by weight-loss.",
    "CLSA differs from VLSA; the medium is HCl and the metric is TWSD.",
])
def test_real_scientific_terms_are_not_blanket_removed(text):
    doc = document(text)
    assert required_fact_anchors(doc)


@pytest.mark.parametrize("link", [
    '[98.96%](#ftn1)', '<a href="#fn1">98.96%</a>',
])
def test_scientific_visible_text_in_footnote_links_is_preserved(link):
    facts = required_fact_anchors(document(link))
    assert [a.text for a in facts] == ["98.96%"]


def test_scientific_pdf_links_and_ambiguous_roman_labels_remain_protected():
    text = "The probability density function [PDF](https://example.com/definition.pdf) was evaluated.\nII. novel compound"
    facts = required_fact_anchors(document(text))
    assert any(a.text == 'PDF' for a in facts)
    assert any(a.text == 'II' for a in facts)


def test_image_alternative_text_preserves_scientific_measurements():
    doc = document('<img src="https://example.com/2019/plot.png" alt="Offset 0.13 V for VLSA">')
    assert {a.text for a in required_fact_anchors(doc)} == {'0.13 V', 'VLSA'}


def test_payload_contains_extracted_facts():
    text = '## CONCLUSION\n[PDF](https://x.example/file.pdf)\nThe rate is 30 FPS; N=50.'
    doc = document(text)
    payload = build_compression_user_payload({"step_title": "step"}, doc, {"content": "new content"})
    assert payload['required_facts'] == [a.text for a in required_fact_anchors(doc)]
    assert payload['required_facts'] == ['30 FPS', '50']


def test_untrusted_exponent_does_not_crash_if_decimal_traps_disabled():
    text = '1e' + '9' * 100 + ' V'
    with localcontext() as context:
        context.traps[InvalidOperation] = False
        assert extract_fact_anchors(text)


def test_dedup_equivalent_facts_and_fallback_for_invalid_passages():
    doc={'original_content': 'The rate was 30 FPS.', 'key_passages': ['30 FPS', '30fps', None, {}]}
    assert len(required_fact_anchors(doc)) == 1
    doc['key_passages'] = 'malformed'
    assert [a.text for a in required_fact_anchors(doc)] == ['30 FPS']


def test_descriptions_keep_word_boundaries_and_reference_original_context():
    doc = document('Low-voltage SRAM has high-reliability-applications at 0.4 V; HYSA-QZ uses F-tests.')
    anchors = required_fact_anchors(doc)
    assert {a.text for a in anchors if a.kind == 'description'} == {'Low-voltage', 'high-reliability-applications'}
    assert {'HYSA-QZ', 'F-tests'} <= {a.text for a in anchors if a.kind == 'identifier'}
    payload = build_compression_user_payload({}, doc, {'content': 'article'})
    assert 'Low-voltage' not in payload['required_facts']
    assert payload['required_descriptions'][0]['key_passage_indexes'] == [0]


def test_continuous_list_ordinals_are_not_measurements():
    doc = document('1. Offset is 0.13 V.\n2. Yield is 98.96%.\n3. Process is 65 nm.')
    assert {a.text for a in required_fact_anchors(doc)} == {'0.13 V', '98.96%', '65 nm'}
    assert any(a.kind == 'number' and a.number == '2' for a in required_fact_anchors(document('2. Samples were tested.')))
    assert any(a.kind == 'number' and a.number == '1' for a in required_fact_anchors(document('1. MHz\n2. GHz')))


@pytest.mark.parametrize('passages', [
    ['Low-voltage SRAM operates at 0.4 V.', None, 'A high-reliability-application uses low-voltage SRAM.'],
    [],
])
def test_payload_reuses_anchor_extraction_and_cleans_description_passages_once(monkeypatch, passages):
    from unittest.mock import Mock
    from openjiuwen_deepsearch.algorithm.research_collector import enrichment_quality as quality
    from openjiuwen_deepsearch.algorithm.research_collector import webpage_enrichment as enrichment

    doc = {'original_content': 'Low-voltage SRAM operates at 0.4 V.', 'key_passages': passages}
    expected = quality.required_description_contexts(doc)
    extraction = Mock(wraps=quality.required_fact_anchors)
    cleaning = Mock(wraps=quality.clean_fact_text)
    monkeypatch.setattr(quality, 'required_fact_anchors', extraction)
    monkeypatch.setattr(enrichment, 'required_fact_anchors', extraction)
    monkeypatch.setattr(quality, 'clean_fact_text', cleaning)
    payload = enrichment.build_compression_user_payload({}, doc, {'content': 'article'})
    assert extraction.call_count == 1
    assert payload['required_descriptions'] == expected
    if passages:
        assert cleaning.call_count == 4  # 两个有效片段各提取一次，再各清洗一次关联所有描述。
        assert payload['required_descriptions'][0]['key_passage_indexes'] == [0, 2]
