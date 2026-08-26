"""Orchestration layer: public ``parse_file`` / ``parse_files`` API."""

import asyncio
from collections.abc import Iterable
from pathlib import Path

import aiofiles
import charset_normalizer

from ..constants import detect_language
from ..languages import BaseLanguageParser, get_default_registry, register_builtins
from ..models.structural import FileNode


async def parse_file(
    path: Path | str,
    *,
    language: str | None = None,
) -> FileNode:
    """Parse a single source file and return a :class:`FileNode`.

    :param path: Path to the source file.
    :param language: Override language detection.  Auto-detected from the
        file extension when ``None``.
    :raises ValueError: If the language cannot be determined or is unsupported.
    """
    path = Path(path)
    register_builtins()
    registry = get_default_registry()

    if language is None:
        language = detect_language(path.name)
    if language is None:
        raise ValueError(f"Cannot determine language for file {path.name!r}")

    parser: BaseLanguageParser | None = registry.get(language)
    if parser is None:
        raise ValueError(f"No parser registered for language {language!r}")

    async with aiofiles.open(path, "rb") as f:
        source = await f.read()

    # Handle files of different encoding
    best_match = charset_normalizer.from_bytes(source).best()
    if best_match is None:
        source = source.decode(encoding="utf-8", errors="replace").encode(encoding="utf-8")
    elif best_match.encoding not in ("utf_8", "ascii"):
        source = str(best_match).encode(encoding="utf-8")

    return await parser.parse(path, source)


async def parse_files(
    paths: Iterable[Path | str],
    *,
    max_concurrency: int = 8,
) -> list[FileNode]:
    """Parse multiple files concurrently with bounded parallelism.

    :param paths: Iterable of file paths to parse.
    :param max_concurrency: Maximum number of files parsed in parallel.
    :return: List of :class:`FileNode` in the same order as *paths*.
    """
    path_list = [Path(p) for p in paths]
    sem = asyncio.Semaphore(max_concurrency)

    async def _guarded(p: Path) -> FileNode:
        async with sem:
            return await parse_file(p)

    return list(await asyncio.gather(*(_guarded(p) for p in path_list)))
