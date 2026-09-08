# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Markdown table caption normalization helpers."""

from __future__ import annotations

import logging
import re

from openjiuwen_deepsearch.algorithm.report.report_utils import sanitize_citation_markers
from openjiuwen_deepsearch.common.common_constants import ENGLISH


logger = logging.getLogger(__name__)

_MARKDOWN_TABLE_DELIMITER_RE = re.compile(r":?-{1,}:?")
_CAPTION_TRIM_RE = re.compile(r"^[*_`>\s]+|[*_`\s]+$")
_CHINESE_CAPTION_PREFIX_RE = re.compile(r"^(?:表格标题|标题)\s*[:：]")
_ENGLISH_CAPTION_PREFIX_RE = re.compile(r"^(?:table\s+title|title)\s*:", flags=re.IGNORECASE)
_TABLE_CAPTION_LINE_RE = re.compile(
    r"^(?:表|Table)\s*"
    r"(?:[\d一二三四五六七八九十]+(?:[-－—.][\d一二三四五六七八九十]+)*|格)?"
    r"\s*[:：\s]",
    flags=re.IGNORECASE,
)
_CAPTION_PREFIX_RE = re.compile(
    r"^(?:表格标题|标题|表格|表|table\s+title|title|Table)\s*[:：]?\s*",
    flags=re.IGNORECASE,
)
_LIST_ITEM_RE = re.compile(r"^(\d+[.)]\s+|[-*+]\s+)")
_HTML_BLOCK_RE = re.compile(
    r"^</?(?:div|table|tr|td|th|ul|ol|li|img|figure|figcaption|pre|code|script|style|svg)\b",
    flags=re.IGNORECASE,
)
_IMAGE_LINE_RE = re.compile(r"^!\[[^\]]*]\([^)]+\)\s*$")
_CITATION_MARKER_RE = re.compile(r"\[(?:checked_)?citation:[^\]]+\]")
_SENTENCE_END_RE = re.compile(r"[。.!！？；;：:]\s*$")
_CHECKED_CITATION_RE = re.compile(r"\[checked_citation:[^\]]+\]")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_MARK_RE = re.compile(r"[*_`>#]")
_WHITESPACE_RE = re.compile(r"\s+")
_INTRO_PREFIX_RE = re.compile(
    r"^(?:下表|以下表格|本表|该表|表格)"
    r"(?:系统)?(?:总结|汇总|对比|展示|梳理|概括|列示|呈现|归纳|说明|反映)?"
    r"(?:了|如下)?"
    r"|^(?:the\s+)?(?:following\s+)?table\s+"
    r"(?:summarizes|compares|shows|lists|presents|outlines)?",
    flags=re.IGNORECASE,
)
_TABLE_CONTEXT_KEYWORD_RE = re.compile(r"(下表|以下表格|本表|该表|表格|table)", flags=re.IGNORECASE)
_CHINESE_CAPTION_PARTS_RE = re.compile(
    r"^(?P<label>表\s*[\d一二三四五六七八九十]+(?:[-－—.][\d一二三四五六七八九十]+)*)"
    r"\s*[:：\s]\s*(?P<title>.+?)\s*$"
)
_ENGLISH_CAPTION_PARTS_RE = re.compile(
    r"^(?P<label>Table\s+[\w]+(?:[-－—.][\w]+)*)\s*[:：\s]\s*(?P<title>.+?)\s*$",
    flags=re.IGNORECASE,
)
_COLON_END_RE = re.compile(r"[:：]\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_MATH_BLOCK_RE = re.compile(r"^\s*\$\$\s*$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_LEADING_NUMBER_RE = re.compile(
    r"^(?:\d+(?:[.\-\s]\d+)*|第?[一二三四五六七八九十\d]+[、章])\s*"
)

_CHINESE_PREVIOUS_REFERENCE_PATTERNS = (
    (re.compile(r"如下表"), r"如{table_label}"),
    (re.compile(r"详见下表"), r"详见{table_label}"),
    (re.compile(r"见下表"), r"见{table_label}"),
    (re.compile(r"以下表格|下表|本表|该表|此表|表格"), r"{table_label}"),
)
_ENGLISH_PREVIOUS_REFERENCE_PATTERNS = (
    (re.compile(r"\bthe\s+following\s+table\b", flags=re.IGNORECASE), r"{table_label}"),
    (re.compile(r"\bfollowing\s+table\b", flags=re.IGNORECASE), r"{table_label}"),
    (re.compile(r"\bthe\s+table\s+below\b", flags=re.IGNORECASE), r"{table_label}"),
    (re.compile(r"\btable\s+below\b", flags=re.IGNORECASE), r"{table_label}"),
)
_ENGLISH_PREVIOUS_WEAK_RE = re.compile(r"\b(?:as follows|below)\b", flags=re.IGNORECASE)
_CHINESE_PREVIOUS_WEAK_RE = re.compile(r"(?:具体|对比|汇总|整理|列示|呈现|梳理|归纳|概括)?如下")
_CHINESE_NEXT_REFERENCE_PATTERNS = (
    (re.compile(r"上表"), r"{table_label}"),
    (re.compile(r"如表所示"), r"如{table_label}所示"),
    (re.compile(r"从表中"), r"从{table_label}中"),
    (re.compile(r"由表中"), r"由{table_label}中"),
    (re.compile(r"本表|该表|此表"), r"{table_label}"),
)
_ENGLISH_NEXT_REFERENCE_PATTERNS = (
    (
        re.compile(r"\bas\s+shown\s+in\s+(?:the\s+)?table\s+above\b", flags=re.IGNORECASE),
        r"as shown in {table_label}",
    ),
    (
        re.compile(r"\bfrom\s+(?:the\s+)?table\s+above\b", flags=re.IGNORECASE),
        r"from {table_label}",
    ),
    (re.compile(r"\bthe\s+table\s+above\b", flags=re.IGNORECASE), r"{table_label}"),
    (re.compile(r"\btable\s+above\b", flags=re.IGNORECASE), r"{table_label}"),
)


def _strip_leading_number(text: str) -> str:
    return _LEADING_NUMBER_RE.sub("", text)


def _normalize_section_idx(section_idx: str | int | None) -> str:
    if section_idx is None:
        return ""
    return _WHITESPACE_RE.sub("", str(section_idx))


def _is_markdown_table_row(line: str) -> bool:
    """Return whether a line is a pipe-style Markdown table row."""
    return line.lstrip().startswith("|")


def _is_markdown_table_delimiter(line: str) -> bool:
    """Return whether a line is a Markdown table delimiter row."""
    if not _is_markdown_table_row(line):
        return False
    cells = [
        cell.strip().replace(" ", "")
        for cell in line.strip().strip("|").split("|")
    ]
    if len(cells) < 2:
        return False
    return all(bool(_MARKDOWN_TABLE_DELIMITER_RE.fullmatch(cell)) for cell in cells)


def _is_markdown_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and _is_markdown_table_row(lines[index])
        and _is_markdown_table_delimiter(lines[index + 1])
    )


def _is_table_caption_line(line: str) -> bool:
    """Return whether a line already looks like a table caption."""
    text = line.strip()
    text = _CAPTION_TRIM_RE.sub("", text)
    if not text:
        return False
    if _CHINESE_CAPTION_PREFIX_RE.match(text):
        return True
    if _ENGLISH_CAPTION_PREFIX_RE.match(text):
        return True
    return bool(_TABLE_CAPTION_LINE_RE.match(text))


def _caption_parts_from_caption_line(line: str) -> tuple[str, str] | None:
    """Extract a caption label/title pair from a line that looks like a table caption."""
    if not _is_table_caption_line(line):
        return None
    caption_parts = _extract_table_caption_parts(line)
    if caption_parts:
        return caption_parts
    title = clean_caption_text(line)
    title = _CAPTION_PREFIX_RE.sub("", title)
    title = title.strip(" ：:，,。.;；")
    return "", title


def _is_plain_table_caption_candidate(line: str) -> bool:
    """Return whether a plain line is likely an LLM-generated table caption."""
    stripped = line.strip()
    if not stripped:
        return False
    if (
        stripped.startswith(("#", "|", ">", "```", "~~~", "$$", "!["))
        or _LIST_ITEM_RE.match(stripped)
        or _HTML_BLOCK_RE.match(stripped)
    ):
        return False
    if _CITATION_MARKER_RE.search(stripped):
        return False

    text = normalize_caption_markup(stripped)
    if _SENTENCE_END_RE.search(text):
        return False
    text = clean_caption_text(text)
    if not text or len(text) < 4 or len(text) > 80:
        return False
    return True


def _caption_parts_from_plain_caption_line(line: str) -> tuple[str, str] | None:
    """Extract a pure-text caption line without accepting explicit table labels."""
    if _is_table_caption_line(line):
        return None
    if _is_plain_table_caption_candidate(line):
        return "", clean_caption_text(line)
    return None


def normalize_caption_markup(text: str) -> str:
    """Remove citation/link/HTML/Markdown markup while preserving caption words."""
    text = _CHECKED_CITATION_RE.sub("", text)
    text = sanitize_citation_markers(text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _MARKDOWN_MARK_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def clean_caption_text(text: str, max_len: int = 80) -> str:
    """Clean a candidate caption while preserving the semantic topic."""
    text = normalize_caption_markup(text)
    text = text.strip(" ：:，,。.;；")
    text = _INTRO_PREFIX_RE.sub("", text).strip()
    text = text.strip(" ：:，,。.;；")
    if len(text) > max_len:
        text = text[:max_len].rstrip(" ：:，,。.;；")
    return text


def _extract_table_headers(header_line: str) -> list[str]:
    headers = [
        clean_caption_text(cell, max_len=20)
        for cell in header_line.strip().strip("|").split("|")
    ]
    return [header for header in headers if header]


def _caption_from_previous_context_lines(context_lines: list[str]) -> str:
    """Use explicit table-introduction context as the preferred caption source."""
    for line in context_lines:
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("#") or _is_markdown_table_row(raw):
            continue
        if _is_table_caption_line(raw):
            continue
        cleaned = clean_caption_text(raw)
        if not cleaned:
            return ""
        if _TABLE_CONTEXT_KEYWORD_RE.search(raw):
            return cleaned
        return ""
    return ""


def _build_table_caption_title(
    table_lines: list[str],
    context_lines: list[str],
    section_title: str,
    language: str,
) -> str:
    context_title = _caption_from_previous_context_lines(context_lines)
    if context_title:
        return context_title

    headers = _extract_table_headers(table_lines[0]) if table_lines else []
    non_source_headers = [
        header for header in headers
        if header not in {"来源", "数据来源", "备注", "Source", "Sources", "Notes"}
    ]

    section_title = clean_caption_text(_strip_leading_number(section_title), max_len=48)
    if language == ENGLISH:
        header_phrase = ", ".join(non_source_headers[:3])
        if section_title and header_phrase:
            return f"{section_title} ({header_phrase})"
        if section_title:
            return f"{section_title} key data"
        if header_phrase:
            return f"{header_phrase} comparison"
        return "Key data summary"

    header_phrase = "、".join(non_source_headers[:3])
    if section_title and header_phrase:
        return f"{section_title}（{header_phrase}）"
    if section_title:
        return f"{section_title}关键数据"
    if header_phrase:
        return f"{header_phrase}对比"
    return "核心数据汇总"


def _format_table_caption_with_label(language: str, table_label: str, title: str) -> str:
    if language == ENGLISH:
        caption_text = f"{table_label}: {title}"
    else:
        caption_text = f"{table_label}：{title}"
    return f'<div style="text-align: center;">\n\n**{caption_text}**\n\n</div>'


def format_table_label(language: str, section_idx_text: str, table_idx: int) -> str:
    table_no = f"{section_idx_text}-{table_idx}" if section_idx_text else str(table_idx)
    if language == ENGLISH:
        return f"Table {table_no}"
    return f"表{table_no}"


def _extract_table_caption_parts(line: str) -> tuple[str, str] | None:
    text = line.strip()
    text = _CAPTION_TRIM_RE.sub("", text)
    caption_match = _CHINESE_CAPTION_PARTS_RE.match(text)
    if not caption_match:
        caption_match = _ENGLISH_CAPTION_PARTS_RE.match(text)
    if not caption_match:
        return None
    label = _WHITESPACE_RE.sub(" ", caption_match.group("label")).strip()
    if label.startswith("表"):
        label = _WHITESPACE_RE.sub("", label)
    title = clean_caption_text(caption_match.group("title"))
    return label, title


def _find_following_table_caption(lines: list[str], table_end: int) -> tuple[int, str, str] | None:
    for index in range(table_end, len(lines)):
        if not lines[index].strip():
            continue
        caption_parts = _caption_parts_from_plain_caption_line(lines[index])
        if caption_parts:
            label, title = caption_parts
            return index, label, title
        caption_parts = _caption_parts_from_caption_line(lines[index])
        if caption_parts:
            label, title = caption_parts
            return index, label, title
        return None
    return None


def _find_previous_table_caption(lines: list[str], table_start: int) -> tuple[int, str, str] | None:
    """Find a caption placed immediately before the current table, not after a prior table."""
    for index in range(table_start - 1, -1, -1):
        if not lines[index].strip():
            continue
        if not _is_table_caption_line(lines[index]):
            return None
        for before_caption in range(index - 1, -1, -1):
            if not lines[before_caption].strip():
                continue
            if _is_markdown_table_row(lines[before_caption]):
                return None
            break
        caption_parts = _caption_parts_from_caption_line(lines[index])
        if caption_parts:
            label, title = caption_parts
            return index, label, title
        return None
    return None


def _pop_trailing_table_caption(lines: list[str]) -> tuple[str, str] | None:
    """Remove a caption already emitted before a table so it can be normalized below."""
    index = len(lines) - 1
    while index >= 0 and not lines[index].strip():
        index -= 1
    if index < 0 or not _is_table_caption_line(lines[index]):
        return None
    caption_parts = _caption_parts_from_caption_line(lines[index])
    del lines[index:]
    return caption_parts


def _is_plain_table_context_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("#", "|", ">", "```", "~~~", "$$")):
        return False
    if _is_table_caption_line(stripped):
        return False
    if _LIST_ITEM_RE.match(stripped):
        return False
    if _IMAGE_LINE_RE.match(stripped):
        return False
    if _HTML_BLOCK_RE.match(stripped):
        return False
    return True


def _is_table_context_boundary_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return (
        stripped.startswith("#")
        or stripped.startswith(("```", "~~~", "$$"))
        or _is_markdown_table_row(stripped)
    )


def _find_previous_table_context_lines(lines: list[str], limit: int = 5) -> list[int]:
    indexes: list[int] = []
    for index in range(len(lines) - 1, -1, -1):
        if not lines[index].strip():
            continue
        if _is_table_context_boundary_line(lines[index]):
            break
        if _is_plain_table_context_line(lines[index]):
            indexes.append(index)
            if len(indexes) >= limit:
                break
    return indexes


def _find_next_table_context_lines(lines: list[str], start: int, limit: int = 5) -> list[int]:
    indexes: list[int] = []
    for index in range(start, len(lines)):
        if not lines[index].strip():
            continue
        if _is_table_context_boundary_line(lines[index]):
            break
        if _is_plain_table_context_line(lines[index]):
            indexes.append(index)
            if len(indexes) >= limit:
                break
    return indexes


def _line_has_table_label(line: str, table_label: str) -> bool:
    text = _HTML_TAG_RE.sub("", line)
    compact_text = _WHITESPACE_RE.sub("", text).lower()
    compact_label = _WHITESPACE_RE.sub("", table_label).lower()
    return bool(compact_label and compact_label in compact_text)


def _rewrite_previous_table_reference(line: str, table_label: str, language: str) -> tuple[str, bool]:
    if _line_has_table_label(line, table_label):
        return line, True
    if language == ENGLISH:
        new_line = line
        for pattern, replacement in _ENGLISH_PREVIOUS_REFERENCE_PATTERNS:
            new_line, count = pattern.subn(replacement.format(table_label=table_label), new_line, count=1)
            if count:
                return new_line, True
        weak_match = _ENGLISH_PREVIOUS_WEAK_RE.search(line)
        if weak_match:
            return line[:weak_match.start()] + f"{table_label} " + line[weak_match.start():], True
        return line, False

    new_line = line
    for pattern, replacement in _CHINESE_PREVIOUS_REFERENCE_PATTERNS:
        new_line, count = pattern.subn(replacement.format(table_label=table_label), new_line, count=1)
        if count:
            return new_line, True

    weak_match = _CHINESE_PREVIOUS_WEAK_RE.search(line)
    if weak_match:
        return line[:weak_match.start()] + table_label + line[weak_match.start():], True
    return line, False


def _rewrite_next_table_reference(line: str, table_label: str, language: str) -> tuple[str, bool]:
    if _line_has_table_label(line, table_label):
        return line, True
    if language == ENGLISH:
        def _preserve_initial_case(match: re.Match[str], replacement: str) -> str:
            if match.group(0)[:1].isupper():
                return replacement[:1].upper() + replacement[1:]
            return replacement

        new_line = line
        for pattern, replacement in _ENGLISH_NEXT_REFERENCE_PATTERNS:
            replacement_text = replacement.format(table_label=table_label)
            new_line, count = pattern.subn(
                lambda match, repl=replacement_text: _preserve_initial_case(match, repl),
                new_line,
                count=1,
            )
            if count:
                return new_line, True
        return line, False

    new_line = line
    for pattern, replacement in _CHINESE_NEXT_REFERENCE_PATTERNS:
        new_line, count = pattern.subn(replacement.format(table_label=table_label), new_line, count=1)
        if count:
            return new_line, True
    return line, False


def _build_table_intro_line(language: str, table_label: str, title: str) -> str:
    title = clean_caption_text(title, max_len=80)
    if language == ENGLISH:
        if title:
            return f"{table_label} summarizes {title}."
        return f"{table_label} summarizes the key data in this section."
    if title:
        return f"{table_label}梳理了{title}："
    return f"{table_label}梳理了本节相关核心数据："


def _merge_table_intro_into_colon_line(
    line: str,
    table_label: str,
    title: str,
    language: str,
) -> tuple[str, bool]:
    """Merge an auto-built table intro into a preceding colon-ended sentence."""
    if not _COLON_END_RE.search(line):
        return line, False
    intro_line = _build_table_intro_line(language, table_label, title)
    intro_line = intro_line.strip().rstrip(" ：:.")
    previous_line = _COLON_END_RE.sub("", line.rstrip())
    separator = ", " if language == ENGLISH else "，"
    end_mark = ":" if language == ENGLISH else "："
    return f"{previous_line}{separator}{intro_line}{end_mark}", True


def _set_line_override(line_overrides: dict[int, str], index: int, value: str) -> None:
    """Store a future line rewrite without silently replacing another table's rewrite."""
    existing = line_overrides.get(index)
    if existing is not None and existing != value:
        logger.debug(
            "Skip conflicting table-reference rewrite at line %s: existing=%r new=%r",
            index,
            existing,
            value,
        )
        return
    line_overrides[index] = value


def _update_code_fence_state(line: str, code_fence_marker: str | None) -> tuple[str | None, bool, bool]:
    fence_match = _FENCE_RE.match(line)
    if not fence_match:
        return code_fence_marker, False, False
    marker = fence_match.group(1)
    if code_fence_marker is None:
        return marker, True, False
    if marker == code_fence_marker:
        return None, True, False
    logger.warning(
        "Mismatched Markdown code fence marker: opened with %s but saw %s; leaving code block open.",
        code_fence_marker,
        marker,
    )
    return code_fence_marker, True, True


def ensure_markdown_table_captions(
    md_text: str | None,
    language: str,
    section_idx: str | int | None = "",
) -> str | None:
    """Add deterministic captions and nearby table-number references to Markdown tables."""
    if not md_text:
        return md_text

    section_idx_text = _normalize_section_idx(section_idx)
    lines = md_text.splitlines()
    output_lines: list[str] = []
    # 代码块/数学块内的竖线内容不参与 Markdown 表格识别
    code_fence_marker: str | None = None
    in_math_block = False
    current_section_title = ""
    table_idx = 0
    i = 0
    line_overrides: dict[int, str] = {}

    while i < len(lines):
        # 后置引用改写会暂存在 line_overrides，等扫描到原行号时再输出
        line = line_overrides.get(i, lines[i])

        # fenced code 只允许相同 marker 闭合；混用 marker 时 helper 会记录 warning
        updated_code_fence_marker, is_fence_line, _ = _update_code_fence_state(line, code_fence_marker)
        if is_fence_line:
            code_fence_marker = updated_code_fence_marker
            output_lines.append(line)
            i += 1
            continue

        if _MATH_BLOCK_RE.match(line):
            in_math_block = not in_math_block
            output_lines.append(line)
            i += 1
            continue

        if code_fence_marker is None and not in_math_block:
            # 用最近的标题作为兜底 caption 的语义来源之一
            heading_match = _HEADING_RE.match(line)
            if heading_match:
                current_section_title = heading_match.group(1)

            if _is_markdown_table_start(lines, i):
                # 识别到标准 pipe table 后，一次性收集整张表；相邻表无空行时也要拆开
                table_start = i
                table_lines = []
                while i < len(lines) and _is_markdown_table_row(lines[i]):
                    if table_lines and _is_markdown_table_start(lines, i):
                        break
                    table_lines.append(lines[i])
                    i += 1

                table_end = i
                table_idx += 1
                table_label = format_table_label(language, section_idx_text, table_idx)
                title = ""
                # caption 标题优先级：表格下方显式 caption > 表格上方旧 caption > 上下文/标题/表头兜底
                following_caption = _find_following_table_caption(lines, table_end)
                caption_index = None
                if following_caption:
                    caption_index, _, existing_title = following_caption
                    title = existing_title or title
                else:
                    previous_caption = _find_previous_table_caption(lines, table_start)
                    if previous_caption:
                        _, _, existing_title = previous_caption
                        popped_caption = _pop_trailing_table_caption(output_lines)
                        if popped_caption:
                            _, popped_title = popped_caption
                            existing_title = existing_title or popped_title
                        title = existing_title or title

                prev_context_indexes = _find_previous_table_context_lines(output_lines)
                # 前文上下文只取已输出内容，避免跨越当前表或未处理内容
                previous_context_lines = [
                    output_lines[context_index]
                    for context_index in prev_context_indexes
                ]
                if not title:
                    title = _build_table_caption_title(
                        table_lines,
                        previous_context_lines,
                        current_section_title,
                        language,
                    )

                has_nearby_reference = False
                # 优先把表前最近几行里的“下表/如下”等泛指改成确定表号
                for prev_context_index in prev_context_indexes:
                    rewritten, has_reference = _rewrite_previous_table_reference(
                        output_lines[prev_context_index],
                        table_label,
                        language,
                    )
                    output_lines[prev_context_index] = rewritten
                    has_nearby_reference = has_nearby_reference or has_reference
                    if has_reference:
                        break
                if not has_nearby_reference and prev_context_indexes:
                    # 若表前句只用冒号引出内容，则把自动引导句合并进去，避免额外插入新段落
                    nearest_prev_context_index = prev_context_indexes[0]
                    rewritten, has_reference = _merge_table_intro_into_colon_line(
                        output_lines[nearest_prev_context_index],
                        table_label,
                        title,
                        language,
                    )
                    output_lines[nearest_prev_context_index] = rewritten
                    has_nearby_reference = has_nearby_reference or has_reference

                next_start = (caption_index + 1) if caption_index is not None else table_end
                next_context_indexes = _find_next_table_context_lines(lines, next_start)
                # 表后引用还没输出，先写入 line_overrides，主循环走到原行号时再保存
                for next_context_index in next_context_indexes:
                    rewritten, has_reference = _rewrite_next_table_reference(
                        line_overrides.get(next_context_index, lines[next_context_index]),
                        table_label,
                        language,
                    )
                    _set_line_override(line_overrides, next_context_index, rewritten)
                    has_nearby_reference = has_nearby_reference or has_reference
                    if has_reference:
                        break

                if not has_nearby_reference:
                    # 前后都没有可改写引用时，补一条表前引导句，确保正文显式提到表号
                    if output_lines and output_lines[-1].strip():
                        output_lines.append("")
                    output_lines.append(_build_table_intro_line(language, table_label, title))
                    output_lines.append("")

                # 表格原文保持不变，统一在表格下方输出规范化 caption；下方原始 caption 会被跳过
                output_lines.extend(table_lines)
                output_lines.append("")
                output_lines.append(_format_table_caption_with_label(language, table_label, title))
                i = (caption_index + 1) if caption_index is not None else table_end
                continue

        output_lines.append(line)
        i += 1

    return "\n".join(output_lines)
