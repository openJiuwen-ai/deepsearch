"""Portable, versioned lexicon data; never deserialize executable objects."""

import gzip
import hashlib
import io

MAGIC = b"DSLEX\x01\n"
FORMAT = "dslex-v1-gzip-utf8-lines"
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024


def encode_lexicon(terms: frozenset[str]) -> bytes:
    """Build deterministic bytes independent of filename, clock and OS header."""
    normalized = sorted(terms)
    if not normalized or any(
        not isinstance(term, str) or len(term) < 2 or term != term.strip()
        or term.startswith("#") or "\n" in term or "\r" in term
        for term in normalized
    ):
        raise ValueError("Invalid lexicon entries")
    payload = ("\n".join(normalized) + "\n").encode("utf-8")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("Lexicon exceeds payload limit")
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as writer:
        writer.write(payload)
    return MAGIC + output.getvalue()


def decode_lexicon(data: bytes, expected_sha256: str) -> frozenset[str]:
    """Check format, integrity, bounded decompression and normalized entries."""
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Lexicon checksum mismatch")
    if not data.startswith(MAGIC):
        raise ValueError("Unsupported lexicon format")
    with gzip.GzipFile(fileobj=io.BytesIO(data[len(MAGIC):]), mode="rb") as reader:
        payload = reader.read(MAX_PAYLOAD_BYTES + 1)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("Lexicon exceeds payload limit")
    lines = payload.decode("utf-8").splitlines()
    if not lines or lines != sorted(set(lines)) or any(
        len(term) < 2 or term != term.strip() or term.startswith("#") for term in lines
    ):
        raise ValueError("Invalid lexicon entries")
    return frozenset(lines)
