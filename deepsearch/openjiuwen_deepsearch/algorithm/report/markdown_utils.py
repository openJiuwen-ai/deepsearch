# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import logging
import re
from openjiuwen_deepsearch.algorithm.report.report_common import (
    EFFECT_SUB_REPORT_TAG,
    LEADING_TITLE_NUMBER_PATTERN,
    INTERNAL_CALLBACK_LABEL_PATTERN,
    MERMAID_SYNTAX_LINE_PATTERN,
    FENCED_BLOCK_PATTERN,
)
from openjiuwen_deepsearch.algorithm.report.report_utils import ArticlePart
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

# 匹配已插入的章节锚点行（含尾随换行），用于清理后重新插入，保证幂等
_CHAPTER_ANCHOR_LINE_RE = re.compile(
    r'<a\b(?=[^>]*\bid\s*=\s*["\']chapter-\d+["\'])[^>]*>\s*</a>\r?\n?',
    flags=re.IGNORECASE,
)


def _convert_bold_formula_to_inline_math(content: str) -> str:
    """把 LLM 误用加粗(**..**)包裹的数学公式转为内联 ``$..$`` 格式。

    根因：摘要/结论提示词只要求"关键信息加粗"，缺公式格式指导，导致 LLM 把
    公式当作关键信息用 ``**..**`` 包裹。本函数作为后处理兜底，仅处理高置信度
    公式特征(等式+数学符号、指数、LaTeX 命令、数学函数、根号)，避免误伤普通
    加粗文本(百分比、关键词、数据对比)。
    """
    def _is_formula(text: str) -> bool:
        if "=" in text:
            # 等式需额外含数学符号，避免误判含 = 的普通加粗
            return bool(re.search(
                r"[_^\\\u00b7\u00d7\u221a]|[\u03b1-\u03c9\u0391-\u03a9]"
                r"|\b(?:ln|log|sqrt|sin|cos|tan|exp)\b",
                text,
            ))
        if "^" in text:
            return True
        if re.search(r"\\(?:frac|sum|int|sqrt)\b", text):
            return True
        if re.search(r"\b(?:ln|log|sqrt|sin|cos|tan|exp)\s*\(", text):
            return True
        if "\u221a" in text and re.search(r"[A-Za-z0-9]", text):
            return True
        return False

    def _convert(match: re.Match) -> str:
        inner = match.group(1)
        return f"${inner}$" if _is_formula(inner) else match.group(0)

    return re.sub(r"\*\*([^*]+)\*\*", _convert, content)


class MarkdownProcessorMixin:
    """Mixin providing markdown processing utilities for report generation."""

    @staticmethod
    def strip_leading_number(s: str) -> str:
        """移除标题前导编号并返回清洗后的文本。"""
        return LEADING_TITLE_NUMBER_PATTERN.sub("", s)

    @classmethod
    def clean_markdown_headers(cls, md_text: str) -> str:
        """
        Process Markdown text:
        1. Remove numbering from H1-H3 headers (e.g. "一、", "(一)", "1.", "(1)", "（1）").
        2. Convert H4+ headers to unordered list items and remove numbering.
        """

        def clean_header(line: str, level: int) -> str:
            """
            Generic header cleanup helper.
            level is the header level (number of '#').
            """
            content = re.sub(rf'^\s*{"#" * level}\s*', "", line).strip()
            content = cls.strip_leading_number(content).strip()
            return f'{"#" * level} {content}'.rstrip()

        lines = md_text.splitlines()
        new_lines = []

        for line in lines:
            stripped = line.strip()

            # Handle H1-H3 uniformly
            if stripped.startswith("# "):
                new_lines.append(clean_header(line, 1))
            elif stripped.startswith("## "):
                new_lines.append(clean_header(line, 2))
            elif stripped.startswith("### "):
                new_lines.append(clean_header(line, 3))

            # H4 and deeper headers
            elif re.match(r"^\s*#{4,}\s+", line):
                content = re.sub(r"^\s*#{4,}\s+", "", line).strip()
                content = cls.strip_leading_number(content).strip()
                transferred_header = f"- **{content}**"
                new_lines.append(transferred_header)

            else:
                new_lines.append(line)

        return "\n".join(new_lines)

    @staticmethod
    def check_chapter_format(text, section_idx) -> tuple[bool, str]:
        """Validate subsection outline plain-text numbering"""
        try:
            n = section_idx
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not lines:
                return False, "outline is empty"

            # Subsection: "1.1 title".
            # Section: "1 title" (title may start with digits, e.g. 2025) or "1. title" but not "1.1".
            escaped_n = re.escape(str(n))
            sub_pat = re.compile(rf"{escaped_n}\.(\d+)\s+.+")
            main_space_pat = re.compile(rf"{escaped_n}\s+.+")
            main_dot_pat = re.compile(rf"{escaped_n}\.(?!\d)\s*.+")
            third_pat = re.compile(r"^\d+\.\d+\.\d+")

            has_main = False
            sub_numbers = []

            for line_no, ln in enumerate(lines, start=1):
                if ln.lstrip().startswith("#"):
                    preview = ln[:120] + ("..." if len(ln) > 120 else "")
                    return (
                        False,
                        f"line {line_no}: markdown heading not allowed "
                        f"(use plain '{n} title' / '{n}.1 title', not '#'): {preview!r}",
                    )
                if third_pat.search(ln):
                    preview = ln[:120] + ("..." if len(ln) > 120 else "")
                    return (
                        False,
                        f"line {line_no}: third-level numbering not allowed (e.g. {n}.1.1): {preview!r}",
                    )
                sub_match = sub_pat.fullmatch(ln)
                if sub_match:
                    if not has_main:
                        preview = ln[:120] + ("..." if len(ln) > 120 else "")
                        return (
                            False,
                            f"line {line_no}: subsection appears before level-1 title: {preview!r}",
                        )
                    sub_numbers.append(int(sub_match.group(1)))
                elif main_space_pat.fullmatch(ln) or main_dot_pat.fullmatch(ln):
                    if line_no != 1:
                        preview = ln[:120] + ("..." if len(ln) > 120 else "")
                        return (
                            False,
                            f"line {line_no}: level-1 title must be the first non-empty line: {preview!r}",
                        )
                    if has_main:
                        preview = ln[:120] + ("..." if len(ln) > 120 else "")
                        return (
                            False,
                            f"line {line_no}: duplicate level-1 title for section {n}: {preview!r}",
                        )
                    has_main = True
                else:
                    preview = ln[:120] + ("..." if len(ln) > 120 else "")
                    if re.match(r"\d+", ln):
                        return (
                            False,
                            f"line {line_no}: line starts with digits but is not a valid "
                            f"'{n} title' or '{n}.x' subsection title: {preview!r}",
                        )
                    return (
                        False,
                        f"line {line_no}: unexpected content; only "
                        f"'{n} title' and '{n}.x subsection title' are allowed: {preview!r}",
                    )

            sorted_subs = sorted(set(sub_numbers))
            if not sorted_subs:
                if has_main:
                    return True, ""
                return (
                    False,
                    f"missing level-1 title line like '{n} section title' "
                    f"(found {len(lines)} non-empty line(s))",
                )
            if sub_numbers[0] != 1:
                return (
                    False,
                    f"first subsection must be {n}.1, got {n}.{sub_numbers[0]} "
                    f"(subsection indices found: {sub_numbers})",
                )
            if not has_main:
                return (
                    False,
                    f"missing level-1 title line like '{n} section title' "
                    f"(subsection indices found: {sorted_subs})",
                )
            expected_sub_numbers = list(range(1, len(sub_numbers) + 1))
            if sub_numbers != expected_sub_numbers:
                return (
                    False,
                    f"subsections must be unique, ordered, and consecutive "
                    f"(expected {expected_sub_numbers}, got {sub_numbers})",
                )
            return True, ""
        except Exception as e:
            if LogManager.is_sensitive():
                return False, f"format check exception for section_idx={section_idx}"
            return False, f"format check exception for section_idx={section_idx}: {e}"

    @classmethod
    def is_valid_chapter_format(cls, text, section_idx) -> bool:
        """Check chapter format"""
        ok, reason = cls.check_chapter_format(text, section_idx)
        if not ok:
            logger.warning(
                "%s [is_valid_chapter_format] section_idx=%s invalid: %s",
                EFFECT_SUB_REPORT_TAG,
                section_idx,
                reason,
            )
        return ok

    @classmethod
    def _normalize_heading_title(cls, title: str) -> str:
        title = cls.strip_leading_number(title or "")
        title = re.sub(r"\s+", " ", title).strip()
        return title

    @classmethod
    def _extract_outline_heading_pairs(cls, sub_section_outline: str) -> list[tuple[int, str]]:
        pairs: list[tuple[int, str]] = []
        for line in sub_section_outline.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            level = 1 if not pairs else 2
            pairs.append((level, cls._normalize_heading_title(stripped)))
        return pairs

    @classmethod
    def _extract_markdown_heading_pairs(cls, content: str) -> list[tuple[int, str]]:
        pairs: list[tuple[int, str]] = []
        for line in content.splitlines():
            match = re.match(r"^\s*(#{1,2})\s+(.+?)\s*$", line)
            if not match:
                continue
            level = len(match.group(1))
            pairs.append((level, cls._normalize_heading_title(match.group(2))))
        return pairs

    @classmethod
    def validate_sub_report_headings_match_outline(
        cls,
        content: str,
        sub_section_outline: str,
    ) -> tuple[bool, str]:
        """Ensure every outline heading appears in the generated report in order.

        Extra headings beyond the outline are allowed; missing, mismatched,
        or out-of-order outline headings cause failure.
        """
        expected_pairs = cls._extract_outline_heading_pairs(sub_section_outline)
        actual_pairs = cls._extract_markdown_heading_pairs(content)

        if not expected_pairs:
            return False, "expected subsection outline headings are empty"
        if not actual_pairs:
            return False, "generated report headings are empty"

        if len(actual_pairs) < len(expected_pairs):
            return (
                False,
                f"heading count insufficient: expected at least {len(expected_pairs)}, got {len(actual_pairs)}",
            )

        search_from = 0
        for expected_level, expected_title in expected_pairs:
            target = (expected_level, expected_title)
            try:
                pos = actual_pairs.index(target, search_from)
            except ValueError:
                return (
                    False,
                    f"outline heading not found: expected H{expected_level} "
                    f"'{expected_title}' not present in generated report",
                )
            search_from = pos + 1

        if len(set(actual_pairs)) != len(actual_pairs):
            return False, "duplicate headings detected in generated report"

        return True, ""

    @staticmethod
    def _extract_level_one_headings(sub_reports_content: str) -> list[dict]:
        """Extract real Markdown H1 headings while ignoring fenced code blocks."""
        headings = []
        fence_char = ""
        fence_length = 0

        offset = 0
        for line in (sub_reports_content or "").splitlines(keepends=True):
            content_line = line.rstrip("\r\n")
            if fence_char:
                closing_fence = re.match(r"^\s{0,3}(`{3,}|~{3,})\s*$", content_line)
                if closing_fence:
                    marker = closing_fence.group(1)
                    if marker[0] == fence_char and len(marker) >= fence_length:
                        fence_char = ""
                        fence_length = 0
                offset += len(line)
                continue

            opening_fence = re.match(r"^\s{0,3}(`{3,}|~{3,})", content_line)
            if opening_fence:
                marker = opening_fence.group(1)
                fence_char = marker[0]
                fence_length = len(marker)
                offset += len(line)
                continue

            heading_match = re.match(r"^\s{0,3}#(?!#)\s+(.+?)\s*$", content_line)
            if heading_match:
                heading = re.sub(
                    r"[ \t]+#+[ \t]*$", "", heading_match.group(1)
                ).strip()
                if heading:
                    headings.append({"title": heading, "offset": offset})
            offset += len(line)

        return headings

    @classmethod
    def _build_table_of_contents(cls, sub_reports_content: str, language: str) -> str:
        """Build a clickable level-one TOC from the final body headings."""
        headings = cls._extract_level_one_headings(sub_reports_content)
        toc_title = ArticlePart.get_title("toc", language).strip()
        if not headings:
            return toc_title

        toc_entries = "\n\n".join(
            "[{0}](#chapter-{1})".format(heading["title"], index)
            for index, heading in enumerate(headings, start=1)
        )
        return f"{toc_title}\n\n{toc_entries}"

    @classmethod
    def _add_chapter_anchor_ids(cls, sub_reports_content: str) -> str:
        """Insert ``<a id="chapter-N"></a>`` on a separate line after each H1.

        The TOC links to ``#chapter-N``, so each body heading referenced by the
        TOC must carry a matching HTML anchor for the link to be clickable in
        the native Markdown report. Anchors are placed on their own line
        immediately AFTER the H1 heading line, keeping the heading text clean so
        downstream consumers (section_locator, truth_verification,
        new_task_processor) that parse heading titles via
        ``^(#{1,6})\\s+(.*\\S)`` are not polluted. When downstream H1 splitting
        assigns content to the chapter starting at each H1, the anchor falls at
        the start of its own chapter's content rather than leaking into the
        previous chapter. Exporters strip these anchors when converting to
        HTML/DOCX and rely on the ``{#chapter-N}`` attribute instead.
        """
        if not sub_reports_content:
            return sub_reports_content
        # 清理已有锚点，保证幂等：对已锚点内容再调不会叠加重复 ID
        sub_reports_content = _CHAPTER_ANCHOR_LINE_RE.sub("", sub_reports_content)
        headings = cls._extract_level_one_headings(sub_reports_content)
        if not headings:
            return sub_reports_content

        newline = "\r\n" if "\r\n" in sub_reports_content else "\n"
        # 从后往前插入，避免偏移量被先前的插入破坏
        for index in range(len(headings), 0, -1):
            offset = headings[index - 1]["offset"]
            # 定位 H1 行末尾的换行符，在其后插入独立锚点行
            line_end = sub_reports_content.find("\n", offset)
            if line_end == -1:
                # H1 是最后一行且无尾随换行，先补换行再插锚点，确保锚点在独立行
                insert_pos = len(sub_reports_content)
                anchor = f'{newline}<a id="chapter-{index}"></a>{newline}'
            else:
                insert_pos = line_end + 1
                anchor = f'<a id="chapter-{index}"></a>{newline}'
            sub_reports_content = (
                sub_reports_content[:insert_pos] + anchor + sub_reports_content[insert_pos:]
            )
        return sub_reports_content

    @staticmethod
    def _contains_mermaid_source(content: str) -> bool:
        """Detect Mermaid source in a chapter draft without modifying the draft.

        Chart source is owned by the controlled chart pipeline, so a chapter
        draft must not contain Mermaid source. This validator deliberately
        rejects invalid output and lets the existing bounded retry loop request
        a new draft; it never removes arbitrary report text after generation.
        """
        if not content:
            return False

        for block in FENCED_BLOCK_PATTERN.finditer(content):
            info = block.group("info").strip().lower()
            body = block.group("body")
            if "mermaid" in info or MERMAID_SYNTAX_LINE_PATTERN.search(body):
                return True

        return False

    @staticmethod
    def _clean_internal_callback_labels(content: str) -> str:
        """Remove leaked dependency-context labels while preserving natural callbacks."""
        if not content:
            return ""
        return INTERNAL_CALLBACK_LABEL_PATTERN.sub("", content)
