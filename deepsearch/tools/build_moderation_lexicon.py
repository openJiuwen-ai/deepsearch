"""Build packaged lexicon resources from an independently obtained source tree."""

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import re

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_CODEC_SPEC = importlib.util.spec_from_file_location(
    "moderation_lexicon_codec",
    PACKAGE_ROOT / "openjiuwen_deepsearch/utils/common_utils/lexicon_resource.py",
)
_CODEC = importlib.util.module_from_spec(_CODEC_SPEC)
_CODEC_SPEC.loader.exec_module(_CODEC)
FORMAT = _CODEC.FORMAT
decode_lexicon = _CODEC.decode_lexicon
encode_lexicon = _CODEC.encode_lexicon
logger = logging.getLogger(__name__)

DEFAULT_EXCLUSIONS = Path(__file__).with_name("moderation_lexicon_exclusions.json")
_TEMPORAL_FRAGMENT = re.compile(r"(?:\d+(?:世纪|年代|周年|小时|分钟|年|月|日|号|天|周|秒))+\d*[前后]?\Z")


def load_exclusions(path: Path) -> tuple[dict, dict[str, str], dict[str, str], str]:
    data = path.read_bytes()
    policy = json.loads(data)
    if not isinstance(policy, dict) or not isinstance(policy.get("version"), str):
        raise ValueError("Invalid lexicon exclusion policy")
    indexes = []
    for kind in ("terms", "sha256"):
        groups = policy.get(kind)
        if not isinstance(groups, dict):
            raise ValueError("Invalid exclusion groups")
        index = {}
        for reason, entries in groups.items():
            if not isinstance(reason, str) or not reason or not isinstance(entries, list):
                raise ValueError("Invalid exclusion group")
            for entry in entries:
                if not isinstance(entry, str) or len(entry) < 2 or entry != entry.strip():
                    raise ValueError("Invalid exclusion entry")
                if kind == "sha256" and not re.fullmatch(r"[0-9a-f]{64}", entry):
                    raise ValueError("Invalid exclusion digest")
                key = entry.casefold() if kind == "terms" else entry
                if key in index:
                    raise ValueError("Duplicate exclusion entry")
                index[key] = reason
        indexes.append(index)
    return policy, indexes[0], indexes[1], hashlib.sha256(data).hexdigest()


def build(
    source: Path, destination: Path, exclusions: Path = DEFAULT_EXCLUSIONS,
    *, source_revision: str | None = None,
) -> dict:
    if source_revision is not None and (
        not isinstance(source_revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", source_revision)
    ):
        raise ValueError("Source revision must be a full 40-character Git commit SHA")
    files = sorted(source.glob("*.txt"))
    if not files:
        raise ValueError("Source directory contains no text lexicons")
    source_terms = set()
    for path in files:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            term = line.strip()
            if len(term) >= 2 and not term.startswith("#"):
                source_terms.add(term)
    policy, reviewed_terms, reviewed_hashes, policy_hash = load_exclusions(exclusions)
    counts: Counter[str] = Counter()
    kept = set()
    for term in source_terms:
        if term.isdecimal():
            reason = "decimal"
        elif _TEMPORAL_FRAGMENT.fullmatch(term):
            reason = "temporal_fragment"
        else:
            reason = reviewed_terms.get(term.casefold()) or reviewed_hashes.get(
                hashlib.sha256(term.encode("utf-8")).hexdigest()
            )
        if reason:
            counts[reason] += 1
        else:
            kept.add(term)
    terms = frozenset(kept)
    data = encode_lexicon(terms)
    checksum = hashlib.sha256(data).hexdigest()
    if decode_lexicon(data, checksum) != terms:
        raise ValueError("Lexicon roundtrip verification failed")
    manifest = {
        "format": FORMAT,
        "sha256": checksum,
        "term_count": len(terms),
        "binary_bytes": len(data),
        "source_file_count": len(files),
        "source_term_count": len(source_terms),
        "excluded_decimal_count": counts["decimal"],
        "excluded_temporal_count": counts["temporal_fragment"],
        "excluded_reviewed_count": sum(counts.values()) - counts["decimal"] - counts["temporal_fragment"],
        "excluded_count": sum(counts.values()),
        "exclusion_counts_by_reason": dict(sorted(counts.items())),
        "filter_policy": "exclude_decimal_temporal_reviewed_v2",
        "exclusion_policy_version": policy["version"],
        "exclusion_policy_sha256": policy_hash,
        "source_url": "https://github.com/Konsheng/Sensitive-lexicon",
        "source_license": "MIT",
        "terms_sha256": hashlib.sha256("\n".join(sorted(terms)).encode("utf-8")).hexdigest(),
    }
    if source_revision is not None:
        manifest["source_revision"] = source_revision.lower()
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "lexicon.bin").write_bytes(data)
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument(
        "--source-revision",
        help="Full upstream Git commit SHA matching the source files (verified by the builder operator)",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=PACKAGE_ROOT / "openjiuwen_deepsearch/utils/common_utils/data/sensitive_lexicon",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    manifest = build(args.source_dir, args.output_dir, source_revision=args.source_revision)
    logger.info("%s", json.dumps(manifest, ensure_ascii=True))


if __name__ == "__main__":
    main()
