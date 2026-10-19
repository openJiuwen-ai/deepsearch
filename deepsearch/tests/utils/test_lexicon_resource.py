"""Binary resource integrity, reproducibility and lazy-loader compatibility."""

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from openjiuwen_deepsearch.utils.common_utils import lexicon_resource as codec
from openjiuwen_deepsearch.utils.common_utils import moderation_mask


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
    (source / "sample.txt").write_text("example\nexample\n测试\nx\n# ignored\n", encoding="utf-8")
    builder = Path(__file__).resolve().parents[2] / "tools/build_moderation_lexicon.py"
    outputs = [tmp_path / "one", tmp_path / "two"]
    for destination in outputs:
        subprocess.run(
            [sys.executable, str(builder), "--source-dir", str(source), "--output-dir", str(destination)],
            check=True, capture_output=True, text=True,
        )
    assert (outputs[0] / "lexicon.bin").read_bytes() == (outputs[1] / "lexicon.bin").read_bytes()
    manifest = json.loads((outputs[0] / "manifest.json").read_text())
    assert manifest["term_count"] == 2
    assert set(p.name for p in outputs[0].iterdir()) == {"lexicon.bin", "manifest.json"}


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
    assert len(terms) == manifest["term_count"] == 50837
    assert hashlib.sha256("\n".join(sorted(terms)).encode("utf-8")).hexdigest() == manifest["terms_sha256"]
    assert not any(p.name.endswith(".txt") for p in root.iterdir())
