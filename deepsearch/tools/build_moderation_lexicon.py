"""Build packaged lexicon resources from an independently obtained source tree."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from openjiuwen_deepsearch.utils.common_utils.lexicon_resource import (  # noqa: E402
    FORMAT,
    decode_lexicon,
    encode_lexicon,
)


def build(source: Path, destination: Path) -> dict:
    files = sorted(source.glob("*.txt"))
    if not files:
        raise ValueError("Source directory contains no text lexicons")
    source_terms = frozenset(
        term
        for path in files
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if (term := line.strip()) and len(term) >= 2 and not term.startswith("#")
    )
    terms = frozenset(term for term in source_terms if not term.isdecimal())
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
        "excluded_decimal_count": len(source_terms) - len(terms),
        "filter_policy": "exclude_unicode_decimal_v1",
        "source_url": "https://github.com/Konsheng/Sensitive-lexicon",
        "source_license": "MIT",
        "terms_sha256": hashlib.sha256("\n".join(sorted(terms)).encode("utf-8")).hexdigest(),
    }
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
        "--output-dir", type=Path,
        default=PACKAGE_ROOT / "openjiuwen_deepsearch/utils/common_utils/data/sensitive_lexicon",
    )
    args = parser.parse_args()
    print(json.dumps(build(args.source_dir, args.output_dir), ensure_ascii=True))


if __name__ == "__main__":
    main()
