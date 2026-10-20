"""Binary resource integrity, reproducibility and lazy-loader compatibility."""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from openjiuwen_deepsearch.utils.common_utils import lexicon_resource as codec
from openjiuwen_deepsearch.utils.common_utils import moderation_mask


@pytest.fixture
def builder():
    path = Path(__file__).resolve().parents[2] / "tools/build_moderation_lexicon.py"
    spec = importlib.util.spec_from_file_location("moderation_lexicon_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_roundtrip_and_deterministic_bytes():
    terms = frozenset({"example", "sample.example", "测试"})
    first = codec.encode_lexicon(terms)
    second = codec.encode_lexicon(frozenset(reversed(sorted(terms))))
    assert first == second
    assert first.startswith(codec.MAGIC)
    assert codec.decode_lexicon(first, hashlib.sha256(first).hexdigest()) == terms


def test_corrupted_bytes_rejected():
    data = codec.encode_lexicon(frozenset({"example"}))
    with pytest.raises(ValueError, match="checksum"):
        codec.decode_lexicon(data + b"broken", hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize("payload", [
    b"unknown-format", codec.MAGIC + b"not-gzip",
    codec.MAGIC + gzip.compress(b"zebra\napple\n"),
    codec.MAGIC + gzip.compress(b"example\nexample\n"),
    codec.MAGIC + gzip.compress(b"\xff\n"),
])
def test_bad_format_and_entries_rejected(payload):
    with pytest.raises((ValueError, OSError, EOFError)):
        codec.decode_lexicon(payload, hashlib.sha256(payload).hexdigest())


def test_decompression_is_bounded(monkeypatch):
    data = codec.encode_lexicon(frozenset({"example"}))
    monkeypatch.setattr(codec, "MAX_PAYLOAD_BYTES", 4)
    with pytest.raises(ValueError, match="limit"):
        codec.decode_lexicon(data, hashlib.sha256(data).hexdigest())


def test_builder_reproduces_resources_without_plaintext_copy(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "sample.txt").write_text("example\nexample\n测试\nx\n# ignored\n24\n509\n２４\n", encoding="utf-8")
    builder = Path(__file__).resolve().parents[2] / "tools/build_moderation_lexicon.py"
    outputs = [tmp_path / "one", tmp_path / "two"]
    revision = "A" * 40
    for destination in outputs:
        result = subprocess.run(
            [sys.executable, str(builder), "--source-dir", str(source), "--output-dir", str(destination),
             "--source-revision", revision],
            check=True, capture_output=True, text=True, cwd=tmp_path,
        )
        logged_manifest = json.loads(result.stderr)
        assert logged_manifest["source_revision"] == revision.lower()
        assert logged_manifest["term_count"] == 2
    assert (outputs[0] / "lexicon.bin").read_bytes() == (outputs[1] / "lexicon.bin").read_bytes()
    assert (outputs[0] / "manifest.json").read_bytes() == (outputs[1] / "manifest.json").read_bytes()
    manifest = json.loads((outputs[0] / "manifest.json").read_text())
    assert manifest["source_revision"] == revision.lower()
    assert manifest["term_count"] == 2
    assert manifest["source_term_count"] == 5
    assert manifest["excluded_decimal_count"] == 3
    assert codec.decode_lexicon((outputs[0] / "lexicon.bin").read_bytes(), manifest["sha256"]) == frozenset({"example", "测试"})
    assert set(p.name for p in outputs[0].iterdir()) == {"lexicon.bin", "manifest.json"}


def test_source_revision_only_changes_manifest_metadata(tmp_path, builder):
    source = tmp_path / "input"
    source.mkdir()
    (source / "sample.txt").write_text("example\n测试\n", encoding="utf-8")
    original = builder.build(source, tmp_path / "without")
    traced = builder.build(source, tmp_path / "with", source_revision="a" * 40)
    assert "source_revision" not in original
    assert traced.pop("source_revision") == "a" * 40
    assert original == traced
    assert (tmp_path / "without/lexicon.bin").read_bytes() == (tmp_path / "with/lexicon.bin").read_bytes()


@pytest.mark.parametrize("revision", ["", "main", "a" * 7, "g" * 40, "a" * 41, 123])
def test_builder_rejects_invalid_source_revision_before_writing(tmp_path, builder, revision):
    destination = tmp_path / "output"
    with pytest.raises(ValueError, match="Source revision"):
        builder.build(tmp_path, destination, source_revision=revision)
    assert not destination.exists()


def test_builder_filters_reviewed_words_and_temporal_fragments(tmp_path, builder):
    source = tmp_path / "input"
    source.mkdir()
    reviewed = {"url", "URL", "www", "WWW", "wikipedia", "Wikipedia", "is", "IS", "this", "BC", "Bc", "没有", "一个", "时间"}
    temporal = {"20年", "70年", "8月17", "12月26日", "１９７８年", "20分钟", "20周年", "20年前"}
    kept = {"研究对象", "2020年保留描述", "sample.example"}
    terms = reviewed | temporal | kept | {"24"}
    (source / "sample.txt").write_text("\n".join(sorted(terms)), encoding="utf-8")
    destination = tmp_path / "output"
    manifest = builder.build(source, destination)
    assert codec.decode_lexicon((destination / "lexicon.bin").read_bytes(), manifest["sha256"]) == kept
    assert manifest["excluded_decimal_count"] == 1
    assert manifest["excluded_temporal_count"] == len(temporal)
    assert manifest["excluded_reviewed_count"] == len(reviewed)
    assert manifest["excluded_count"] == len(terms) - len(kept)
    assert manifest["exclusion_policy_sha256"] == hashlib.sha256(builder.DEFAULT_EXCLUSIONS.read_bytes()).hexdigest()


def test_builder_supports_reviewed_context_digest_without_rewriting_terms(tmp_path, builder):
    source = tmp_path / "input"
    source.mkdir()
    excluded = "20年上下文样例"
    (source / "sample.txt").write_text(excluded + "\n2020年保留描述\n", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": "test-v1", "terms": {}, "sha256": {
        "reviewed_context": [hashlib.sha256(excluded.encode("utf-8")).hexdigest()],
    }}), encoding="utf-8")
    destination = tmp_path / "output"
    manifest = builder.build(source, destination, policy)
    assert codec.decode_lexicon((destination / "lexicon.bin").read_bytes(), manifest["sha256"]) == {"2020年保留描述"}
    assert manifest["exclusion_counts_by_reason"] == {"reviewed_context": 1}


def test_neutral_entity_exclusions_are_exact_not_substring_rules(tmp_path, builder):
    source = tmp_path / "input"
    source.mkdir()
    raw = "美国\n美国形象\n日本\n日本研究\n"
    (source / "sample.txt").write_text(raw, encoding="utf-8")
    destination = tmp_path / "output"
    manifest = builder.build(source, destination)
    terms = codec.decode_lexicon((destination / "lexicon.bin").read_bytes(), manifest["sha256"])
    assert terms == {"美国形象", "日本研究"}
    assert manifest["exclusion_counts_by_reason"] == {"neutral_entities": 2}
    assert (source / "sample.txt").read_text(encoding="utf-8") == raw


def test_reviewed_body_exclusions_include_case_variants_but_keep_longer_terms(tmp_path, builder):
    source = tmp_path / "input"
    source.mkdir()
    excluded = {"MAKING", "making", "SYSTEM", "System", "system", "TEST", "TEsT", "test",
                "社会", "国家", "文化", "代表", "未来", "其他", "希望", "安全", "近年", "管理", "支持",
                "第一次", "第四", "学历", "代开", "不是国", "是中国", "手机",
                "WAY", "way", "paper", "Undergo", "SIMPLE", "simple", "ISIS", "isis", "muslim"}
    kept = {"system design", "国家研究", "学历教育", "代开服务", "保留测试长词", "paper example", "手机测试长词"}
    raw = "\n".join(sorted(excluded | kept))
    (source / "sample.txt").write_text(raw, encoding="utf-8")
    destination = tmp_path / "output"
    manifest = builder.build(source, destination)
    terms = codec.decode_lexicon((destination / "lexicon.bin").read_bytes(), manifest["sha256"])
    assert terms == kept
    assert manifest["excluded_reviewed_count"] == len(excluded)
    assert manifest["exclusion_counts_by_reason"] == {
        "english_ordinary_words": 14, "reviewed_ordinary_words": 14, "reviewed_cross_word_collisions": 4,
        "reviewed_english_neutral_labels": 3,
    }
    assert (source / "sample.txt").read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("policy", [
    [], {"version": 1, "terms": {}, "sha256": {}},
    {"version": "test", "terms": [], "sha256": {}},
    {"version": "test", "terms": {"review": [None]}, "sha256": {}},
    {"version": "test", "terms": {"review": ["is", "IS"]}, "sha256": {}},
    {"version": "test", "terms": {}, "sha256": {"review": ["not-a-digest"]}},
])
def test_builder_rejects_invalid_exclusions_before_writing(tmp_path, builder, policy):
    source = tmp_path / "input"
    source.mkdir()
    (source / "sample.txt").write_text("example\n", encoding="utf-8")
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    destination = tmp_path / "output"
    with pytest.raises(ValueError):
        builder.build(source, destination, path)
    assert not destination.exists()


@pytest.mark.parametrize("failure", ["missing", "checksum", "version", "count", "count_type"])
def test_bundled_failure_returns_empty_without_partial_terms(tmp_path, monkeypatch, failure):
    terms = frozenset({"example"})
    data = codec.encode_lexicon(terms)
    manifest = {"format": codec.FORMAT, "sha256": hashlib.sha256(data).hexdigest(), "term_count": 1}
    if failure == "checksum":
        manifest["sha256"] = "0" * 64
    elif failure == "version":
        manifest["format"] = "unknown"
    elif failure == "count":
        manifest["term_count"] = 2
    elif failure == "count_type":
        manifest["term_count"] = True
    if failure != "missing":
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
        (tmp_path / "lexicon.bin").write_bytes(data)
    monkeypatch.setattr(moderation_mask.resources, "files", lambda _: tmp_path)
    moderation_mask._load_lexicon_bundled.cache_clear()
    try:
        assert moderation_mask._load_lexicon_bundled() == frozenset()
    finally:
        moderation_mask._load_lexicon_bundled.cache_clear()


def test_packaged_manifest_matches_loaded_terms():
    root = moderation_mask.resources.files(moderation_mask._LEXICON_DIR_RESOURCE)
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    terms = moderation_mask._load_lexicon_bundled()
    assert len(terms) == manifest["term_count"] == 49877
    assert manifest["source_term_count"] == 50837
    assert manifest["source_revision"] == "d967c30b053fa40b06c5a0dddf0be493f2dfae46"
    assert manifest["excluded_decimal_count"] == 869
    assert manifest["excluded_temporal_count"] == 20
    assert manifest["excluded_reviewed_count"] == 71
    assert manifest["exclusion_counts_by_reason"]["neutral_entities"] == 12
    assert manifest["exclusion_counts_by_reason"]["english_ordinary_words"] == 17
    assert manifest["exclusion_counts_by_reason"]["reviewed_ordinary_words"] == 14
    assert manifest["exclusion_counts_by_reason"]["reviewed_english_neutral_labels"] == 3
    assert manifest["exclusion_counts_by_reason"]["reviewed_cross_word_collisions"] == 4
    assert manifest["excluded_count"] == sum(manifest["exclusion_counts_by_reason"].values()) == 960
    assert not {"isis", "muslim", "way", "paper", "undergo", "simple", "手机"} & {term.casefold() for term in terms}
    assert not any(term.isdecimal() for term in terms)
    assert not {"美国", "日本", "俄国", "俄罗斯", "朝鲜", "台湾", "香港", "北京", "华盛顿", "亚洲", "非洲", "联合国"} & terms
    assert hashlib.sha256("\n".join(sorted(terms)).encode("utf-8")).hexdigest() == manifest["terms_sha256"]
    assert not any(p.name.endswith(".txt") for p in root.iterdir())
